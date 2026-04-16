import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestR167AssetApiContract(unittest.IsolatedAsyncioTestCase):
    async def test_callback_delivery_preserves_asset_api_only_refs_without_view_fetch(
        self,
    ):
        import services.callback_delivery as callback_delivery

        sent_payloads = []
        history_item = {
            "outputs": {
                "3": {
                    "images": [
                        {
                            "asset": {
                                "id": "asset-only-42",
                            }
                        }
                    ]
                }
            }
        }

        async def fake_run_io(func, *args, **kwargs):
            if func is callback_delivery.fetch_history:
                return history_item
            if func is callback_delivery.safe_request_json:
                sent_payloads.append(args[2])
                return {"ok": True}
            raise AssertionError(f"unexpected func: {func}")

        with (
            patch.object(
                callback_delivery, "run_io_in_thread", side_effect=fake_run_io
            ),
            patch.object(
                callback_delivery.asyncio, "sleep", AsyncMock(return_value=None)
            ),
            patch.object(
                callback_delivery,
                "get_callback_allow_hosts",
                return_value={"example.com"},
            ),
            patch.object(callback_delivery, "get_job_status", return_value="completed"),
            patch.object(
                callback_delivery, "get_job_event_store", return_value=MagicMock()
            ),
            patch.object(callback_delivery.trace_store, "add_event", return_value=None),
        ):
            await callback_delivery._watch_and_deliver(
                "p-r167",
                {"url": "https://example.com/hook"},
                trace_id="trace-r167",
            )

        self.assertEqual(len(sent_payloads), 1)
        outputs = sent_payloads[0]["outputs"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["asset_api_id"], "asset-only-42")
        self.assertTrue(outputs[0]["asset_api_required"])
        self.assertEqual(outputs[0]["resolution"], "asset_api_required")
        self.assertEqual(outputs[0]["view_url"], "")


if __name__ == "__main__":
    unittest.main()
