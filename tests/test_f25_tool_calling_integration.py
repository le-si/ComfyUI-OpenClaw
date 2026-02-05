"""
F25: Integration tests for tool calling paths in Planner/Refiner.
"""

import json
import os
import unittest
from unittest.mock import patch


class _FakeLLMClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._response


def _make_tool_call_raw(function_name: str, arguments_obj: dict) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": function_name,
                                "arguments": json.dumps(arguments_obj),
                            },
                        }
                    ]
                }
            }
        ]
    }


class TestPlannerToolCalling(unittest.TestCase):
    def test_planner_tool_call_success(self):
        from services.planner import PlannerService

        response = {
            "text": "",
            "raw": _make_tool_call_raw(
                "openclaw_planner_output",
                {
                    "positive": "p",
                    "negative": "n",
                    "params": {"width": 99999, "steps": -10, "cfg_scale": 9.0},
                },
            ),
        }

        planner = PlannerService()
        planner.llm_client = _FakeLLMClient(response)

        with patch.dict(os.environ, {"OPENCLAW_ENABLE_TOOL_CALLING": "1"}):
            pos, neg, params = planner.plan_generation(
                profile_id="SDXL-v1",
                requirements="req",
                style_directives="style",
                seed=42,
            )

        self.assertEqual(pos, "p")
        self.assertEqual(neg, "n")
        self.assertEqual(params["seed"], 42)
        self.assertLessEqual(params["width"], 4096)
        self.assertGreaterEqual(params["width"], 256)
        self.assertGreaterEqual(params["steps"], 1)
        self.assertIn("cfg", params)
        self.assertEqual(len(planner.llm_client.calls), 1)
        _, kwargs = planner.llm_client.calls[0]
        self.assertIn("tools", kwargs)
        self.assertEqual(kwargs.get("tool_choice"), "auto")

    def test_planner_tool_call_falls_back_to_json(self):
        from services.planner import PlannerService

        response = {
            "text": json.dumps(
                {
                    "positive_prompt": "p2",
                    "negative_prompt": "n2",
                    "params": {"width": 99999, "cfg": 999},
                }
            ),
            "raw": {"choices": [{"message": {"content": "no tool call"}}]},
        }

        planner = PlannerService()
        planner.llm_client = _FakeLLMClient(response)

        with patch.dict(os.environ, {"OPENCLAW_ENABLE_TOOL_CALLING": "1"}):
            pos, neg, params = planner.plan_generation(
                profile_id="SDXL-v1",
                requirements="req",
                style_directives="style",
                seed=7,
            )

        self.assertEqual(pos, "p2")
        self.assertEqual(neg, "n2")
        self.assertEqual(params["seed"], 7)
        self.assertLessEqual(params["width"], 4096)
        self.assertLessEqual(params["cfg"], 30.0)


class TestRefinerToolCalling(unittest.TestCase):
    def test_refiner_tool_call_success(self):
        from services.refiner import RefinerService

        response = {
            "text": "",
            "raw": _make_tool_call_raw(
                "openclaw_refiner_output",
                {
                    "refined_positive": "rp",
                    "refined_negative": "rn",
                    "param_patch": {"steps": 999, "cfg_scale": 999},
                    "rationale": "because",
                },
            ),
        }

        refiner = RefinerService()
        refiner.llm_client = _FakeLLMClient(response)

        base_params = {
            "width": 1024,
            "height": 1024,
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "seed": 1,
        }

        with patch.dict(os.environ, {"OPENCLAW_ENABLE_TOOL_CALLING": "1"}):
            rp, rn, patch_out, rationale = refiner.refine_prompt(
                image_b64="dummy",
                orig_positive="op",
                orig_negative="on",
                issue="hands",
                params_json=json.dumps(base_params),
                goal="fix",
            )

        self.assertEqual(rp, "rp")
        self.assertEqual(rn, "rn")
        self.assertEqual(rationale, "because")
        self.assertIn("steps", patch_out)
        self.assertEqual(patch_out["steps"], 100)
        self.assertIn("cfg", patch_out)
        self.assertEqual(patch_out["cfg"], 30.0)
        self.assertEqual(len(refiner.llm_client.calls), 1)
        _, kwargs = refiner.llm_client.calls[0]
        self.assertIn("tools", kwargs)
        self.assertEqual(kwargs.get("tool_choice"), "auto")


if __name__ == "__main__":
    unittest.main()
