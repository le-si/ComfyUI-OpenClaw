import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services.chatops.transport_contract import (
    DeliveryMessage,
    DeliveryTarget,
    TransportType,
)


class TestR129IOLaneMigration(unittest.IsolatedAsyncioTestCase):
    async def test_callback_delivery_uses_io_lane_for_history_and_http(self):
        import services.callback_delivery as callback_delivery

        mock_store = MagicMock()
        mock_store.emit = MagicMock()

        run_io = AsyncMock(side_effect=[{"dummy": "history"}, {"ok": True}])
        with (
            patch.object(callback_delivery, "run_io_in_thread", run_io),
            patch.object(
                callback_delivery.asyncio, "sleep", AsyncMock(return_value=None)
            ),
            patch.object(
                callback_delivery,
                "get_callback_allow_hosts",
                return_value={"example.com"},
            ),
            patch.object(callback_delivery, "get_job_status", return_value="completed"),
            patch.object(callback_delivery, "extract_images", return_value=[]),
            patch.object(
                callback_delivery, "get_job_event_store", return_value=mock_store
            ),
            patch.object(callback_delivery.trace_store, "add_event", return_value=None),
        ):
            await callback_delivery._watch_and_deliver(
                "prompt-1",
                {"url": "https://example.com/callback"},
                trace_id="trace-1",
            )

        self.assertGreaterEqual(run_io.await_count, 2)
        self.assertIs(
            run_io.await_args_list[0].args[0], callback_delivery.fetch_history
        )
        self.assertIs(
            run_io.await_args_list[1].args[0], callback_delivery.safe_request_json
        )

    async def test_http_callback_adapter_uses_io_lane(self):
        os.environ["MOLTBOT_BRIDGE_CALLBACK_HOST_ALLOWLIST"] = "example.com"
        try:
            import importlib

            import services.delivery.http_callback as http_callback

            importlib.reload(http_callback)
            run_io = AsyncMock(return_value={"ok": True})
            with patch.object(http_callback, "run_io_in_thread", run_io):
                adapter = http_callback.HttpCallbackAdapter()
                target = DeliveryTarget(
                    TransportType.WEBHOOK, "https://example.com/api"
                )
                message = DeliveryMessage("hello")
                result = await adapter.deliver(target, message)

            self.assertTrue(result)
            self.assertEqual(run_io.await_count, 1)
            self.assertIs(run_io.await_args.args[0], http_callback.safe_request_json)
        finally:
            os.environ.pop("MOLTBOT_BRIDGE_CALLBACK_HOST_ALLOWLIST", None)
