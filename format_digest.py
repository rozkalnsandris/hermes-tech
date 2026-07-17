#!/usr/bin/env python3
"""Hermes Tech Markdown readability wrapper."""

# HERMES_READABILITY_V13

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


CORE = Path(__file__).with_name("format_digest_core.py")
HERMES_MARKER = re.compile(
    r"(?:💬\s*)?Hermes\s*:\s*",
    flags=re.IGNORECASE,
)


def separate_hermes(markdown: str) -> str:
    """Move every Hermes marker into its own Markdown blockquote."""
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    in_fence = False

    def blank() -> None:
        if output and output[-1] != "":
            output.append("")

    for raw_line in markdown.split("\n"):
        line = raw_line.rstrip()

        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            output.append(line)
            continue

        if in_fence:
            output.append(line)
            continue

        # Jau pareizi formatētu blockquote atstājam kā blockquote,
        # tikai normalizējam etiķeti.
        if re.match(r"^\s*>", line):
            stripped = re.sub(r"^\s*>\s?", "", line)
            match = HERMES_MARKER.search(stripped)
            if match:
                before = stripped[:match.start()].strip()
                comment = stripped[match.end():].strip()
                blank()
                label = "> **Hermes:**"
                if before:
                    label += f" {before}"
                if comment:
                    label += f" {comment}"
                output.append(label)
                blank()
            else:
                output.append(line)
            continue

        match = HERMES_MARKER.search(line)
        if match:
            before = line[:match.start()].rstrip()
            comment = line[match.end():].strip()

            if before:
                output.append(before)
            blank()

            quote = "> **Hermes:**"
            if comment:
                quote += f" {comment}"
            output.append(quote)
            blank()
            continue

        output.append(line)

    text = "\n".join(output)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def main() -> int:
    if not CORE.is_file():
        print(f"KĻŪDA: nav atrasts formatētāja kodols: {CORE}", file=sys.stderr)
        return 1

    source = sys.stdin.read()

    # Pirmais solis palīdz vecajam formatētājam ieraudzīt Hermes marķieri
    # kā atsevišķu Markdown bloku.
    prepared = separate_hermes(source)

    proc = subprocess.run(
        [sys.executable, str(CORE)],
        input=prepared,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        return proc.returncode

    # Otrais solis ir drošības tīkls, ja kodols marķieri atkal saliek
    # vienā rindkopā ar avota kopsavilkumu.
    sys.stdout.write(separate_hermes(proc.stdout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
