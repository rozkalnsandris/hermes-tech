#!/usr/bin/env python3
"""Hermes Tech — digest ģenerators v4.
Ar globalo event router, cross-category validāciju un diversity.

Lietošana:
  digest.py classify                              # globālā klasifikācija
  digest.py digest <devops|ai|agents>             # per-category digest
  digest.py validate                              # cross-category validācija
  digest.py publish <devops|ai|agents> <date>     # publicē individuālu digest
"""
from __future__ import annotations

import html as htmllib
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE = Path.home() / "hermes-tech"
DB = BASE / "data" / "hermes.db"
LOG = BASE / "logs" / "digest.log"
RUNS = BASE / "data" / "runs"
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
MAX_CLASSIFY_BATCH = 15
MAX_OUT_TOKENS = 16000
MAX_TG_CHUNK = 3900
FETCH_HOURS = 36


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


def load_editorial_context() -> str:
    editorial_dir = BASE / "editorial"
    parts = []
    for name in ("VOICE.md", "WRITING.md", "REVIEW.md"):
        p = editorial_dir / name
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
        else:
            log(f"BRĪDINĀJUMS: trūkst editorial/{name}")
    return "\n\n---\n\n".join(parts)

# HERMES_DEEPSEEK_V4_JSON_V30
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_JSON_MAX_ATTEMPTS = 3
DEEPSEEK_JSON_BACKOFF_SECONDS = (0, 2, 5)


def _deepseek_expected_schema(user: str) -> str:
    if "selected_ids" in user and '"digest"' in user: return "digest"
    if '"events"' in user or "events:" in user: return "events"
    return "object"


def _validate_deepseek_structure(payload: dict, schema: str) -> str | None:
    if schema == "digest":
        if not isinstance(payload.get("selected_ids"), list): return "selected_ids nav list"
        if not isinstance(payload.get("digest"), str): return "digest nav string"
    elif schema == "events" and not isinstance(payload.get("events"), list):
        return "events nav list"
    return None


def call_deepseek(api_key: str, system: str, user: str) -> str:
    schema=_deepseek_expected_schema(user); last_error="unknown"
    for attempt in range(1,DEEPSEEK_JSON_MAX_ATTEMPTS+1):
        if attempt>1:
            delay=DEEPSEEK_JSON_BACKOFF_SECONDS[attempt-1]
            log(f"DeepSeek retry {attempt}/{DEEPSEEK_JSON_MAX_ATTEMPTS} pēc {delay}s")
            time.sleep(delay)
        retry_note="" if attempt==1 else (
            "\n\nJSON RETRY REQUIREMENT: Return exactly one valid JSON object matching the requested schema. "
            "No markdown fences. Correctly escape quotes, backslashes, newlines, tabs and control characters."
        )
        try:
            resp=requests.post(DEEPSEEK_URL,headers={
                "Authorization":f"Bearer {api_key}","Content-Type":"application/json"},json={
                "model":DEEPSEEK_MODEL,
                "messages":[{"role":"system","content":system},{"role":"user","content":user+retry_note}],
                "max_tokens":MAX_OUT_TOKENS,
                "response_format":{"type":"json_object"},
                "thinking":{"type":"disabled"}},timeout=180)
        except requests.RequestException as exc:
            last_error=f"transport: {exc}"; log(f"DeepSeek {last_error}")
            if attempt<DEEPSEEK_JSON_MAX_ATTEMPTS: continue
            raise RuntimeError(last_error) from exc
        if resp.status_code==429 or 500<=resp.status_code<=599:
            last_error=f"HTTP {resp.status_code}: {(resp.text or '')[:250]}"; log(f"DeepSeek transient {last_error}")
            if attempt<DEEPSEEK_JSON_MAX_ATTEMPTS: continue
            raise RuntimeError(last_error)
        try: resp.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"DeepSeek non-retryable HTTP {resp.status_code}: {(resp.text or '')[:500]}") from exc
        try:
            data=resp.json(); choice=data["choices"][0]
        except (ValueError,KeyError,IndexError,TypeError) as exc:
            last_error=f"invalid API envelope: {exc}"
            if attempt<DEEPSEEK_JSON_MAX_ATTEMPTS: continue
            raise RuntimeError(last_error) from exc
        finish=choice.get("finish_reason"); usage=data.get("usage") or {}
        log(f"Tokeni: in={usage.get('prompt_tokens','?')} (cache hit={usage.get('prompt_cache_hit_tokens',0)}) "
            f"out={usage.get('completion_tokens','?')} finish={finish} model={DEEPSEEK_MODEL}")
        if finish=="length":
            raise RuntimeError(f"DeepSeek output nogriezts pie {MAX_OUT_TOKENS} tokeniem; identisks retry netiek veikts")
        if finish=="content_filter": raise RuntimeError("DeepSeek finish_reason=content_filter")
        if finish=="tool_calls": raise RuntimeError("DeepSeek negaidīti atgrieza tool_calls")
        if finish=="insufficient_system_resource":
            last_error=finish
            if attempt<DEEPSEEK_JSON_MAX_ATTEMPTS: continue
            raise RuntimeError(last_error)
        if finish not in (None,"stop"):
            last_error=f"unexpected finish_reason={finish!r}"
            if attempt<DEEPSEEK_JSON_MAX_ATTEMPTS: continue
            raise RuntimeError(last_error)
        content=((choice.get("message") or {}).get("content") or "").strip()
        if not content:
            last_error="empty JSON content"; log(f"DeepSeek {last_error}")
            if attempt<DEEPSEEK_JSON_MAX_ATTEMPTS: continue
            raise RuntimeError(last_error)
        clean=re.sub(r"^```(?:json)?\s*|\s*```$","",content,flags=re.IGNORECASE).strip()
        try: parsed=json.loads(clean)
        except json.JSONDecodeError as exc:
            a=max(0,exc.pos-100); b=min(len(clean),exc.pos+100)
            last_error=(f"JSONDecodeError line={exc.lineno} col={exc.colno}: {exc.msg}; fragment={clean[a:b]!r}")
            log(f"DeepSeek malformed JSON: {last_error}")
            if attempt<DEEPSEEK_JSON_MAX_ATTEMPTS: continue
            raise RuntimeError(last_error) from exc
        if not isinstance(parsed,dict):
            last_error=f"JSON root is {type(parsed).__name__}, expected object"
            if attempt<DEEPSEEK_JSON_MAX_ATTEMPTS: continue
            raise RuntimeError(last_error)
        structure_error=_validate_deepseek_structure(parsed,schema)
        if structure_error:
            last_error=structure_error; log(f"DeepSeek schema mismatch: {structure_error}")
            if attempt<DEEPSEEK_JSON_MAX_ATTEMPTS: continue
            raise RuntimeError(last_error)
        if attempt>1: log(f"DeepSeek JSON retry izdevās ({attempt}/{DEEPSEEK_JSON_MAX_ATTEMPTS})")
        return json.dumps(parsed,ensure_ascii=False)
    raise RuntimeError(last_error)




# ---------------------------------------------------------------------------
# MD → Telegram HTML (existing)
# ---------------------------------------------------------------------------

def md_to_tg_html(text: str) -> str:
    t = htmllib.escape(text)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.DOTALL)
    t = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"<b>\1</b>", t)
    t = re.sub(r"(?m)^Hermes: (.+)$", r"💬 <i>\1</i>", t)
    return t


def chunk_paragraphs(text: str, limit: int) -> list[str]:
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        candidate = f"{cur}\n\n{para}" if cur else para
        if len(candidate) <= limit:
            cur = candidate
        else:
            if cur:
                chunks.append(cur)
            cur = para[:limit]
    if cur:
        chunks.append(cur)
    return chunks


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


# ---------------------------------------------------------------------------
# STEP: CLASSIFY — global event router
# ---------------------------------------------------------------------------

def fetch_all_candidates(conn) -> list[dict]:
    """Visas kandidātu rindas, kam nav primary_category un ir 36h logā."""
    rows = conn.execute(
        """SELECT id, source, title, link, summary, category, content
           FROM articles
           WHERE primary_category IS NULL
             AND fetched_at >= datetime('now', ?)
           ORDER BY id DESC LIMIT 500""",
        (f"-{FETCH_HOURS} hours",),
    ).fetchall()
    return [
        {"id": r[0], "source": r[1], "title": r[2], "link": r[3],
         "summary": (r[4] or "")[:300], "feed_cat": r[5],
         "content_length": len(r[6] or "")}
        for r in rows
    ]


def build_classify_system_prompt() -> str:
    return """You are a news routing classifier for Hermes Tech, a DevOps/SRE/AI/Agents technology publication.

Your job: group articles about the same real-world event, assign each group a normalized topic_key, classify the group into exactly one primary category, and select the best source(s).

CLASSIFICATION RULES:

AI category (primary subject = models, AI research, benchmarks):
- Foundation model releases, model benchmarks, training/inference advances
- Embeddings, multimodal models, quantization, AI safety research
- A model release is AI even if it can power agents or run on infrastructure

AGENTS category (primary subject = AI agent systems):
- Agent frameworks, coding agents (Claude Code, Codex), agent orchestration
- Multi-agent systems, MCP, tool use, agent memory, agent runtimes
- Agent-specific security, evaluation, observability
- NOT general model releases even if they can power agents

DEVOPS category (primary subject = infrastructure/operations):
- Kubernetes, cloud, CI/CD, observability, databases, networking, containers
- Deployment, SRE, platform engineering, operational security
- AI infrastructure ONLY when production operations are the primary subject

REJECT: tutorial-style content, personal blogs without original reporting,
enterprise product announcements without technical depth, opinion pieces
without evidence, content clearly outside the Hermes Tech scope.

IMPORTANT: Assign one topic_key per real-world event. Multiple articles
covering the same event must share the same topic_key.

topic_key format: kebab-case, e.g. "moonshot-kimi-k3-release"

IDENTITY FIELDS (required for every event):
- "primary_entity": the main software, product, company or project name
  (e.g. "moonshot-kimi-k3", "crewai", "claude-code", "netflix")
- "event_type": one of: "release", "security-advisory", "acquisition",
  "open-source", "benchmark", "partnership", "research", "incident",
  "conference", "regulation", "funding", "opinion"
- "version": explicit version string if the event is about a specific
  release/version (e.g. "1.15.4", "k3", "975b", "v2.1.212").
  Use null if no version is involved.

Return a JSON object with an "events" array where each event has:
{
  "topic_key": "kebab-case-event-name",
  "primary_category": "devops|ai|agents|reject",
  "primary_entity": "string — canonical entity name",
  "event_type": "string — the event type",
  "version": "string or null — explicit version",
  "confidence": 0.0-1.0,
  "reason": "Brief explanation",
  "article_ids": [list of all article IDs],
  "best_source_ids": [article ID(s) for primary source]
}"""


def build_classify_user_prompt(articles: list[dict],
                                known_events: list[dict] | None = None) -> str:
    prompt = (
        f"Today is {datetime.now(timezone.utc).strftime('%Y-%m-%d')}. "
        f"Below are {len(articles)} candidate articles, as a JSON list.\n\n"
        f"{json.dumps(articles, ensure_ascii=False)}\n\n"
        "Group articles covering the same real-world event.\n"
        "Assign one topic_key and one primary_category per event.\n"
        "Use exactly the categories: devops, ai, agents, reject.\n"
    )
    if known_events:
        prompt += (
            "\nAlready known events from previous batches (do NOT create "
            "duplicate topic_keys; if a new article belongs to an existing "
            "event, assign the SAME topic_key):\n"
            f"{json.dumps(known_events, ensure_ascii=False)}\n\n"
        )
    prompt += (
        "CRITICAL: Every article_id from the input list must appear in "
        "exactly one event's article_ids. Do not skip any article.\n"
        "Return JSON: {events: [{topic_key, primary_category, confidence, "
        "reason, article_ids, best_source_ids}]}"
    )
    return prompt


# HERMES_CLASSIFY_DUPLICATE_RETRY_V1
class DuplicateArticleAssignmentError(RuntimeError):
    """One article ID was assigned to more than one event in one LLM response."""


def classify_batch(api_key: str, articles: list[dict],
                   known_events: list[dict] | None = None) -> list[dict]:
    if not articles:
        return []
    article_ids_in = {a["id"] for a in articles}
    system = build_classify_system_prompt()
    user = build_classify_user_prompt(articles, known_events)
    raw = call_deepseek(api_key, system, user)
    # Defensive parse
    raw_clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(),
                       flags=re.MULTILINE)
    result = json.loads(raw_clean)
    events = result.get("events", [])
    if not isinstance(events, list):
        raise RuntimeError(
            f"'events' nav saraksts pēc JSON parse: {type(events)}"
        )

    # Validate: every input article_id appears exactly once
    # Validate identity fields
    required_str_fields = ["primary_entity", "event_type"]
    covered_ids: set[int] = set()
    for ev in events:
        # Auto-fill missing identity fields for robustness
        if not ev.get("primary_entity"):
            ev["primary_entity"] = ev.get("topic_key", "unknown")
        if not ev.get("event_type"):
            ev["event_type"] = "release"
        for field in required_str_fields:
            if not isinstance(ev.get(field), str) or not ev[field]:
                raise RuntimeError(
                    f"Notikumam '{ev.get('topic_key', '?')}' trūkst "
                    f"obligātā lauka '{field}'"
                )
        ver = ev.get("version")
        if ver is not None and (not isinstance(ver, str) or not ver):
            raise RuntimeError(
                f"Notikumam '{ev.get('topic_key', '?')}' version "
                f"ir jābūt string vai null, nevis {type(ver)}"
            )
        for aid in ev.get("article_ids", []):
            if not isinstance(aid, int):
                raise RuntimeError(
                    f"Nederīgs article_id tipa {type(aid)}: {aid}"
                )
            if aid in covered_ids:
                raise DuplicateArticleAssignmentError(
                    f"article_id {aid} parādās vairāk kā vienā notikumā"
                )
            if aid not in article_ids_in:
                raise RuntimeError(
                    f"article_id {aid} nav šī batch kandidātos"
                )
            covered_ids.add(aid)

    missing = article_ids_in - covered_ids
    if missing:
        log(f"TRŪKST: {len(missing)} article_id nav segti pirmajā mēģinājumā: "
            f"{sorted(missing)}")
        return events, sorted(missing)

    return events, []


def topic_key_similarity(k1: str, k2: str) -> float:
    """Jaccard similarity on words extracted from kebab-case keys."""
    w1 = set(k1.lower().split("-"))
    w2 = set(k2.lower().split("-"))
    # Filter out generic words
    generic = {"and", "the", "a", "an", "for", "in", "of", "to", "with",
               "new", "release", "update", "latest", "announces"}
    w1 = w1 - generic
    w2 = w2 - generic
    if not w1 or not w2:
        return 0.0
    intersection = w1 & w2
    union = w1 | w2
    return len(intersection) / len(union)


def global_reconciliation(events: list[dict]) -> list[dict]:
    """Globāla event reconciliation — identity-based + similarity.

    Rules:
    1. Exact identity (entity + event_type + version) → merge
    2. Both have version AND versions differ → HARD BLOCK (never merge)
    3. Similarity ≥ 0.5 → merge candidate, but override by rule 2
    """
    if len(events) < 2:
        return events

    merged = [dict(e) for e in events]  # mutable copy

    def identity_key(ev: dict) -> tuple:
        return (
            ev.get("primary_entity", ""),
            ev.get("event_type", ""),
            ev.get("version"),
        )

    def has_explicit_version(ev: dict) -> bool:
        v = ev.get("version")
        return isinstance(v, str) and bool(v.strip())

    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(merged):
            j = i + 1
            while j < len(merged):
                ki = merged[i]
                kj = merged[j]
                ei = identity_key(ki)
                ej = identity_key(kj)

                # Rule 1: exact identity match → merge
                can_merge = (ei == ej)

                if not can_merge:
                    # Rule 2: both have version AND versions differ → HARD BLOCK
                    vi = has_explicit_version(ki)
                    vj = has_explicit_version(kj)
                    ver_i = ki.get("version")
                    ver_j = kj.get("version")
                    if vi and vj and ver_i != ver_j:
                        # Different versions of same software — NEVER merge
                        log(
                            f"BLOCK (version): {ki['topic_key']} "
                            f"(v={ver_i}) ↔ {kj['topic_key']} (v={ver_j})"
                        )
                        j += 1
                        continue

                    # Rule 3: similarity check for merge candidate
                    sim = topic_key_similarity(
                        ki.get("topic_key", ""),
                        kj.get("topic_key", ""),
                    )
                    can_merge = sim >= 0.5

                if can_merge:
                    # Merge j into i
                    merged[i]["article_ids"] = sorted(
                        set(ki.get("article_ids", []))
                        | set(kj.get("article_ids", []))
                    )
                    merged[i]["best_source_ids"] = sorted(
                        set(ki.get("best_source_ids", []))
                        | set(kj.get("best_source_ids", []))
                    )
                    # Use more canonical topic_key (shorter)
                    if len(kj.get("topic_key", "")) < len(
                            ki.get("topic_key", "")):
                        merged[i]["topic_key"] = kj["topic_key"]
                    # Merge identity fields
                    if not ki.get("primary_entity") and kj.get("primary_entity"):
                        merged[i]["primary_entity"] = kj["primary_entity"]
                    if not ki.get("version") and kj.get("version"):
                        merged[i]["version"] = kj["version"]

                    # Category conflict: pick higher confidence
                    pc_i = ki.get("primary_category", "reject")
                    pc_j = kj.get("primary_category", "reject")
                    if pc_i != pc_j:
                        conf_i = ki.get("confidence", 0)
                        conf_j = kj.get("confidence", 0)
                        if conf_j > conf_i:
                            merged[i]["primary_category"] = pc_j
                            merged[i]["confidence"] = conf_j
                            merged[i]["reason"] = kj.get(
                                "reason", ki.get("reason", "")
                            )

                    # Log with reason
                    if ei == ej:
                        reason = "identity"
                        sim_val = 0.0
                    else:
                        reason = f"sim={sim:.2f}"
                        sim_val = sim
                    log(
                        f"MERGE: {ki['topic_key']} ↔ {kj['topic_key']} "
                        f"({reason}, cat={merged[i]['primary_category']}, "
                        f"ids={len(merged[i]['article_ids'])}"
                    )
                    merged.pop(j)
                    changed = True
                else:
                    j += 1
            i += 1

    # Remove any events with empty article_ids
    merged = [e for e in merged if e.get("article_ids")]
    return merged


def write_routing_manifest(manifest_date: str, events: list[dict]) -> Path:
    """Saglabā routing manifestu data/runs/YYYY-MM-DD-routing.json"""
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{manifest_date}-routing.json"
    manifest = {
        "digest_date": manifest_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "events": [
            {
                "topic_key": e["topic_key"],
                "primary_category": e["primary_category"],
                "primary_entity": e.get("primary_entity", ""),
                "event_type": e.get("event_type", ""),
                "version": e.get("version"),
                "confidence": e.get("confidence", 0),
                "reason": e.get("reason", ""),
                "article_ids": sorted(e.get("article_ids", [])),
                "best_source_ids": sorted(e.get("best_source_ids", [])),
            }
            for e in events
        ],
    }
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"Routing manifests saglabāts: {path}")
    return path


def write_routing_to_db(conn, events: list[dict]) -> int:
    """Raksta primary_category, topic_key un routed_at DB."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated = 0
    for ev in events:
        pc = ev.get("primary_category", "reject")
        topic_key = ev.get("topic_key", "")
        if not topic_key:
            continue
        article_ids = ev.get("article_ids", [])
        if not article_ids:
            continue
        placeholders = ",".join("?" for _ in article_ids)
        conn.execute(
            f"UPDATE articles SET primary_category=?, topic_key=?, "
            f"routed_at=? WHERE id IN ({placeholders})",
            (pc, topic_key, now, *article_ids),
        )
        updated += conn.total_changes  # approximate; accurate enough
    conn.commit()
    # Re-read actual count
    updated = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE routed_at = ?",
        (now,),
    ).fetchone()[0]
    return updated


# HERMES_CROSS_CATEGORY_OWNER_V1
def _canonicalize_topic_owners(events: list[dict]) -> list[dict]:
    # Give every topic_key exactly one deterministic owner category.

    def confidence_value(event: dict) -> float:
        try:
            return float(event.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def owner_key(event: dict) -> tuple:
        category = str(event.get("primary_category") or "reject")
        article_ids = set(event.get("article_ids") or [])
        best_source_ids = set(event.get("best_source_ids") or [])

        # Lower tuple wins. Evidence strength comes before the lexical
        # tie-break, so ownership is independent of batch/generation order.
        return (
            1 if category == "reject" else 0,
            -len(article_ids),
            -confidence_value(event),
            -len(best_source_ids),
            category,
            str(event.get("primary_entity") or ""),
            str(event.get("event_type") or ""),
            str(event.get("version") or ""),
            json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    groups: dict[str, list[dict]] = {}
    for event in events:
        topic_key = str(event.get("topic_key") or "").strip()
        if not topic_key:
            raise RuntimeError(
                "CROSS-DEDUP: eventam trūkst topic_key; nevar noteikt owner"
            )
        groups.setdefault(topic_key, []).append(event)

    canonical: list[dict] = []
    for topic_key in sorted(groups):
        group = groups[topic_key]
        if len(group) == 1:
            canonical.append(dict(group[0]))
            continue

        winner = dict(min(group, key=owner_key))
        all_article_ids: set[int] = set()
        all_best_source_ids: set[int] = set()
        confidence_values: list[float] = []

        for event in group:
            all_article_ids.update(event.get("article_ids") or [])
            all_best_source_ids.update(event.get("best_source_ids") or [])
            confidence_values.append(confidence_value(event))

        winner["article_ids"] = sorted(all_article_ids)
        winner["best_source_ids"] = sorted(all_best_source_ids)
        winner["confidence"] = max(confidence_values, default=0.0)

        categories = sorted(
            str(event.get("primary_category") or "reject")
            for event in group
        )
        log(
            f"CROSS-DEDUP: topic_key={topic_key} "
            f"cats={categories} → {winner.get('primary_category', 'reject')} "
            f"ids={len(all_article_ids)}"
        )
        canonical.append(winner)

    seen: set[str] = set()
    for event in canonical:
        topic_key = str(event.get("topic_key") or "").strip()
        if topic_key in seen:
            raise RuntimeError(
                f"CROSS-DEDUP: topic_key {topic_key} joprojām nav unikāls"
            )
        seen.add(topic_key)

    return canonical


def step_classify(api_key: str) -> int:
    log("=== STEP: CLASSIFY — global event router ===")
    conn = sqlite3.connect(DB, timeout=30)
    candidates = fetch_all_candidates(conn)
    total = len(candidates)
    log(f"Kandidāti bez primary_category: {total}")

    if total == 0:
        log("Nav jaunu kandidātu — klasifikācija nav nepieciešama")
        conn.close()
        return 0

    # Stats tracking
    stats = {
        "input_articles": total,
        "explicitly_classified": 0,
        "retry_classified": 0,
        "missing_articles": 0,
        "fallback_reject_articles": 0,
    }

    # Build lookup: id → candidate dict
    candidate_by_id = {c["id"]: c for c in candidates}

    def _classify_batch_with_retry(
        batch: list[dict],
        known: list[dict] | None,
        retry_level: int = 0,
    ) -> list[dict]:
        """Classify a batch with up to 3 retry levels.

        retry_level=0: normal batch (MAX_CLASSIFY_BATCH articles)
        retry_level=1: small batch (max 5 articles)
        retry_level=2: single article (1 article)
        """
        try:
            events, missing = classify_batch(
                api_key, batch, known_events=known
            )
        except DuplicateArticleAssignmentError as exc:
            log(f"DUPLIKĀTS: {exc}")
            failed_ids = [article["id"] for article in batch]
            if retry_level >= 2:
                log(
                    "KĻŪDA: duplicate article assignment saglabājas arī "
                    f"single-article batch: {failed_ids}"
                )
                stats["missing_articles"] += len(failed_ids)
                return []
            events = []
            missing = failed_ids

        if not missing:
            return events

        if retry_level < 2:
            # Retry missing articles in smaller batches
            sub_size = 5 if retry_level == 0 else 1
            all_events = list(events)
            for start in range(0, len(missing), sub_size):
                sub_ids = missing[start:start + sub_size]
                sub_batch = [candidate_by_id[aid] for aid in sub_ids]
                sub_events = _classify_batch_with_retry(
                    sub_batch, known, retry_level + 1
                )
                # Count IDs classified in this retry
                retry_ids = set()
                for se in sub_events:
                    for aid in se.get("article_ids", []):
                        if aid in sub_ids:
                            retry_ids.add(aid)
                stats["retry_classified"] += len(retry_ids)
                all_events.extend(sub_events)
            return all_events
        else:
            # Last resort: single article still failed
            log(f"KĻŪDA: article_id {missing} nesekmīgi klasificēts "
                f"arī kā single-article batch")
            stats["missing_articles"] += len(missing)
            return events

    # Batch processing with known-event forwarding + retry
    all_events: list[dict] = []
    for start in range(0, total, MAX_CLASSIFY_BATCH):
        batch = candidates[start:start + MAX_CLASSIFY_BATCH]
        log(f"Batch {start // MAX_CLASSIFY_BATCH + 1}: "
            f"{len(batch)} raksti (indeksi {start}-{start + len(batch)})")

        # Forward already-known events (identity info only)
        if all_events:
            known_slim = []
            for ev in all_events[-5:]:
                known_slim.append({
                    "topic_key": ev.get("topic_key", ""),
                    "primary_category": ev.get("primary_category", ""),
                    "primary_entity": ev.get("primary_entity", ""),
                    "event_type": ev.get("event_type", ""),
                    "version": ev.get("version"),
                })
            known = known_slim
        else:
            known = None

        batch_events = _classify_batch_with_retry(batch, known, retry_level=0)
        log(f"  → {len(batch_events)} notikumi")
        all_events.extend(batch_events)

    # Count explicitly classified (from LLM) vs retry
    all_ids = set()
    for ev in all_events:
        for aid in ev.get("article_ids", []):
            if aid not in all_ids:
                all_ids.add(aid)
    stats["explicitly_classified"] = len(all_ids)

    # Apply entity-based pre-filter for reconciliation
    def event_family(ev: dict) -> str:
        """Coarse family grouping for reconciliation pre-filter."""
        entity = (ev.get("primary_entity") or "").lower().strip()
        if entity and entity not in ("unknown", "unclassified"):
            # Use first 2 significant words from entity
            words = [w for w in entity.replace("-", " ").split()
                     if w not in REJECT_GENERIC]
            if words:
                return " ".join(words[:2])
        # Fallback: use first 2 words of topic_key
        tk = (ev.get("topic_key") or "").split("-")[:2]
        return "-".join(tk) if tk else "zzz_other"

    REJECT_GENERIC = {
        "and", "the", "a", "an", "for", "in", "of", "to", "with",
        "new", "release", "update", "latest", "announces", "inc",
        "corp", "llc", "ltd", "co",
    }

    # Group events by family, reconcile within each group
    families: dict[str, list[dict]] = {}
    for ev in all_events:
        fk = event_family(ev)
        families.setdefault(fk, []).append(ev)

    reconciled: list[dict] = []
    for fk, group in families.items():
        if len(group) > 1:
            merged = global_reconciliation(group)
            reconciled.extend(merged)
        else:
            reconciled.append(group[0])

    log(f"Kopā unikāli notikumi pēc reconcile: {len(reconciled)}")

    # Deterministic topic_key dedup: apvieno notikumus ar identisku
    # topic_key un primary_category (ne LLM, tikai exact match)
    key_groups: dict[tuple[str, str], list[dict]] = {}
    for ev in reconciled:
        tk = ev.get("topic_key", "")
        pc = ev.get("primary_category", "")
        key_groups.setdefault((tk, pc), []).append(ev)

    deduped: list[dict] = []
    for (tk, pc), group in key_groups.items():
        if len(group) == 1:
            deduped.append(group[0])
            continue
        # Merge: union article_ids, union best_source_ids, highest confidence
        base = dict(group[0])
        all_ids: set[int] = set()
        all_best: set[int] = set()
        best_conf = base.get("confidence", 0)
        for ev in group:
            all_ids.update(ev.get("article_ids", []))
            all_best.update(ev.get("best_source_ids", []))
            best_conf = max(best_conf, ev.get("confidence", 0))
        base["article_ids"] = sorted(all_ids)
        base["best_source_ids"] = sorted(all_best)
        base["confidence"] = best_conf
        log(f"DEDUP: topic_key={tk} cat={pc}"
            f" ({len(group)} events→1, ids={len(all_ids)})")
        deduped.append(base)

    reconciled = deduped
    log(f"Kopā unikāli notikumi pēc topic_key dedup: {len(reconciled)}")

    reconciled = _canonicalize_topic_owners(reconciled)
    log(
        "Kopā unikāli notikumi pēc cross-category owner dedup: "
        f"{len(reconciled)}"
    )

    # Verify no article_id appears in >1 event after reconciliation
    all_ids_set = set()
    for ev in reconciled:
        for aid in ev.get("article_ids", []):
            if aid in all_ids_set:
                raise RuntimeError(
                    f"PĒC RECONCILE: article_id {aid} ir >1 notikumā!"
                )
            all_ids_set.add(aid)

    # Final stats
    stats["missing_articles"] = total - len(all_ids_set)
    if stats["missing_articles"] > 0:
        log(f"KĻŪDA: {stats['missing_articles']} raksti palika neklasificēti")
        # Attempt single-article classification for truly stuck IDs
        missing_final = total - len(all_ids_set)
        if missing_final > 0:
            stats["missing_articles"] = missing_final
            log(f"KĻŪDA: {stats['missing_articles']} raksti neklasificēti "
                f"— publicēšana atcelta")
            conn.close()
            return 1

    # Statistics log
    log("=== Klasifikācijas statistika ===")
    log(f"  input_articles:             {stats['input_articles']}")
    log(f"  explicitly_classified:      {stats['explicitly_classified']}")
    log(f"  retry_classified:           {stats['retry_classified']}")
    log(f"  missing_articles:           {stats['missing_articles']}")
    log(f"  fallback_reject_articles:   {stats['fallback_reject_articles']}")

    # Category counts
    cat_counts: dict[str, int] = {}
    for ev in reconciled:
        pc = ev.get("primary_category", "reject")
        cat_counts[pc] = cat_counts.get(pc, 0) + 1
    for cat in ("devops", "ai", "agents", "reject"):
        log(f"  {cat}: {cat_counts.get(cat, 0)}")

    # Write manifest + DB
    manifest_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    write_routing_manifest(manifest_date, reconciled)
    updated = write_routing_to_db(conn, reconciled)
    log(f"Atjaunināti DB ieraksti: {updated}")

    conn.close()
    return 0


# ---------------------------------------------------------------------------
# STEP: DIGEST — per-category digest generation (modified from v3)
# ---------------------------------------------------------------------------

def fetch_routed_candidates(conn, category: str) -> list[dict]:
    """Kandidāti ar primary_category=category, 36h logā."""
    rows = conn.execute(
        """SELECT id, source, title, link, summary, topic_key, content
           FROM articles
           WHERE primary_category = ?
             AND topic_key IS NOT NULL
             AND digest_date IS NULL
             AND fetched_at >= datetime('now', ?)
           ORDER BY id DESC""",
        (category, f"-{FETCH_HOURS} hours"),
    ).fetchall()
    return [
        {"id": r[0], "source": r[1], "title": r[2], "link": r[3],
         "summary": (r[4] or "")[:300], "topic_key": r[5] or "",
         "content_length": len(r[6] or "")}
        for r in rows
    ]


def diversity_filter(articles: list[dict], max_count: int = 15) -> list[dict]:
    """Diversity-aware pre-filter: max 1 per topic_key (HARD),
    prefer diversity of vendors/subtopics (SOFT).

    Strādā uz ~30-100 kandidātiem → atgriež max ~15 diverse.
    """
    if len(articles) <= max_count:
        return articles

    # Group by topic_key
    by_topic: dict[str, list[dict]] = {}
    for a in articles:
        tk = a.get("topic_key", "") or "unknown"
        by_topic.setdefault(tk, []).append(a)

    # One per topic_key first (HARD RULE)
    diverse = []
    for tk, group in by_topic.items():
        # Pick the one with longest content (proxy for quality)
        group.sort(key=lambda a: a.get("content_length", 0), reverse=True)
        diverse.append(group[0])

    if len(diverse) <= max_count:
        return diverse

    # SOFT penalty: vendor clustering
    vendor_patterns = {
        "google": ["google", "gcp", "gemini"],
        "aws": ["aws", "amazon"],
        "microsoft": ["microsoft", "azure", "openai"],
        "meta": ["meta", "llama"],
        "anthropic": ["anthropic", "claude"],
        "moonshot": ["moonshot", "kimi"],
        "deepseek": ["deepseek"],
        "docker": ["docker"],
        "kubernetes": ["kubernetes", "k8s"],
        "cloudflare": ["cloudflare"],
        "huggingface": ["huggingface", "hf"],
    }

    def vendor_score(a: dict) -> str:
        title_lower = a.get("title", "").lower()
        source_lower = a.get("source", "").lower()
        combined = title_lower + " " + source_lower
        for vendor, keywords in vendor_patterns.items():
            if any(k in combined for k in keywords):
                return vendor
        return "other"

    vendor_counts: dict[str, int] = {}
    scored: list[tuple[float, dict]] = []
    for a in diverse:
        v = vendor_score(a)
        vendor_counts.setdefault(v, 0)
        # Base score: high relevance (keep original order)
        score = 1.0
        # Penalize vendors that already have items
        if vendor_counts[v] >= 2:
            score -= 0.5
        elif vendor_counts[v] >= 1:
            score -= 0.2
        scored.append((score, a))

    # Sort by score descending, keep top max_count
    scored.sort(key=lambda x: -x[0])
    result = [a for _, a in scored[:max_count]]
    log(f"Diversity filter: {len(articles)} → {len(result)} "
        f"(by topic: {len(diverse)}, final: {len(result)})")
    return result


# HERMES_QUALITY_RETRY_V1
# Modelim dodam šaurāku mērķi, validatoram — drošu tolerances logu.
# Abas prasības dzīvo vienā vietā, lai prompti un validatora ziņas nedriftētu.
ANALYSIS_TARGET_WORDS = "70–110"
ANALYSIS_TARGET_SENTENCES = "4–6"
ANALYSIS_HARD_WORD_MIN = 55
ANALYSIS_HARD_WORD_MAX = 140
ANALYSIS_HARD_SENTENCE_MIN = 3
ANALYSIS_HARD_SENTENCE_MAX = 7
MAX_QUALITY_RETRIES = 2


# HERMES_DIGEST_SOURCE_RESTORE_V1
def _restore_digest_source_links(path: Path) -> None:
    """Restore source links without depending on Markdown heading levels."""
    import os as _os
    import re as _re

    text = path.read_text(encoding="utf-8")
    selected = _re.search(
        r"<!-- selected_ids:\s*([0-9,\s]+)\s*-->",
        text,
    )
    if not selected:
        raise RuntimeError(f"selected_ids metadata nav atrasta: {path}")

    selected_ids = [
        int(value.strip())
        for value in selected.group(1).split(",")
        if value.strip()
    ]

    conn = sqlite3.connect(DB)
    try:
        placeholders = ",".join("?" for _ in selected_ids)
        rows = conn.execute(
            f"SELECT id, title, link FROM articles "
            f"WHERE id IN ({placeholders})",
            selected_ids,
        ).fetchall()
    finally:
        conn.close()

    by_id = {int(row[0]): (str(row[1]), str(row[2])) for row in rows}
    if set(by_id) != set(selected_ids):
        missing = sorted(set(selected_ids) - set(by_id))
        raise RuntimeError(f"DB trūkst selected article ID: {missing}")

    lines = text.splitlines()
    hermes_re = _re.compile(
        r"^\s*💬\s*Hermes\s*:",
        _re.IGNORECASE,
    )
    heading_re = _re.compile(r"^\s*#{1,6}\s+\S")
    markdown_link_re = _re.compile(
        r"^(?:Source:\s*)?\[[^\]]+\]\(https?://[^)]+\)\s*$",
        _re.IGNORECASE,
    )

    hermes_indexes = [
        index
        for index, line in enumerate(lines)
        if hermes_re.match(line)
    ]
    if len(hermes_indexes) != len(selected_ids):
        raise RuntimeError(
            "Source restore 1:1 pārbaude neizdevās: "
            f"Hermes blocks={len(hermes_indexes)} "
            f"selected_ids={len(selected_ids)}"
        )

    previous_hermes = -1
    for article_id, hermes_index in zip(selected_ids, hermes_indexes):
        title, url = by_id[article_id]
        if not url.startswith(("http://", "https://")):
            raise RuntimeError(
                f"article_id {article_id} nav derīga HTTP(S) URL"
            )

        heading_candidates = [
            index
            for index in range(previous_hermes + 1, hermes_index)
            if heading_re.match(lines[index])
        ]
        if not heading_candidates:
            raise RuntimeError(
                f"article_id {article_id} pirms Hermes nav article heading"
            )
        heading_index = heading_candidates[-1]

        content_indexes = [
            index
            for index in range(heading_index + 1, hermes_index)
            if lines[index].strip()
        ]
        if len(content_indexes) < 2:
            raise RuntimeError(
                f"article_id {article_id} blokā nav droši identificējamas "
                "summary + source rindas"
            )

        source_index = content_indexes[-1]
        source_line = lines[source_index].strip()

        if not markdown_link_re.match(source_line):
            def _normalise(value: str) -> str:
                value = _re.sub(
                    r"^Source:\s*",
                    "",
                    value,
                    flags=_re.IGNORECASE,
                )
                value = value.replace("[", "").replace("]", "")
                value = value.replace("*", "").replace("_", "")
                value = _re.sub(r"\s+", " ", value).strip().casefold()
                return value

            source_norm = _normalise(source_line)
            title_norm = _normalise(title)
            if not source_norm or not title_norm:
                raise RuntimeError(
                    f"article_id {article_id} source/title normalizācija tukša"
                )
            if not (
                source_norm == title_norm
                or source_norm in title_norm
                or title_norm in source_norm
            ):
                raise RuntimeError(
                    f"article_id {article_id} pēdējā rinda pirms Hermes "
                    "neizskatās pēc source; fail-closed"
                )

        link_label = title.strip()
        leading_tag = _re.match(r"^\[([^\]]+)\]\s*(.*)$", link_label)
        if leading_tag:
            tag, rest = leading_tag.groups()
            link_label = f"{tag}: {rest}" if rest else tag

        # Existing formatter LINK_RE expects no nested square brackets
        # inside the link label.
        link_label = link_label.replace("[", "(").replace("]", ")")
        link_label = _re.sub(r"\s+", " ", link_label).strip()
        if not link_label:
            link_label = f"Article {article_id}"

        lines[source_index] = f"[{link_label}]({url})"
        if not markdown_link_re.match(lines[source_index]):
            raise RuntimeError(
                f"article_id {article_id} source links netika atjaunots"
            )

        previous_hermes = hermes_index

    new_text = "\n".join(lines).rstrip() + "\n"
    tmp = path.with_name(path.name + ".source-restore.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(new_text)
            handle.flush()
            _os.fsync(handle.fileno())
        _os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()



def build_digest_system_prompt(cat: str) -> str:
    persona = load_editorial_context()
    # HERMES_ANALYSIS_DEPTH_V1
    persona += f"""
MANDATORY HERMES ANALYSIS DEPTH FOR EVERY SELECTED ARTICLE:
- The `💬 Hermes:` section is analysis, not a slogan or closing remark.
- Write {ANALYSIS_TARGET_WORDS} words in {ANALYSIS_TARGET_SENTENCES} complete sentences, as one compact paragraph.
- Explain why the development matters operationally, who is affected.
- Include one concrete risk, limitation, trade-off, or source caveat.
- Do not repeat the title or merely paraphrase the article summary.
- Use only facts supported by the supplied article data.
"""
    # Diversity + dedup instruction
    persona += f"""
DIVERSITY REQUIREMENTS FOR THIS CATEGORY:
- Max 1 article per topic_key (HARD RULE).
- Avoid selecting multiple stories about the same vendor or subtopic.
- Penalize repeated sub-topics unless the story is significantly important.
- The 5 selected items should give a broad view of {cat} news today.
"""
    return persona


def build_digest_user_prompt(cat: str, today: str,
                             articles: list[dict],
                             retry_note: str = "") -> str:
    meta = CATS[cat]
    return (
        f"Today is {today}. Below are candidate articles collected in the last "
        f"{FETCH_HOURS} hours for the '{cat}' category, already classified "
        f"and diversity-filtered. Each has a topic_key.\n\n"
        f"{json.dumps(articles, ensure_ascii=False)}\n\n"
        f"Task: select the 5 most important items for {meta['audience']}. "
        "Scoring factors: official source, covered by multiple sources, "
        "security importance, community interest, industry impact. "
        "Fact-checking rule: for unconfirmed claims, state the uncertainty. "
        "DIVERSITY: do not select two articles with the same topic_key. "
        f"Then write the daily digest in the Hermes Tech voice, in English, "
        f"with the title '{meta['title']} — {today}'. "
        "Per topic: 2-3 sentences (what + why it matters), the source link "
        "as one plain markdown link, and a substantive Hermes analysis "
        f"({ANALYSIS_TARGET_WORDS} words, {ANALYSIS_TARGET_SENTENCES} sentences). "
        "Plain markdown, no HTML."
        + retry_note +
        "\n\nReturn strictly a JSON object: "
        '{"selected_ids": [list of chosen article id numbers], '
        '"digest": "the full digest as markdown"}'
    )


def validate_hermes_style(markdown: str) -> list[str]:
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
        "in today\u2019s rapidly evolving landscape",
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
                f"Hermes analīze #{index}: jābūt dabiskai rindkopai, "
                "ne sarakstam"
            )
    return issues


def validate_hermes_analyses(markdown: str,
                             expected: int | None) -> list[str]:
    marker_re = re.compile(
        r"(?m)^[ \t]*(?:>[ \t]*)?(?:💬[ \t]*)?Hermes:[ \t]*"
    )
    starts = list(marker_re.finditer(markdown))
    issues: list[str] = []
    if expected and len(starts) != expected:
        issues.append(
            f"Hermes analīžu skaits {len(starts)}, "
            f"bet selected_ids skaits {expected}"
        )
    if not starts:
        issues.append("digestā nav neviena Hermes analīzes bloka")
        return issues
    boundary_re = re.compile(
        r"\n[ \t]*\n(?=[ \t]*(?:#{1,6}[ \t]+|\*\*[^\n]+\*\*))"
    )
    word_re = re.compile(r"[A-Za-z0-9][A-Za-z0-9''_\-]*")
    sentence_re = re.compile(r"[.!?](?=(?:[\"'\u201d\u2019)\]])*(?:\s|$))")
    for index, match in enumerate(starts, start=1):
        tail = markdown[match.end():]
        boundary = boundary_re.search(tail)
        block = tail[:boundary.start()] if boundary else tail
        block = re.sub(r"(?m)^[ \t]*>[ \t]?", "", block).strip()
        words = len(word_re.findall(block))
        sentences = len(sentence_re.findall(block))
        if not ANALYSIS_HARD_WORD_MIN <= words <= ANALYSIS_HARD_WORD_MAX:
            issues.append(
                f"Hermes analīze #{index}: {words} vārdi; atļauts "
                f"{ANALYSIS_HARD_WORD_MIN}–{ANALYSIS_HARD_WORD_MAX}"
            )
        if not (
            ANALYSIS_HARD_SENTENCE_MIN
            <= sentences
            <= ANALYSIS_HARD_SENTENCE_MAX
        ):
            issues.append(
                f"Hermes analīze #{index}: {sentences} teikumi; atļauts "
                f"{ANALYSIS_HARD_SENTENCE_MIN}–{ANALYSIS_HARD_SENTENCE_MAX}"
            )
    return issues


def step_digest(api_key: str, category: str,
                dry_run: bool = False) -> int:
    conn = sqlite3.connect(DB, timeout=30)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Fetch routed candidates
    articles = fetch_routed_candidates(conn, category)
    conn.close()

    log(f"[{category}] Kandidāti ar primary_category: {len(articles)}")

    if len(articles) < 3:
        log(f"[{category}] KĻŪDA: par maz kandidātu ({len(articles)}) "
            f"— digest netiek ģenerēts")
        return 1

    # Diversity pre-filter
    articles = diversity_filter(articles, max_count=15)
    if len(articles) < 3:
        log(f"[{category}] KĻŪDA: pēc diversity filtra par maz "
            f"({len(articles)})")
        return 1

    system = build_digest_system_prompt(category)

    warning = ""
    raw = call_deepseek(api_key, system,
                        build_digest_user_prompt(category, today, articles))
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(),
                     flags=re.MULTILINE)
        result = json.loads(raw)

    digest = result.get("digest", "").strip()
    selected = result.get("selected_ids", [])

    # Retry on forbidden words
    hits = FORBIDDEN.findall(digest)
    if hits:
        log(f"[{category}] Aizliegtie vārdi: {hits} — retry")
        retry_note = (
            " IMPORTANT: your previous draft used marketing words that are "
            "banned in this voice. Rewrite without any of these words: "
            "revolutionary, game changer, amazing, incredible, "
            "next level, disruptive, cutting edge."
        )
        raw = call_deepseek(
            api_key, system,
            build_digest_user_prompt(category, today, articles, retry_note),
        )
        try:
            result = json.loads(raw)
            digest = result.get("digest", "").strip()
            selected = result.get("selected_ids", selected)
        except json.JSONDecodeError:
            log("Retry atbilde nav JSON — palieku pie pirmās versijas")
        hits = FORBIDDEN.findall(digest)
        if hits:
            warning = (
                f"⚠️ UZMANĪBU: digestā palika aizliegtie vārdi: "
                f"{', '.join(set(h.lower() for h in hits))}\n\n"
            )

    # HERMES_QUALITY_RETRY_V1
    # Stila/garuma kļūdas ir soft quality gate: dodam modelim līdz diviem
    # mērķētiem repair mēģinājumiem. Tikai pēc tam kategorija tiek noraidīta.
    quality_retry = 0
    while True:
        if not digest:
            quality_issues = ["tukšs digest no modeļa"]
        else:
            style_issues = validate_hermes_style(digest)
            analysis_issues = validate_hermes_analyses(
                digest,
                len(selected) if isinstance(selected, list) else None,
            )
            quality_issues = [*style_issues, *analysis_issues]
            forbidden_hits = FORBIDDEN.findall(digest)
            if forbidden_hits:
                quality_issues.append(
                    "aizliegtie vārdi palika tekstā: "
                    + ", ".join(sorted(set(h.lower() for h in forbidden_hits)))
                )

        if not quality_issues:
            if quality_retry:
                log(
                    f"[{category}] Kvalitātes repair izdevās "
                    f"({quality_retry}/{MAX_QUALITY_RETRIES})"
                )
            break

        for issue in quality_issues:
            log(f"[{category}] Kvalitātes problēma: {issue}")

        if quality_retry >= MAX_QUALITY_RETRIES:
            log(
                f"[{category}] Digest noraidīts pēc "
                f"{MAX_QUALITY_RETRIES} kvalitātes repair mēģinājumiem"
            )
            return 1

        quality_retry += 1
        retry_note = (
            "\n\nQUALITY REPAIR REQUIRED. The previous draft failed these "
            "validation checks:\n- "
            + "\n- ".join(quality_issues)
            + "\nRewrite the full digest while preserving factual meaning, "
              "source links, and the JSON output schema. Keep selected_ids "
              "restricted to the supplied candidate IDs. For every `💬 Hermes:` "
              f"analysis target {ANALYSIS_TARGET_WORDS} words and "
              f"{ANALYSIS_TARGET_SENTENCES} complete sentences. Hard acceptance "
              f"limits are {ANALYSIS_HARD_WORD_MIN}–{ANALYSIS_HARD_WORD_MAX} "
              "words and "
              f"{ANALYSIS_HARD_SENTENCE_MIN}–{ANALYSIS_HARD_SENTENCE_MAX} "
              "sentences. Fix every listed issue. Return JSON only."
        )
        log(
            f"[{category}] Kvalitātes repair mēģinājums "
            f"{quality_retry}/{MAX_QUALITY_RETRIES}"
        )
        raw = call_deepseek(
            api_key,
            system,
            build_digest_user_prompt(category, today, articles, retry_note),
        )
        try:
            repaired = json.loads(raw)
        except json.JSONDecodeError:
            cleaned = re.sub(
                r"^```(json)?|```$",
                "",
                raw.strip(),
                flags=re.MULTILINE,
            )
            try:
                repaired = json.loads(cleaned)
            except json.JSONDecodeError:
                log(
                    f"[{category}] Repair atbilde nav derīgs JSON — "
                    "mēģināšu vēlreiz, ja atlicis retry"
                )
                continue

        repaired_digest = repaired.get("digest", "").strip()
        repaired_selected = repaired.get("selected_ids", selected)
        if repaired_digest:
            digest = repaired_digest
        if isinstance(repaired_selected, list):
            selected = repaired_selected

    # Validate selected_ids
    if not isinstance(selected, list):
        log(f"[{category}] KĻŪDA: selected_ids nav saraksts")
        return 1

    candidate_ids = {a["id"] for a in articles}
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
        log(f"[{category}] Ignorēti nederīgi selected_ids: {ignored_ids}")
    if not ids:
        log(f"[{category}] KĻŪDA: modelis neatdeva nevienu derīgu selected_id")
        return 1

    # Save digest to file (always, even in dry-run)
    DIGESTS.mkdir(parents=True, exist_ok=True)
    out = DIGESTS / f"{today}-{category}.md"
    metadata = ",".join(str(aid) for aid in ids)
    out.write_text(
        f"<!-- selected_ids: {metadata} -->\n{digest}\n",
        encoding="utf-8",
    )
    _restore_digest_source_links(out)
    log(f"[{category}] Digest saglabāts: {out} (dry_run={dry_run})")
    return 0


# ---------------------------------------------------------------------------
# STEP: VALIDATE — cross-category check
# ---------------------------------------------------------------------------

def step_validate(api_key: str) -> int:
    """Cross-category validācija: pārbauda routing manifestu.

    Ja kāds topic_key ir >1 primary_category, atsakās publicēt.
    """
    log("=== STEP: VALIDATE — cross-category check ===")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    manifest_path = RUNS / f"{today}-routing.json"

    if not manifest_path.exists():
        log(f"KĻŪDA: nav routing manifesta: {manifest_path}")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    events = manifest.get("events", [])

    # Check topic_key uniqueness across categories
    by_key: dict[str, set[str]] = {}
    for ev in events:
        pc = ev.get("primary_category", "reject")
        if pc == "reject":
            continue
        tk = ev.get("topic_key", "")
        if not tk:
            continue
        by_key.setdefault(tk, set()).add(pc)

    conflicts = {tk: cats for tk, cats in by_key.items() if len(cats) > 1}

    if conflicts:
        msg_lines = ["🚫 CROSS-CATEGORY VALIDATION FAILED"]
        msg_lines.append(f"Datums: {today}")
        msg_lines.append("")
        for tk, cats in sorted(conflicts.items()):
            msg_lines.append(f"  {tk}: {', '.join(sorted(cats))}")
        msg_lines.append("")
        msg_lines.append("Neviens digests netika publicēts.")
        error_msg = "\n".join(msg_lines)
        log(error_msg)

        env = load_env()
        send_telegram(env, error_msg)
        log("Cross-category validācija NEIZDEVĀS — publicēšana atcelta")
        return 1

    log("Cross-category validācija OK — nav konfliktu")
    return 0


# ---------------------------------------------------------------------------
# STEP: PUBLISH — call publish.sh (only after validate)
# ---------------------------------------------------------------------------

def step_publish(api_key: str, category: str, date: str) -> int:
    """Publicē vienu kategorijas digestu caur publish.sh."""
    env = load_env()
    log(f"[{category}] Publicēju digest {date}...")
    try:
        proc = subprocess.run(
            [str(BASE / "publish.sh"), category, date],
            check=True, capture_output=True, text=True, timeout=90,
        )
        if proc.stderr.strip():
            log(f"[{category}] publish.sh brīdinājums: {proc.stderr[:300]}")
        url = f"https://tech.rozkalns.net/{SECTIONS[category]}/{date}/"
        log(f"[{category}] Publicēts: {url}")
        return 0
    except subprocess.CalledProcessError as exc:
        details = exc.stderr or exc.stdout or str(exc)
        log(f"[{category}] publicēšana KĻŪDA: {details[:300]}")
        return 1
    except subprocess.TimeoutExpired:
        log(f"[{category}] publicēšana KĻŪDA: pārsniegts 90 sekunžu limits")
        return 1
    except OSError as exc:
        log(f"[{category}] publicēšana KĻŪDA: {exc}")
        return 1


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def print_usage() -> None:
    print(__doc__, file=sys.stderr)


def main() -> int:
    if len(sys.argv) < 2:
        print_usage()
        return 1

    step = sys.argv[1]
    env = load_env()
    api_key = env.get("DEEPSEEK_API_KEY", "")
    if not api_key and step in ("classify", "digest", "validate"):
        log("KĻŪDA: DEEPSEEK_API_KEY nav .env — apstājos")
        return 1

    if step == "classify":
        return step_classify(api_key)

    elif step == "digest":
        if len(sys.argv) < 3 or sys.argv[2] not in CATS:
            log("KĻŪDA: digest step vajag kategoriju (devops|ai|agents)")
            return 1
        dry_run = "--dry-run" in sys.argv
        return step_digest(api_key, sys.argv[2], dry_run=dry_run)

    elif step == "validate":
        return step_validate(api_key)

    elif step == "publish":
        if len(sys.argv) < 4 or sys.argv[2] not in CATS:
            log("KĻŪDA: publish step vajag kategoriju un datumu "
                "(devops|ai|agents YYYY-MM-DD)")
            return 1
        return step_publish(api_key, sys.argv[2], sys.argv[3])

    else:
        log(f"KĻŪDA: nezināms step '{step}'")
        print_usage()
        return 1


if __name__ == "__main__":
    sys.exit(main())
