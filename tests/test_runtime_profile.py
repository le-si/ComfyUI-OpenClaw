"""
Unit tests for R83 Runtime Profile Contract.
"""

import os
import unittest
from unittest.mock import patch

from services.runtime_profile import (
    ProfileResolver,
    RuntimeProfile,
    get_runtime_profile,
    is_hardened_mode,
)


class TestRuntimeProfile(unittest.TestCase):

    def setUp(self):
        # Clear env var before each test to ensure isolation
        if "OPENCLAW_RUNTIME_PROFILE" in os.environ:
            del os.environ["OPENCLAW_RUNTIME_PROFILE"]

    def test_default_is_minimal(self):
        """Test that default profile is MINIMAL when env var is unset."""
        profile = ProfileResolver.resolve()
        self.assertEqual(profile, RuntimeProfile.MINIMAL)
        self.assertFalse(ProfileResolver.is_hardened())
        self.assertEqual(get_runtime_profile(), RuntimeProfile.MINIMAL)
        self.assertFalse(is_hardened_mode())

    def test_explicit_minimal(self):
        """Test that explicitly setting 'minimal' works."""
        with patch.dict(os.environ, {"OPENCLAW_RUNTIME_PROFILE": "minimal"}):
            self.assertEqual(ProfileResolver.resolve(), RuntimeProfile.MINIMAL)
            self.assertFalse(ProfileResolver.is_hardened())

    def test_hardened_mode(self):
        """Test that setting 'hardened' activates HARDENED profile."""
        with patch.dict(os.environ, {"OPENCLAW_RUNTIME_PROFILE": "hardened"}):
            self.assertEqual(ProfileResolver.resolve(), RuntimeProfile.HARDENED)
            self.assertTrue(ProfileResolver.is_hardened())
            self.assertEqual(get_runtime_profile(), RuntimeProfile.HARDENED)
            self.assertTrue(is_hardened_mode())

    def test_case_insensitivity(self):
        """Test that env var is case-insensitive."""
        with patch.dict(os.environ, {"OPENCLAW_RUNTIME_PROFILE": "HARDENED"}):
            self.assertEqual(ProfileResolver.resolve(), RuntimeProfile.HARDENED)

        with patch.dict(os.environ, {"OPENCLAW_RUNTIME_PROFILE": "Minimal"}):
            self.assertEqual(ProfileResolver.resolve(), RuntimeProfile.MINIMAL)

    def test_invalid_fallback(self):
        """Test that invalid values fall back to MINIMAL."""
        with patch.dict(os.environ, {"OPENCLAW_RUNTIME_PROFILE": "ultra-secure"}):
            self.assertEqual(ProfileResolver.resolve(), RuntimeProfile.MINIMAL)
            self.assertFalse(ProfileResolver.is_hardened())


if __name__ == "__main__":
    unittest.main()
