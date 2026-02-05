import json
import os
import sys
import unittest
from unittest.mock import MagicMock

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ModuleNotFoundError:
    np = None
    NUMPY_AVAILABLE = False

# Ensure we can import the module from current directory
sys.path.append(os.getcwd())

try:
    from nodes.prompt_refiner import MoltbotPromptRefiner
except ModuleNotFoundError:
    MoltbotPromptRefiner = None
from models.schemas import GenerationParams


@unittest.skipIf(
    (not NUMPY_AVAILABLE) or (MoltbotPromptRefiner is None),
    "numpy (and node deps) not available",
)
class TestPromptRefiner(unittest.TestCase):
    def setUp(self):
        self.node = MoltbotPromptRefiner()
        # F21 Refactor: LLM Client is now in the service
        self.node.service.llm_client = MagicMock()
        self.fake_image = np.zeros((1, 64, 64, 3), dtype=np.float32)

    def test_allowlist_filtering(self):
        """Test that only allowed keys are returned in the patch."""
        # Mock LLM returning disallowed keys
        mock_response = {
            "text": json.dumps(
                {
                    "refined_positive": "pos",
                    "refined_negative": "neg",
                    "param_patch": {
                        "steps": 30,
                        "malicious_key": "delete_all",
                        "width": 1024,
                    },
                    "rationale": "reason",
                }
            )
        }
        self.node.service.llm_client.complete.return_value = mock_response

        _, _, patch_json, _ = self.node.refine_prompt(
            image=self.fake_image,
            orig_positive="orig",
            orig_negative="neg",
            issue="low_detail",
        )

        patch = json.loads(patch_json)
        self.assertIn("steps", patch)
        self.assertIn("width", patch)
        self.assertNotIn("malicious_key", patch)

    def test_clamping(self):
        """Test that patched values are clamped."""
        # Mock LLM returning out of range values
        mock_response = {
            "text": json.dumps(
                {
                    "refined_positive": "pos",
                    "refined_negative": "neg",
                    "param_patch": {
                        "cfg": 50.0,  # limit is 30.0
                        "width": 1023,  # should round to 1016 or 1024? (rounding down to nearest 8 usually or whatever schema does)
                    },
                    "rationale": "reason",
                }
            )
        }
        self.node.service.llm_client.complete.return_value = mock_response

        _, _, patch_json, _ = self.node.refine_prompt(
            image=self.fake_image,
            orig_positive="orig",
            orig_negative="neg",
            issue="other",
        )

        patch = json.loads(patch_json)
        self.assertEqual(patch["cfg"], 30.0)
        # Schema clamps 50 -> 30. 1023 -> 1016.
        self.assertEqual(patch["width"], 1016)

    def test_baseline_merge(self):
        """Test merging patch into baseline params."""
        baseline = {"steps": 20, "cfg": 7.0}

        mock_response = {
            "text": json.dumps(
                {
                    "refined_positive": "pos",
                    "refined_negative": "neg",
                    "param_patch": {"steps": 25},  # Upgrade steps
                    "rationale": "reason",
                }
            )
        }
        self.node.service.llm_client.complete.return_value = mock_response

        _, _, patch_json, _ = self.node.refine_prompt(
            image=self.fake_image,
            orig_positive="orig",
            orig_negative="neg",
            issue="other",
            params_json=json.dumps(baseline),
        )

        # Output patch should contain ONLY the changes found in the LLM response (clamped)
        # It shouldn't return the full baseline state unless it changed?
        # Current logic: filters raw_patch keys present in allowlist.
        # So it returns {"steps": 25}. cfg is not in LLM patch, so not in output.
        patch = json.loads(patch_json)
        self.assertEqual(patch["steps"], 25)
        self.assertNotIn("cfg", patch)


if __name__ == "__main__":
    unittest.main()
