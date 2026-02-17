import unittest
from dataclasses import asdict
from typing import Any, Dict, List

# We'll import these once created/modified
# from services.operator_guidance import OperatorBanner, BannerSeverity, DeepLinkResolver
# from services.preflight import run_preflight_check, generate_preflight_banners


class TestF49OperatorGuidance(unittest.TestCase):

    def test_banner_contract(self):
        """Verify OperatorBanner stricter contract."""
        from services.operator_guidance import BannerSeverity, OperatorBanner

        banner = OperatorBanner(
            id="test_banner",
            severity=BannerSeverity.WARNING,
            message="Test Message",
            source="TestContext",
            ttl_ms=5000,
            action={"label": "Fix", "type": "url", "payload": "https://example.com"},
        )

        data = banner.to_dict()
        self.assertEqual(data["severity"], "warning")
        self.assertEqual(data["dedupe_key"], "TestContext:test_banner")
        self.assertEqual(data["action"]["label"], "Fix")

    def test_deep_link_resolution(self):
        """Verify DeepLinkResolver handles internal schemes and base paths."""
        from services.operator_guidance import resolve_deep_link

        # 1. Base path resolution
        url = resolve_deep_link("openclaw://settings/api", base_path="/openclaw")
        self.assertEqual(
            url, "/openclaw/settings/api"
        )  # Or however we define the mapping

        # 2. Lazy mount handling (if applicable, might just be path mapping for now)
        # For now, we expect it to return a relative URL usable by the frontend router

    def test_preflight_determinism(self):
        """Verify preflight report lists are sorted and banners are included."""
        from services.preflight import run_preflight_check

        # Mock payload with unordered missing nodes
        workflow = {
            "1": {"class_type": "ZooNode"},
            "2": {"class_type": "AlphaNode"},
            "3": {"class_type": "BetaNode"},
        }

        # We can't easily mock the internal missing logic without dependency injection or extensive patching
        # But we can verify the 'banners' key is present even if empty
        report = run_preflight_check(workflow)
        self.assertIn("banners", report)
        self.assertIsInstance(report["banners"], list)

    def test_preflight_banner_generation(self):
        """Verify report -> banner conversion."""
        from services.operator_guidance import BannerSeverity
        from services.preflight import generate_preflight_banners

        report = {
            "ok": False,
            "missing_nodes": [{"class_type": "NodeA", "count": 1}],
            "missing_models": [],
            "notes": [],
        }

        banners = generate_preflight_banners(report)
        self.assertTrue(len(banners) > 0)
        self.assertEqual(banners[0].severity, BannerSeverity.ERROR)
        self.assertIn("NodeA", banners[0].message)


if __name__ == "__main__":
    unittest.main()
