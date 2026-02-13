"""
R77 Integrity Envelopes.

Provides canonical serialization and integrity verification for persisted state.
"""

import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union

logger = logging.getLogger("ComfyUI-OpenClaw.services.integrity")


@dataclass
class IntegrityEnvelope:
    """
    Wrapper for persisted data with integrity metadata.
    """

    version: int
    data: Dict[str, Any]
    hash: str  # SHA256 of canonical(data)
    algo: str = "sha256"
    meta: Optional[Dict[str, Any]] = None


class IntegrityError(Exception):
    """Raised when integrity verification fails."""

    pass


def canonical_dumps(data: Any) -> bytes:
    """
    Serialize data to canonical JSON (sorted keys, no whitespace).
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def calculate_hash(data: Any, algo: str = "sha256") -> str:
    """
    Calculate hash of canonicalized data.
    """
    if algo != "sha256":
        raise ValueError(f"Unsupported hash algorithm: {algo}")

    payload = canonical_dumps(data)
    return hashlib.sha256(payload).hexdigest()


def save_verified(path: str, data: Dict[str, Any], version: int = 1) -> None:
    """
    Save data wrapped in an integrity envelope.
    Atomic write.
    """
    data_hash = calculate_hash(data)
    envelope = IntegrityEnvelope(
        version=version, data=data, hash=data_hash, algo="sha256"
    )

    # Write to temp string first to Ensure serialization works
    try:
        content = json.dumps(asdict(envelope), indent=2)
    except Exception as e:
        logger.error(f"Failed to serialize integrity envelope for {path}: {e}")
        raise

    # Atomic write
    dir_name = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_name, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dir_name, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(fd)

        # Renaissance-style atomic rename
        os.replace(tmp_path, path)
    except Exception as e:
        logger.error(f"Failed to save verified file {path}: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_verified(
    path: str, expected_version: int = 1, migrate: bool = True
) -> Dict[str, Any]:
    """
    Load data from an integrity envelope.

    If `migrate` is True and the file is valid legacy JSON (no envelope),
    it returns the data as-is (caller should save back to upgrade).

    Raises IntegrityError if hash mismatch or malformed.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise IntegrityError(f"Corrupt JSON file {path}: {e}")

    # Check if it's an envelope
    if isinstance(raw, dict) and "hash" in raw and "data" in raw and "version" in raw:
        # Verify integrity
        stored_hash = raw["hash"]
        stored_data = raw["data"]

        computed_hash = calculate_hash(stored_data)
        if computed_hash != stored_hash:
            raise IntegrityError(f"Integrity check failed for {path} (hash mismatch)")

        # Verify version if needed
        # We can implement version migration logic here if multiple envelope versions exist

        return stored_data

    # Legacy Fallback
    if migrate:
        logger.info(
            f"R77: Loaded legacy file {path}, integrity check skipped (pending migration)."
        )
        # For legacy files, we assume the whole content is the data.
        return raw

    raise IntegrityError(f"File {path} is not a valid integrity envelope")
