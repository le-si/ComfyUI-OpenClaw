
import unittest
import asyncio
from unittest.mock import patch, MagicMock
from api.config import config_put_handler

class TestR53ApplyFeedbackRepro(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    @patch("api.config.update_config")
    @patch("api.config.get_effective_config")
    @patch("api.config.require_admin_token")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_same_origin_if_no_token")
    def test_put_returns_apply_metadata(self, mock_csrf, mock_rate, mock_auth, mock_get_config, mock_update):
        # Setup mocks
        mock_csrf.return_value = None
        mock_rate.return_value = True
        mock_auth.return_value = (True, None)
        
        # update_config succeeds
        mock_update.return_value = (True, [])
        
        # get_effective_config returns the new state
        mock_get_config.return_value = ({"provider": "openai"}, {"provider": "file"})

        # Mock Request
        request = MagicMock()
        request.json = MagicMock(return_value=asyncio.Future())
        request.json.return_value.set_result({"llm": {"provider": "openai"}})
        request.remote = "127.0.0.1"

        # Execute
        response = self.loop.run_until_complete(config_put_handler(request))
        body = json.loads(response.body)

        # Assertion: R53 requires an 'apply' field in the PUT /config response.
        self.assertIn("apply", body, "R53 Failure: Response missing 'apply' metadata")

import json
if __name__ == "__main__":
    unittest.main()
