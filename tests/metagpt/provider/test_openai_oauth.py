from pathlib import Path

from metagpt.auth.store import AuthStore
from metagpt.configs.llm_config import LLMConfig
from metagpt.provider import OpenAILLM


def test_make_client_kwargs_with_oauth_profile(tmp_path: Path):
    auth_store_path = tmp_path / "auth" / "auth-profiles.json"
    codex_auth_path = tmp_path / "codex-auth.json"
    codex_auth_path.write_text('{"tokens": {"access_token": "oauth-access"}}', encoding="utf-8")

    store = AuthStore(path=auth_store_path)
    profile_id = store.import_codex_cli_auth(profile_label="default", codex_auth_path=codex_auth_path)

    config = LLMConfig(
        api_type="openai_codex",
        auth_mode="oauth_profile",
        auth_profile=profile_id,
        api_key="",
        base_url="http://127.0.0.1:8787/v1",
        model="gpt-5.4",
    )

    original_default = AuthStore.default
    AuthStore.default = classmethod(lambda cls: cls(path=auth_store_path))
    try:
        instance = OpenAILLM(config)
        kwargs = instance._make_client_kwargs()
    finally:
        AuthStore.default = original_default

    assert kwargs["api_key"] == "oauth-access"
    assert kwargs["base_url"] == "http://127.0.0.1:8787/v1"
