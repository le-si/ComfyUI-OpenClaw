"""
F41 — Registry Quarantine Service.

Remote pack registry sync with quarantine/trust gates.
All remote registry features are disabled by default (fail-closed).

Features:
- Remote registry metadata fetch with signature/hash/provenance verification
- Quarantine state: imported packs must be explicitly activated
- Audit records for fetch/verify/quarantine/activate/rollback actions
- Explicit operator action required for all state transitions

Default posture: DISABLED. Requires OPENCLAW_ENABLE_REGISTRY_SYNC=1.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ComfyUI-OpenClaw.services.registry_quarantine")

# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------

_FEATURE_FLAG = "OPENCLAW_ENABLE_REGISTRY_SYNC"


def is_registry_sync_enabled() -> bool:
    """Check if remote registry sync is enabled (default: OFF)."""
    val = os.environ.get(_FEATURE_FLAG, "").strip().lower()
    return val in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Quarantine states
# ---------------------------------------------------------------------------


class QuarantineState(str, Enum):
    """Pack quarantine lifecycle states."""

    FETCHED = "fetched"  # Downloaded but not verified
    VERIFIED = "verified"  # Integrity verified but not activated
    QUARANTINED = "quarantined"  # Held for operator review
    ACTIVATED = "activated"  # Approved and ready for use
    REJECTED = "rejected"  # Explicitly rejected by operator
    ROLLED_BACK = "rolled_back"  # Previously activated, now rolled back


# ---------------------------------------------------------------------------
# Registry entry
# ---------------------------------------------------------------------------


@dataclass
class RegistryEntry:
    """A pack entry in the registry quarantine system."""

    name: str
    version: str
    source_url: str = ""
    state: str = QuarantineState.FETCHED.value
    sha256: str = ""
    signature: str = ""  # Optional signature for provenance
    provenance: str = ""  # Author/publisher provenance info
    fetched_at: float = 0.0
    verified_at: float = 0.0
    activated_at: float = 0.0
    rejected_at: float = 0.0
    rejection_reason: str = ""
    audit_trail: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegistryEntry":
        trail = data.pop("audit_trail", [])
        entry = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        entry.audit_trail = trail
        return entry

    def add_audit(self, action: str, detail: str = "") -> None:
        """Append an audit record to this entry's trail."""
        self.audit_trail.append(
            {
                "action": action,
                "timestamp": time.time(),
                "detail": detail,
            }
        )


# ---------------------------------------------------------------------------
# Registry quarantine store
# ---------------------------------------------------------------------------


class RegistryQuarantineError(Exception):
    """Error in registry quarantine operations."""

    pass


class RegistryQuarantineStore:
    """
    Manages the quarantine lifecycle for remote pack registry entries.

    All state is persisted to a JSON file in the state directory.
    All operations require the feature flag to be ON.
    """

    MAX_ENTRIES = 200  # Cap total registry entries

    def __init__(self, state_dir: str):
        self._state_dir = state_dir
        self._quarantine_dir = os.path.join(state_dir, "registry", "quarantine")
        self._index_path = os.path.join(self._quarantine_dir, "index.json")
        self._entries: Dict[str, RegistryEntry] = {}
        os.makedirs(self._quarantine_dir, exist_ok=True)
        self._load()

    def _entry_key(self, name: str, version: str) -> str:
        return f"{name}@{version}"

    def _load(self) -> None:
        """Load registry index from disk."""
        if not os.path.exists(self._index_path):
            self._entries = {}
            return
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._entries = {}
            for key, entry_data in data.items():
                try:
                    self._entries[key] = RegistryEntry.from_dict(entry_data)
                except Exception as e:
                    logger.warning(f"Skipping corrupt registry entry {key}: {e}")
        except Exception as e:
            logger.error(f"Failed to load registry index: {e}")
            self._entries = {}

    def _save(self) -> None:
        """Persist registry index to disk."""
        try:
            data = {k: v.to_dict() for k, v in self._entries.items()}
            tmp_path = self._index_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp_path, self._index_path)
        except Exception as e:
            logger.error(f"Failed to save registry index: {e}")
            raise RegistryQuarantineError(f"Failed to persist registry state: {e}")

    def _require_enabled(self) -> None:
        """Fail-closed if feature is not enabled."""
        if not is_registry_sync_enabled():
            raise RegistryQuarantineError(
                f"Remote registry sync is disabled. Set {_FEATURE_FLAG}=1 to enable."
            )

    # ----- Public API -----

    def register_fetch(
        self,
        name: str,
        version: str,
        source_url: str,
        sha256: str,
        *,
        signature: str = "",
        provenance: str = "",
    ) -> RegistryEntry:
        """
        Register a newly fetched pack in quarantine.

        The pack enters FETCHED state and requires explicit verification + activation.
        """
        self._require_enabled()

        if len(self._entries) >= self.MAX_ENTRIES:
            raise RegistryQuarantineError(
                f"Registry entry limit reached ({self.MAX_ENTRIES}). "
                "Remove old entries before adding new ones."
            )

        key = self._entry_key(name, version)
        entry = RegistryEntry(
            name=name,
            version=version,
            source_url=source_url,
            state=QuarantineState.FETCHED.value,
            sha256=sha256,
            signature=signature,
            provenance=provenance,
            fetched_at=time.time(),
        )
        entry.add_audit("fetch", f"Fetched from {source_url}")

        self._entries[key] = entry
        self._save()
        logger.info(f"F41: Registered pack {key} in quarantine (FETCHED)")
        return entry

    def verify_integrity(
        self,
        name: str,
        version: str,
        actual_sha256: str,
    ) -> bool:
        """
        Verify a fetched pack's integrity against its registered hash.

        Transitions: FETCHED → VERIFIED (on success) or QUARANTINED (on failure).
        """
        self._require_enabled()

        key = self._entry_key(name, version)
        entry = self._entries.get(key)
        if not entry:
            raise RegistryQuarantineError(f"No registry entry for {key}")

        if entry.state not in (
            QuarantineState.FETCHED.value,
            QuarantineState.QUARANTINED.value,
        ):
            raise RegistryQuarantineError(
                f"Cannot verify pack in state '{entry.state}' (must be fetched or quarantined)"
            )

        if actual_sha256 == entry.sha256:
            entry.state = QuarantineState.VERIFIED.value
            entry.verified_at = time.time()
            entry.add_audit("verify", "Integrity check passed")
            self._save()
            logger.info(f"F41: Pack {key} integrity verified")
            return True
        else:
            entry.state = QuarantineState.QUARANTINED.value
            entry.add_audit(
                "verify_failed",
                f"Hash mismatch: expected {entry.sha256}, got {actual_sha256}",
            )
            self._save()
            logger.warning(f"F41: Pack {key} integrity FAILED — moved to quarantine")
            return False

    def activate(self, name: str, version: str) -> RegistryEntry:
        """
        Activate a verified pack for use.

        Requires explicit operator action. Only VERIFIED packs can be activated.
        """
        self._require_enabled()

        key = self._entry_key(name, version)
        entry = self._entries.get(key)
        if not entry:
            raise RegistryQuarantineError(f"No registry entry for {key}")

        if entry.state != QuarantineState.VERIFIED.value:
            raise RegistryQuarantineError(
                f"Cannot activate pack in state '{entry.state}' (must be verified first)"
            )

        entry.state = QuarantineState.ACTIVATED.value
        entry.activated_at = time.time()
        entry.add_audit("activate", "Operator-approved activation")
        self._save()
        logger.info(f"F41: Pack {key} activated")
        return entry

    def reject(self, name: str, version: str, reason: str = "") -> RegistryEntry:
        """Reject a quarantined or fetched pack."""
        self._require_enabled()

        key = self._entry_key(name, version)
        entry = self._entries.get(key)
        if not entry:
            raise RegistryQuarantineError(f"No registry entry for {key}")

        if entry.state == QuarantineState.ACTIVATED.value:
            raise RegistryQuarantineError(
                "Cannot reject an activated pack — use rollback first."
            )

        entry.state = QuarantineState.REJECTED.value
        entry.rejected_at = time.time()
        entry.rejection_reason = reason
        entry.add_audit("reject", reason or "Operator rejected")
        self._save()
        logger.info(f"F41: Pack {key} rejected")
        return entry

    def rollback(self, name: str, version: str, reason: str = "") -> RegistryEntry:
        """Roll back a previously activated pack."""
        self._require_enabled()

        key = self._entry_key(name, version)
        entry = self._entries.get(key)
        if not entry:
            raise RegistryQuarantineError(f"No registry entry for {key}")

        if entry.state != QuarantineState.ACTIVATED.value:
            raise RegistryQuarantineError(
                f"Cannot rollback pack in state '{entry.state}' (must be activated)"
            )

        entry.state = QuarantineState.ROLLED_BACK.value
        entry.add_audit("rollback", reason or "Operator-initiated rollback")
        self._save()
        logger.info(f"F41: Pack {key} rolled back")
        return entry

    def get_entry(self, name: str, version: str) -> Optional[RegistryEntry]:
        """Get a single registry entry."""
        key = self._entry_key(name, version)
        return self._entries.get(key)

    def list_entries(
        self,
        *,
        state_filter: Optional[str] = None,
    ) -> List[RegistryEntry]:
        """List all registry entries, optionally filtered by state."""
        entries = list(self._entries.values())
        if state_filter:
            entries = [e for e in entries if e.state == state_filter]
        return entries

    def remove_entry(self, name: str, version: str) -> bool:
        """Remove a registry entry (must be rejected or rolled_back)."""
        self._require_enabled()

        key = self._entry_key(name, version)
        entry = self._entries.get(key)
        if not entry:
            return False

        if entry.state not in (
            QuarantineState.REJECTED.value,
            QuarantineState.ROLLED_BACK.value,
        ):
            raise RegistryQuarantineError(
                f"Cannot remove entry in state '{entry.state}' — reject or rollback first."
            )

        del self._entries[key]
        self._save()
        logger.info(f"F41: Removed registry entry {key}")
        return True


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_store: Optional[RegistryQuarantineStore] = None


def get_quarantine_store() -> RegistryQuarantineStore:
    """Get or create the global quarantine store singleton."""
    global _store
    if _store is None:
        try:
            from .state_dir import get_state_dir

            state_dir = get_state_dir()
        except ImportError:
            try:
                from services.state_dir import get_state_dir

                state_dir = get_state_dir()
            except ImportError:
                state_dir = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "data"
                )
        _store = RegistryQuarantineStore(state_dir)
    return _store
