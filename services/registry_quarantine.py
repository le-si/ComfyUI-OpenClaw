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

# S61: Ed25519 signature verification
import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

try:
    from .rate_limit import TokenBucket
except ImportError:
    from services.rate_limit import TokenBucket

try:
    from .packs.pack_archive import PackArchive, PackError
except ImportError:
    try:
        from services.packs.pack_archive import PackArchive, PackError
    except ImportError:
        PackArchive = None
        PackError = None


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


# ---------------------------------------------------------------------------
# S61: Trust root governance
# ---------------------------------------------------------------------------


@dataclass
class TrustRoot:
    """A trusted signing key for registry signature verification."""

    key_id: str  # Unique identifier
    public_key_pem: str  # PEM-encoded Ed25519 public key
    fingerprint: str = ""  # SHA-256 fingerprint of the public key
    valid_from: float = 0.0  # Unix timestamp
    valid_until: float = 0.0  # Unix timestamp (0 = no expiry)
    revoked: bool = False
    revocation_reason: str = ""
    added_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrustRoot":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class TrustRootStore:
    """
    S61: Manages trusted signing keys for registry signature verification.

    Keys are persisted to a JSON file. Supports rotation via overlap window
    (multiple active keys). Revoked keys deterministically block verification.
    """

    def __init__(self, state_dir: str):
        self._trust_dir = os.path.join(state_dir, "registry", "trust")
        self._trust_path = os.path.join(self._trust_dir, "trust_roots.json")
        self._roots: Dict[str, TrustRoot] = {}
        os.makedirs(self._trust_dir, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._trust_path):
            return
        try:
            with open(self._trust_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key_id, root_data in data.items():
                self._roots[key_id] = TrustRoot.from_dict(root_data)
            logger.info(f"S61: Loaded {len(self._roots)} trust roots")
        except Exception as e:
            logger.error(f"S61: Failed to load trust roots: {e}")

    def _save(self) -> None:
        try:
            data = {k: v.to_dict() for k, v in self._roots.items()}
            tmp = self._trust_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._trust_path)
        except Exception as e:
            logger.error(f"S61: Failed to save trust roots: {e}")

    def add_root(self, root: TrustRoot) -> None:
        """Add a trusted signing key."""
        if not root.added_at:
            root.added_at = time.time()
        # Compute fingerprint if not provided
        if not root.fingerprint and root.public_key_pem:
            root.fingerprint = hashlib.sha256(
                root.public_key_pem.encode("utf-8")
            ).hexdigest()[:16]
        self._roots[root.key_id] = root
        self._save()
        logger.info(f"S61: Added trust root {root.key_id} (fp={root.fingerprint})")

    def revoke_root(self, key_id: str, reason: str = "") -> bool:
        """Revoke a trust root. Returns True if found and revoked."""
        root = self._roots.get(key_id)
        if not root:
            return False
        root.revoked = True
        root.revocation_reason = reason
        self._roots[key_id] = root
        self._save()
        logger.warning(f"S61: Revoked trust root {key_id}: {reason}")
        return True

    def get_active_roots(self) -> List[TrustRoot]:
        """Return all non-revoked, currently valid trust roots."""
        now = time.time()
        active = []
        for root in self._roots.values():
            if root.revoked:
                continue
            if root.valid_from and now < root.valid_from:
                continue
            if root.valid_until and now > root.valid_until:
                continue
            active.append(root)
        return active

    def get_root(self, key_id: str) -> Optional[TrustRoot]:
        return self._roots.get(key_id)

    def list_roots(self) -> List[TrustRoot]:
        return list(self._roots.values())

    def verify_signature(
        self,
        data_bytes: bytes,
        signature_b64: str,
        key_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        S61: Verify a signature against active trust roots.

        Args:
            data_bytes: The data that was signed
            signature_b64: Base64-encoded Ed25519 signature
            key_id: Optional specific key_id to verify against

        Returns:
            (is_valid, message)
        """
        if not _HAS_CRYPTO:
            return False, "S61: cryptography library not available (fail-closed)"

        if not signature_b64:
            return False, "S61: Missing signature"

        try:
            sig_bytes = base64.b64decode(signature_b64)  # type: ignore[name-defined]
        except Exception:
            return False, "S61: Invalid signature encoding (not valid base64)"

        # Determine which keys to try
        if key_id:
            root = self._roots.get(key_id)
            if not root:
                return False, f"S61: Unknown key_id '{key_id}'"
            if root.revoked:
                return (
                    False,
                    f"S61: Key '{key_id}' has been revoked: {root.revocation_reason}",
                )
            candidates = [root]
        else:
            candidates = self.get_active_roots()

        if not candidates:
            return False, "S61: No active trust roots configured"

        for root in candidates:
            try:
                public_key = serialization.load_pem_public_key(  # type: ignore[name-defined]
                    root.public_key_pem.encode("utf-8")
                )
                if not isinstance(public_key, Ed25519PublicKey):  # type: ignore[name-defined]
                    continue
                public_key.verify(sig_bytes, data_bytes)
                return True, f"S61: Signature verified with key '{root.key_id}'"
            except Exception:
                continue

        return (
            False,
            "S61: Signature verification failed against all active trust roots",
        )


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

        # S38: Anti-Abuse State
        # dedupe_cache: source_url -> timestamp
        self._dedupe_cache: Dict[str, float] = {}
        # fetch_buckets: context (IP/token) -> TokenBucket
        self._fetch_buckets: Dict[str, TokenBucket] = {}
        self._global_bucket = TokenBucket(capacity=10, tokens_per_second=10.0 / 60.0)
        self._dedupe_window = 60.0  # seconds

        # S39: Trust Policy
        # modes: "audit" (log warning on sig fail), "strict" (block on sig fail)
        self._policy_mode = os.environ.get("OPENCLAW_REGISTRY_POLICY", "audit").lower()

        # S61: Trust root store for signature verification
        self._trust_root_store = TrustRootStore(state_dir)

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

    def _check_abuse(self, source_url: str, context: str = "global") -> None:
        """
        S38: Enforce anti-abuse controls (Rate Limit + Dedupe).
        context: client IP or token for per-client rate limiting.
        """
        now = time.time()

        # 1. Dedupe (Bounded Window)
        if source_url:
            last_seen = self._dedupe_cache.get(source_url, 0.0)
            if now - last_seen < self._dedupe_window:
                raise RegistryQuarantineError(
                    f"Duplicate fetch for {source_url} rejected (dedupe window {self._dedupe_window}s)"
                )

        # 2. Rate Limit (Per-Context + Global Fallback)
        # Global limit
        if not self._global_bucket.consume(1):
            raise RegistryQuarantineError("Global registry fetch rate limit exceeded")

        # Context limit (if strictly context provided)
        if context and context != "global":
            if context not in self._fetch_buckets:
                # Per-IP limit: 5/min? Or same as global?
                # Let's say 5/min per IP to be stricter than global.
                self._fetch_buckets[context] = TokenBucket(
                    capacity=5, tokens_per_second=5.0 / 60.0
                )
            if not self._fetch_buckets[context].consume(1):
                raise RegistryQuarantineError(
                    f"Rate limit exceeded for client {context} (5/min)"
                )

        # Update state
        if source_url:
            self._dedupe_cache[source_url] = now

    def _prune_stale_entries(self) -> int:
        """
        S38: Prune stale entries and anti-abuse state.
        Returns count of removed entries.
        """
        now = time.time()
        removed = 0

        # Prune dedupe cache
        stale_dedupe = [
            k
            for k, v in self._dedupe_cache.items()
            if now - v > self._dedupe_window * 2
        ]
        for k in stale_dedupe:
            del self._dedupe_cache[k]

        # Prune registry entries (Rejected/Rolled Back > 7 days)
        # Or just enforcing MAX_ENTRIES is enough?
        # S38 says "scheduled prune". Let's remove very old rejected items.
        ttl = 7 * 24 * 3600
        to_remove = []
        for key, entry in self._entries.items():
            if entry.state in (
                QuarantineState.REJECTED.value,
                QuarantineState.ROLLED_BACK.value,
            ):
                # If timestamp is 0, use fetched_at
                ts = entry.rejected_at or entry.fetched_at
                if now - ts > ttl:
                    to_remove.append(key)

        for key in to_remove:
            del self._entries[key]
            removed += 1

        if removed > 0:
            self._save()
            logger.info(f"F41: Pruned {removed} stale registry entries")

        return removed

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
        request_context: str = "global",
    ) -> RegistryEntry:
        """
        Register a newly fetched pack in quarantine.

        request_context: IP or user identifier for rate limiting.
        """
        self._require_enabled()
        self._check_abuse(source_url, request_context)
        self._prune_stale_entries()

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
        file_path: Optional[str] = None,
    ) -> bool:
        """
        Verify a fetched pack's integrity against its registered hash.
        A local file_path is REQUIRED for S39 Static Code Safety checks.

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

        # S39: Static Preflight Scan (Code Safety) - MANDATORY
        # We cannot verify safety if we don't have the file.
        if not file_path:
            raise RegistryQuarantineError(
                "Integrity verification requires local file_path for preflight scan."
            )

        if file_path and PackArchive:
            # Check existence
            if not os.path.exists(file_path):
                raise RegistryQuarantineError(
                    f"Preflight scan failed: file not found {file_path}"
                )

            # Check safety
            import zipfile

            try:
                with zipfile.ZipFile(file_path, "r") as zf:
                    PackArchive._check_code_safety(zf)
                # If we get here, safety check passed
                entry.add_audit("preflight_scan", "Code safety check passed")
            except Exception as e:
                msg = f"Code safety violation: {e}"

                # Strict Mode -> Block
                if self._policy_mode == "strict":
                    entry.state = QuarantineState.QUARANTINED.value
                    entry.add_audit("preflight_failed", msg)
                    self._save()
                    logger.warning(
                        f"F41: Pack {key} blocked by strict safety policy: {msg}"
                    )
                    return False
                else:
                    # Audit Mode -> Warn
                    entry.add_audit("policy_warning", msg)
                    logger.warning(f"F41: Pack {key} safety warning: {msg}")

        if actual_sha256 == entry.sha256:
            # S39: Signature Policy Check
            sig_ok, sig_msg = self._verify_signature(entry)
            if not sig_ok:
                if self._policy_mode == "strict":
                    entry.state = QuarantineState.QUARANTINED.value
                    entry.add_audit(
                        "verify_failed", f"Signature policy failed: {sig_msg}"
                    )
                    self._save()
                    logger.warning(
                        f"F41: Pack {key} blocked by strict signature policy: {sig_msg}"
                    )
                    return False
                else:
                    entry.add_audit(
                        "policy_warning", f"Signature check failed: {sig_msg}"
                    )
                    logger.warning(f"F41: Pack {key} policy warning: {sig_msg}")

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

    def _verify_signature(self, entry: RegistryEntry) -> Tuple[bool, str]:
        """
        S61: Verify entry signature against trusted keys using Ed25519.

        Returns (is_valid, message).

        Behavior:
        - No signature present → fail ("Missing signature")
        - cryptography unavailable → fail-closed
        - No active trust roots → fail-closed
        - Revoked signer → deterministic block
        - Valid signature → pass
        """
        if not entry.signature:
            return False, "Missing signature"

        # Build data to verify: canonical representation
        data_str = f"{entry.name}@{entry.version}:{entry.sha256}"
        data_bytes = data_str.encode("utf-8")

        return self._trust_root_store.verify_signature(
            data_bytes=data_bytes,
            signature_b64=entry.signature,
        )

    @property
    def trust_root_store(self) -> TrustRootStore:
        """Access the trust root store for key management."""
        return self._trust_root_store

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
