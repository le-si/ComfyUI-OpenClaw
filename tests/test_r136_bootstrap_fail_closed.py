"""R136: bootstrap security-gate failures must propagate fail-closed."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import route_bootstrap


class TestR136BootstrapFailClosed(unittest.TestCase):
    def test_initialize_registries_propagates_security_gate_failure(self):
        """Security gate failure must not be swallowed by bootstrap init."""
        cfg = SimpleNamespace(bridge_enabled=False)

        with (
            patch("services.runtime_config.get_config", return_value=cfg),
            patch("services.registry.ServiceRegistry.register"),
            patch("services.modules.enable_module"),
            patch("services.modules.ModuleRegistry.lock"),
            patch("services.modules.ModuleRegistry.get_enabled_list", return_value=[]),
            patch("services.idempotency_store.IdempotencyStore.configure_durable"),
            patch("services.state_dir.get_state_dir", return_value="."),
            patch(
                "services.security_gate.enforce_startup_gate",
                side_effect=RuntimeError("security-gate-failed"),
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                route_bootstrap._initialize_registries_and_security_gate()

        self.assertIn("security-gate-failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
