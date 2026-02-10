import logging
import os
from unittest.mock import MagicMock, patch

import pytest


# Contract: Config Precedence
def test_config_precedence():
    """
    Contract: OPENCLAW_* env vars > MOLTBOT_* env vars > file config > defaults.
    """
    # Mocking json load to avoid file I/O dependencies
    mock_json_load = MagicMock(return_value={})

    with (
        patch("builtins.open", new_callable=MagicMock),
        patch("json.load", mock_json_load),
        patch("os.path.exists", return_value=True),
    ):

        from services.runtime_config import get_effective_config

        with patch.dict(
            os.environ,
            {"OPENCLAW_LLM_PROVIDER": "openai", "MOLTBOT_LLM_PROVIDER": "anthropic"},
        ):
            config, sources = get_effective_config()
            assert config["provider"] == "openai"
            # sources dict stores the raw "env" as source type, checking specific env var correctness
            # might depend on implementation details of sources dict population.
            # services/runtime_config.py sets sources[key] = "env".
            assert sources["provider"] == "env"


# Contract: Secret Safety
def test_secrets_never_exposed():
    """
    Contract: get_effective_config() MUST NOT return api_key in plain text.
    Contract: __str__ or __repr__ of config objects MUST NOT leak secrets.
    """
    # Mock json load
    mock_json_load = MagicMock(return_value={})

    with (
        patch("builtins.open", new_callable=MagicMock),
        patch("json.load", mock_json_load),
        patch("os.path.exists", return_value=True),
    ):

        from services.runtime_config import get_effective_config

        DUMMY_KEY = "sk-danger-12345"

        with patch.dict(os.environ, {"OPENCLAW_API_KEY": DUMMY_KEY}):
            config, _ = get_effective_config()

            # 1. Verify key is NOT in the returned config dict (runtime_config filters it)
            assert "api_key" not in config

            # 2. Verify key is NOT in string representation of the config dict
            config_str = str(config)
            assert DUMMY_KEY not in config_str

            # 3. Verify key is NOT in repr
            config_repr = repr(config)
            assert DUMMY_KEY not in config_repr
