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

            # 2. Safety Check
            for info in infos:
                if (
                    info.filename.startswith("/")
                    or ".." in info.filename
                    or "\\" in info.filename
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
            for root, _, files in os.walk(source_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, source_dir)
                    zf.write(full_path, rel_path)
