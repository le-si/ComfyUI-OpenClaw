import re
import unittest
from pathlib import Path


class TestFrontendImportPaths(unittest.TestCase):
    def test_comfy_app_import_paths_use_extension_safe_prefix(self):
        """
        Guard against frontend import regressions that break sidebar loading in real ComfyUI.

        Modules under /web/extensions and /web/tabs are mounted at:
        /extensions/<pack>/web/<subdir>/...
        so they must use ../../../scripts/app.js to reach ComfyUI core scripts.
        """
        repo_root = Path(__file__).resolve().parents[1]
        expected = "../../../scripts/app.js"
        targets = [
            repo_root / "web" / "extensions" / "context_toolbox.js",
            repo_root / "web" / "tabs" / "parameter_lab_tab.js",
        ]

        pattern = re.compile(r'import\s+\{\s*app\s*\}\s+from\s+["\']([^"\']+)["\']')

        for path in targets:
            content = path.read_text(encoding="utf-8")
            match = pattern.search(content)
            self.assertIsNotNone(match, f"Missing app import in {path}")
            self.assertEqual(
                match.group(1),
                expected,
                f"Unexpected app import path in {path}.",
            )
