import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import policy_posture


class TestR103PolicyBundle(unittest.TestCase):
    def _make_bundle(self):
        if not policy_posture.HAS_CRYPTO:
            self.skipTest("cryptography not available")

        private_key = policy_posture.ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        public_hex = public_key.public_bytes(
            encoding=policy_posture.serialization.Encoding.Raw,
            format=policy_posture.serialization.PublicFormat.Raw,
        ).hex()

        payload = policy_posture.PolicyPayload(
            allowlists={"hosts": ["example.com"]},
            high_risk_flags={"dangerous_override": False},
            quota_posture={"max_jobs": 10},
            meta={"version": "v1"},
        )
        sig = private_key.sign(payload.to_canonical_bytes()).hex()
        bundle = policy_posture.PolicyBundle(
            payload=payload, signature=sig, signer_id="test"
        )
        return bundle, {"test": public_hex}

    def test_verify_valid_signature(self):
        bundle, keys = self._make_bundle()
        self.assertTrue(bundle.verify(keys))

    def test_verify_unknown_signer(self):
        bundle, _ = self._make_bundle()
        self.assertFalse(bundle.verify({}))

    def test_verify_tampered_payload(self):
        bundle, keys = self._make_bundle()
        bundle.payload.meta["version"] = "v2"
        self.assertFalse(bundle.verify(keys))


class TestR103PolicyManager(unittest.TestCase):
    def _make_bundle_dict(self):
        if not policy_posture.HAS_CRYPTO:
            self.skipTest("cryptography not available")

        private_key = policy_posture.ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        public_hex = public_key.public_bytes(
            encoding=policy_posture.serialization.Encoding.Raw,
            format=policy_posture.serialization.PublicFormat.Raw,
        ).hex()

        payload = policy_posture.PolicyPayload(meta={"version": "v1"})
        sig = private_key.sign(payload.to_canonical_bytes()).hex()
        bundle = policy_posture.PolicyBundle(
            payload=payload, signature=sig, signer_id="test"
        )
        return bundle.to_dict(), {"test": public_hex}

    def test_stage_and_activate_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dict, keys = self._make_bundle_dict()

            with (
                patch("services.policy_posture.get_state_dir", return_value=tmp),
                patch(
                    "services.policy_posture.build_audit_event",
                    side_effect=lambda event_type, payload, meta: {
                        "event_type": event_type,
                        "payload": payload,
                        "meta": meta,
                    },
                ),
                patch("services.policy_posture.emit_audit_event"),
            ):
                manager = policy_posture.PolicyManager()
                manager.trusted_keys = keys
                self.assertTrue(manager.stage_bundle(bundle_dict))
                self.assertTrue(manager.activate_staged())
                self.assertIsNotNone(manager.get_effective_policy())

    def test_fail_closed_active_policy_without_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dict, _ = self._make_bundle_dict()
            policy_dir = Path(tmp) / policy_posture.POLICY_DIR_NAME
            policy_dir.mkdir(parents=True, exist_ok=True)
            (policy_dir / policy_posture.ACTIVE_BUNDLE_NAME).write_text(
                json.dumps(bundle_dict), encoding="utf-8"
            )

            with patch("services.policy_posture.get_state_dir", return_value=tmp):
                with self.assertRaises(RuntimeError):
                    policy_posture.PolicyManager()


if __name__ == "__main__":
    unittest.main()
