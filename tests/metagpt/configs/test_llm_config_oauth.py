import pytest

from metagpt.configs.llm_config import LLMConfig


def test_llm_config_allows_oauth_profile_without_real_api_key():
    cfg = LLMConfig(
        api_type="openai_codex",
        auth_mode="oauth_profile",
        auth_profile="openai-codex:default",
        api_key="",
        base_url="http://127.0.0.1:8787/v1",
        model="gpt-5.4",
    )

    assert cfg.auth_mode == "oauth_profile"
    assert cfg.auth_profile == "openai-codex:default"
    assert cfg.api_type.value == "openai_codex"


def test_llm_config_still_requires_api_key_for_api_key_mode():
    with pytest.raises(ValueError):
        LLMConfig(api_type="openai", api_key="", model="gpt-4o")
