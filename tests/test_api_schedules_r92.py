import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from aiohttp import web  # noqa: F401

    AIOHTTP_AVAILABLE = True
except ModuleNotFoundError:
    AIOHTTP_AVAILABLE = False


@unittest.skipIf(not AIOHTTP_AVAILABLE, "aiohttp not available")
class TestScheduleApiR92(unittest.IsolatedAsyncioTestCase):
    async def test_run_now_blocked_in_delegated_mode(self):
        from api.schedules import ScheduleHandlers

        mock_store = MagicMock()
        mock_store.get.return_value = MagicMock(template_id="tmpl_1")
        mock_runner = MagicMock()
        mock_runner.is_execution_delegated.return_value = True

        with patch("api.schedules.get_schedule_store", return_value=mock_store):
            handlers = ScheduleHandlers()

        request = AsyncMock()
        request.match_info = {"schedule_id": "sched_1"}

        with patch("api.schedules._get_scheduler_runner", return_value=mock_runner):
            resp = await handlers.run_now(request)

        self.assertEqual(resp.status, 503)
        body = json.loads(resp.body)
        self.assertEqual(body["code"], "scheduler_delegated")
        mock_runner._execute_schedule.assert_not_called()

    async def test_run_now_executes_when_embedded_mode(self):
        from api.schedules import ScheduleHandlers

        mock_schedule = MagicMock(template_id="tmpl_2")
        mock_store = MagicMock()
        mock_store.get.return_value = mock_schedule
        mock_runner = MagicMock()
        mock_runner.is_execution_delegated.return_value = False

        with patch("api.schedules.get_schedule_store", return_value=mock_store):
            handlers = ScheduleHandlers()

        request = AsyncMock()
        request.match_info = {"schedule_id": "sched_2"}

        with patch("api.schedules._get_scheduler_runner", return_value=mock_runner):
            resp = await handlers.run_now(request)

        self.assertEqual(resp.status, 200)
        body = json.loads(resp.body)
        self.assertTrue(body["triggered"])
        self.assertEqual(body["schedule_id"], "sched_2")
        mock_runner._execute_schedule.assert_called_once()


if __name__ == "__main__":
    unittest.main()
