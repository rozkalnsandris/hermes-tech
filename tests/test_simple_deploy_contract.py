import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHARED_SHA = "e05ed760791a127c7c9628696806ef39c9fe329c"
FORBIDDEN = {
    "database-schema-data-mutation",
    "destructive-recovery",
    "secrets-credentials-permissions",
    "cloudflare-dns-network",
    "private-provider-activation",
    "unrelated-host-control",
}


class SimpleDeployContractTest(unittest.TestCase):
    def test_manifest_is_static_origin_only(self):
        manifest = json.loads((ROOT / ".simple-deploy.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "rozkalns.simple-deploy.consumer.v1")
        self.assertEqual(manifest["repository"], "rozkalnsandris/hermes-tech")
        self.assertEqual(manifest["image"], "ghcr.io/rozkalnsandris/hermes-tech")
        self.assertEqual(manifest["build"], {
            "context": ".",
            "dockerfile": "Dockerfile",
            "architecture": "linux/arm64",
        })
        self.assertEqual(manifest["target"], {
            "alias": "hermes-tech-public-rpi5",
            "runtime_class": "rpi5-compose",
        })
        self.assertEqual(manifest["compose"], {
            "project": "hermes-tech-public",
            "file": "deploy/docker-compose.simple.yml",
            "service": "hermes-tech",
        })
        self.assertEqual(manifest["health"]["liveness_path"], "/health")
        self.assertEqual(manifest["health"]["readiness"], {"state": "required", "path": "/ready"})
        self.assertEqual(manifest["persistence"], {"volumes": []})
        self.assertEqual(manifest["registry"], {"pull_profile": "public-anonymous-pull"})
        self.assertEqual(set(manifest["forbidden_operations"]), FORBIDDEN)

    def test_caller_is_exactly_pinned_and_has_no_live_logic(self):
        workflow = (ROOT / ".github/workflows/simple-deploy.yml").read_text(encoding="utf-8")
        self.assertIn(
            f"uses: rozkalnsandris/ops-workflows/.github/workflows/simple-deploy.yml@{SHARED_SHA}",
            workflow,
        )
        self.assertIn("source_sha: ${{ github.sha }}", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("run:", workflow)
        self.assertNotIn("secrets:", workflow)

    def test_docker_build_only_consumes_public_site_source(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY site ./site", dockerfile)
        self.assertIn("--destination /tmp/out", dockerfile)
        self.assertIn("COPY --from=build /tmp/out /usr/share/nginx/html", dockerfile)
        self.assertNotIn("--destination /out", dockerfile)
        self.assertNotIn("COPY --from=build /out /usr/share/nginx/html", dockerfile)
        for forbidden in (
            "COPY . .",
            ".env",
            "data/",
            "digests/",
            "collector.py",
            "digest.py",
            "publish.sh",
            "run_digests.sh",
            "requirements.txt",
        ):
            self.assertNotIn(forbidden, dockerfile)

    def test_compose_has_one_read_only_stateless_service(self):
        compose = (ROOT / "deploy/docker-compose.simple.yml").read_text(encoding="utf-8")
        self.assertIn("image: ghcr.io/rozkalnsandris/hermes-tech:production", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("cap_drop:", compose)
        self.assertIn("http://127.0.0.1:8080/ready", compose)
        self.assertNotIn("volumes:", compose)
        self.assertNotIn("environment:", compose)
        self.assertNotIn("network_mode:", compose)

    def test_nginx_health_contract_is_fixed_and_local(self):
        nginx = (ROOT / "deploy/nginx.conf").read_text(encoding="utf-8")
        self.assertIn("listen 8080;", nginx)
        self.assertIn("location = /health", nginx)
        self.assertIn("location = /ready", nginx)
        self.assertIn("root /usr/share/nginx/html;", nginx)


if __name__ == "__main__":
    unittest.main()
