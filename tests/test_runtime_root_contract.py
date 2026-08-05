#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = Path(__file__).resolve().parents[1]


FAKE_DIGEST_CORE = r'''
from __future__ import annotations
from datetime import datetime, timezone
import os
from pathlib import Path

BASE = Path.home() / "hermes-tech"
DB = BASE / "data" / "hermes.db"
LOG = BASE / "logs" / "digest.log"
RUNS = BASE / "data" / "runs"
DIGESTS = BASE / "digests"
ENV_FILE = BASE / ".env"
CATS = {"devops": {}, "ai": {}, "agents": {}}


def log(msg):
    print(msg)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(msg + "\n")


def load_env():
    result = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in raw:
                key, value = raw.split("=", 1)
                result[key.strip()] = value.strip()
    return result


def step_classify(api_key):
    return int(os.environ.get("FAKE_CLASSIFY_RC", "0"))


def step_digest(api_key, category, dry_run=False):
    rc = int(os.environ.get("FAKE_DIGEST_RC", "0"))
    if rc:
        return rc
    # Deliberately mutate every configured output. A correct wrapper points all
    # of these at a temporary runtime during --dry-run.
    DB.write_bytes(DB.read_bytes() + b"-mutated")
    log(f"fake digest category={category} dry_run={dry_run}")
    DIGESTS.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (DIGESTS / f"{today}-{category}.md").write_text(
        "<!-- selected_ids: 1 -->\n# Dry run output\n",
        encoding="utf-8",
    )
    return 0


def step_validate(api_key):
    print(f"validate-api-key={api_key!r}")
    return int(os.environ.get("FAKE_VALIDATE_RC", "0"))


def step_publish(api_key, category, date):
    return int(os.environ.get("FAKE_PUBLISH_RC", "0"))


def send_telegram(env, text):
    return True
'''

FAKE_COLLECTOR_CORE = r'''
from pathlib import Path
BASE = Path.home() / "hermes-tech"
DB = BASE / "data" / "hermes.db"
FEEDS = BASE / "feeds.txt"
LOG = BASE / "logs" / "collector.log"

def main():
    print(f"ROOT={BASE}")
    print(f"DB={DB}")
    print(f"FEEDS={FEEDS}")
    print(f"LOG={LOG}")
    return 0
'''

FAKE_OGCARD_CORE = r'''
from pathlib import Path
BASE = Path(__file__).resolve().parent
STATIC = BASE / "site" / "static"
WORDMARK = STATIC / "brand" / "wordmark.png"
MARK = STATIC / "brand" / "mark.png"
OUT_DIR = STATIC / "og"

def main():
    print(f"ROOT={BASE}")
    print(f"OUT={OUT_DIR}")
    return 0
'''


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        digest.update(rel.encode())
        if path.is_symlink():
            digest.update(b"L")
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            digest.update(b"F")
            digest.update(path.read_bytes())
        else:
            digest.update(b"D")
    return digest.hexdigest()


class RuntimeRootContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_obj.name)
        self.fixture = self.tmp / "fixture"
        self.root = self.tmp / "isolated-worktree"
        self.fixture.mkdir()
        self.root.mkdir()

        for name in ("hermes_runtime.py", "digest.py", "collector.py", "ogcard.py"):
            shutil.copy2(REPO / name, self.fixture / name)
        (self.fixture / "digest_core.py").write_text(FAKE_DIGEST_CORE, encoding="utf-8")
        (self.fixture / "collector_core.py").write_text(FAKE_COLLECTOR_CORE, encoding="utf-8")
        (self.fixture / "ogcard_core.py").write_text(FAKE_OGCARD_CORE, encoding="utf-8")

        (self.root / "data").mkdir()
        conn = sqlite3.connect(self.root / "data" / "hermes.db")
        conn.execute("CREATE TABLE sentinel(value TEXT)")
        conn.execute("INSERT INTO sentinel(value) VALUES ('unchanged')")
        conn.commit()
        conn.close()
        (self.root / ".env").write_text("DEEPSEEK_API_KEY=test-key\n", encoding="utf-8")
        (self.root / "feeds.txt").write_text("https://example.invalid/rss\n", encoding="utf-8")

        subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Hermes Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "--", ".env", "data/hermes.db", "feeds.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)

    def tearDown(self) -> None:
        self.tmp_obj.cleanup()

    def env(self, **extra: str) -> dict[str, str]:
        result = {"HERMES_TECH_ROOT": str(self.root), "PYTHONDONTWRITEBYTECODE": "1"}
        result.update(extra)
        return result

    def git_status(self) -> str:
        return subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=self.root,
            text=True,
        )

    def test_dry_run_leaves_filesystem_db_and_git_unchanged(self) -> None:
        before_tree = tree_digest(self.root)
        before_git = self.git_status()
        before_db = (self.root / "data" / "hermes.db").read_bytes()

        proc = run(
            sys.executable,
            str(self.fixture / "digest.py"),
            "digest",
            "devops",
            "--dry-run",
            cwd=self.fixture,
            env=self.env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("HERMES DRY-RUN OUTPUT BEGIN", proc.stdout)
        self.assertIn("# Dry run output", proc.stdout)
        self.assertEqual((self.root / "data" / "hermes.db").read_bytes(), before_db)
        self.assertEqual(tree_digest(self.root), before_tree)
        self.assertEqual(self.git_status(), before_git)
        self.assertFalse((self.root / "logs").exists())
        self.assertFalse((self.root / "digests").exists())

    def test_validate_needs_no_env_or_api_key(self) -> None:
        (self.root / ".env").unlink()
        proc = run(
            sys.executable,
            str(self.fixture / "digest.py"),
            "validate",
            cwd=self.fixture,
            env=self.env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("validate-api-key=''", proc.stdout)

    def test_stable_cli_exit_codes(self) -> None:
        usage = run(sys.executable, str(self.fixture / "digest.py"), cwd=self.fixture, env=self.env())
        self.assertEqual(usage.returncode, 2)

        validation = run(
            sys.executable,
            str(self.fixture / "digest.py"),
            "validate",
            cwd=self.fixture,
            env=self.env(FAKE_VALIDATE_RC="1"),
        )
        self.assertEqual(validation.returncode, 3)

        operational = run(
            sys.executable,
            str(self.fixture / "digest.py"),
            "digest",
            "ai",
            cwd=self.fixture,
            env=self.env(FAKE_DIGEST_RC="1"),
        )
        self.assertEqual(operational.returncode, 1)

    def test_python_entrypoints_receive_selected_root(self) -> None:
        collector = run(
            sys.executable,
            str(self.fixture / "collector.py"),
            cwd=self.fixture,
            env=self.env(),
        )
        self.assertEqual(collector.returncode, 0, collector.stderr)
        self.assertIn(f"ROOT={self.root}", collector.stdout)
        self.assertIn(f"DB={self.root / 'data' / 'hermes.db'}", collector.stdout)

        ogcard = run(
            sys.executable,
            str(self.fixture / "ogcard.py"),
            "slug",
            "title",
            cwd=self.fixture,
            env=self.env(),
        )
        self.assertEqual(ogcard.returncode, 0, ogcard.stderr)
        self.assertIn(f"ROOT={self.root}", ogcard.stdout)
        self.assertIn(f"OUT={self.root / 'site' / 'static' / 'og'}", ogcard.stdout)

    def test_dynamic_import_keeps_send_telegram_compatibility(self) -> None:
        probe = textwrap.dedent(
            f"""
            import importlib.util
            from pathlib import Path
            path = Path({str((Path('/tmp') / 'placeholder'))!r})
            """
        )
        probe = (
            "import importlib.util\n"
            f"path = {str(self.fixture / 'digest.py')!r}\n"
            "spec = importlib.util.spec_from_file_location('notify_probe', path)\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "assert callable(module.send_telegram)\n"
        )
        outside = self.tmp / "outside"
        outside.mkdir()
        proc = run(
            sys.executable,
            "-c",
            probe,
            cwd=outside,
            env=self.env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_default_root_remains_home_hermes_tech(self) -> None:
        home = self.tmp / "home"
        home.mkdir()
        expected = home / "hermes-tech"
        proc = run(
            sys.executable,
            str(self.fixture / "collector.py"),
            cwd=self.fixture,
            env={
                "HOME": str(home),
                "HERMES_TECH_ROOT": "",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"ROOT={expected}", proc.stdout)

    def test_relative_runtime_root_is_rejected(self) -> None:
        proc = run(
            sys.executable,
            str(self.fixture / "digest.py"),
            "validate",
            cwd=self.fixture,
            env={"HERMES_TECH_ROOT": "relative/path", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("absolūtam ceļam", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
