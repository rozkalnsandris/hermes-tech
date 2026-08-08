from __future__ import annotations

from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "restore-drill.md"
SPEC = spec_from_file_location(
    "restore_acceptance_tool",
    ROOT / "tools" / "verify_restore_root.py",
)
assert SPEC is not None and SPEC.loader is not None
restore_acceptance = module_from_spec(SPEC)
SPEC.loader.exec_module(restore_acceptance)


def file_sha(path: Path) -> str:
    digest = sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


class RestoreAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory(prefix="hermes-restore-test-")
        self.addCleanup(self.tmp_obj.cleanup)
        self.restore_root = Path(self.tmp_obj.name) / "restore"
        self.app = self.restore_root / "home" / "andris" / "hermes-tech"
        self.app.mkdir(parents=True)

        shutil.copytree(ROOT / "site", self.app / "site")
        (self.app / "data").mkdir()
        (self.app / "tools").mkdir()

        required_files = {
            ".python-version": "3.11.15\n",
            "requirements.txt": "# fixture\n",
            "collector.py": "def main(): return 0\n",
            "digest.py": "def main(): return 0\n",
            "publish.sh": "#!/usr/bin/env bash\nexit 0\n",
            "run_digests.sh": "#!/usr/bin/env bash\nexit 0\n",
            "tools/ci.sh": "#!/usr/bin/env bash\nexit 0\n",
            "README.md": "restore fixture\n",
        }
        for relative, content in required_files.items():
            path = self.app / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        env = self.app / ".env"
        env.write_text("SECRET_FIXTURE=not-read-by-verifier\n", encoding="utf-8")
        os.chmod(env, 0o600)

        db = self.app / "data" / "hermes.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL UNIQUE,
                published TEXT,
                summary TEXT,
                fetched_at TEXT NOT NULL,
                digest_date TEXT,
                category TEXT DEFAULT 'devops',
                content TEXT,
                primary_category TEXT,
                topic_key TEXT,
                routed_at TEXT
            );
            CREATE TABLE sources (
                name TEXT PRIMARY KEY,
                fetch_ok INTEGER DEFAULT 0,
                fetch_fail INTEGER DEFAULT 0,
                collected INTEGER DEFAULT 0,
                picked INTEGER DEFAULT 0
            );
            PRAGMA user_version = 3;
            INSERT INTO sources(name, fetch_ok) VALUES ('Fixture', 1);
            INSERT INTO articles(
                id, source, title, link, fetched_at, category,
                primary_category, topic_key
            ) VALUES (
                1, 'Fixture', 'Restored article', 'https://example.test/1',
                '2026-08-08T00:00:00+00:00', 'devops', 'devops', 'restore-fixture'
            );
            """
        )
        conn.commit()
        conn.close()

        subprocess.run(["git", "init", "-q"], cwd=self.app, check=True)
        subprocess.run(["git", "add", "README.md"], cwd=self.app, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Restore Test",
                "-c",
                "user.email=restore-test@example.invalid",
                "commit",
                "-q",
                "-m",
                "restore fixture",
            ],
            cwd=self.app,
            check=True,
        )

    def test_isolated_restore_passes_without_mutating_db_or_reading_env(self) -> None:
        db = self.app / "data" / "hermes.db"
        env = self.app / ".env"
        before_db = file_sha(db)
        before_env = file_sha(env)

        report = restore_acceptance.verify_restore(self.restore_root)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["mode"], "isolated-restore-acceptance")
        self.assertFalse(report["production_root_touched"])
        self.assertEqual(report["git"]["fsck"], "ok")
        self.assertEqual(len(report["git"]["head"]), 40)
        self.assertEqual(report["sqlite"]["quick_check"], "ok")
        self.assertEqual(report["sqlite"]["user_version"], 3)
        self.assertEqual(report["sqlite"]["article_count"], 1)
        self.assertEqual(report["sqlite"]["source_count"], 1)
        self.assertTrue(report["sqlite"]["unchanged_during_check"])
        self.assertFalse(report["env"]["contents_read"])
        self.assertEqual(report["env"]["mode"], "0600")
        self.assertGreater(report["hugo"]["index_bytes"], 0)
        self.assertGreater(report["hugo"]["sitemap_bytes"], 0)
        self.assertGreater(report["hugo"]["robots_bytes"], 0)
        self.assertEqual(file_sha(db), before_db)
        self.assertEqual(file_sha(env), before_env)

    def test_broad_env_permissions_fail_closed_without_reading_contents(self) -> None:
        env = self.app / ".env"
        before = file_sha(env)
        os.chmod(env, 0o640)
        with self.assertRaisesRegex(
            restore_acceptance.RestoreVerificationError,
            "permissions are too broad",
        ):
            restore_acceptance.verify_restore(self.restore_root)
        self.assertEqual(file_sha(env), before)

    def test_live_production_path_is_explicitly_rejected(self) -> None:
        with patch.object(restore_acceptance, "PRODUCTION_APP", self.app):
            with self.assertRaisesRegex(
                restore_acceptance.RestoreVerificationError,
                "refusing to verify the live production Hermes root",
            ):
                restore_acceptance.verify_restore(self.restore_root)

    def test_unversioned_database_fails_closed(self) -> None:
        db = self.app / "data" / "hermes.db"
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
        conn.close()
        before = file_sha(db)

        with self.assertRaisesRegex(
            restore_acceptance.RestoreVerificationError,
            "schema is not versioned",
        ):
            restore_acceptance.verify_restore(self.restore_root)

        self.assertEqual(file_sha(db), before)

    def test_restore_policy_defines_cadence_rpo_rto_and_host_ownership(self) -> None:
        text = " ".join(DOC.read_text(encoding="utf-8").split())
        for marker in (
            "RPO target is at most 24 hours",
            "at least once every 90 days",
            "RTO objective is two hours",
            "not a proven guarantee",
            "`RPi5_main` owns",
            "`HERMES_TECH_DEPLOY_REQUIRED=no`",
            "`RPI5_MAIN_CHANGE_REQUIRED=yes`",
        ):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
