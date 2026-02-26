import io
import json
import logging
import os
import unittest
from unittest.mock import patch


class TestR65StructuredLogging(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test.r65.structured")
        self.logger.handlers = []
        self.logger.propagate = False
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def tearDown(self):
        self.logger.handlers = []

    def test_json_formatter_emits_event_and_fields(self):
        from services.structured_logging import OpenClawJsonFormatter, emit_structured_log

        self.logger.handlers[0].setFormatter(OpenClawJsonFormatter())
        with patch.dict(os.environ, {"OPENCLAW_LOG_FORMAT": "json"}, clear=False):
            emit_structured_log(
                self.logger,
                level=logging.INFO,
                event="queue.submit.success",
                fields={"trace_id": "t1", "prompt_id": "p1"},
            )
        line = self.stream.getvalue().strip()
        payload = json.loads(line)
        self.assertEqual(payload["event"], "queue.submit.success")
        self.assertEqual(payload["fields"]["trace_id"], "t1")
        self.assertIn("logger", payload)

    def test_configure_logger_for_structured_output_is_opt_in(self):
        from services import structured_logging as sl

        sl.reset_structured_logging_state_for_tests()
        self.assertFalse(sl.configure_logger_for_structured_output(self.logger))
        with patch.dict(os.environ, {"OPENCLAW_LOG_FORMAT": "json"}, clear=False):
            applied = sl.configure_logger_for_structured_output(self.logger)
            self.assertTrue(applied)
            self.assertFalse(sl.configure_logger_for_structured_output(self.logger))
        sl.reset_structured_logging_state_for_tests()

    def test_sanitize_fields_truncates_long_values(self):
        from services.structured_logging import emit_structured_log, OpenClawJsonFormatter

        self.logger.handlers[0].setFormatter(OpenClawJsonFormatter())
        with patch.dict(os.environ, {"OPENCLAW_STRUCTURED_LOGS": "1"}, clear=False):
            emit_structured_log(
                self.logger,
                level=logging.INFO,
                event="llm.request.failure",
                fields={"msg": "x" * 500},
            )
        payload = json.loads(self.stream.getvalue().strip())
        self.assertIn("[truncated]", payload["fields"]["msg"])


if __name__ == "__main__":
    unittest.main()
