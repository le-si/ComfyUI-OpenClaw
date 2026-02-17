from typing import Optional

try:
    from aiohttp import web
except ImportError:
    # NOTE:
    # - ComfyUI runtime always has `aiohttp` available.
    # - Unit tests may run in a minimal Python environment without `aiohttp`.
    #   We still want this module to be importable, but any HTTP handlers must
    #   fail fast if invoked without real `aiohttp.web`.
    class MockWeb:
        _IS_MOCKWEB = True

        class Request:
            pass

        class Response:
            pass

        class FileResponse:
            def __init__(self, path: str, headers: Optional[dict] = None):
                self._path = path
                self.headers = headers or {}

            async def prepare(self, request):  # pragma: no cover
                return None

        @staticmethod
        def json_response(*args, **kwargs):  # pragma: no cover
            raise RuntimeError("aiohttp not available")

    web = MockWeb()

import os
import re
import shutil
import tempfile

# OpenClaw imports
if __package__ and "." in __package__:
    from ..services.access_control import require_admin_token
    from ..services.packs.pack_archive import PackArchive, PackError
    from ..services.packs.pack_registry import PackRegistry
else:
    from services.access_control import require_admin_token
    from services.packs.pack_archive import PackArchive, PackError
    from services.packs.pack_registry import PackRegistry

# R98: Endpoint Metadata
if __package__ and "." in __package__:
    from ..services.endpoint_manifest import AuthTier, RiskTier, endpoint_metadata
else:
    from services.endpoint_manifest import AuthTier, RiskTier, endpoint_metadata

# Strict pattern for pack name/version URL route parameters.
_SAFE_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _is_safe_pack_segment(value: str) -> bool:
    """Check if a pack name or version segment is safe for filesystem use."""
    if not value or value in (".", ".."):
        return False
    return bool(_SAFE_SEGMENT_RE.match(value))


if web:

    class CleanupFileResponse(web.FileResponse):
        """FileResponse that deletes the file after sending."""

        async def prepare(self, request):
            try:
                return await super().prepare(request)
            finally:
                path = self._path
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

else:
    CleanupFileResponse = None


class PacksHandlers:
    def __init__(self, state_dir: str):
        self.registry = PackRegistry(state_dir)

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.LOW,
        summary="List packs",
        description="List installed packs.",
        audit="packs.list",
    )
    async def list_packs_handler(self, request: web.Request) -> web.Response:
        """GET /packs - List installed packs."""
        if getattr(web, "_IS_MOCKWEB", False) is True:
            raise RuntimeError("aiohttp not available")

        # S8: Public read or authenticated?
        # Usually list is fine to be public-read if not strictly protected,
        # but admin token check is safer for system info.
        # Plan says "Integrity (Local)", implies authenticated management.
        # list_packs might be needed for UI.
        # Let's verify admin token for consistency with other sensitive endpoints.
        # Actually, let's keep list public-ish for UI discovery?
        # No, "require_admin_token" for everything per F32 is safer.
        # But for now, let's just implement listing.

        # NOTE: S8/F11 implies rigorous management.
        if not await self._check_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        try:
            packs = self.registry.list_packs()
            return web.json_response({"ok": True, "packs": packs})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.HIGH,
        summary="Import pack",
        description="Install pack from zip upload.",
        audit="packs.import",
    )
    async def import_pack_handler(self, request: web.Request) -> web.Response:
        """POST /packs/import - Install pack from zip upload."""
        if getattr(web, "_IS_MOCKWEB", False) is True:
            raise RuntimeError("aiohttp not available")

        if not await self._check_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        # Multipart reader
        reader = await request.multipart()
        field = await reader.next()
        if not field or field.name != "file":
            return web.json_response(
                {"ok": False, "error": "Missing file field"}, status=400
            )

        filename = field.filename or "pack.zip"

        # Save to temp file
        fd, temp_path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)

        try:
            with open(temp_path, "wb") as f:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)

            overwrite = request.query.get("overwrite", "false").lower() == "true"

            try:
                meta = self.registry.install_pack(temp_path, overwrite=overwrite)
                return web.json_response({"ok": True, "pack": meta})
            except PackError as e:
                return web.json_response({"ok": False, "error": str(e)}, status=400)

        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.HIGH,
        summary="Delete pack",
        description="Uninstall pack.",
        audit="packs.delete",
    )
    async def delete_pack_handler(self, request: web.Request) -> web.Response:
        """DELETE /packs/{name}/{version} - Uninstall pack."""
        if getattr(web, "_IS_MOCKWEB", False) is True:
            raise RuntimeError("aiohttp not available")

        if not await self._check_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        name = request.match_info.get("name")
        version = request.match_info.get("version")

        if not name or not version:
            return web.json_response(
                {"ok": False, "error": "Missing name/version"}, status=400
            )

        if not _is_safe_pack_segment(name) or not _is_safe_pack_segment(version):
            return web.json_response(
                {"ok": False, "error": "Invalid name or version format"}, status=400
            )

        try:
            success = self.registry.uninstall_pack(name, version)
            if success:
                return web.json_response({"ok": True})
            else:
                return web.json_response(
                    {"ok": False, "error": "Not found"}, status=404
                )
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Export pack",
        description="Download pack zip.",
        audit="packs.export",
    )
    async def export_pack_handler(self, request: web.Request) -> web.Response:
        """GET /packs/export/{name}/{version} - Download pack zip."""
        if getattr(web, "_IS_MOCKWEB", False) is True:
            raise RuntimeError("aiohttp not available")

        if not await self._check_auth(request):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

        name = request.match_info.get("name")
        version = request.match_info.get("version")

        if not name or not version:
            return web.json_response(
                {"ok": False, "error": "Missing name/version"}, status=400
            )

        if not _is_safe_pack_segment(name) or not _is_safe_pack_segment(version):
            return web.json_response(
                {"ok": False, "error": "Invalid name or version format"}, status=400
            )

        pack_path = self.registry.get_pack_path(name, version)
        if not pack_path:
            return web.json_response(
                {"ok": False, "error": "Pack not found"}, status=404
            )

        # Create temp zip
        fd, temp_zip = tempfile.mkstemp(suffix=".zip")
        os.close(fd)

        try:
            # Ensure manifest exists (it should for installed packs)
            if not os.path.exists(os.path.join(pack_path, "manifest.json")):
                return web.json_response(
                    {"ok": False, "error": "Pack manifest missing/corrupt"}, status=500
                )

            # Create deterministic zip
            PackArchive.create_pack_archive(pack_path, temp_zip)

            # Stream response
            return CleanupFileResponse(
                temp_zip,
                headers={
                    "Content-Disposition": f'attachment; filename="{name.replace(chr(34), "")}-{version.replace(chr(34), "")}.zip"',
                    "Content-Type": "application/zip",
                },
            )
        except Exception as e:
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _check_auth(self, request: web.Request) -> bool:
        # Re-use require_admin_token logic from access_control?
        # require_admin_token(request) returns (allowed, error_msg)
        # Wait, require_admin_token in access_control might depend on config.
        # Let's import it.
        try:
            allowed, _ = require_admin_token(request)
            return allowed
        except Exception:
            return False
