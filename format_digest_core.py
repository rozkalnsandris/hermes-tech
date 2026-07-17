#!/usr/bin/env python3
"""Pārstrukturē Hermes Tech digest markdown lasāmākā formā priekš Hugo/Goldmark.

Problēma: modelis raksta katru vienumu ar vienkāršiem rindu pārtraukumiem
(virsraksts / teksts / links / Hermes-komentārs), bet CommonMark/Goldmark
vienu \n uzskata par atstarpi, nevis jaunu bloku — tāpēc viss saplūst vienā
rindkopā. Šis skripts katru vienumu sadala īstos blokos:
  ### Headline
  body teksts...
  [Source](url)
  > Hermes: komentārs
Lietošana: format_digest.py < input.md > output.md
"""
import re
import sys

LINK_RE = re.compile(r"^\[([^\]]+)\]\((https?://[^\s)]+)\)\s*$")
HERMES_RE = re.compile(r"^Hermes:\s*(.+)$")
HEAD_RE = re.compile(r"^\*\*(.+?)\*\*\s*(.*)$")


def process_block(block: str) -> str:
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return ""

    m = HEAD_RE.match(lines[0])
    if not m:
        # Nav standarta vienuma formāts (piem., ievadrindkopa) — atstājam kā ir
        return block.strip()

    headline, rest_of_first = m.group(1).strip(), m.group(2).strip()
    body_parts = [rest_of_first] if rest_of_first else []
    link_line = ""
    hermes_line = ""

    for ln in lines[1:]:
        lm = LINK_RE.match(ln)
        hm = HERMES_RE.match(ln)
        if hm:
            hermes_line = hm.group(1).strip()
        elif lm:
            link_line = ln
        else:
            body_parts.append(ln)

    out = [f"### {headline}", ""]
    if body_parts:
        out.append(" ".join(body_parts))
        out.append("")
    if link_line:
        out.append(link_line)
        out.append("")
    if hermes_line:
        out.append(f"> 💬 **Hermes:** {hermes_line}")
        out.append("")
    return "\n".join(out).rstrip()


def main() -> int:
    raw = sys.stdin.read()
    blocks = re.split(r"\n\s*\n", raw.strip())
    processed = [process_block(b) for b in blocks]
    print("\n\n".join(p for p in processed if p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
