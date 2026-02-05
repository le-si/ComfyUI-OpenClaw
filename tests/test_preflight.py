import json
import unittest
from unittest.mock import MagicMock, patch
try:
    from aiohttp import web
    from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
except Exception:  # pragma: no cover
    web = None  # type: ignore
    AioHTTPTestCase = unittest.TestCase  # type: ignore

    def unittest_run_loop(fn):  # type: ignore
        return fn

from api.preflight_handler import preflight_handler
import services.preflight

@unittest.skipIf(web is None, "aiohttp not installed")
class TestPreflightBackend(AioHTTPTestCase):
    
    async def get_application(self):
        app = web.Application()
        app.router.add_post("/openclaw/preflight", preflight_handler)
        return app

    @patch("api.preflight_handler.check_rate_limit")
    @patch("api.preflight_handler.require_admin_token")
    @unittest_run_loop
    async def test_preflight_success(self, mock_require_admin, mock_rate_limit):
        # Clear cache to ensure fresh run
        services.preflight._CACHE.clear()

        # Setup Auth/RateLimit
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)

        # Use create=True to handle cases where conditional imports resulted in missing attributes
        with patch.object(services.preflight, "nodes", MagicMock(), create=True) as mock_nodes, \
             patch.object(services.preflight, "folder_paths", MagicMock(), create=True) as mock_folder_paths:
            
            # Setup Node Inventory
            mock_nodes.NODE_CLASS_MAPPINGS = {
                "KSampler": object,
                "CheckpointLoaderSimple": object
            }

            # Setup Model Inventory
            mock_folder_paths.folder_names_and_paths = {}

            def get_filenames(ftype):
                if ftype == "checkpoints":
                    return ["v1-5-pruned.ckpt"]
                return []
            mock_folder_paths.get_filename_list.side_effect = get_filenames
            
            # Test Workflow (Valid)
            workflow = {
                "1": {
                    "class_type": "KSampler",
                    "inputs": {}
                },
                "2": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {
                        "ckpt_name": "v1-5-pruned.ckpt"
                    }
                }
            }

            resp = await self.client.post("/openclaw/preflight", json=workflow)
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["summary"]["missing_nodes"], 0)
            self.assertEqual(data["summary"]["missing_models"], 0)

    @patch("api.preflight_handler.check_rate_limit")
    @patch("api.preflight_handler.require_admin_token")
    @unittest_run_loop
    async def test_preflight_missing_items(self, mock_require_admin, mock_rate_limit):
        # Clear cache
        services.preflight._CACHE.clear()

        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)

        with patch.object(services.preflight, "nodes", MagicMock(), create=True) as mock_nodes, \
             patch.object(services.preflight, "folder_paths", MagicMock(), create=True) as mock_folder_paths:
             
            # Setup Inventory (Missing CustomNode and SDXL model)
            mock_nodes.NODE_CLASS_MAPPINGS = {"KSampler": object}
            mock_folder_paths.get_filename_list.return_value = [] # No models
            mock_folder_paths.folder_names_and_paths = {} # Needed for dynamic check

            workflow = {
                "1": {
                    "class_type": "UnknownCustomNode", # Missing
                    "inputs": {}
                },
                "2": {
                    "class_type": "KSampler",
                    "inputs": {
                        "ckpt_name": "sd_xl_base_1.0.safetensors" # Missing
                    }
                }
            }

            resp = await self.client.post("/openclaw/preflight", json=workflow)
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            
            self.assertFalse(data["ok"]) # Should fail check
            self.assertEqual(data["summary"]["missing_nodes"], 1)
            self.assertEqual(data["summary"]["missing_models"], 1)
            
            # Verify details
            self.assertEqual(data["missing_nodes"][0]["class_type"], "UnknownCustomNode")
            self.assertEqual(data["missing_models"][0]["name"], "sd_xl_base_1.0.safetensors")

    @patch("api.preflight_handler.check_rate_limit")
    @patch("api.preflight_handler.require_admin_token")
    @unittest_run_loop
    async def test_preflight_auth_fail(self, mock_require_admin, mock_rate_limit):
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (False, "invalid_token")

        resp = await self.client.post("/openclaw/preflight", json={})
        self.assertEqual(resp.status, 403)
