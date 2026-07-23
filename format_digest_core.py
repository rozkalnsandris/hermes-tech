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


def _process_block_original(block: str) -> str:
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

# BEGIN MANAGED: PRESENTATION_POLISH_V1
def _normalize_source_input(block: str) -> str:
    # Allow canonical Source: links without changing the legacy core parser.
    import re as _re

    source_re = _re.compile(
        r"^Source:\s*\[([^\]]+)\]\((https?://[^)]+)\)\s*$",
        _re.IGNORECASE,
    )
    out = []
    for line in block.splitlines():
        match = source_re.match(line)
        if match:
            label, url = match.groups()
            line = f"[{label}]({url})"
        out.append(line)
    return "\n".join(out)


def _normalize_presentation_output(rendered: str) -> str:
    # Canonicalize source links and remove the known empty-emphasis artifact.
    import re as _re

    rendered = rendered.replace("** **", "")

    heading_re = _re.compile(r"^#{2,4}\s+(.+?)\s*$")
    link_re = _re.compile(
        r"^\[([^\]]+)\]\((https?://[^)]+)\)\s*$"
    )

    current_heading = None
    out = []

    for line in rendered.splitlines():
        heading = heading_re.match(line)
        if heading and heading.group(1).strip().casefold() != "hermes:":
            current_heading = heading.group(1).strip()

        link = link_re.match(line)
        if link:
            label, url = link.groups()
            label = label.strip()
            if label.casefold() == "source" and current_heading:
                label = current_heading
            line = f"Source: [{label}]({url})"

        out.append(line)

    return "\n".join(out).strip()


def process_block(*args, **kwargs):
    # separate_hermes() has already isolated Hermes analysis as a blockquote.
    # Returning it untouched prevents HEAD_RE from reconstructing it as a
    # heading, which is what produced the visible empty ** ** artifact.
    if args and isinstance(args[0], str) and args[0].lstrip().startswith(">"):
        return args[0].strip().replace("** **", "")

    call_args = list(args)
    if call_args and isinstance(call_args[0], str):
        call_args[0] = _normalize_source_input(call_args[0])

    rendered = _process_block_original(*call_args, **kwargs)
    if not isinstance(rendered, str):
        return rendered

    return _normalize_presentation_output(rendered)
# END MANAGED: PRESENTATION_POLISH_V1


def main() -> int:
    raw = sys.stdin.read()
    blocks = re.split(r"\n\s*\n", raw.strip())
    processed = [process_block(b) for b in blocks]
    print("\n\n".join(p for p in processed if p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
