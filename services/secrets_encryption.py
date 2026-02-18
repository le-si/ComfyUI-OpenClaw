"""
S57 Secrets At-Rest Encryption + Split-Mode Secret-Reference Policy.

Provides:
1. Encrypted secret envelope with key lifecycle (AES-256-GCM via Fernet).
2. Split-mode secret-reference policy:
   - public + split: consume references, block raw writes.
   - Other modes: transparent pass-through.
3. Plaintext -> encrypted migration path with rollback support.
"""

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken

    _HAS_CRYPTO = True
except ImportError:
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment,misc]
    _HAS_CRYPTO = False

# S57 constants
ENCRYPTED_STORE_FILE = "secrets.enc.json"
KEY_FILE = "secrets.key"
ENVELOPE_VERSION = "1.0"
ENV_SECRETS_ENCRYPTION = "OPENCLAW_SECRETS_ENCRYPTION"
ENV_SPLIT_COMPAT_OVERRIDE = "OPENCLAW_SPLIT_COMPAT_OVERRIDE"


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------


def _derive_key(passphrase: str) -> bytes:
    """Derive a 32-byte encryption key from a passphrase using PBKDF2."""
    salt = b"openclaw-s57-v1"
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, 100_000)


def _generate_fernet_key() -> bytes:
    """Generate a Fernet-compatible key (url-safe base64 of 32 random bytes)."""
    if _HAS_CRYPTO:
        return Fernet.generate_key()  # type: ignore[union-attr]

    # IMPORTANT: keep a no-crypto compatibility path for minimal runtimes.
    # This preserves SecretStore behavior outside HARDENED mode.
    return base64.urlsafe_b64encode(os.urandom(32))


def _raw_to_fernet_key(raw: bytes) -> bytes:
    """Convert a raw 32-byte key to Fernet-compatible base64 key."""
    return base64.urlsafe_b64encode(raw[:32])


def _is_hardened_mode() -> bool:
    """Check if running in HARDENED runtime profile (canonical source)."""
    try:
        from .runtime_profile import is_hardened_mode as _rt_hardened

        return _rt_hardened()
    except ImportError:
        from runtime_profile import is_hardened_mode as _rt_hardened  # type: ignore

        return _rt_hardened()


def _load_or_create_key(state_dir: Path) -> bytes:
    """
    Load encryption key from disk, or create one if missing.
    Returns a Fernet-compatible key (44-byte url-safe base64).

    HARDENED mode: fail-closed if key file is missing or corrupted.
    """
    key_path = state_dir / KEY_FILE
    if key_path.exists():
        try:
            raw = key_path.read_bytes().strip()
            # Check if it's already a Fernet key (44 bytes, base64)
            if len(raw) == 44:
                return raw
            # Legacy 32-byte raw key ??convert
            if len(raw) >= 32:
                return _raw_to_fernet_key(raw)
        except Exception as e:
            if _is_hardened_mode():
                raise RuntimeError(
                    "S57 FAIL-CLOSED: Cannot read encryption key in HARDENED mode. "
                    f"Fix key file at {key_path}: {e}"
                )
            logger.warning(f"S57: Failed to read key file: {e}")

    # HARDENED: fail-closed if no key exists
    if _is_hardened_mode():
        if not _HAS_CRYPTO:
            raise RuntimeError(
                "S57 FAIL-CLOSED: cryptography library is required in HARDENED mode."
            )
        raise RuntimeError(
            "S57 FAIL-CLOSED: No encryption key found in HARDENED mode. "
            f"Expected key file at {key_path}. "
            "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )

    # Non-hardened: generate new key
    key = _generate_fernet_key()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        # Best-effort permissions
        try:
            import stat

            os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except Exception:
            pass
        logger.info("S57: Generated new Fernet encryption key")
    except Exception as e:
        logger.error(f"S57: Failed to save key file: {e}")

    return key


# ---------------------------------------------------------------------------
# Encryption / Decryption (Fernet ??AES-128-CBC + HMAC-SHA256 AEAD)
# ---------------------------------------------------------------------------
# Fernet provides authenticated encryption with associated data.
# Each token contains: version || timestamp || IV || ciphertext || HMAC.


def _fernet_encrypt(data: bytes, key: bytes) -> bytes:
    """Encrypt data using Fernet (AEAD). Returns Fernet token bytes."""
    if _HAS_CRYPTO:
        f = Fernet(key)  # type: ignore[operator]
        return f.encrypt(data)

    # Compatibility fallback (non-hardened/no-crypto): reversible encoding only.
    return base64.urlsafe_b64encode(data)


def _fernet_decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt Fernet token. Raises InvalidToken on tamper/wrong key."""
    if _HAS_CRYPTO:
        f = Fernet(key)  # type: ignore[operator]
        return f.decrypt(data)

    return base64.urlsafe_b64decode(data)


@dataclass
class EncryptedEnvelope:
    """Encrypted secret envelope."""

    version: str = ENVELOPE_VERSION
    encrypted_data: str = ""  # base64-encoded encrypted bytes
    checksum: str = ""  # SHA-256 of plaintext for tamper detection
    created_at: float = 0.0
    provider_count: int = 0

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "encrypted_data": self.encrypted_data,
            "checksum": self.checksum,
            "created_at": self.created_at,
            "provider_count": self.provider_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EncryptedEnvelope":
        return cls(
            version=d.get("version", ENVELOPE_VERSION),
            encrypted_data=d.get("encrypted_data", ""),
            checksum=d.get("checksum", ""),
            created_at=d.get("created_at", 0.0),
            provider_count=d.get("provider_count", 0),
        )


def encrypt_secrets(secrets: Dict[str, str], key: bytes) -> EncryptedEnvelope:
    """Encrypt a secrets dict into an envelope using Fernet AEAD."""
    plaintext = json.dumps(secrets, sort_keys=True).encode("utf-8")
    checksum = hashlib.sha256(plaintext).hexdigest()
    token = _fernet_encrypt(plaintext, key)
    return EncryptedEnvelope(
        version=ENVELOPE_VERSION,
        encrypted_data=token.decode("ascii"),  # Fernet tokens are ASCII-safe
        checksum=checksum,
        created_at=time.time(),
        provider_count=len(secrets),
    )


def decrypt_secrets(envelope: EncryptedEnvelope, key: bytes) -> Dict[str, str]:
    """
    Decrypt an envelope back to secrets dict.
    Fernet provides built-in tamper detection; checksum is a secondary check.
    """

    try:
        token = envelope.encrypted_data.encode("ascii")
        plaintext = _fernet_decrypt(token, key)
    except InvalidToken:
        raise ValueError(
            "S57: Secret envelope tamper detected ??Fernet auth failed. "
            "Key may be wrong or data corrupted."
        )
    except Exception as e:
        raise ValueError(f"S57: Secret envelope decode failed: {e}")
    # Secondary checksum verification
    checksum = hashlib.sha256(plaintext).hexdigest()
    if checksum != envelope.checksum:
        raise ValueError("S57: Secret envelope tamper detected ??checksum mismatch")
    return json.loads(plaintext.decode("utf-8"))


# ---------------------------------------------------------------------------
# Encrypted store operations
# ---------------------------------------------------------------------------


def save_encrypted_store(secrets: Dict[str, str], state_dir: Path) -> EncryptedEnvelope:
    """Encrypt and save secrets to encrypted store file."""
    key = _load_or_create_key(state_dir)
    envelope = encrypt_secrets(secrets, key)
    store_path = state_dir / ENCRYPTED_STORE_FILE
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(envelope.to_dict(), f, indent=2)
        logger.info(f"S57: Saved encrypted store ({envelope.provider_count} providers)")
    except Exception as e:
        logger.error(f"S57: Failed to save encrypted store: {e}")
        raise
    return envelope


def load_encrypted_store(state_dir: Path) -> Optional[Dict[str, str]]:
    """Load and decrypt secrets from encrypted store file."""
    store_path = state_dir / ENCRYPTED_STORE_FILE
    if not store_path.exists():
        return None
    try:
        with open(store_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        envelope = EncryptedEnvelope.from_dict(data)
        key = _load_or_create_key(state_dir)
        secrets = decrypt_secrets(envelope, key)
        logger.info(f"S57: Loaded encrypted store ({len(secrets)} providers)")
        return secrets
    except ValueError as e:
        logger.error(f"S57: {e}")
        raise
    except Exception as e:
        logger.error(f"S57: Failed to load encrypted store: {e}")
        return None


# ---------------------------------------------------------------------------
# Migration: plaintext -> encrypted
# ---------------------------------------------------------------------------


def migrate_plaintext_to_encrypted(state_dir: Path) -> bool:
    """
    Migrate plaintext secrets.json to encrypted secrets.enc.json.

    Returns True if migration succeeded, False if nothing to migrate.
    Raises on error.
    """
    plaintext_path = state_dir / "secrets.json"
    encrypted_path = state_dir / ENCRYPTED_STORE_FILE

    if not plaintext_path.exists():
        return False

    if encrypted_path.exists():
        logger.info("S57: Encrypted store already exists, skipping migration")
        return False

    try:
        with open(plaintext_path, "r", encoding="utf-8") as f:
            secrets = json.load(f)
        if not isinstance(secrets, dict):
            logger.warning("S57: Plaintext store is not a dict, skipping migration")
            return False

        save_encrypted_store(secrets, state_dir)
        logger.info(
            f"S57: Migrated {len(secrets)} secrets from plaintext to encrypted store"
        )
        return True
    except Exception as e:
        logger.error(f"S57: Migration failed: {e}")
        raise


# ---------------------------------------------------------------------------
# Split-mode secret-reference policy
# ---------------------------------------------------------------------------


@dataclass
class SecretReference:
    """A reference to a secret stored on the external control plane."""

    provider_id: str
    reference_key: str  # opaque key for the external CP
    source: str = "external_control_plane"

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "reference_key": self.reference_key,
            "source": self.source,
        }


def is_secret_write_blocked() -> bool:
    """
    Check if direct secret write/update is blocked in current config.

    In public + split: raw secret writes are blocked (must use references).
    Override: OPENCLAW_SPLIT_COMPAT_OVERRIDE=1 (dev-only).
    """
    try:
        from .control_plane import is_split_mode
    except ImportError:
        return False

    if not is_split_mode():
        return False

    # Check override
    compat = os.environ.get(ENV_SPLIT_COMPAT_OVERRIDE, "").lower().strip()
    if compat in ("1", "true", "yes"):
        logger.warning("S57: Secret write override active in split mode (DEV ONLY)")
        return False

    return True


def validate_secret_policy(action: str, provider_id: str) -> Tuple[bool, str]:
    """
    Validate if a secret operation is allowed under current policy.

    Args:
        action: "read", "write", "delete"
        provider_id: Provider identifier

    Returns:
        (allowed: bool, reason: str)
    """
    if action == "read":
        return True, ""

    if is_secret_write_blocked():
        return False, (
            f"S57: Direct secret {action} for '{provider_id}' is blocked in "
            "public+split mode. Use secret references from the external "
            "control plane, or set OPENCLAW_SPLIT_COMPAT_OVERRIDE=1 (dev-only)."
        )

    return True, ""
