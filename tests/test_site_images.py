from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "check_site_images.py"
spec = importlib.util.spec_from_file_location("check_site_images", TOOL_PATH)
image_tool = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(image_tool)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class SiteImageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_obj.name) / "repo"
        self.public = self.root / "public"
        self.root.mkdir()
        self.public.mkdir()
        git(self.root, "init", "-q", "--initial-branch=main")
        git(self.root, "config", "user.name", "Hermes Test")
        git(self.root, "config", "user.email", "hermes-test@example.invalid")

    def tearDown(self) -> None:
        self.tmp_obj.cleanup()

    def write(self, relative: str, content: str | bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def commit_fixture(self) -> None:
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "fixture")

    def test_inventory_classifies_tracked_images_and_content_usage(self) -> None:
        self.write("site/static/brand/logo.png", b"png")
        self.write("site/static/og/card.jpg", b"jpg")
        self.write("site/assets/images/global.webp", b"webp")
        self.write("site/content/post/photo.jpeg", b"jpeg")
        self.write(
            "site/content/post/index.md",
            "![Alt text](photo.jpeg)\n\n<img src=\"inline.png\" alt=\"inline\">\n",
        )
        self.write("public/index.html", '<img src="/logo.png" alt="Logo" width="10" height="20">')
        self.commit_fixture()

        report = image_tool.build_report(self.root, self.public)
        self.assertEqual(report["tracked"]["image_files"], 4)
        self.assertEqual(report["tracked"]["content_raster_count"], 1)
        self.assertEqual(report["tracked"]["classes"]["static_brand"], 1)
        self.assertEqual(report["tracked"]["classes"]["static_og"], 1)
        self.assertEqual(report["tracked"]["classes"]["assets"], 1)
        self.assertEqual(report["content_usage"]["markdown_image_count"], 1)
        self.assertEqual(report["content_usage"]["inline_img_file_count"], 1)

    def test_rendered_local_images_require_numeric_dimensions(self) -> None:
        self.write(
            "public/index.html",
            '<img src="/a.png" alt="A" width="320" height="180">'
            '<img src="/b.png" alt="B" width="100%">'
            '<img src="https://example.com/c.png" alt="C">',
        )
        usage = image_tool.rendered_image_usage(self.public)
        self.assertEqual(usage["img_tags"], 3)
        self.assertEqual(usage["local_img_tags"], 2)
        self.assertEqual(usage["missing_dimensions_count"], 1)
        self.assertEqual(usage["missing_dimensions"][0]["src"], "/b.png")

    def test_alt_attribute_presence_is_reported_separately(self) -> None:
        self.write(
            "public/index.html",
            '<img src="/decorative.svg" alt="" width="20" height="20">'
            '<img src="/missing.png" width="20" height="20">',
        )
        usage = image_tool.rendered_image_usage(self.public)
        self.assertEqual(usage["missing_dimensions_count"], 0)
        self.assertEqual(usage["missing_alt_count"], 1)
        self.assertEqual(usage["missing_alt"][0]["src"], "/missing.png")


if __name__ == "__main__":
    unittest.main()
