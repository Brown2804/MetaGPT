from pathlib import Path

from metagpt.actions.action import Action
from metagpt.auth.store import AuthStore
from metagpt.configs.models_config import ModelsConfig


def test_action_uses_named_oauth_profile_model(context, tmp_path: Path):
    auth_store_path = tmp_path / "auth" / "auth-profiles.json"
    codex_auth_path = tmp_path / "codex-auth.json"
    codex_auth_path.write_text('{"tokens": {"access_token": "oauth-access"}}', encoding="utf-8")

    store = AuthStore(path=auth_store_path)
    profile_id = store.import_codex_cli_auth(profile_label="default", codex_auth_path=codex_auth_path)

    original_auth_default = AuthStore.default
    original_models_default = ModelsConfig.default
    AuthStore.default = classmethod(lambda cls: cls(path=auth_store_path))
    ModelsConfig.default = classmethod(
        lambda cls: cls(
            models={
                "codex-work": {
                    "api_type": "openai_codex",
                    "auth_mode": "oauth_profile",
                    "auth_profile": profile_id,
                    "api_key": "",
                    "base_url": "http://127.0.0.1:8787/v1",
                    "model": "gpt-5.4",
                }
            }
        )
    )
    try:
        action = Action(context=context, llm_name_or_type="codex-work")
        kwargs = action.llm._make_client_kwargs()
    finally:
        AuthStore.default = original_auth_default
        ModelsConfig.default = original_models_default

    assert action.llm.model == "gpt-5.4"
    assert action.llm.config.auth_mode == "oauth_profile"
    assert kwargs["api_key"] == "oauth-access"
    assert kwargs["base_url"] == "http://127.0.0.1:8787/v1"
