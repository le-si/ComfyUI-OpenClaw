import json
import os
import sys
import unittest

# Ensure we can import the module from current directory
sys.path.append(os.getcwd())

from config import get_api_key
from models.schemas import GenerationParams, Profile


class TestMVP(unittest.TestCase):
    def test_schema_clamping(self):
        """Test that GenerationParams clamps values correctly using dataclasses."""
        # Test width/height clamping (rounding to 8)
        p = GenerationParams(width=1023, height=1025)
        # Dataclasses don't auto-validate via __init__ unless __post_init__ is called.
        # Standard dataclass behavior matches this.
        self.assertEqual(p.width, 1016)  # 1023 // 8 * 8 = 127 * 8 = 1016
        self.assertEqual(p.height, 1024)  # 1025 // 8 * 8 = 128 * 8 = 1024

        # Test default values
        p_default = GenerationParams()
        self.assertEqual(p_default.steps, 20)
        self.assertEqual(p_default.cfg, 7.0)

    def test_config_env(self):
        """Test config environment variable retrieval."""
        # Preferred key
        os.environ["MOLTBOT_LLM_API_KEY"] = "sk-test-primary"
        self.assertEqual(get_api_key(), "sk-test-primary")
        del os.environ["MOLTBOT_LLM_API_KEY"]

        # Legacy fallback
        os.environ["CLAWDBOT_LLM_API_KEY"] = "sk-test-legacy"
        self.assertEqual(get_api_key(), "sk-test-legacy")
        del os.environ["CLAWDBOT_LLM_API_KEY"]

    def test_from_dict(self):
        """Test from_dict factory method."""
        data = {"width": 515, "unknown_field": "ignore me"}  # Should clamp to 512
        p = GenerationParams.from_dict(data)
        self.assertEqual(p.width, 512)


if __name__ == "__main__":
    unittest.main()
