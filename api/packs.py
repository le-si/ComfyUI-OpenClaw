import os
import tempfile

from aiohttp import web

from ..services.access_control import require_admin_token
from ..services.packs.pack_archive import PackArchive, PackError
from ..services.packs.pack_registry import PackRegistry


class PacksHandlers:
    def __init__(self, state_dir: str):
        self.registry = PackRegistry(state_dir)

    async def list_packs_handler(self, request: web.Request) -> web.Response:
        """GET /moltbot/packs"""
        allowed, error = require_admin_token(request)
        if not allowed:
            return web.json_response({"ok": False, "error": error}, status=403)

        packs = self.registry.list_packs()
        return web.json_response({"ok": True, "packs": packs})

    async def import_pack_handler(self, request: web.Request) -> web.Response:
        """POST /moltbot/packs/import (multipart/form-data)"""
        allowed, error = require_admin_token(request)
        if not allowed:
            return web.json_response({"ok": False, "error": error}, status=403)

        reader = await request.multipart()
        field = await reader.next()

        if not field or field.name != "file":
            return web.json_response({"ok": False, "error": "missing_file"}, status=400)

        # Write upload to temp file
        fd, temp_path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)

        try:
            with open(temp_path, "wb") as f:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)

            # Install
            overwrite = request.query.get("overwrite", "").lower() == "true"
            meta = self.registry.install_pack(temp_path, overwrite=overwrite)

            return web.json_response({"ok": True, "pack": meta})

        except PackError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        except Exception as e:
            return web.json_response(
                {"ok": False, "error": f"Internal error: {str(e)}"}, status=500
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    async def export_pack_handler(self, request: web.Request) -> web.Response:
        """GET /moltbot/packs/export/{name}/{version}"""
        allowed, error = require_admin_token(request)
        if not allowed:
            return web.json_response({"ok": False, "error": error}, status=403)

        name = request.match_info.get("name")
        version = request.match_info.get("version")

        pack_dir = self.registry.get_pack_path(name, version)
        if not pack_dir:
            return web.json_response(
                {"ok": False, "error": "pack_not_found"}, status=404
            )

        # Create temp zip
        fd, temp_path = tempfile.mkstemp(suffix=f"-{name}-{version}.zip")
        os.close(fd)

        try:
            PackArchive.create_pack_archive(pack_dir, temp_path)

            # Serve file
            return web.FileResponse(
                temp_path,
                headers={
                    "Content-Disposition": f'attachment; filename="{name}-{version}.moltpack"'
                },
            )
            # Note: FileResponse might not clean up temp file automatically.
            # In a real system, we'd want a cleanup mechanism (e.g. background task).
            # For now, we rely on OS temp cleanup or add a cleanup callback if aiohttp supports it.
            # A safer way allows a streaming response that deletes after.
            # But standard ComfyUI extensions often simple-serve.
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def delete_pack_handler(self, request: web.Request) -> web.Response:
        """DELETE /moltbot/packs/{name}/{version}"""
        allowed, error = require_admin_token(request)
        if not allowed:
            return web.json_response({"ok": False, "error": error}, status=403)

        name = request.match_info.get("name")
        version = request.match_info.get("version")

        success = self.registry.uninstall_pack(name, version)
        if success:
            return web.json_response({"ok": True})
        else:
            return web.json_response(
                {"ok": False, "error": "pack_not_found"}, status=404
            )
