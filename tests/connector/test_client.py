"""
Unit Tests for OpenClawClient (F29 Remediation Verification).
Verifies that client constructs correct HTTP requests/payloads.
"""
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from connector.config import ConnectorConfig
from connector.openclaw_client import OpenClawClient


class TestOpenClawClient(unittest.TestCase):
    def setUp(self):
        self.config = ConnectorConfig()
        self.config.admin_token = "admin-secret"
        self.client = OpenClawClient(self.config)

    def _setup_mock_session(self, MockSession, json_response):
        mock_session = MockSession.return_value
        mock_session.close = AsyncMock() # Fix await close()
        
        # Response Context Manager
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=json_response)
        
        # Enter returns the response object
        mock_ctx = MagicMock()
        mock_ctx.__aenter__.return_value = mock_resp
        mock_ctx.__aexit__.return_value = None
        
        mock_session.request.return_value = mock_ctx
        return mock_session

    def test_submit_job_payload(self):
        """Verify submit_job calls /openclaw/triggers/fire with admin token and correct payload."""
        with patch('aiohttp.ClientSession') as MockSession:
            mock_session = self._setup_mock_session(MockSession, {"ok": True, "prompt_id": "p-1"})
            
            asyncio.run(self.client.submit_job("tmpl-1", {"k": "v"}))
            
            # Assert
            mock_session.request.assert_called_once()
            args, kwargs = mock_session.request.call_args
            
            method, url = args
            self.assertEqual(method, "POST")
            self.assertTrue(url.endswith("/openclaw/triggers/fire"))
            
            headers = kwargs["headers"]
            self.assertEqual(headers["X-OpenClaw-Admin-Token"], "admin-secret")
            
            data = kwargs["json"]
            self.assertEqual(data["template_id"], "tmpl-1")
            self.assertEqual(data["inputs"], {"k": "v"})
            self.assertFalse(data["require_approval"])
            self.assertTrue("trace_id" in data)

    def test_interrupt_output(self):
        """Verify interrupt calls /api/interrupt."""
        with patch('aiohttp.ClientSession') as MockSession:
            mock_session = self._setup_mock_session(MockSession, {})

            asyncio.run(self.client.interrupt_output())
            
            method, url = mock_session.request.call_args[0]
            self.assertEqual(method, "POST")
            self.assertTrue(url.endswith("/api/interrupt"))

    def test_get_approvals_query(self):
        """Verify get_approvals uses query param and parses nested response."""
        with patch('aiohttp.ClientSession') as MockSession:
            backend_resp = {
                "approvals": [{"approval_id": "apr_1", "template_id": "tmpl_x"}],
                "count": 1,
                "pending_count": 1,
            }
            mock_session = self._setup_mock_session(MockSession, backend_resp)

            res = asyncio.run(self.client.get_approvals())
            
            # Request Check
            method, url = mock_session.request.call_args[0]
            self.assertEqual(method, "GET")
            self.assertTrue("?status=pending" in url)
            
            # Response Parsing Check (OpenClawClient.get_approvals logic)
            self.assertTrue(res["ok"])
            self.assertEqual(len(res["items"]), 1)
            self.assertEqual(res["items"][0]["approval_id"], "apr_1")
            self.assertEqual(res["pending_count"], 1)

if __name__ == "__main__":
    unittest.main()
