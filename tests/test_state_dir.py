"""
Tests for State Directory Service (R11).
Uses MOLTBOT_STATE_DIR override to avoid side effects on the runner.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestStateDir(unittest.TestCase):
    """Test state directory resolution logic."""

    @classmethod
    def setUpClass(cls):
        """Use temp dir for all tests to avoid side effects."""
        cls.temp_dir = tempfile.mkdtemp(prefix="moltbot_test_")
        os.environ["MOLTBOT_STATE_DIR"] = cls.temp_dir
        # Reset cached state
        import services.state_dir as sd

        sd.STATE_DIR = None

    @classmethod
    def tearDownClass(cls):
        """Cleanup temp dir."""
        import shutil

        if os.path.exists(cls.temp_dir):
            shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.pop("MOLTBOT_STATE_DIR", None)

    def test_env_override(self):
        """MOLTBOT_STATE_DIR should override default."""
        from services.state_dir import STATE_DIR_ENV, get_state_dir

        test_dir = "/tmp/test-moltbot-state-override"
        with patch.dict(os.environ, {STATE_DIR_ENV: test_dir}):
            # Reset cache
            import services.state_dir as sd

            sd.STATE_DIR = None
            result = get_state_dir()
            self.assertEqual(result, os.path.abspath(test_dir))

    def test_windows_default(self):
        """Windows should use LOCALAPPDATA."""
        from services.state_dir import STATE_DIR_NAME, _get_user_data_dir

        with patch("sys.platform", "win32"):
            with patch.dict(
                os.environ, {"LOCALAPPDATA": "C:\\Users\\Test\\AppData\\Local"}
            ):
                result = _get_user_data_dir()
                self.assertIn(STATE_DIR_NAME, result)
                self.assertIn("AppData", result)

    def test_linux_default(self):
        """Linux should use XDG_DATA_HOME or ~/.local/share."""
        from services.state_dir import STATE_DIR_NAME, _get_user_data_dir

        with patch("sys.platform", "linux"):
            with patch.dict(
                os.environ, {"XDG_DATA_HOME": "/home/test/.local/share"}, clear=False
            ):
                result = _get_user_data_dir()
                self.assertIn(STATE_DIR_NAME, result)

    def test_macos_default(self):
        """macOS should use ~/Library/Application Support."""
        from services.state_dir import STATE_DIR_NAME, _get_user_data_dir

        with patch("sys.platform", "darwin"):
            result = _get_user_data_dir()
            self.assertIn(STATE_DIR_NAME, result)
            self.assertIn("Application Support", result)

    def test_get_log_path(self):
        """Log path should be under state dir."""
        from services.state_dir import get_log_path, get_state_dir

        log_path = get_log_path()
        state_dir = get_state_dir()
        self.assertTrue(log_path.startswith(state_dir))
        self.assertTrue(log_path.endswith(".log"))

    def test_get_cache_dir(self):
        """Cache dir should be under state dir."""
        from services.state_dir import get_cache_dir, get_state_dir

        cache_dir = get_cache_dir()
        state_dir = get_state_dir()
        self.assertTrue(cache_dir.startswith(state_dir))
        self.assertIn("cache", cache_dir)


if __name__ == "__main__":
    unittest.main()
