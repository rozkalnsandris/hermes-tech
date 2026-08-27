from __future__ import annotations

from pathlib import Path
import subprocess
import types
from unittest.mock import patch

import digest_publish


def _fake_core(tmp_path: Path):
    core = types.ModuleType("fake_digest_core")
    core.BASE = tmp_path
    core.SECTIONS = {"devops": "digest", "ai": "ai", "agents": "agents"}
    core.logs = []
    core.load_env = lambda: {}
    core.log = core.logs.append
    core.step_publish = lambda *_args: 1
    return core


def test_preserves_publish_sh_exit_code_76(tmp_path: Path) -> None:
    core = _fake_core(tmp_path)
    digest_publish.install_publish_exit_contracts(core)

    completed = subprocess.CompletedProcess(
        args=[str(tmp_path / "publish.sh"), "devops", "2026-08-27"],
        returncode=76,
        stdout="",
        stderr="production checkout is behind origin/main",
    )
    with patch("digest_publish.subprocess.run", return_value=completed):
        rc = core.step_publish("", "devops", "2026-08-27")

    assert rc == 76
    assert any("publish.sh rc=76" in line for line in core.logs)


def test_preserves_transient_and_divergence_codes(tmp_path: Path) -> None:
    for expected in (74, 75):
        core = _fake_core(tmp_path)
        digest_publish.install_publish_exit_contracts(core)
        completed = subprocess.CompletedProcess(
            args=[str(tmp_path / "publish.sh"), "ai", "2026-08-27"],
            returncode=expected,
            stdout="failure",
            stderr="",
        )
        with patch("digest_publish.subprocess.run", return_value=completed):
            assert core.step_publish("", "ai", "2026-08-27") == expected


def test_timeout_maps_to_standard_timeout_code(tmp_path: Path) -> None:
    core = _fake_core(tmp_path)
    digest_publish.install_publish_exit_contracts(core)

    with patch(
        "digest_publish.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="publish.sh", timeout=90),
    ):
        rc = core.step_publish("", "agents", "2026-08-27")

    assert rc == 124
    assert any("90 sekunžu" in line for line in core.logs)


def test_success_remains_zero(tmp_path: Path) -> None:
    core = _fake_core(tmp_path)
    digest_publish.install_publish_exit_contracts(core)
    completed = subprocess.CompletedProcess(
        args=[str(tmp_path / "publish.sh"), "agents", "2026-08-27"],
        returncode=0,
        stdout="ok",
        stderr="",
    )
    with patch("digest_publish.subprocess.run", return_value=completed):
        rc = core.step_publish("", "agents", "2026-08-27")

    assert rc == 0
    assert any("Publicēts:" in line for line in core.logs)
