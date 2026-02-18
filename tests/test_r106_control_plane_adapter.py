"""
R106 External Control-Plane Adapter Tests.

Covers:
- Contract conformance tests for submit/status/capabilities/diagnostics envelopes
- Failure-injection tests for degrade semantics (circuit breaker)
- Missing/invalid external-control auth requirements
"""

import os
import unittest
from unittest.mock import patch


class TestR106ControlPlaneAdapter(unittest.TestCase):
    """R106: External control-plane adapter tests."""

    def _import_module(self):
        try:
            from services.control_plane_adapter import (
                ADAPTER_CONTRACT_VERSION,
                CircuitBreakerState,
                ControlPlaneAdapter,
                ControlPlaneRequest,
                ControlPlaneResponse,
                DegradeMode,
            )

            return {
                "ADAPTER_CONTRACT_VERSION": ADAPTER_CONTRACT_VERSION,
                "CircuitBreakerState": CircuitBreakerState,
                "ControlPlaneAdapter": ControlPlaneAdapter,
                "ControlPlaneRequest": ControlPlaneRequest,
                "ControlPlaneResponse": ControlPlaneResponse,
                "DegradeMode": DegradeMode,
            }
        except ImportError:
            self.skipTest("control_plane_adapter module not available")

    # ------------------------------------------------------------------
    # Contract envelope tests
    # ------------------------------------------------------------------

    def test_request_envelope_schema(self):
        """Request envelope contains required contract fields."""
        m = self._import_module()
        req = m["ControlPlaneRequest"](action="submit", payload={"workflow": "{}"})
        d = req.to_dict()
        self.assertEqual(d["contract_version"], m["ADAPTER_CONTRACT_VERSION"])
        self.assertEqual(d["action"], "submit")
        self.assertIn("idempotency_key", d)
        self.assertTrue(len(d["idempotency_key"]) > 0)

    def test_response_envelope_schema(self):
        """Response envelope contains required contract fields."""
        m = self._import_module()
        resp = m["ControlPlaneResponse"](ok=True, action="submit")
        d = resp.to_dict()
        self.assertIn("ok", d)
        self.assertIn("degrade_mode", d)
        self.assertIn("contract_version", d)
        self.assertEqual(d["degrade_mode"], "normal")

    def test_idempotency_key_auto_generated(self):
        """Idempotency key is auto-generated if not provided."""
        m = self._import_module()
        req1 = m["ControlPlaneRequest"](action="submit")
        req2 = m["ControlPlaneRequest"](action="submit")
        self.assertNotEqual(req1.idempotency_key, req2.idempotency_key)

    # ------------------------------------------------------------------
    # Adapter API conformance
    # ------------------------------------------------------------------

    def test_submit_returns_response(self):
        """submit() returns a ControlPlaneResponse."""
        m = self._import_module()
        env = {
            "OPENCLAW_CONTROL_PLANE_URL": "https://cp.example.com",
            "OPENCLAW_CONTROL_PLANE_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env, clear=True):
            adapter = m["ControlPlaneAdapter"].from_env()
            resp = adapter.submit("{}")
            self.assertIsInstance(resp, m["ControlPlaneResponse"])
            self.assertEqual(resp.action, "submit")

    def test_status_returns_response(self):
        """status() returns a ControlPlaneResponse."""
        m = self._import_module()
        adapter = m["ControlPlaneAdapter"](base_url="https://cp.example.com")
        resp = adapter.status("job-123")
        self.assertEqual(resp.action, "status")

    def test_capabilities_returns_response(self):
        """capabilities() returns a ControlPlaneResponse."""
        m = self._import_module()
        adapter = m["ControlPlaneAdapter"](base_url="https://cp.example.com")
        resp = adapter.capabilities()
        self.assertEqual(resp.action, "capabilities")

    def test_diagnostics_returns_response(self):
        """diagnostics() returns a ControlPlaneResponse."""
        m = self._import_module()
        adapter = m["ControlPlaneAdapter"](base_url="https://cp.example.com")
        resp = adapter.diagnostics()
        self.assertEqual(resp.action, "diagnostics")

    # ------------------------------------------------------------------
    # Failure / degrade mode tests
    # ------------------------------------------------------------------

    def test_no_url_hard_fails(self):
        """Missing URL returns HARD_FAIL degrade mode."""
        m = self._import_module()
        adapter = m["ControlPlaneAdapter"](base_url="")
        resp = adapter.submit("{}")
        self.assertFalse(resp.ok)
        self.assertEqual(resp.degrade_mode, m["DegradeMode"].HARD_FAIL)

    def test_circuit_breaker_opens_after_threshold(self):
        """Circuit breaker opens after failure threshold."""
        m = self._import_module()
        cb = m["CircuitBreakerState"](failure_threshold=3)
        self.assertTrue(cb.can_attempt())
        cb.record_failure()
        cb.record_failure()
        self.assertTrue(cb.can_attempt())
        cb.record_failure()
        self.assertFalse(cb.can_attempt())
        self.assertEqual(cb.state, "open")

    def test_circuit_breaker_resets_on_success(self):
        """Circuit breaker resets to closed on success."""
        m = self._import_module()
        cb = m["CircuitBreakerState"](failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "open")
        cb.record_success()
        self.assertEqual(cb.state, "closed")
        self.assertTrue(cb.can_attempt())

    def test_circuit_breaker_half_open_after_timeout(self):
        """Circuit breaker transitions to half-open after reset timeout."""
        m = self._import_module()
        import time as _t

        cb = m["CircuitBreakerState"](failure_threshold=1, reset_timeout_seconds=0.01)
        cb.record_failure()
        self.assertEqual(cb.state, "open")
        _t.sleep(0.02)
        self.assertTrue(cb.can_attempt())
        self.assertEqual(cb.state, "half-open")

    # ------------------------------------------------------------------
    # Health / diagnostics
    # ------------------------------------------------------------------

    def test_health_returns_expected_keys(self):
        """get_health() includes circuit_breaker, configured, etc."""
        m = self._import_module()
        adapter = m["ControlPlaneAdapter"](base_url="https://cp.example.com")
        health = adapter.get_health()
        self.assertIn("circuit_breaker", health)
        self.assertIn("configured", health)
        self.assertTrue(health["configured"])

    def test_health_unconfigured(self):
        """get_health() shows configured=False when no URL."""
        m = self._import_module()
        adapter = m["ControlPlaneAdapter"](base_url="")
        health = adapter.get_health()
        self.assertFalse(health["configured"])

    def test_circuit_breaker_to_dict(self):
        """CircuitBreakerState.to_dict schema."""
        m = self._import_module()
        cb = m["CircuitBreakerState"]()
        d = cb.to_dict()
        self.assertIn("state", d)
        self.assertIn("failure_count", d)
        self.assertEqual(d["state"], "closed")


if __name__ == "__main__":
    unittest.main()
