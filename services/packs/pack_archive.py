import json
import os
import shutil
import tempfile
import zipfile
from typing import Optional

from .pack_manifest import (
    MAX_FILE_SIZE_MB,
    MAX_MANIFEST_FILES,
    PackError,
    validate_manifest_integrity,
    validate_pack_metadata,
)
from .pack_types import PackMetadata

MAX_COMPRESSION_RATIO = 100


class PackArchive:
    @staticmethod
    def _is_safe_path(base_dir: str, rel_path: str) -> bool:
        # Prevent path traversal
        abs_base = os.path.abspath(base_dir)
        abs_target = os.path.abspath(os.path.join(base_dir, rel_path))
        return abs_target.startswith(abs_base)

    @staticmethod
    def extract_pack(zip_path: str, target_dir: str) -> PackMetadata:
        """
        Safely extracts a pack archive to target_dir.
        1. Checks limits (count/size).
        2. checks for symlinks/unsafe paths.
        3. Extracts.
        4. Validates manifest.json (integrity).
        5. Validates pack.json (schema).
        Returns the parsed PackMetadata.
        """
        if not os.path.exists(zip_path):
            raise PackError("Archive not found")

        with zipfile.ZipFile(zip_path, "r") as zf:
            # 1. Pre-flight Check
            infos = zf.infolist()
            if len(infos) > MAX_MANIFEST_FILES:
                raise PackError(
                    f"Too many files in archive ({len(infos)} > {MAX_MANIFEST_FILES})"
                )

            total_size = sum(i.file_size for i in infos)
            if total_size > MAX_FILE_SIZE_MB * 1024 * 1024:
                raise PackError(f"Archive content too large ({total_size} bytes)")

            # Check compression ratio
            compressed_size = sum(i.compress_size for i in infos)
            if compressed_size > 0:
                ratio = total_size / compressed_size
                if ratio > MAX_COMPRESSION_RATIO:
                    raise PackError(f"Compression ratio too high ({ratio:.1f} > {MAX_COMPRESSION_RATIO})")

            # 2. Safety Check
            for info in infos:
                if (
                    info.filename.startswith("/")
                    or ".." in info.filename
                    or "\\" in info.filename
                    or any(c < ' ' for c in info.filename) # Control chars
                ):
                    raise PackError(f"Unsafe filename: {info.filename}")

                # Check for symlinks (S_IFLNK - 0xA000)
                # ZipInfo.external_attr: upper 16 bits are Unix permissions
                attr = info.external_attr >> 16
                if (attr & 0xF000) == 0xA000:
                    raise PackError(f"Symlinks not allowed: {info.filename}")

            # 3. Extract to temp dir first
            with tempfile.TemporaryDirectory() as tmp_dir:
                zf.extractall(tmp_dir)

                # 4/5. Validate Manifest & Metadata *before* moving to final
                manifest_path = os.path.join(tmp_dir, "manifest.json")
                pack_json_path = os.path.join(tmp_dir, "pack.json")

                if not os.path.exists(manifest_path):
                    raise PackError("Missing manifest.json")
                if not os.path.exists(pack_json_path):
                    raise PackError("Missing pack.json")

                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    with open(pack_json_path, "r", encoding="utf-8") as f:
                        pack_meta = json.load(f)
                except json.JSONDecodeError:
                    raise PackError("Invalid JSON in manifest or pack definition")

                # Validate Metadata Schema
                validate_pack_metadata(pack_meta)

                # Validate Integrity
                integrity_errors = validate_manifest_integrity(tmp_dir, manifest)
                if integrity_errors:
                    raise PackError(
                        f"Integrity check failed: {'; '.join(integrity_errors)}"
                    )

                # Safe to move to target
                # Ensure target exists and is empty/ready
                if os.path.exists(target_dir):
                    shutil.rmtree(
                        target_dir
                    )  # Overwrite logic managed by registry, but here we clean up
                os.makedirs(target_dir, exist_ok=True)

                # Copy content
                shutil.copytree(tmp_dir, target_dir, dirs_exist_ok=True)

                return pack_meta  # type: ignore

    @staticmethod
    def create_pack_archive(source_dir: str, output_zip: str):
        """
        Creates a zip archive from source_dir.
        Does NOT re-generate manifest (assumes it exists and is correct).
        """
        if not os.path.exists(source_dir):
            raise PackError("Source directory does not exist")

        with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            files_to_add = []
            for root, _, files in os.walk(source_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, source_dir)
                    files_to_add.append((full_path, rel_path))
            
            # Deterministic order
            files_to_add.sort(key=lambda x: x[1])

            for full_path, rel_path in files_to_add:
                 # Deterministic metadata (timestamp)
                 # ZipInfo requires a tuple (year, month, day, hour, min, sec)
                 # We use a fixed epoch for reproducibility, or file mtime? 
                 # Plan says "regenerate manifest deterministically". 
                 # If we use file mtime, it changes if we touch files.
                 # Using fixed timestamp ensures identical binary hash for identical content.
                 # But standard zip tools use mtime.
                 # Let's use 1980-01-01 00:00:00 (DOS epoch)
                 zinfo = zipfile.ZipInfo(rel_path)
                 zinfo.date_time = (1980, 1, 1, 0, 0, 0)
                 zinfo.compress_type = zipfile.ZIP_DEFLATED
                 
                 # Set regular file permissions (0o644)
                 # External attr: (0o100644 << 16) = 0x81A40000
                 zinfo.external_attr = 0x81A40000
                 
                 with open(full_path, "rb") as f:
                     zf.writestr(zinfo, f.read())
