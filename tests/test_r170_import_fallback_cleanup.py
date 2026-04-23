from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

TARGETS = {
    "api/webhook.py": {
        "must_contain": ("import_attrs_dual",),
        "must_not_contain": ("except ImportError",),
    },
    "api/presets.py": {
        "must_contain": ("import_attrs_dual",),
        "must_not_contain": ("except ImportError",),
    },
    "api/tools.py": {
        "must_contain": ("import_attrs_dual", "check_surface"),
        "must_not_contain": ("except ImportError",),
    },
    "services/surface_guard.py": {
        "must_contain": ("import_attrs_dual",),
        "must_not_contain": (
            "from aiohttp_compat import import_aiohttp_web",
            "from runtime_profile import is_hardened_mode",
        ),
    },
}


class TestR170ImportFallbackCleanup(unittest.TestCase):
    def test_target_modules_use_shared_import_fallback_helpers(self):
        for rel_path, expectations in TARGETS.items():
            with self.subTest(path=rel_path):
                text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
                for needle in expectations["must_contain"]:
                    self.assertIn(needle, text)
                for needle in expectations["must_not_contain"]:
                    self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
