"""
S60 startup MAE gate integration tests for api.routes.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api import routes


class TestS60RoutesStartupGate(unittest.TestCase):
    def setUp(self):
        self.prev_profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE")

    def tearDown(self):
        if self.prev_profile is None:
            os.environ.pop("OPENCLAW_DEPLOYMENT_PROFILE", None)
        else:
            os.environ["OPENCLAW_DEPLOYMENT_PROFILE"] = self.prev_profile

    def test_public_profile_raises_on_mae_violation(self):
        os.environ["OPENCLAW_DEPLOYMENT_PROFILE"] = "public"
        fake_server = SimpleNamespace(app=object())

        with (
            patch(
                "services.endpoint_manifest.generate_manifest",
                return_value=[{"path": "/openclaw/config"}],
            ),
            patch(
                "services.endpoint_manifest.validate_mae_posture",
                return_value=(False, ["violation"]),
            ),
        ):
            with self.assertRaises(RuntimeError):
                routes._run_mae_startup_gate(fake_server)

    def test_local_profile_warns_but_does_not_raise(self):
        os.environ["OPENCLAW_DEPLOYMENT_PROFILE"] = "local"
        fake_server = SimpleNamespace(app=object())

        with (
            patch(
                "services.endpoint_manifest.generate_manifest",
                return_value=[{"path": "/openclaw/config"}],
            ),
            patch(
                "services.endpoint_manifest.validate_mae_posture",
                return_value=(False, ["violation"]),
            ),
        ):
            routes._run_mae_startup_gate(fake_server)


if __name__ == "__main__":
    unittest.main()
