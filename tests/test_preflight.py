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


import services.preflight
from api.preflight_handler import preflight_handler


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
        with (
            patch.object(
                services.preflight, "nodes", MagicMock(), create=True
            ) as mock_nodes,
            patch.object(
                services.preflight, "folder_paths", MagicMock(), create=True
            ) as mock_folder_paths,
        ):

            # Setup Node Inventory
            mock_nodes.NODE_CLASS_MAPPINGS = {
                "KSampler": object,
                "CheckpointLoaderSimple": object,
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
                "1": {"class_type": "KSampler", "inputs": {}},
                "2": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": "v1-5-pruned.ckpt"},
                },
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

        with (
            patch.object(
                services.preflight, "nodes", MagicMock(), create=True
            ) as mock_nodes,
            patch.object(
                services.preflight, "folder_paths", MagicMock(), create=True
            ) as mock_folder_paths,
        ):

            # Setup Inventory (Missing CustomNode and SDXL model)
            mock_nodes.NODE_CLASS_MAPPINGS = {"KSampler": object}
            mock_folder_paths.get_filename_list.return_value = []  # No models
            mock_folder_paths.folder_names_and_paths = {}  # Needed for dynamic check

            workflow = {
                "1": {"class_type": "UnknownCustomNode", "inputs": {}},  # Missing
                "2": {
                    "class_type": "KSampler",
                    "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},  # Missing
                },
            }

            resp = await self.client.post("/openclaw/preflight", json=workflow)
            self.assertEqual(resp.status, 200)
            data = await resp.json()

            self.assertFalse(data["ok"])  # Should fail check
            self.assertEqual(data["summary"]["missing_nodes"], 1)
            self.assertEqual(data["summary"]["missing_models"], 1)

            # Verify details
            self.assertEqual(
                data["missing_nodes"][0]["class_type"], "UnknownCustomNode"
            )
            self.assertEqual(
                data["missing_models"][0]["name"], "sd_xl_base_1.0.safetensors"
            )

    def test_current_comfyui_model_aliases_use_current_folder_keys(self):
        missing_counts = {}
        services.preflight._check_inputs_for_models(
            {
                "clip_name": "t5xxl_fp16.safetensors",
                "unet_name": "flux1-dev.safetensors",
                "geometry_estimation_name": "moge-vitl.safetensors",
                "face_detection_model": "blazeface.safetensors",
            },
            {
                "text_encoders": [],
                "diffusion_models": [],
                "geometry_estimation": [],
                "detection": [],
            },
            missing_counts,
        )

        self.assertEqual(
            missing_counts["text_encoders:t5xxl_fp16.safetensors"]["type"],
            "text_encoders",
        )
        self.assertEqual(
            missing_counts["diffusion_models:flux1-dev.safetensors"]["type"],
            "diffusion_models",
        )
        self.assertEqual(
            missing_counts["geometry_estimation:moge-vitl.safetensors"]["type"],
            "geometry_estimation",
        )
        self.assertEqual(
            missing_counts["detection:blazeface.safetensors"]["type"],
            "detection",
        )

    def test_inventory_model_types_track_current_comfyui_keys_and_exclude_custom_nodes(
        self,
    ):
        with patch.object(
            services.preflight, "folder_paths", MagicMock(), create=True
        ) as mock_folder_paths:
            mock_folder_paths.folder_names_and_paths = {
                "configs": [],
                "gligen": [],
                "latent_upscale_models": [],
                "hypernetworks": [],
                "photomaker": [],
                "classifiers": [],
                "model_patches": [],
                "custom_nodes": [],
            }

            model_types = services.preflight._resolve_inventory_model_types()

        for model_type in (
            "configs",
            "diffusers",
            "gligen",
            "latent_upscale_models",
            "hypernetworks",
            "photomaker",
            "classifiers",
            "model_patches",
            "geometry_estimation",
            "optical_flow",
            "detection",
        ):
            with self.subTest(model_type=model_type):
                self.assertIn(model_type, model_types)
        self.assertNotIn("custom_nodes", model_types)

    @patch("api.preflight_handler.check_rate_limit")
    @patch("api.preflight_handler.require_admin_token")
    @unittest_run_loop
    async def test_preflight_auth_fail(self, mock_require_admin, mock_rate_limit):
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (False, "invalid_token")

        resp = await self.client.post("/openclaw/preflight", json={})
        self.assertEqual(resp.status, 403)
