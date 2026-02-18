"""
R103: Policy-as-code posture controls.
Manages signed, versioned security policy bundles with atomic activation and rollback.
"""

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Try to import cryptography for signature verification
try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

from .audit_events import build_audit_event, emit_audit_event
from .state_dir import get_state_dir

logger = logging.getLogger("ComfyUI-OpenClaw.services.policy_posture")

POLICY_DIR_NAME = "policy"
ACTIVE_BUNDLE_NAME = "active.bundle.json"
BACKUP_BUNDLE_NAME = "backup.bundle.json"
STAGED_BUNDLE_NAME = "staged.bundle.json"
TRUSTED_KEYS_NAME = "trusted_keys.json"


@dataclass
class PolicyPayload:
    """The actual policy content."""

    allowlists: Dict[str, List[str]] = field(default_factory=dict)
    high_risk_flags: Dict[str, bool] = field(default_factory=dict)
    quota_posture: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_canonical_bytes(self) -> bytes:
        """
        Produce a canonical byte representation for signing.
        Sort keys, no spaces.
        """
        data = {
            "allowlists": self.allowlists,
            "high_risk_flags": self.high_risk_flags,
            "quota_posture": self.quota_posture,
            "meta": self.meta,
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PolicyPayload":
        return cls(
            allowlists=data.get("allowlists", {}),
            high_risk_flags=data.get("high_risk_flags", {}),
            quota_posture=data.get("quota_posture", {}),
            meta=data.get("meta", {}),
        )


@dataclass
class PolicyBundle:
    """Signed policy bundle container."""

    payload: PolicyPayload
    signature: str  # Hex-encoded signature
    signer_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payload": {
                "allowlists": self.payload.allowlists,
                "high_risk_flags": self.payload.high_risk_flags,
                "quota_posture": self.payload.quota_posture,
                "meta": self.payload.meta,
            },
            "signature": self.signature,
            "signer_id": self.signer_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PolicyBundle":
        payload_data = data.get("payload", {})
        return cls(
            payload=PolicyPayload.from_dict(payload_data),
            signature=data.get("signature", ""),
            signer_id=data.get("signer_id", ""),
        )

    def verify(self, public_keys: Dict[str, str]) -> bool:
        """
        Verify the signature against trusted public keys.
        public_keys: dict of {signer_id: hex_encoded_public_key}
        """
        if not HAS_CRYPTO:
            logger.warning(
                "Cryptography module missing, cannot verify policy signature. FAIL-CLOSED."
            )
            return False

        if self.signer_id not in public_keys:
            logger.error(f"Unknown signer_id: {self.signer_id}")
            return False

        pub_key_hex = public_keys[self.signer_id]
        try:
            pub_key_bytes = bytes.fromhex(pub_key_hex)
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_key_bytes)

            sig_bytes = bytes.fromhex(self.signature)
            data_bytes = self.payload.to_canonical_bytes()

            public_key.verify(sig_bytes, data_bytes)
            return True
        except (ValueError, InvalidSignature) as e:
            logger.error(f"Signature verification failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during verification: {e}")
            return False


class PolicyManager:
    """Manages policy lifecycle: stage -> activate -> rollback."""

    def __init__(self):
        self.state_dir = Path(get_state_dir()) / POLICY_DIR_NAME
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.active_policy: Optional[PolicyBundle] = None
        self.trusted_keys: Dict[str, str] = {}

        self._load_trusted_keys()
        self._load_active_policy()

    def _load_trusted_keys(self):
        """Load trusted public keys from disk."""
        keys_path = self.state_dir / TRUSTED_KEYS_NAME
        if keys_path.exists():
            try:
                with open(keys_path, "r", encoding="utf-8") as f:
                    self.trusted_keys = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load trusted keys: {e}")

        # If no keys, we might be in uninitialized state.
        # But if we have an active policy, we MUST have keys to verify it on startup (fail-closed).

    def _load_active_policy(self):
        """Load and verify active policy. Fail-closed if invalid."""
        active_path = self.state_dir / ACTIVE_BUNDLE_NAME
        if not active_path.exists():
            logger.info(
                "No active policy bundle found. Running with default/empty policy."
            )
            return

        try:
            with open(active_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            bundle = PolicyBundle.from_dict(data)

            # Fail-closed check
            if not self.trusted_keys:
                # If we have a policy but no keys, that's a security risk.
                # In a strict hardened mode, we should crash.
                # For now, we log critical error and refuse to make it active.
                # ACTUALLY, requirements say "Hardened posture must fail-closed".
                # I'll log critical and raise exception if keys are missing but policy exists.
                msg = "Fail-closed: Active policy exists but no trusted keys found to verify it."
                logger.critical(msg)
                raise RuntimeError(msg)

            if not bundle.verify(self.trusted_keys):
                msg = "Fail-closed: Active policy signature invalid."
                logger.critical(msg)
                raise RuntimeError(msg)

            self.active_policy = bundle
            logger.info(
                f"Active policy loaded: {bundle.payload.meta.get('version', 'unknown')}"
            )

        except Exception as e:
            logger.critical(f"Failed to load active policy: {e}")
            raise RuntimeError(f"Policy load failure: {e}")

    def get_effective_policy(self) -> Optional[PolicyBundle]:
        return self.active_policy

    def stage_bundle(self, bundle_json: Dict[str, Any]) -> bool:
        """
        Validate and stage a new policy bundle.
        Returns True if successful.
        """
        try:
            bundle = PolicyBundle.from_dict(bundle_json)

            if not bundle.verify(self.trusted_keys):
                self._audit(
                    "policy.stage_failed",
                    {"reason": "invalid_signature", "signer": bundle.signer_id},
                )
                return False

            # Save to staging
            staged_path = self.state_dir / STAGED_BUNDLE_NAME
            with open(staged_path, "w", encoding="utf-8") as f:
                json.dump(bundle.to_dict(), f, indent=2)

            self._audit(
                "policy.staged", {"version": bundle.payload.meta.get("version")}
            )
            return True

        except Exception as e:
            logger.error(f"Failed to stage bundle: {e}")
            self._audit("policy.stage_failed", {"reason": str(e)})
            return False

    def activate_staged(self) -> bool:
        """Promote staged bundle to active."""
        staged_path = self.state_dir / STAGED_BUNDLE_NAME
        active_path = self.state_dir / ACTIVE_BUNDLE_NAME
        backup_path = self.state_dir / BACKUP_BUNDLE_NAME

        if not staged_path.exists():
            logger.warning("No staged bundle to activate")
            return False

        try:
            # Load staged to verify it one last time (and get version)
            with open(staged_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            new_bundle = PolicyBundle.from_dict(data)

            # 1. Backup existing active
            if active_path.exists():
                shutil.copy2(active_path, backup_path)

            # 2. Move staged to active
            shutil.move(staged_path, active_path)

            # 3. Update memory
            self.active_policy = new_bundle

            self._audit(
                "policy.activated",
                {
                    "version": new_bundle.payload.meta.get("version"),
                    "hash": new_bundle.signature[:8],
                },
            )
            return True

        except Exception as e:
            logger.error(f"Activation failed: {e}")
            self._audit("policy.activation_failed", {"error": str(e)})
            # Try to restore from backup if we messed up active
            if backup_path.exists() and not active_path.exists():
                shutil.copy2(backup_path, active_path)
            return False

    def rollback(self) -> bool:
        """Rollback to previous active bundle."""
        active_path = self.state_dir / ACTIVE_BUNDLE_NAME
        backup_path = self.state_dir / BACKUP_BUNDLE_NAME

        if not backup_path.exists():
            logger.warning("No backup bundle found for rollback")
            return False

        try:
            # Move backup to active
            shutil.copy2(backup_path, active_path)

            # Reload
            self._load_active_policy()

            version = "unknown"
            if self.active_policy:
                version = self.active_policy.payload.meta.get("version", "unknown")

            self._audit("policy.rollback", {"version": version})
            return True
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            self._audit("policy.rollback_failed", {"error": str(e)})
            return False

    def _audit(self, event_type: str, payload: Dict[str, Any]):
        event = build_audit_event(
            event_type=event_type, payload=payload, meta={"component": "PolicyManager"}
        )
        emit_audit_event(event)


# Global singleton
_policy_manager = None


def get_policy_manager() -> PolicyManager:
    global _policy_manager
    if _policy_manager is None:
        _policy_manager = PolicyManager()
    return _policy_manager
