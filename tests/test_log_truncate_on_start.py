import logging
import os
import tempfile
import unittest
from unittest.mock import patch

import config


class TestLogTruncateOnStart(unittest.TestCase):
    def setUp(self):
        self._orig_log_file = config.LOG_FILE
        self._orig_data_dir = config.DATA_DIR
        self._orig_applied = getattr(config, "_LOG_TRUNCATE_APPLIED", False)
        self.tmp = tempfile.TemporaryDirectory()
        self.log_file = os.path.join(self.tmp.name, "openclaw.log")
        self._logger_names: set[str] = set()

    def tearDown(self):
        for name in list(self._logger_names):
            self._reset_logger(name)
        config.LOG_FILE = self._orig_log_file
        config.DATA_DIR = self._orig_data_dir
        config._LOG_TRUNCATE_APPLIED = self._orig_applied
        self.tmp.cleanup()

    def _reset_logger(self, name: str) -> None:
        logger = logging.getLogger(name)
        for h in list(logger.handlers):
            try:
                h.close()
            finally:
                logger.removeHandler(h)

    def _prepare_log_fixture(self, content: str) -> None:
        os.makedirs(self.tmp.name, exist_ok=True)
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write(content)
        config.LOG_FILE = self.log_file
        config.DATA_DIR = self.tmp.name
        config._LOG_TRUNCATE_APPLIED = False

    def test_no_truncate_when_flag_disabled(self):
        self._prepare_log_fixture("legacy-line\n")
        logger_name = "test.log_truncate.disabled"
        self._reset_logger(logger_name)
        self._logger_names.add(logger_name)

        with patch.dict(
            os.environ, {"OPENCLAW_LOG_TRUNCATE_ON_START": "0"}, clear=False
        ):
            config.setup_logger(logger_name)

        with open(self.log_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "legacy-line\n")

    def test_truncate_when_flag_enabled(self):
        self._prepare_log_fixture("legacy-line\n")
        logger_name = "test.log_truncate.enabled"
        self._reset_logger(logger_name)
        self._logger_names.add(logger_name)

        with patch.dict(
            os.environ, {"OPENCLAW_LOG_TRUNCATE_ON_START": "1"}, clear=False
        ):
            config.setup_logger(logger_name)

        with open(self.log_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("legacy-line", content)
        self.assertEqual(content, "")

    def test_truncate_applies_once_per_process(self):
        self._prepare_log_fixture("legacy-line\n")
        logger_name_a = "test.log_truncate.once.a"
        logger_name_b = "test.log_truncate.once.b"
        self._reset_logger(logger_name_a)
        self._reset_logger(logger_name_b)
        self._logger_names.update({logger_name_a, logger_name_b})

        with patch.dict(
            os.environ, {"OPENCLAW_LOG_TRUNCATE_ON_START": "1"}, clear=False
        ):
            logger_a = config.setup_logger(logger_name_a)
            logger_a.info("after-first-init")
            logger_b = config.setup_logger(logger_name_b)
            logger_b.info("after-second-init")

        with open(self.log_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("after-first-init", content)
        self.assertIn("after-second-init", content)
        self.assertNotIn("legacy-line", content)


if __name__ == "__main__":
    unittest.main()
