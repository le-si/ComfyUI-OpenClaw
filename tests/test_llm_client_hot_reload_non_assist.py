import json
import os
import unittest
from unittest.mock import patch


class _DynamicComposerLLMClient:
    _next_id = 0

    def __init__(self):
        type(self)._next_id += 1
        self.instance_id = type(self)._next_id

    def complete(self, *args, **kwargs):
        # No tool call -> compose path falls back to deterministic payload.
        return {
            "text": "",
            "raw": {"choices": [{"message": {"content": f"plain-{self.instance_id}"}}]},
        }


class _DynamicVisionLLMClient:
    _next_id = 0

    def __init__(self):
        type(self)._next_id += 1
        self.instance_id = type(self)._next_id

    def complete(self, *args, **kwargs):
        return {
            "text": json.dumps(
                {
                    "caption": f"caption-{self.instance_id}",
                    "tags": ["tag1", "tag2"],
                    "prompt_suggestion": f"prompt-{self.instance_id}",
                }
            ),
            "raw": {},
        }


class TestLLMClientHotReloadNonAssist(unittest.TestCase):
    def test_automation_composer_refreshes_default_llm_client_per_request(self):
        import services.automation_composer as composer_mod

        _DynamicComposerLLMClient._next_id = 0
        with (
            patch.object(composer_mod, "LLMClient", _DynamicComposerLLMClient),
            patch.object(composer_mod, "TOOL_CALLING_AVAILABLE", True),
            patch.object(composer_mod, "is_template_allowed", return_value=True),
            patch.dict(os.environ, {"OPENCLAW_ENABLE_TOOL_CALLING": "1"}),
        ):
            svc = composer_mod.AutomationComposerService()
            first_init_client = svc.llm_client

            res1 = svc.compose_payload(
                kind="trigger",
                template_id="tmpl",
                intent="compose 1",
                inputs_hint={"requirements": "a"},
            )
            first_request_client = svc.llm_client
            res2 = svc.compose_payload(
                kind="trigger",
                template_id="tmpl",
                intent="compose 2",
                inputs_hint={"requirements": "b"},
            )
            second_request_client = svc.llm_client

        self.assertIsNot(first_init_client, first_request_client)
        self.assertIsNot(first_request_client, second_request_client)
        self.assertEqual(first_request_client.instance_id, 2)
        self.assertEqual(second_request_client.instance_id, 3)
        self.assertFalse(res1["used_tool_calling"])
        self.assertFalse(res2["used_tool_calling"])
        self.assertTrue(any("tool_call_fallback" in w for w in res1["warnings"]))

    def test_image_to_prompt_refreshes_default_llm_client_per_request(self):
        import nodes.image_to_prompt as vision_mod

        _DynamicVisionLLMClient._next_id = 0
        with patch.object(vision_mod, "LLMClient", _DynamicVisionLLMClient):
            node = vision_mod.MoltbotImageToPrompt()
            init_client = node.llm_client

            with patch.object(node, "_tensor_to_base64_png", return_value="ZmFrZQ=="):
                cap1, tags1, prompt1 = node.generate_prompt(
                    image=object(),
                    goal="goal",
                    detail_level="medium",
                    max_image_side=512,
                )
                first_request_client = node.llm_client
                cap2, tags2, prompt2 = node.generate_prompt(
                    image=object(),
                    goal="goal",
                    detail_level="medium",
                    max_image_side=512,
                )
                second_request_client = node.llm_client

        self.assertIsNot(init_client, first_request_client)
        self.assertIsNot(first_request_client, second_request_client)
        self.assertEqual(first_request_client.instance_id, 2)
        self.assertEqual(second_request_client.instance_id, 3)
        self.assertNotEqual(cap1, cap2)
        self.assertEqual(tags1, "tag1, tag2")
        self.assertNotEqual(prompt1, prompt2)
        self.assertEqual(tags2, "tag1, tag2")


if __name__ == "__main__":
    unittest.main()
