from pathlib import Path

from metagpt.auth.resolver import resolve_openai_credentials
from metagpt.auth.store import AuthStore
from metagpt.configs.llm_config import LLMConfig


def test_resolve_openai_credentials_uses_static_config():
    config = LLMConfig(api_type="openai", api_key="static-key", base_url="http://localhost:9999/v1", model="gpt-4o")

    credentials = resolve_openai_credentials(config)

    assert credentials.api_key == "static-key"
    assert credentials.base_url == "http://localhost:9999/v1"
    assert credentials.auth_profile is None


def test_resolve_openai_credentials_uses_oauth_profile(tmp_path: Path):
    auth_store_path = tmp_path / "auth" / "auth-profiles.json"
    codex_auth_path = tmp_path / "codex-auth.json"
    codex_auth_path.write_text(
        '{"tokens": {"access_token": "oauth-access", "account_id": "acct-1"}}', encoding="utf-8"
    )

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

    # point the default store at our temp file for this test only
    original_default = AuthStore.default
    AuthStore.default = classmethod(lambda cls: cls(path=auth_store_path))
    try:
        credentials = resolve_openai_credentials(config)
    finally:
        AuthStore.default = original_default

    assert credentials.api_key == "oauth-access"
    assert credentials.base_url == "http://127.0.0.1:8787/v1"
    assert credentials.auth_profile == profile_id
