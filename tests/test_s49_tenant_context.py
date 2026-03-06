import asyncio
import unittest
from unittest.mock import patch

from services.tenant_context import (
    DEFAULT_TENANT_ID,
    TenantBoundaryError,
    get_current_tenant_id,
    request_tenant_scope,
    resolve_tenant_context,
    tenant_scope,
)


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


class _Token:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id


class TestTenantContext(unittest.IsolatedAsyncioTestCase):
    def test_single_tenant_default(self):
        ctx = resolve_tenant_context(request=_Req({"X-OpenClaw-Tenant-Id": "team-a"}))
        self.assertEqual(ctx.tenant_id, DEFAULT_TENANT_ID)

    def test_multi_tenant_header_resolution(self):
        with patch.dict("os.environ", {"OPENCLAW_MULTI_TENANT_ENABLED": "1"}):
            ctx = resolve_tenant_context(
                request=_Req({"X-OpenClaw-Tenant-Id": "team-a"})
            )
            self.assertEqual(ctx.tenant_id, "team-a")

    def test_multi_tenant_mismatch_fail_closed(self):
        with patch.dict("os.environ", {"OPENCLAW_MULTI_TENANT_ENABLED": "1"}):
            with self.assertRaises(TenantBoundaryError) as exc:
                resolve_tenant_context(
                    request=_Req({"X-OpenClaw-Tenant-Id": "team-b"}),
                    token_info=_Token("team-a"),
                )
            self.assertEqual(exc.exception.code, "tenant_mismatch")

    async def test_async_context_propagation(self):
        async def _read_tenant():
            await asyncio.sleep(0)
            return get_current_tenant_id()

        with patch.dict("os.environ", {"OPENCLAW_MULTI_TENANT_ENABLED": "1"}):
            with tenant_scope("team-a"):
                value = await _read_tenant()
            self.assertEqual(value, "team-a")

    def test_request_scope_sets_current_tenant(self):
        with patch.dict("os.environ", {"OPENCLAW_MULTI_TENANT_ENABLED": "1"}):
            with request_tenant_scope(request=_Req({"X-OpenClaw-Tenant-Id": "team-a"})):
                self.assertEqual(get_current_tenant_id(), "team-a")


if __name__ == "__main__":
    unittest.main()
