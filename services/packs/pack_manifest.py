import hashlib
import json
import os
from typing import Any, Dict, List, Tuple

from .pack_types import PackManifest, PackMetadata, PackType

MAX_MANIFEST_FILES = 1000
MAX_FILE_SIZE_MB = 100


class PackError(Exception):
    pass


def validate_pack_metadata(data: Dict[str, Any]) -> PackMetadata:
    """
    Validates the raw dictionary against the PackMetadata schema.
    Raises PackError if invalid.
    """
    required_fields = ["name", "version", "type", "author"]
    for field in required_fields:
        if field not in data:
            raise PackError(f"Missing required field in pack.json: {field}")

    # Accept either new or legacy minimum-version field.
    if "min_openclaw_version" not in data and "min_moltbot_version" not in data:
        raise PackError(
            "Missing required field in pack.json: min_openclaw_version (or legacy min_moltbot_version)"
        )

    # Validate type
    try:
        PackType(data["type"])
    except ValueError:
        raise PackError(
            f"Invalid pack type: {data.get('type')}. Must be one of {[t.value for t in PackType]}"
        )

    return data  # type: ignore


def compute_sha256(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        # Read in chunks to avoid memory issues
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def validate_manifest_integrity(base_dir: str, manifest: PackManifest) -> List[str]:
    """
    Verifies that all files in the manifest exist in base_dir and match their SHA256.
    Returns a list of error messages (empty if valid).
    Intentionally does NOT raise exception immediately to allow collecting all errors.
    """
    errors = []

    if len(manifest.get("files", [])) > MAX_MANIFEST_FILES:
        return [f"Manifest exceeds maximum file count ({MAX_MANIFEST_FILES})"]

    for item in manifest.get("files", []):
        rel_path = item.get("path")
        expected_hash = item.get("sha256")

        # S4: Path Traversal Check (Redundant but critical)
        if ".." in rel_path or rel_path.startswith("/") or "\\" in rel_path:
            errors.append(f"Invalid path in manifest: {rel_path}")
            continue

        full_path = os.path.join(base_dir, rel_path)

        if not os.path.exists(full_path):
            errors.append(f"Missing file: {rel_path}")
            continue

        if not os.path.isfile(full_path):
            errors.append(f"Not a file: {rel_path}")
            continue

        # Computed hash
        try:
            actual_hash = compute_sha256(full_path)
            if actual_hash != expected_hash:
                errors.append(
                    f"Hash mismatch for {rel_path}: expected {expected_hash}, got {actual_hash}"
                )
        except Exception as e:
            errors.append(f"Error reading {rel_path}: {str(e)}")

    return errors


def create_manifest(base_dir: str, metadata: Dict[str, Any]) -> str:
    """
    Generates and writes a deterministic manifest.json for the given value.
    Returns the path to the written manifest.
    """
    manifest_path = os.path.join(base_dir, "manifest.json")
    
    # 1. Collect all files and compute hashes
    files_list = []
    for root, _, files in os.walk(base_dir):
        for file in files:
            if file == "manifest.json":
                continue # Do not include manifest in manifest
                
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, base_dir).replace("\\", "/") # Normalize separators
            
            sha = compute_sha256(full_path)
            
            # Use deterministic dictionary structure
            files_list.append({
                "path": rel_path,
                "sha256": sha,
                "size": os.path.getsize(full_path)
            })
            
    # 2. Sort files by path (Important for determinism)
    files_list.sort(key=lambda x: x["path"])
    
    # 3. Create manifest object
    manifest = {
        "version": metadata.get("version", "0.0.0"),
        "files": files_list,
        # Add metadata keys sorted?
        **{k: v for k, v in sorted(metadata.items()) if k != "version"}
    }
    
    # 4. Write with sort_keys=True
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n") # POSIX newline
        
    return manifest_path
