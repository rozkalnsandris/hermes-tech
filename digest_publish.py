"""Fail-closed publication adapter that preserves publish.sh exit semantics."""
from __future__ import annotations

import subprocess
from types import ModuleType


def install_publish_exit_contracts(core: ModuleType) -> None:
    """Replace the compatibility publish step with an exit-code preserving adapter."""

    def step_publish(api_key: str, category: str, date: str) -> int:
        del api_key
        core.load_env()
        core.log(f"[{category}] Publicēju digest {date}...")
        try:
            proc = subprocess.run(
                [str(core.BASE / "publish.sh"), category, date],
                check=False,
                capture_output=True,
                text=True,
                timeout=90,
            )
        except subprocess.TimeoutExpired:
            core.log(
                f"[{category}] publicēšana KĻŪDA: pārsniegts 90 sekunžu limits"
            )
            return 124
        except OSError as exc:
            core.log(f"[{category}] publicēšana KĻŪDA: {exc}")
            return 1

        if proc.returncode != 0:
            details = (proc.stderr or proc.stdout or "").strip()
            suffix = f": {details[:300]}" if details else ""
            core.log(
                f"[{category}] publicēšana KĻŪDA: publish.sh rc={proc.returncode}{suffix}"
            )
            if 1 <= proc.returncode <= 255:
                return proc.returncode
            return 1

        if proc.stderr.strip():
            core.log(f"[{category}] publish.sh brīdinājums: {proc.stderr[:300]}")
        url = f"https://tech.rozkalns.net/{core.SECTIONS[category]}/{date}/"
        core.log(f"[{category}] Publicēts: {url}")
        return 0

    core.step_publish = step_publish
