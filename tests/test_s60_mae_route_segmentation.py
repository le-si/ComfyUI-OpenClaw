"""
S60 — MAE Route Segmentation Gate Tests.

Tests RoutePlane classification, MAE posture validation,
and public-profile denylist enforcement.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.endpoint_manifest import (
    AuthTier,
    EndpointMetadata,
    RiskTier,
    RoutePlane,
    endpoint_metadata,
    validate_mae_posture,
)


class TestS60RoutePlane(unittest.TestCase):
    """S60: RoutePlane enum and EndpointMetadata classification tests."""

    def test_route_plane_enum_values(self):
        """RoutePlane has USER, ADMIN, INTERNAL, EXTERNAL."""
        self.assertEqual(RoutePlane.USER.value, "user")
        self.assertEqual(RoutePlane.ADMIN.value, "admin")
        self.assertEqual(RoutePlane.INTERNAL.value, "internal")
        self.assertEqual(RoutePlane.EXTERNAL.value, "external")

    def test_endpoint_metadata_default_plane_is_none(self):
        """EndpointMetadata defaults to None (explicit classification required)."""
        meta = EndpointMetadata(
            auth_tier=AuthTier.PUBLIC,
            risk_tier=RiskTier.LOW,
            summary="Test endpoint",
        )
        self.assertIsNone(meta.route_plane)

    def test_endpoint_metadata_admin_plane(self):
        """EndpointMetadata can be set to ADMIN plane."""
        meta = EndpointMetadata(
            auth_tier=AuthTier.ADMIN,
            risk_tier=RiskTier.HIGH,
            summary="Admin endpoint",
            route_plane=RoutePlane.ADMIN,
        )
        self.assertEqual(meta.route_plane, RoutePlane.ADMIN)

    def test_endpoint_metadata_internal_plane(self):
        """EndpointMetadata can be set to INTERNAL plane."""
        meta = EndpointMetadata(
            auth_tier=AuthTier.INTERNAL,
            risk_tier=RiskTier.CRITICAL,
            summary="Internal endpoint",
            route_plane=RoutePlane.INTERNAL,
        )
        self.assertEqual(meta.route_plane, RoutePlane.INTERNAL)


class TestS60DecoratorPlane(unittest.TestCase):
    """S60: endpoint_metadata decorator with plane support."""

    def test_decorator_sets_plane(self):
        """Decorator correctly sets route_plane on handler."""

        @endpoint_metadata(
            auth=AuthTier.ADMIN,
            risk=RiskTier.HIGH,
            summary="Admin op",
            plane=RoutePlane.ADMIN,
        )
        async def admin_handler(request):
            pass

        meta = getattr(admin_handler, "__openclaw_meta__", None)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.route_plane, RoutePlane.ADMIN)

    def test_decorator_default_plane_is_none(self):
        """Decorator defaults to None (explicit classification required)."""

        @endpoint_metadata(
            auth=AuthTier.PUBLIC,
            risk=RiskTier.LOW,
            summary="Public op",
        )
        async def public_handler(request):
            pass

        meta = getattr(public_handler, "__openclaw_meta__", None)
        self.assertIsNotNone(meta)
        self.assertIsNone(meta.route_plane)


class TestS60MAEPostureValidation(unittest.TestCase):
    """S60: validate_mae_posture() enforcement tests."""

    def _make_entry(self, method, path, auth, risk, plane, classified=True):
        entry = {
            "method": method,
            "path": path,
            "handler": "test_handler",
            "classified": classified,
            "metadata": None,
        }
        if classified:
            entry["metadata"] = {
                "auth": auth,
                "risk": risk,
                "summary": f"Test {path}",
                "audit": None,
                "plane": plane,
            }
        return entry

    # ------------------------------------------------------------------
    # Local profile: no enforcement
    # ------------------------------------------------------------------

    def test_local_profile_always_valid(self):
        """Local profile does not enforce route-plane rules."""
        manifest = [
            self._make_entry("GET", "/admin/nuke", "public", "critical", "admin"),
        ]
        ok, violations = validate_mae_posture(manifest, profile="local")
        self.assertTrue(ok)
        self.assertEqual(violations, [])

    # ------------------------------------------------------------------
    # Public profile: enforcement active
    # ------------------------------------------------------------------

    def test_public_profile_blocks_admin_route_with_public_auth(self):
        """Admin-plane route with public auth is a violation in public profile."""
        manifest = [
            self._make_entry("POST", "/admin/restart", "public", "critical", "admin"),
        ]
        ok, violations = validate_mae_posture(manifest, profile="public")
        self.assertFalse(ok)
        self.assertEqual(len(violations), 1)
        self.assertIn("admin-plane", violations[0])

    def test_public_profile_blocks_internal_route_with_public_auth(self):
        """Internal-plane route with public auth is a violation."""
        manifest = [
            self._make_entry("GET", "/internal/metrics", "public", "low", "internal"),
        ]
        ok, violations = validate_mae_posture(manifest, profile="public")
        self.assertFalse(ok)
        self.assertIn("internal-plane", violations[0])

    def test_public_profile_allows_admin_route_with_admin_auth(self):
        """Admin-plane route with admin auth is allowed (properly protected)."""
        manifest = [
            self._make_entry("POST", "/admin/restart", "admin", "high", "admin"),
        ]
        ok, violations = validate_mae_posture(manifest, profile="public")
        self.assertTrue(ok)
        self.assertEqual(violations, [])

    def test_public_profile_allows_user_plane_public_auth(self):
        """User-plane route with public auth is normal and allowed."""
        manifest = [
            self._make_entry("GET", "/health", "public", "none", "user"),
        ]
        ok, violations = validate_mae_posture(manifest, profile="public")
        self.assertTrue(ok)

    def test_public_profile_flags_unclassified_route(self):
        """Unclassified route in public profile is a violation."""
        manifest = [
            {
                "method": "GET",
                "path": "/mystery",
                "handler": "mystery_handler",
                "classified": False,
                "metadata": None,
            }
        ]
        ok, violations = validate_mae_posture(manifest, profile="public")
        self.assertFalse(ok)
        self.assertIn("Unclassified", violations[0])

    # ------------------------------------------------------------------
    # Hardened profile: same enforcement as public
    # ------------------------------------------------------------------

    def test_hardened_profile_blocks_admin_route_with_public_auth(self):
        """Hardened profile enforces same rules as public."""
        manifest = [
            self._make_entry("POST", "/admin/config", "public", "high", "admin"),
        ]
        ok, violations = validate_mae_posture(manifest, profile="hardened")
        self.assertFalse(ok)

    # ------------------------------------------------------------------
    # Mixed manifest
    # ------------------------------------------------------------------

    def test_mixed_manifest_reports_all_violations(self):
        """Multiple violations are accumulated."""
        manifest = [
            self._make_entry("GET", "/health", "public", "none", "user"),
            self._make_entry("POST", "/admin/restart", "public", "critical", "admin"),
            self._make_entry("GET", "/internal/debug", "public", "low", "internal"),
        ]
        ok, violations = validate_mae_posture(manifest, profile="public")
        self.assertFalse(ok)
        self.assertEqual(len(violations), 2)


if __name__ == "__main__":
    unittest.main()
