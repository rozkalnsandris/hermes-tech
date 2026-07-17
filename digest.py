#!/usr/bin/env python3
"""Hermes Tech — digest ģenerators v3.
Lietošana: digest.py [devops|ai|agents]   (noklusējums: devops)
Jaunumi: kategoriju atbalsts + faktu pārbaudes atruna promptā.
"""
import html as htmllib
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = Path.home() / "hermes-tech"
DB = BASE / "data" / "hermes.db"
LOG = BASE / "logs" / "digest.log"
DIGESTS = BASE / "digests"
ENV_FILE = BASE / ".env"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"

CATS = {
    "devops": {
        "title": "What mattered in DevOps yesterday",
        "audience": "platform and DevOps engineers running production systems",
        "label": "📰 DEVOPS",
    },
    "ai": {
        "title": "What mattered in AI yesterday",
        "audience": ("engineers following AI models, products and platform "
                     "changes (new models, pricing, limits, capabilities)"),
        "label": "🧠 AI",
    },
    "agents": {
        "title": "What mattered in AI agents yesterday",
        "audience": ("engineers building and running AI agents and automation "
                     "(agent frameworks, releases, tooling, real-world usage)"),
        "label": "🤖 AGENTS",
    },
}

SECTIONS = {"devops": "digest", "ai": "ai", "agents": "agents"}

FORBIDDEN = re.compile(
    r"\b(revolutionary|game[- ]?chang\w*|amazing|incredible|"
    r"next[- ]level|disruptive|cutting[- ]edge)\b",
    re.IGNORECASE,
)

MAX_ARTICLES_IN = 60
MAX_OUT_TOKENS = 5000
MAX_TG_CHUNK = 3900


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load_persona() -> str:
    parts = []
    for name in ("SOUL.md", "STYLE.md", "VALUES.md"):
        p = BASE / name
        if p.exists():
            parts.append(p.read_text())
        else:
            log(f"BRĪDINĀJUMS: trūkst {name}")
    return "\n\n---\n\n".join(parts)


def fetch_candidates(conn, category: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, source, title, link, summary FROM articles
           WHERE digest_date IS NULL
             AND category = ?
             AND fetched_at >= datetime('now', '-36 hours')
           ORDER BY id DESC LIMIT ?""",
        (category, MAX_ARTICLES_IN),
    ).fetchall()
    return [
        {"id": r[0], "source": r[1], "title": r[2], "link": r[3],
         "summary": (r[4] or "")[:300]}
        for r in rows
    ]


def call_deepseek(api_key: str, system: str, user: str) -> str:
    resp = requests.post(
        DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "temperature": 0.4,
            "max_tokens": MAX_OUT_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage", {})
    choice = data["choices"][0]
    finish = choice.get("finish_reason", "")
    log(f"Tokeni: in={usage.get('prompt_tokens')} "
        f"(cache hit={usage.get('prompt_cache_hit_tokens')}) "
        f"out={usage.get('completion_tokens')} finish={finish}")
    if finish == "length":
        raise RuntimeError(
            f"Atbilde nogriezta pie {MAX_OUT_TOKENS} tokeniem — "
            "palielini MAX_OUT_TOKENS vai samazini MAX_ARTICLES_IN"
        )
    return choice["message"]["content"]


def build_user_prompt(cat: str, today: str, articles: list[dict],
                      retry_note: str = "") -> str:
    meta = CATS[cat]
    return (
        f"Today is {today}. Below are candidate articles collected in the last "
        f"36 hours for the '{cat}' section, as a JSON list.\n\n"
        f"{json.dumps(articles, ensure_ascii=False)}\n\n"
        f"Task: select the 5 most important items for {meta['audience']}. "
        "Scoring factors: official source, covered by multiple sources, "
        "security importance, community interest, industry impact. "
        "Fact-checking rule: if an important claim appears in only one source "
        "and is not from an official/first-party source, state explicitly in "
        "that item that the information is not yet fully confirmed. "
        "Then write the daily digest in the Hermes Tech voice defined in the "
        "system prompt, in English, following the daily digest format from "
        f"STYLE.md, with the title '{meta['title']} — {today}'. "
        "Per topic: 2-3 sentences (what + why it matters), the source link "
        "as one plain markdown link, and a substantive Hermes analysis (70–110 words, 4–6 sentences). "
        "Plain markdown, no HTML."
        + retry_note +
        "\n\nReturn strictly a JSON object: "
        '{"selected_ids": [list of chosen article id numbers], '
        '"digest": "the full digest as markdown"}'
    )


def md_to_tg_html(text: str) -> str:
    """Digest markdown → Telegram HTML (b/i/a tagi, escaped)."""
    t = htmllib.escape(text)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.DOTALL)
    t = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"<b>\1</b>", t)
    t = re.sub(r"(?m)^Hermes: (.+)$", r"💬 <i>\1</i>", t)
    return t


def chunk_paragraphs(text: str, limit: int) -> list[str]:
    """Sadala tekstu pa rindkopām, nepārraujot HTML tagus vidū."""
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        candidate = f"{cur}\n\n{para}" if cur else para
        if len(candidate) <= limit:
            cur = candidate
        else:
            if cur:
                chunks.append(cur)
            cur = para[:limit]  # ārkārtas apgriešana ļoti garai rindkopai
    if cur:
        chunks.append(cur)
    return chunks


# HERMES_CRON_SAFETY_V2
def send_telegram(env: dict, text: str) -> bool:
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log("Telegram nav konfigurēts (.env) — digest tikai failā")
        return False

    ok = True
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in chunk_paragraphs(text, MAX_TG_CHUNK):
        try:
            r = requests.post(
                api,
                json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=30,
            )
            if r.status_code == 400:
                # Bojāts HTML — fallback uz plain tekstu bez tagiem.
                plain = re.sub(r"<[^>]+>", "", chunk)
                r = requests.post(
                    api,
                    json={"chat_id": chat_id, "text": plain,
                          "disable_web_page_preview": True},
                    timeout=30,
                )
                log("Telegram: HTML noraidīts, nosūtīts plain fallback")
            if not r.ok:
                log(f"Telegram kļūda: {r.status_code} {r.text[:200]}")
                ok = False
        except requests.RequestException as exc:
            log(f"Telegram tīkla kļūda: {exc}")
            ok = False
    return ok


def main() -> int:
    cat = sys.argv[1] if len(sys.argv) > 1 else "devops"
    if cat not in CATS:
        log(f"KĻŪDA: nezināma kategorija '{cat}' (devops|ai|agents)")
        return 1

    env = load_env()
    api_key = env.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        log("KĻŪDA: DEEPSEEK_API_KEY nav .env — apstājos")
        return 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB, timeout=30)
    articles = fetch_candidates(conn, cat)
    if len(articles) < 3:
        log(f"[{cat}] KĻŪDA: par maz kandidātu ({len(articles)}) — digest netiek ģenerēts")
        conn.close()
        return 1

    system = load_persona()
    # HERMES_HUMAN_STYLE_V2
    system += """

MANDATORY HUMAN WRITING STYLE FOR EVERY `💬 Hermes:` ANALYSIS:
- Write like an experienced DevOps/SRE or platform engineer explaining the
  practical meaning to another technical professional.
- Sound natural, confident, thoughtful, and conversational, while remaining
  technically precise and professional.
- Start with the strongest concrete judgment, consequence, or recommendation.
  Do not open by restating the headline or saying that the article discusses it.
- Vary sentence length and pacing. Mix short, direct sentences with somewhat
  longer explanations. Avoid repetitive or predictable sentence structures.
- Prefer plain, specific language. Use technical terms only when they improve
  accuracy; do not replace clear wording with corporate or academic jargon.
- Add a real opinion grounded in the supplied facts: what changes, why it
  matters operationally, who is affected, and what a practical team should do.
- Preserve uncertainty and source caveats. Never invent facts, dates, numbers,
  product behavior, or official confirmation that the source does not support.
- Do not add fake emotion, marketing hype, dramatic claims, filler, or forced
  casual language. The goal is a credible practitioner voice, not a persona act.
- Avoid canned AI-style openings and transitions, including: "This development
  highlights", "It is important to note", "It is worth noting", "This serves
  as a reminder", "This underscores the importance", "In today's rapidly
  evolving landscape", "As technology continues to evolve", and "In conclusion".
- Keep the existing output schema, Markdown structure, selected_ids, source
  links, article count, and all other formatting requirements unchanged.
"""

    def validate_hermes_style(markdown: str) -> list[str]:
        """Reject canned AI phrasing inside Hermes analysis blocks."""
        marker_re = re.compile(
            r"(?m)^[ \t]*(?:>[ \t]*)?(?:💬[ \t]*)?Hermes:[ \t]*"
        )
        boundary_re = re.compile(
            r"\n[ \t]*\n(?=[ \t]*(?:#{1,6}[ \t]+|\*\*[^\n]+\*\*))"
        )
        banned = (
            "this development highlights",
            "it is important to note",
            "it is worth noting",
            "this serves as a reminder",
            "this underscores the importance",
            "in today's rapidly evolving landscape",
            "in today’s rapidly evolving landscape",
            "as technology continues to evolve",
            "in conclusion",
            "the ever-evolving landscape",
            "game-changing development",
        )
        starts = list(marker_re.finditer(markdown))
        issues: list[str] = []

        for index, match in enumerate(starts, start=1):
            tail = markdown[match.end():]
            boundary = boundary_re.search(tail)
            block = tail[:boundary.start()] if boundary else tail
            block = re.sub(r"(?m)^[ \t]*>[ \t]?", "", block).strip()
            normalized = re.sub(r"\s+", " ", block).lower()

            found = [phrase for phrase in banned if phrase in normalized]
            if found:
                issues.append(
                    f"Hermes analīze #{index}: šabloniska AI frāze '{found[0]}'"
                )

            if re.search(r"(?m)^[ \t]*[-*][ \t]+", block):
                issues.append(
                    f"Hermes analīze #{index}: jābūt dabiskai rindkopai, ne sarakstam"
                )

        return issues
    # HERMES_ANALYSIS_DEPTH_V1
    # Šie noteikumi ir augstākas prioritātes par veco īsā komentāra prasību.
    system += """

MANDATORY HERMES ANALYSIS DEPTH FOR EVERY SELECTED ARTICLE:
- The `💬 Hermes:` section is analysis, not a slogan or closing remark.
- Write 70–110 words in 4–6 complete sentences, as one compact paragraph.
- Explain why the development matters operationally, who is affected, and
  what an engineer or team should consider doing next.
- Include one concrete risk, limitation, trade-off, or source caveat when
  it is relevant to the supplied material.
- Do not repeat the title or merely paraphrase the article summary.
- Prefer specific DevOps, SRE, platform, security, cost, reliability, or
  maintainability consequences over generic enthusiasm.
- Use only facts supported by the supplied article data. Do not invent
  product behavior, dates, numbers, or official confirmation.
- Preserve the existing JSON schema, selected_ids, Markdown structure,
  article count, source links, and all other output requirements exactly.
- Any earlier request for one sentence, a short comment, a brief remark,
  or a punchline is superseded by these rules.
"""

    def validate_hermes_analyses(markdown: str, expected: int | None) -> list[str]:
        """Reject slogan-length Hermes blocks before anything is published."""
        marker_re = re.compile(
            r"(?m)^[ \t]*(?:>[ \t]*)?(?:💬[ \t]*)?Hermes:[ \t]*"
        )
        starts = list(marker_re.finditer(markdown))
        issues: list[str] = []

        if expected and len(starts) != expected:
            issues.append(
                f"Hermes analīžu skaits {len(starts)}, bet selected_ids skaits {expected}"
            )

        if not starts:
            issues.append("digestā nav neviena Hermes analīzes bloka")
            return issues

        boundary_re = re.compile(
            r"\n[ \t]*\n(?=[ \t]*(?:#{1,6}[ \t]+|\*\*[^\n]+\*\*))"
        )
        word_re = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’_-]*")
        sentence_re = re.compile(r"[.!?](?=(?:[\"'”’)\]]*)?(?:\s|$))")

        for index, match in enumerate(starts, start=1):
            tail = markdown[match.end():]
            boundary = boundary_re.search(tail)
            block = tail[:boundary.start()] if boundary else tail
            block = re.sub(r"(?m)^[ \t]*>[ \t]?", "", block).strip()
            words = len(word_re.findall(block))
            sentences = len(sentence_re.findall(block))

            # Prompta mērķis ir 70–110 un 4–6; validators atstāj nelielu
            # toleranci, bet vienas rindas saukļus vairs neielaiž publicēšanā.
            if not 60 <= words <= 140:
                issues.append(
                    f"Hermes analīze #{index}: {words} vārdi; atļauts 60–140"
                )
            if not 4 <= sentences <= 7:
                issues.append(
                    f"Hermes analīze #{index}: {sentences} teikumi; atļauts 4–7"
                )

        return issues
    log(f"[{cat}] Kandidāti: {len(articles)}, persona: {len(system)} rakstz.")

    warning = ""
    raw = call_deepseek(api_key, system, build_user_prompt(cat, today, articles))
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE)
        result = json.loads(raw)

    digest = result.get("digest", "").strip()
    selected = result.get("selected_ids", [])

    hits = FORBIDDEN.findall(digest)
    if hits:
        log(f"[{cat}] Aizliegtie vārdi: {hits} — retry")
        retry_note = (
            " IMPORTANT: your previous draft used marketing words that are "
            "banned in this voice. Rewrite without any of these words or their "
            "variants: revolutionary, game changer, amazing, incredible, "
            "next level, disruptive, cutting edge."
        )
        raw = call_deepseek(api_key, system,
                            build_user_prompt(cat, today, articles, retry_note))
        try:
            result = json.loads(raw)
            digest = result.get("digest", "").strip()
            selected = result.get("selected_ids", selected)
        except json.JSONDecodeError:
            log("Retry atbilde nav JSON — palieku pie pirmās versijas")
        hits = FORBIDDEN.findall(digest)
        if hits:
            warning = (f"⚠️ UZMANĪBU: digestā palika aizliegtie vārdi: "
                       f"{', '.join(set(h.lower() for h in hits))}\n\n")

    if not digest:
        log(f"[{cat}] KĻŪDA: tukšs digest no modeļa")
        conn.close()
        return 1

    style_issues = validate_hermes_style(digest)
    if style_issues:
        for issue in style_issues:
            log(f"[{cat}] KĻŪDA: {issue}")
        log(f"[{cat}] Publicēšana apturēta: Hermes teksts skan pārāk šabloniski")
        conn.close()
        return 1

    analysis_issues = validate_hermes_analyses(
        digest,
        len(selected) if isinstance(selected, list) else None,
    )
    if analysis_issues:
        for issue in analysis_issues:
            log(f"[{cat}] KĻŪDA: {issue}")
        log(f"[{cat}] Publicēšana apturēta: Hermes analīze nav pietiekami izvērsta")
        conn.close()
        return 1

    # HERMES_PUBLISH_SAFETY_V2
    # Modelis drīkst izvēlēties tikai ID no šīs palaišanas kandidātu saraksta.
    if not isinstance(selected, list):
        log(f"[{cat}] KĻŪDA: selected_ids nav saraksts")
        conn.close()
        return 1

    candidate_ids = {article["id"] for article in articles}
    ids = []
    ignored_ids = []
    for value in selected:
        if isinstance(value, bool):
            ignored_ids.append(value)
            continue
        if isinstance(value, int):
            article_id = value
        elif isinstance(value, str) and value.isdigit():
            article_id = int(value)
        else:
            ignored_ids.append(value)
            continue

        if article_id not in candidate_ids:
            ignored_ids.append(value)
        elif article_id not in ids:
            ids.append(article_id)

    if ignored_ids:
        log(f"[{cat}] Ignorēti nederīgi selected_ids: {ignored_ids}")

    if not ids:
        log(f"[{cat}] KĻŪDA: modelis neatdeva nevienu derīgu selected_id")
        conn.close()
        return 1

    # DB vēl netiek mainīta. ID tiek nodoti publish.sh paslēptā metadatu rindā.
    DIGESTS.mkdir(parents=True, exist_ok=True)
    out = DIGESTS / f"{today}-{cat}.md"
    metadata = ",".join(str(article_id) for article_id in ids)
    out.write_text(
        f"<!-- selected_ids: {metadata} -->\n{digest}\n",
        encoding="utf-8",
    )
    log(f"[{cat}] Digest saglabāts: {out}")

    published = False
    publish_note = ""
    if warning:
        # Aizliegtie vārdi palika arī pēc retry — NEpublicējam automātiski
        publish_note = ("\n\n🚫 <b>NAV publicēts automātiski</b> — aizliegtie "
                         "vārdi palika arī pēc pārrakstīšanas. Pārbaudi manuāli:\n"
                         f"<code>~/hermes-tech/publish.sh {cat} {today}</code>")
        log(f"[{cat}] Auto-publish IZLAISTS (aizliegtie vārdi palika)")
    else:
        try:
            proc = subprocess.run(
                [str(BASE / "publish.sh"), cat, today],
                check=True, capture_output=True, text=True, timeout=90,
            )
            if proc.stderr.strip():
                log(f"[{cat}] publish.sh brīdinājums: {proc.stderr[:300]}")
            published = True
            url = f"https://tech.rozkalns.net/{SECTIONS[cat]}/{today}/"
            publish_note = f'\n\n✅ <a href="{url}">Publicēts: /{SECTIONS[cat]}/{today}/</a>'
            log(f"[{cat}] Auto-publish OK")
        except subprocess.CalledProcessError as exc:
            details = exc.stderr or exc.stdout or str(exc)
            err = htmllib.escape(details[:300])
            publish_note = (f"\n\n⚠️ <b>Auto-publish neizdevās:</b> {err}\n"
                            f"Manuāli: <code>~/hermes-tech/publish.sh {cat} {today}</code>")
            log(f"[{cat}] Auto-publish KĻŪDA: {details[:300]}")
        except subprocess.TimeoutExpired:
            publish_note = (
                "\n\n⚠️ <b>Auto-publish neizdevās:</b> "
                "publish.sh pārsniedza 90 sekunžu limitu.\n"
                f"Manuāli: <code>~/hermes-tech/publish.sh {cat} {today}</code>"
            )
            log(f"[{cat}] Auto-publish KĻŪDA: pārsniegts 90 sekunžu limits")
        except OSError as exc:
            err = htmllib.escape(str(exc)[:300])
            publish_note = (f"\n\n⚠️ <b>Auto-publish neizdevās:</b> {err}\n"
                            f"Manuāli: <code>~/hermes-tech/publish.sh {cat} {today}</code>")
            log(f"[{cat}] Auto-publish KĻŪDA: {exc}")

    # Telegram versija: izmetam pirmo virsrakstu (dublē header), konvertējam uz HTML
    body_md = re.sub(r"^#{1,6}[^\n]*\n+", "", digest.strip())
    tg_body = md_to_tg_html(body_md)
    tg_warning = htmllib.escape(warning) if warning else ""
    header = (f"{tg_warning}{CATS[cat]['label']} <b>{CATS[cat]['title']}</b>\n"
              f"<i>{today}</i>\n{'─' * 22}\n\n")
    telegram_ok = send_telegram(env, header + tg_body + publish_note)
    if not telegram_ok:
        log(f"[{cat}] BRĪDINĀJUMS: Telegram paziņojums netika pilnībā nosūtīts")

    conn.close()
    if not published:
        log(f"[{cat}] KĻŪDA: digests netika publicēts")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
