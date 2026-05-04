import os
import tempfile
import unittest
from unittest.mock import AsyncMock

from connector.config import ConnectorConfig
from connector.contract import CommandResponse
from connector.platforms.telegram_polling import TelegramPolling


class _FakeResponse:
    def __init__(self, *, status=200, text="ok", json_data=None):
        self.status = status
        self._text = text
        self._json_data = json_data

    async def text(self):
        return self._text

    async def json(self):
        return self._json_data if self._json_data is not None else {"ok": True}


class _FakePostContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, *, post_status=200):
        self.posts = []
        self.post_status = post_status

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _FakePostContext(_FakeResponse(status=self.post_status))


class _FakePollingSession(_FakeSession):
    def __init__(self, updates, *, post_status=200):
        super().__init__(post_status=post_status)
        self.updates = updates
        self.gets = []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return _FakePostContext(
            _FakeResponse(json_data={"ok": True, "result": self.updates})
        )


class _FakeRouter:
    def __init__(self):
        self.requests = []

    async def handle(self, req):
        self.requests.append(req)
        return CommandResponse(text="topic reply")


def _form_field_value(form_data, name):
    for headers, _extra, value in form_data._fields:
        if headers.get("name") == name:
            return value
    return None


class TestTelegramTopicDelivery(unittest.IsolatedAsyncioTestCase):
    def _server(self, *, state_path=None):
        cfg = ConnectorConfig()
        cfg.telegram_bot_token = "token"
        cfg.telegram_allowed_chats = [-100123]
        if state_path:
            cfg.state_path = state_path
        router = _FakeRouter()
        server = TelegramPolling(cfg, router)
        server.session = _FakeSession()
        return server, router

    async def test_inbound_topic_update_preserves_thread_and_replies_to_topic(self):
        server, router = self._server()

        await server._process_update(
            {
                "update_id": 1,
                "message": {
                    "message_id": 77,
                    "message_thread_id": 456,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 42, "username": "alice"},
                    "text": "/status",
                },
            }
        )

        self.assertEqual(router.requests[0].thread_id, "456")
        _url, kwargs = server.session.posts[-1]
        self.assertEqual(kwargs["json"]["message_thread_id"], 456)

    async def test_poll_commits_offset_after_successful_update_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server, router = self._server(
                state_path=os.path.join(temp_dir, "state.json")
            )
            update = {
                "update_id": 8,
                "message": {
                    "message_id": 77,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 42, "username": "alice"},
                    "text": "/status",
                },
            }
            server.session = _FakePollingSession([update])

            await server._poll_once()

            self.assertEqual(server.offset, 9)
            self.assertEqual(len(router.requests), 1)

    async def test_poll_releases_update_when_response_delivery_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server, router = self._server(
                state_path=os.path.join(temp_dir, "state.json")
            )
            update = {
                "update_id": 8,
                "message": {
                    "message_id": 77,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 42, "username": "alice"},
                    "text": "/status",
                },
            }
            server.session = _FakePollingSession([update], post_status=500)

            await server._poll_once()
            self.assertEqual(server.offset, 0)

            server.session = _FakePollingSession([update], post_status=200)
            await server._poll_once()

            self.assertEqual(server.offset, 9)
            self.assertEqual(len(router.requests), 2)

    async def test_send_message_includes_valid_thread_id(self):
        server, _router = self._server()

        await server.send_message(
            "-100123",
            "done",
            delivery_context={"thread_id": "456"},
        )

        _url, kwargs = server.session.posts[-1]
        self.assertEqual(kwargs["json"]["message_thread_id"], 456)

    async def test_send_image_includes_valid_thread_id(self):
        server, _router = self._server()

        await server.send_image(
            "-100123",
            b"image",
            filename="out.png",
            delivery_context={"thread_id": "456"},
        )

        _url, kwargs = server.session.posts[-1]
        self.assertEqual(_form_field_value(kwargs["data"], "message_thread_id"), "456")

    async def test_malformed_thread_id_is_diagnostic_not_telegram_parameter(self):
        server, _router = self._server()
        server._send_thread_diagnostic = AsyncMock()

        await server.send_message(
            "-100123",
            "done",
            delivery_context={"thread_id": "abc123"},
        )

        _url, kwargs = server.session.posts[-1]
        self.assertNotIn("message_thread_id", kwargs["json"])
        server._send_thread_diagnostic.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
