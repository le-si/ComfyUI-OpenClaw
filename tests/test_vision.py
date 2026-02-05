import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ModuleNotFoundError:
    np = None
    NUMPY_AVAILABLE = False

# Ensure we can import the module from current directory
sys.path.append(os.getcwd())

try:
    from nodes.image_to_prompt import MoltbotImageToPrompt
except ModuleNotFoundError:
    MoltbotImageToPrompt = None
from services.llm_client import LLMClient


@unittest.skipIf(
    (not NUMPY_AVAILABLE) or (MoltbotImageToPrompt is None),
    "numpy (and node deps) not available",
)
class TestImageToPrompt(unittest.TestCase):
    def setUp(self):
        self.node = MoltbotImageToPrompt()
        self.node.llm_client = MagicMock()

    def test_preprocessing_tensor_mock(self):
        """Test tensor to base64 conversion logic with numpy simulation."""
        # Simulate a 512x512 RGB image tensor (Batch=1)
        # Using numpy array as fake tensor
        fake_tensor = np.zeros((1, 512, 512, 3), dtype=np.float32)

        # We need to assert that the method calls PIL and produces base64
        # Since we don't assume torch is installed in this test env, we pass ability to handle numpy
        # The node code handles numpy arrays if cpu() attributes missing.

        b64 = self.node._tensor_to_base64_png(fake_tensor, max_side=1024)
        self.assertTrue(isinstance(b64, str))
        self.assertTrue(len(b64) > 100)  # Should be a valid b64 string

    def test_downscaling(self):
        """Test that large images are downscaled."""
        # 2048x2048 image -> should be scaled down to 1024
        fake_tensor = np.zeros((1, 2048, 2048, 3), dtype=np.float32)

        # We can't easily check internal PIL size without mocking Image.open or inspecting the base64
        # Let's mock PIL.Image inside the node class?
        # Or just trust the integration test if PIL is installed.
        # Let's try to decode the output to check size.

        b64 = self.node._tensor_to_base64_png(fake_tensor, max_side=1024)

        import base64
        import io

        from PIL import Image

        img_data = base64.b64decode(b64)
        img = Image.open(io.BytesIO(img_data))
        self.assertEqual(img.size, (1024, 1024))

    def test_llm_payload_and_parsing(self):
        """Test that the node constructs correct calls and parses JSON response."""

        # Mock LLM return
        mock_response = {
            "text": json.dumps(
                {
                    "caption": "A futuristic city",
                    "tags": ["sci-fi", "neon"],
                    "prompt_suggestion": "Cyberpunk city with neon lights",
                }
            )
        }
        self.node.llm_client.complete.return_value = mock_response

        fake_tensor = np.zeros((1, 64, 64, 3), dtype=np.float32)

        caption, tags, prompt = self.node.generate_prompt(
            image=fake_tensor,
            goal="test goal",
            detail_level="medium",
            max_image_side=1024,
        )

        # Verify LLM was called with image_base64 arg
        args, kwargs = self.node.llm_client.complete.call_args
        self.assertIn("image_base64", kwargs)
        self.assertIsNotNone(kwargs["image_base64"])

        # Verify outputs
        self.assertEqual(caption, "A futuristic city")
        self.assertEqual(tags, "sci-fi, neon")
        self.assertEqual(prompt, "Cyberpunk city with neon lights")


if __name__ == "__main__":
    unittest.main()
