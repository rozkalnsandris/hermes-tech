#!/usr/bin/env python3
"""Redacted, deterministic secret scan for Git-tracked Hermes Tech files."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER_RE = re.compile(
    r"^(?:"
    r"replace[-_ ]?me|change[-_ ]?me|example|dummy|placeholder|"
    r"your[-_ ].*|<.*>|x{6,}|\$\{\{.*\}\}|\$\{.*\}"
    r")$",
    re.IGNORECASE,
)

RULES = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"
        ),
    ),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("telegram-bot-token", re.compile(r"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b")),
    ("api-secret-token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
)

ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|bot[_-]?token|"
    r"client[_-]?secret|password|passwd|private[_-]?key|secret)\b"
    r"\s*[:=]\s*[\"']?(?P<value>[^\s\"'#]{12,})"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str

    def render(self) -> str:
        return f"SECRET-SCAN: {self.path}:{self.line}: {self.rule} [REDACTED]"


def is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER_RE.fullmatch(value.strip().rstrip(",;")))


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in RULES:
            if pattern.search(line):
                findings.append(Finding(path, number, rule))
        assignment = ASSIGNMENT_RE.search(line)
        if assignment and not is_placeholder(assignment.group("value")):
            findings.append(Finding(path, number, "credential-assignment"))
    return findings


def tracked_files(root: Path) -> list[Path]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip())
    return [root / raw.decode("utf-8") for raw in proc.stdout.split(b"\0") if raw]


def scan_repository(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in tracked_files(root):
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"cannot read {path}: {exc}") from exc
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        findings.extend(scan_text(path.relative_to(root).as_posix(), text))
    return findings


def self_test() -> None:
    fake = "ghp_" + "A" * 36
    findings = scan_text("fixture.env", f"TOKEN={fake}\n")
    if not findings:
        raise RuntimeError("self-test failed to detect a synthetic token")
    rendered = "\n".join(item.render() for item in findings)
    if fake in rendered:
        raise RuntimeError("self-test leaked the synthetic token")
    if scan_text("example.env", "API_KEY=replace-me\n"):
        raise RuntimeError("self-test rejected an allowed placeholder")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        self_test()
        print("Secret scanner self-test OK (output remains redacted)")
        return 0

    root = Path(args.root).resolve()
    findings = scan_repository(root)
    if findings:
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        print(f"KĻŪDA: atrasti {len(findings)} iespējamie noslēpumi", file=sys.stderr)
        return 1
    print("Secret scan OK: no credential patterns in tracked text files")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"KĻŪDA: secret scan: {exc}", file=sys.stderr)
        raise SystemExit(1)
