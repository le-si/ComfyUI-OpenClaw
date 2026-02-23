"""
F25 Phase B: Tests for automation payload composer service.
"""

import json
import os
import unittest
from unittest.mock import patch

from services.automation_composer import AutomationComposerService


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


class _FakeLLMClient:
    def __init__(self, response):
        self._response = response

    def complete(self, *args, **kwargs):
        return self._response


class TestAutomationComposerService(unittest.TestCase):
    def test_trigger_fallback_payload(self):
        svc = AutomationComposerService()
        with patch(
            "services.automation_composer.is_template_allowed", return_value=True
        ):
            result = svc.compose_payload(
                kind="trigger",
                template_id="portrait_v1",
                intent="render portrait draft",
                inputs_hint={"requirements": "portrait", "junk": {"drop": True}},
                require_approval=True,
                trace_id="trace_1",
            )

        self.assertEqual(result["kind"], "trigger")
        self.assertFalse(result["used_tool_calling"])
        self.assertEqual(result["payload"]["template_id"], "portrait_v1")
        self.assertEqual(result["payload"]["inputs"], {"requirements": "portrait"})
        self.assertTrue(result["payload"]["require_approval"])
        self.assertEqual(result["payload"]["trace_id"], "trace_1")

    def test_webhook_fallback_defaults_profile(self):
        svc = AutomationComposerService()
        with patch(
            "services.automation_composer.is_template_allowed", return_value=True
        ):
            result = svc.compose_payload(
                kind="webhook",
                template_id="portrait_v1",
                intent="render portrait draft",
                inputs_hint={"requirements": "portrait"},
            )

        self.assertEqual(result["kind"], "webhook")
        self.assertEqual(result["payload"]["version"], 1)
        self.assertEqual(result["payload"]["profile_id"], "default")
        self.assertEqual(result["payload"]["inputs"], {"requirements": "portrait"})

    def test_compose_rejects_unknown_template(self):
        svc = AutomationComposerService()
        with patch(
            "services.automation_composer.is_template_allowed", return_value=False
        ):
            with self.assertRaises(ValueError) as ctx:
                svc.compose_payload(
                    kind="trigger",
                    template_id="missing_template",
                    intent="anything",
                )
        self.assertIn("not found", str(ctx.exception))

    def test_tool_calling_success(self):
        response = {
            "text": "",
            "raw": _make_tool_call_raw(
                "openclaw_trigger_request",
                {
                    "template_id": "portrait_v1",
                    "inputs": {"requirements": "portrait"},
                    "require_approval": True,
                },
            ),
        }
        svc = AutomationComposerService()
        svc.llm_client = _FakeLLMClient(response)

        with (
            patch(
                "services.automation_composer.is_template_allowed", return_value=True
            ),
            patch("services.automation_composer.TOOL_CALLING_AVAILABLE", True),
            patch.dict(os.environ, {"OPENCLAW_ENABLE_TOOL_CALLING": "1"}),
        ):
            result = svc.compose_payload(
                kind="trigger",
                template_id="portrait_v1",
                intent="render portrait draft",
                inputs_hint={"requirements": "from-fallback"},
                require_approval=False,
            )

        self.assertTrue(result["used_tool_calling"])
        self.assertEqual(result["payload"]["inputs"], {"requirements": "portrait"})
        self.assertTrue(result["payload"]["require_approval"])
        self.assertEqual(result["warnings"], [])

    def test_tool_calling_missing_tool_falls_back(self):
        response = {"text": "", "raw": {"choices": [{"message": {"content": "plain"}}]}}
        svc = AutomationComposerService()
        svc.llm_client = _FakeLLMClient(response)

        with (
            patch(
                "services.automation_composer.is_template_allowed", return_value=True
            ),
            patch("services.automation_composer.TOOL_CALLING_AVAILABLE", True),
            patch.dict(os.environ, {"OPENCLAW_ENABLE_TOOL_CALLING": "1"}),
        ):
            result = svc.compose_payload(
                kind="webhook",
                template_id="portrait_v1",
                intent="render portrait draft",
                profile_id="SDXL-v1",
                inputs_hint={"requirements": "from-fallback"},
            )

        self.assertFalse(result["used_tool_calling"])
        self.assertEqual(result["payload"]["profile_id"], "SDXL-v1")
        self.assertEqual(result["payload"]["inputs"], {"requirements": "from-fallback"})
        self.assertTrue(any("tool_call_fallback" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
