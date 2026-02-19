"""
R117 -- Observability Redaction Endpoint Tests.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

mock_aiohttp = MagicMock()
mock_web = MagicMock()
mock_aiohttp.web = mock_web

with patch.dict(sys.modules, {"aiohttp": mock_aiohttp, "aiohttp.web": mock_web}):
    import api.config
    import api.routes
    import services.redaction


class TestR117ObservabilityRedactionEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        mock_web.reset_mock()
        mock_aiohttp.reset_mock()

    async def test_logs_tail_redaction(self):
        mock_request = MagicMock()
        mock_request.query = {}

        with (
            patch.object(api.routes, "web", mock_web),
            patch.object(
                api.routes,
                "_ensure_observability_deps_ready",
                return_value=(True, None),
            ),
            patch.object(api.routes, "require_admin_token", return_value=(True, None)),
            patch.object(api.routes, "check_rate_limit", return_value=True),
            patch.object(api.routes, "tail_log") as mock_tail_log,
        ):
            real_redact = services.redaction.redact_text
            mock_tail_log.return_value = [
                "INFO: Starting up",
                "DEBUG: Slack bot token: xoxb-1234-5678-abcdef",
                "DEBUG: App token: xapp-9876-5432-fedcba",
                "INFO: Ready",
            ]

            with patch.object(api.routes, "redact_text", side_effect=real_redact):
                await api.routes.logs_tail_handler(mock_request)

            mock_web.json_response.assert_called_once()
            args, _ = mock_web.json_response.call_args
            response_data = args[0]
            self.assertTrue(
                response_data.get("ok"), f"Response failed: {response_data}"
            )

            content = response_data.get("content", [])
            self.assertTrue(
                any("Slack bot token: ***REDACTED***" in line for line in content),
                f"xoxb token not redacted. Got: {content}",
            )
            self.assertFalse(any("xoxb-" in line for line in content), "xoxb leaked")
            self.assertTrue(
                any("App token: ***REDACTED***" in line for line in content),
                "xapp token not redacted",
            )
            self.assertFalse(any("xapp-" in line for line in content), "xapp leaked")

    async def test_trace_handler_redaction(self):
        mock_request = MagicMock()
        mock_request.match_info = {"prompt_id": "test-prompt-id"}

        with (
            patch.object(api.routes, "web", mock_web),
            patch.object(
                api.routes,
                "_ensure_observability_deps_ready",
                return_value=(True, None),
            ),
            patch.object(api.routes, "require_admin_token", return_value=(True, None)),
            patch.object(api.routes, "trace_store") as mock_trace_store,
        ):
            mock_record = MagicMock()
            mock_record.to_dict.return_value = {
                "prompt_id": "test-prompt-id",
                "events": [
                    {
                        "type": "tool_input",
                        "data": {
                            "command": "search",
                            "query": "secret project",
                            "meta": {"slack_token": "xoxp-user-token-leak"},
                        },
                    }
                ],
            }
            mock_trace_store.get.return_value = mock_record

            await api.routes.trace_handler(mock_request)

            mock_web.json_response.assert_called_once()
            args, _ = mock_web.json_response.call_args
            response_data = args[0]
            self.assertTrue(
                response_data.get("ok"), f"Response failed: {response_data}"
            )

            trace = response_data.get("trace", {})
            events = trace.get("events", [])
            tool_input = events[0]["data"]
            self.assertEqual(tool_input["meta"]["slack_token"], "***REDACTED***")

    async def test_config_handler_redaction_check(self):
        mock_request = MagicMock()

        with (
            patch.object(api.config, "web", mock_web),
            patch.object(
                api.config, "require_observability_access", return_value=(True, None)
            ),
            patch.object(api.config, "check_rate_limit", return_value=True),
            patch.object(api.config, "get_effective_config") as mock_get_config,
            patch.object(api.config, "get_settings_schema", return_value=None),
        ):
            mock_get_config.return_value = (
                {
                    "provider": "slack",
                    "base_url": "https://slack.com/api/",
                    "some_field": "xoxr-refresh-token-leak",
                },
                {},
            )
            await api.config.config_get_handler(mock_request)

            args, _ = mock_web.json_response.call_args
            response_config = args[0].get("config", {})
            self.assertEqual(
                response_config.get("some_field"), "xoxr-refresh-token-leak"
            )


if __name__ == "__main__":
    unittest.main()
