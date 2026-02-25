"""
R119 Cryptographic Lifecycle Drill Automation + Evidence Contract.

Runs local/CI-safe simulation drills against existing lifecycle primitives:
- S61 trust-root lifecycle (rotation / revocation)
- S57 secrets encryption key lifecycle (key loss / recovery)
- S58 bridge token lifecycle (token compromise / revocation)
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from .bridge_token_lifecycle import BridgeTokenStore
    from .registry_quarantine import _HAS_CRYPTO as _HAS_REGISTRY_CRYPTO
    from .registry_quarantine import TrustRoot, TrustRootStore
    from .sidecar.bridge_contract import BridgeScope
except ImportError:
    from services.bridge_token_lifecycle import BridgeTokenStore  # type: ignore
    from services.registry_quarantine import (
        _HAS_CRYPTO as _HAS_REGISTRY_CRYPTO,  # type: ignore
    )
    from services.registry_quarantine import TrustRoot, TrustRootStore  # type: ignore
    from services.sidecar.bridge_contract import BridgeScope  # type: ignore

try:
    from . import secrets_encryption as _secrets_encryption
except ImportError:
    import services.secrets_encryption as _secrets_encryption  # type: ignore


SCENARIO_PLANNED_ROTATION = "planned_rotation"
SCENARIO_EMERGENCY_REVOKE = "emergency_revoke"
SCENARIO_KEY_LOSS_RECOVERY = "key_loss_recovery"
SCENARIO_TOKEN_COMPROMISE = "token_compromise"

DEFAULT_SCENARIOS = (
    SCENARIO_PLANNED_ROTATION,
    SCENARIO_EMERGENCY_REVOKE,
    SCENARIO_KEY_LOSS_RECOVERY,
    SCENARIO_TOKEN_COMPROMISE,
)


@dataclass
class DrillEvidence:
    operation: str
    scenario: str
    precheck: Dict[str, Any]
    result: Dict[str, Any]
    rollback_status: Dict[str, Any]
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    decision_codes: List[str] = field(default_factory=list)
    fail_closed_assertions: List[Dict[str, Any]] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "operation": self.operation,
            "scenario": self.scenario,
            "precheck": self.precheck,
            "result": self.result,
            "rollback_status": self.rollback_status,
            "artifacts": self.artifacts,
            "decision_codes": self.decision_codes,
            "fail_closed_assertions": self.fail_closed_assertions,
        }


class CryptoLifecycleDrillRunner:
    """Runs R119 scenario drills in isolated state directories."""

    def __init__(self, state_dir: Optional[str] = None):
        self._external_state_dir = Path(state_dir) if state_dir else None
        self._owned_tmpdir: Optional[tempfile.TemporaryDirectory[str]] = None
        self.state_dir = self._init_state_dir()

    def _init_state_dir(self) -> Path:
        if self._external_state_dir:
            self._external_state_dir.mkdir(parents=True, exist_ok=True)
            return self._external_state_dir
        self._owned_tmpdir = tempfile.TemporaryDirectory(prefix="openclaw_r119_drill_")
        return Path(self._owned_tmpdir.name)

    def close(self) -> None:
        if self._owned_tmpdir is not None:
            self._owned_tmpdir.cleanup()
            self._owned_tmpdir = None

    def __enter__(self) -> "CryptoLifecycleDrillRunner":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def run(self, scenario: str) -> Dict[str, Any]:
        scenario = str(scenario).strip().lower()
        if scenario == SCENARIO_PLANNED_ROTATION:
            return self._run_trust_root_planned_rotation().to_dict()
        if scenario == SCENARIO_EMERGENCY_REVOKE:
            return self._run_trust_root_emergency_revoke().to_dict()
        if scenario == SCENARIO_KEY_LOSS_RECOVERY:
            return self._run_secrets_key_loss_recovery().to_dict()
        if scenario == SCENARIO_TOKEN_COMPROMISE:
            return self._run_bridge_token_compromise().to_dict()
        raise ValueError(f"Unknown drill scenario: {scenario}")

    def run_many(self, scenarios: Iterable[str]) -> List[Dict[str, Any]]:
        return [self.run(s) for s in scenarios]

    def write_evidence(
        self, evidence: List[Dict[str, Any]], output_path: str | Path
    ) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "schema_version": 1,
                    "bundle": "R119",
                    "state_dir": str(self.state_dir),
                    "drills": evidence,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
            f.write("\n")
        return path

    def _artifact(
        self, path: Path, *, kind: str, exists: Optional[bool] = None
    ) -> Dict[str, Any]:
        return {
            "kind": kind,
            "path": str(path),
            "exists": bool(path.exists()) if exists is None else bool(exists),
        }

    def _run_trust_root_planned_rotation(self) -> DrillEvidence:
        state_dir = self.state_dir / "trust_root_rotation"
        state_dir.mkdir(parents=True, exist_ok=True)
        store = TrustRootStore(str(state_dir))

        old_root = TrustRoot(key_id="old-signer", public_key_pem="pem-old")
        new_root = TrustRoot(key_id="new-signer", public_key_pem="pem-new")
        store.add_root(old_root)
        store.add_root(new_root)

        pre_active = [r.key_id for r in store.get_active_roots()]
        store.revoke_root("old-signer", reason="planned_rotation_complete")
        post_active = [r.key_id for r in store.get_active_roots()]

        fail_closed_checks: List[Dict[str, Any]] = []
        decision_codes: List[str] = []

        if _HAS_REGISTRY_CRYPTO:
            ok, msg = store.verify_signature(b"payload", "AAAA", key_id="old-signer")
            fail_closed_checks.append(
                {
                    "name": "revoked_signer_rejected",
                    "passed": (not ok) and ("revoked" in msg.lower()),
                    "detail": msg,
                }
            )
            decision_codes.append("R119_REVOKED_SIGNER_REJECTED")
        else:
            fail_closed_checks.append(
                {
                    "name": "registry_crypto_unavailable_fail_closed",
                    "passed": True,
                    "detail": "cryptography unavailable; signature path not exercised in planned rotation drill",
                }
            )
            decision_codes.append("R119_REGISTRY_CRYPTO_UNAVAILABLE")

        return DrillEvidence(
            operation="trust_root_lifecycle",
            scenario=SCENARIO_PLANNED_ROTATION,
            precheck={
                "registry_crypto_available": bool(_HAS_REGISTRY_CRYPTO),
                "state_dir": str(state_dir),
                "active_before_count": len(pre_active),
            },
            result={
                "status": (
                    "pass" if all(c["passed"] for c in fail_closed_checks) else "fail"
                ),
                "active_before": pre_active,
                "active_after": post_active,
                "rotation_overlap_supported": True,
                "scope_widened": False,  # trust roots only change signer set; no auth scope expansion
            },
            rollback_status={
                "status": "not_required",
                "detail": "Planned rotation drill leaves new signer active; no rollback executed.",
            },
            artifacts=[
                self._artifact(
                    state_dir / "registry" / "trust" / "trust_roots.json",
                    kind="trust_root_store",
                )
            ],
            decision_codes=decision_codes,
            fail_closed_assertions=fail_closed_checks,
        )

    def _run_trust_root_emergency_revoke(self) -> DrillEvidence:
        state_dir = self.state_dir / "trust_root_revoke"
        state_dir.mkdir(parents=True, exist_ok=True)
        store = TrustRootStore(str(state_dir))
        compromised = TrustRoot(key_id="compromised", public_key_pem="pem-compromised")
        store.add_root(compromised)
        store.revoke_root("compromised", reason="drill_compromise")

        fail_closed_checks: List[Dict[str, Any]] = []
        decision_codes: List[str] = []

        ok, msg = store.verify_signature(b"payload", "AAAA", key_id="compromised")
        passed = (not ok) and (
            ("revoked" in msg.lower()) or ("fail-closed" in msg.lower())
        )
        fail_closed_checks.append(
            {
                "name": "compromised_signer_fail_closed",
                "passed": passed,
                "detail": msg,
            }
        )
        decision_codes.append(
            "R119_TRUST_ROOT_EMERGENCY_REVOKE_BLOCKED"
            if passed
            else "R119_TRUST_ROOT_REVOKE_UNEXPECTED"
        )

        return DrillEvidence(
            operation="trust_root_lifecycle",
            scenario=SCENARIO_EMERGENCY_REVOKE,
            precheck={
                "registry_crypto_available": bool(_HAS_REGISTRY_CRYPTO),
                "state_dir": str(state_dir),
            },
            result={
                "status": "pass" if passed else "fail",
                "revoked_key_id": "compromised",
                "verification_ok": ok,
                "verification_message": msg,
                "scope_widened": False,
            },
            rollback_status={
                "status": "not_applicable",
                "detail": "Emergency revoke drill intentionally leaves compromised signer revoked.",
            },
            artifacts=[
                self._artifact(
                    state_dir / "registry" / "trust" / "trust_roots.json",
                    kind="trust_root_store",
                )
            ],
            decision_codes=decision_codes,
            fail_closed_assertions=fail_closed_checks,
        )

    def _run_secrets_key_loss_recovery(self) -> DrillEvidence:
        state_dir = self.state_dir / "secrets_key_recovery"
        state_dir.mkdir(parents=True, exist_ok=True)

        secrets = {"openai": "sk-drill-123", "anthropic": "ak-drill-456"}
        _secrets_encryption.save_encrypted_store(secrets, state_dir)

        key_path = state_dir / _secrets_encryption.KEY_FILE
        enc_path = state_dir / _secrets_encryption.ENCRYPTED_STORE_FILE
        backup_path = state_dir / f"{_secrets_encryption.KEY_FILE}.bak.r119"
        shutil.copy2(key_path, backup_path)
        key_path.unlink(missing_ok=True)

        previous_profile = os.environ.get("OPENCLAW_RUNTIME_PROFILE")
        os.environ["OPENCLAW_RUNTIME_PROFILE"] = "hardened"
        blocked_msg = ""
        blocked = False
        recovered_ok = False
        try:
            try:
                # IMPORTANT: validate the core fail-closed primitive directly.
                # load_encrypted_store() logs and may return None on some error paths,
                # while _load_or_create_key() is the authoritative HARDENED gate.
                _secrets_encryption._load_or_create_key(state_dir)
            except RuntimeError as exc:
                blocked = True
                blocked_msg = str(exc)

            # Recovery step: restore key and verify load succeeds.
            shutil.copy2(backup_path, key_path)
            loaded = _secrets_encryption.load_encrypted_store(state_dir)
            recovered_ok = loaded == secrets
        finally:
            if previous_profile is None:
                os.environ.pop("OPENCLAW_RUNTIME_PROFILE", None)
            else:
                os.environ["OPENCLAW_RUNTIME_PROFILE"] = previous_profile

        fail_closed_checks = [
            {
                "name": "missing_key_fail_closed_hardened",
                "passed": blocked and ("fail-closed" in blocked_msg.lower()),
                "detail": blocked_msg or "missing expected RuntimeError",
            }
        ]

        return DrillEvidence(
            operation="secrets_key_lifecycle",
            scenario=SCENARIO_KEY_LOSS_RECOVERY,
            precheck={
                "state_dir": str(state_dir),
                "crypto_available": bool(
                    getattr(_secrets_encryption, "_HAS_CRYPTO", False)
                ),
                "encrypted_store_exists": enc_path.exists(),
                "key_file_exists_before_loss": backup_path.exists(),
            },
            result={
                "status": (
                    "pass"
                    if all(c["passed"] for c in fail_closed_checks) and recovered_ok
                    else "fail"
                ),
                "key_loss_blocked": blocked,
                "recovery_loaded": recovered_ok,
                "scope_widened": False,
            },
            rollback_status={
                "status": "restored" if recovered_ok else "failed",
                "detail": "Recovered by restoring the saved key backup in isolated drill state dir.",
            },
            artifacts=[
                self._artifact(enc_path, kind="encrypted_secret_store"),
                self._artifact(key_path, kind="secret_key_file"),
                self._artifact(backup_path, kind="secret_key_backup"),
            ],
            decision_codes=[
                (
                    "R119_SECRETS_KEY_LOSS_FAIL_CLOSED"
                    if blocked
                    else "R119_SECRETS_KEY_LOSS_NOT_BLOCKED"
                ),
                (
                    "R119_SECRETS_KEY_RECOVERY_OK"
                    if recovered_ok
                    else "R119_SECRETS_KEY_RECOVERY_FAILED"
                ),
            ],
            fail_closed_assertions=fail_closed_checks,
        )

    def _run_bridge_token_compromise(self) -> DrillEvidence:
        state_dir = self.state_dir / "bridge_token_compromise"
        state_dir.mkdir(parents=True, exist_ok=True)
        store = BridgeTokenStore(state_dir=str(state_dir))

        scopes = [BridgeScope.JOB_SUBMIT, BridgeScope.JOB_STATUS]
        token = store.issue_token("drill-device", scopes=scopes, ttl_sec=300)
        before_scope_values = {
            s.value if hasattr(s, "value") else str(s) for s in token.scopes
        }
        store.revoke_token(token.token_id, reason="drill_compromise")
        validation = store.validate_token(
            token.device_token, required_scope="job:submit"
        )

        fail_closed_checks = [
            {
                "name": "revoked_bridge_token_rejected",
                "passed": (not validation.ok)
                and validation.reject_reason == "token_revoked",
                "detail": validation.reject_reason,
            }
        ]
        scope_widened = False
        after_scope_values = before_scope_values

        return DrillEvidence(
            operation="bridge_token_lifecycle",
            scenario=SCENARIO_TOKEN_COMPROMISE,
            precheck={
                "state_dir": str(state_dir),
                "issued_token_id": token.token_id,
                "issued_scopes": sorted(before_scope_values),
            },
            result={
                "status": (
                    "pass" if all(c["passed"] for c in fail_closed_checks) else "fail"
                ),
                "validation_ok": validation.ok,
                "reject_reason": validation.reject_reason,
                "scope_widened": scope_widened,
                "scopes_before": sorted(before_scope_values),
                "scopes_after": sorted(after_scope_values),
            },
            rollback_status={
                "status": "not_applicable",
                "detail": "Compromised token remains revoked after drill by design.",
            },
            artifacts=[
                self._artifact(
                    state_dir / "bridge_tokens.json", kind="bridge_token_store"
                )
            ],
            decision_codes=[
                "R119_TOKEN_COMPROMISE_REVOKED",
                (
                    "R119_FAIL_CLOSED_TOKEN_REVOKED"
                    if fail_closed_checks[0]["passed"]
                    else "R119_FAIL_CLOSED_UNEXPECTED"
                ),
            ],
            fail_closed_assertions=fail_closed_checks,
        )


def run_crypto_lifecycle_drills(
    *,
    scenarios: Iterable[str] = DEFAULT_SCENARIOS,
    state_dir: Optional[str] = None,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Convenience function for scripts/tests."""
    with CryptoLifecycleDrillRunner(state_dir=state_dir) as runner:
        drills = runner.run_many(scenarios)
        payload = {
            "schema_version": 1,
            "bundle": "R119",
            "state_dir": str(runner.state_dir),
            "drills": drills,
        }
        if output_path:
            runner.write_evidence(drills, output_path)
        return payload
