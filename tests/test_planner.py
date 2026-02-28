import json
import os
import sys
import unittest
from unittest.mock import MagicMock

# Ensure we can import the module from current directory
sys.path.append(os.getcwd())

from nodes.prompt_planner import MoltbotPromptPlanner


class TestPromptPlanner(unittest.TestCase):
    def setUp(self):
        self.node = MoltbotPromptPlanner()
        # F8 Refactor: LLM Client is now in the service
        self.node.service.llm_client = MagicMock()

    def test_planner_parses_json_and_injects_seed(self):
        mock_response = {
            "text": json.dumps(
                {
                    "positive_prompt": "a cat",
                    "negative_prompt": "blurry",
                    "params": {
                        "width": 1024,
                        "height": 1024,
                        "steps": 28,
                        "cfg": 7.5,
                        "sampler_name": "euler",
                        "scheduler": "normal",
                    },
                }
            )
        }
        self.node.service.llm_client.complete.return_value = mock_response

        pos, neg, params_json = self.node.plan_generation(
            profile="SDXL-v1",
            requirements="A cute cat",
            style_directives="photorealistic",
            seed=123,
        )

        self.assertEqual(pos, "a cat")
        self.assertEqual(neg, "blurry")
        params = json.loads(params_json)
        self.assertEqual(params["seed"], 123)
        self.assertEqual(params["width"], 1024)
        self.assertEqual(params["height"], 1024)

    def test_planner_clamps_params_via_schema(self):
        mock_response = {
            "text": json.dumps(
                {
                    "positive_prompt": "x",
                    "negative_prompt": "y",
                    "params": {
                        "width": 1023,
                        "height": 1025,
                        "steps": 9999,
                        "cfg": 999.0,
                    },
                }
            )
        }
        self.node.service.llm_client.complete.return_value = mock_response

        _, _, params_json = self.node.plan_generation(
            profile="SDXL-v1",
            requirements="x",
            style_directives="y",
            seed=0,
        )
        params = json.loads(params_json)
        # width/height should be rounded down to multiples of 8
        self.assertEqual(params["width"], 1016)
        self.assertEqual(params["height"], 1024)
        # cfg/steps should be clamped by schema (exact limits in GenerationParams)
        self.assertEqual(params["cfg"], 30.0)
        self.assertEqual(params["steps"], 100)


if __name__ == "__main__":
    unittest.main()
