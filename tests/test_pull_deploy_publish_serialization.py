from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
POLLER = ROOT / "tools" / "pull-deploy" / "release" / "hermes-tech-pull-deploy"


class PullDeployPublishSerializationTests(unittest.TestCase):
    def test_poller_snapshots_under_shared_publish_lock(self) -> None:
        text = POLLER.read_text(encoding="utf-8")
        subprocess.run(["bash", "-n", str(POLLER)], check=True)

        publish_lock = 'PUBLISH_LOCK="$PRIMARY/.publish.lock"'
        lock_open = 'exec 8>"$PUBLISH_LOCK"'
        busy_result = "PULL_DEPLOY_RESULT=NO_OP_PUBLISH_BUSY"
        fetch_main = 'git -C "$SOURCE" fetch --prune origin main'
        target_snapshot = 'TARGET_SHA=$(git -C "$SOURCE" rev-parse refs/remotes/origin/main)'
        production_snapshot = 'PRODUCTION_SHA=$(git -C "$PRIMARY" rev-parse HEAD)'
        ancestry_gate = 'merge-base --is-ancestor "$PRODUCTION_SHA" "$TARGET_SHA"'
        unlock = "flock -u 8"
        impact_classification = 'IMPACT_JSON=$(classify_range "$PRODUCTION_SHA" "$TARGET_SHA")'

        for marker in (
            publish_lock,
            lock_open,
            "flock -n 8",
            busy_result,
            fetch_main,
            target_snapshot,
            production_snapshot,
            ancestry_gate,
            unlock,
            impact_classification,
        ):
            self.assertIn(marker, text)

        # The target/production relationship must be captured and validated while
        # the publisher is excluded. Only a genuinely-behind snapshot may release
        # the short poller lock and continue into impact/CI/deploy processing.
        self.assertLess(text.index(lock_open), text.index(fetch_main))
        self.assertLess(text.index(fetch_main), text.index(target_snapshot))
        self.assertLess(text.index(target_snapshot), text.index(ancestry_gate))
        self.assertLess(text.index(production_snapshot), text.index(ancestry_gate))
        self.assertLess(text.index(ancestry_gate), text.index(unlock))
        self.assertLess(text.index(unlock), text.index(impact_classification))


if __name__ == "__main__":
    unittest.main()
