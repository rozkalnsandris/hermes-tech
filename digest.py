#!/usr/bin/env python3
"""Hermes Tech — dienas digest ģenerators.
1. Paņem pēdējo 36h rakstus no SQLite
2. DeepSeek V4 Flash izvēlas top 5 un uzraksta digest Hermes Tech balsī
3. Regex pārbauda aizliegtos vārdus (cietais filtrs, neatkarīgs no modeļa)
4. Saglabā digests/YYYY-MM-DD.md un nosūta uz Telegram apstiprināšanai
5. Ping healthchecks.io (ja konfigurēts)
"""
import json
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = Path.home() / "hermes-tech"
DB = BASE / "data" / "hermes.db"
LOG = BASE / "logs" / "digest.log"
DIGESTS = BASE / "digests"
ENV_FILE = BASE / ".env"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"  # NB: deepseek-chat alias mirst 2026-07-24

FORBIDDEN = re.compile(
    r"\b(revolutionary|game[- ]?chang\w*|amazing|incredible|"
    r"next[- ]level|disruptive|cutting[- ]edge)\b",
    re.IGNORECASE,
)

MAX_ARTICLES_IN = 60      # cik rakstus dodam modelim izvēlei
MAX_OUT_TOKENS = 5000     # 2500 bija par maz — JSON tika nogriezts
MAX_TG_CHUNK = 3900       # Telegram ziņas limits ir 4096


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


def fetch_candidates(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT id, source, title, link, summary FROM articles
           WHERE digest_date IS NULL
             AND fetched_at >= datetime('now', '-36 hours')
           ORDER BY id DESC LIMIT ?""",
        (MAX_ARTICLES_IN,),
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


def build_user_prompt(today: str, articles: list[dict], retry_note: str = "") -> str:
    return (
        f"Today is {today}. Below are candidate articles collected in the last "
        f"36 hours, as a JSON list.\n\n{json.dumps(articles, ensure_ascii=False)}\n\n"
        "Task: select the 5 most important items for platform/DevOps engineers. "
        "Scoring factors: official source, covered by multiple sources, security "
        "importance, community interest, industry impact. Then write the daily "
        "digest in the Hermes Tech voice defined in the system prompt, in English, "
        "following the daily digest format from STYLE.md "
        f"(title: 'What mattered in DevOps yesterday — {today}'). "
        "Per topic: 2-3 sentences (what + why it matters), the source link, and a "
        "one-line Hermes take. Plain markdown, no HTML."
        + retry_note +
        "\n\nReturn strictly a JSON object: "
        '{"selected_ids": [list of chosen article id numbers], '
        '"digest": "the full digest as markdown"}'
    )


def send_telegram(env: dict, text: str) -> bool:
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log("Telegram nav konfigurēts (.env) — digest tikai failā")
        return False
    ok = True
    for i in range(0, len(text), MAX_TG_CHUNK):
        chunk = text[i:i + MAX_TG_CHUNK]
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk,
                  "disable_web_page_preview": True},
            timeout=30,
        )
        if not r.ok:
            log(f"Telegram kļūda: {r.status_code} {r.text[:200]}")
            ok = False
    return ok


def ping_healthcheck(env: dict) -> None:
    url = env.get("HEALTHCHECK_URL", "")
    if not url:
        return
    try:
        urllib.request.urlopen(url, timeout=10)
        log("Healthcheck ping OK")
    except Exception as exc:  # noqa: BLE001
        log(f"Healthcheck ping neizdevās: {exc}")


def main() -> int:
    env = load_env()
    api_key = env.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        log("KĻŪDA: DEEPSEEK_API_KEY nav ~/.hermes-tech/.env — apstājos")
        return 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB)
    articles = fetch_candidates(conn)
    if len(articles) < 3:
        log(f"Par maz kandidātu ({len(articles)}) — digest netiek ģenerēts")
        ping_healthcheck(env)  # pipeline dzīvs, vienkārši nav satura
        return 0

    system = load_persona()
    log(f"Kandidāti: {len(articles)}, prompta persona: {len(system)} rakstz.")

    warning = ""
    raw = call_deepseek(api_key, system, build_user_prompt(today, articles))
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE)
        result = json.loads(raw)

    digest = result.get("digest", "").strip()
    selected = result.get("selected_ids", [])

    # Cietais aizliegto vārdu filtrs — 1 retry, tad brīdinājums
    hits = FORBIDDEN.findall(digest)
    if hits:
        log(f"Aizliegtie vārdi: {hits} — retry")
        retry_note = (
            " IMPORTANT: your previous draft used marketing words that are "
            "banned in this voice. Rewrite without any of these words or their "
            "variants: revolutionary, game changer, amazing, incredible, "
            "next level, disruptive, cutting edge."
        )
        raw = call_deepseek(api_key, system,
                            build_user_prompt(today, articles, retry_note))
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
        log("KĻŪDA: tukšs digest no modeļa")
        return 1

    # Atzīmējam izmantotos rakstus + avotu statistiku
    ids = [int(i) for i in selected if str(i).isdigit()]
    if ids:
        q = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE articles SET digest_date = ? WHERE id IN ({q})",
            [today, *ids],
        )
        conn.execute(
            f"""UPDATE sources SET picked = picked + 1 WHERE name IN
                (SELECT source FROM articles WHERE id IN ({q}))""",
            ids,
        )
        conn.commit()

    DIGESTS.mkdir(parents=True, exist_ok=True)
    out = DIGESTS / f"{today}.md"
    out.write_text(digest + "\n")
    log(f"Digest saglabāts: {out}")

    header = (f"{warning}📰 Hermes Tech digest — {today}\n"
              f"(APSTIPRINĀŠANAI — publicēšana Fāzē 3)\n\n")
    send_telegram(env, header + digest)
    ping_healthcheck(env)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
