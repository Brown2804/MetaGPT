from pathlib import Path

from metagpt.auth.store import AuthStore


def test_import_codex_cli_auth(tmp_path: Path):
    auth_store_path = tmp_path / "auth" / "auth-profiles.json"
    codex_auth_path = tmp_path / "codex-auth.json"
    codex_auth_path.write_text(
        '{"tokens": {"access_token": "access-123", "refresh_token": "refresh-123", "account_id": "acct-1", "expires_at": "2026-03-22T12:00:00Z"}}',
        encoding="utf-8",
    )

    store = AuthStore(path=auth_store_path)
    profile_id = store.import_codex_cli_auth(profile_label="default", codex_auth_path=codex_auth_path)

    assert profile_id == "openai-codex:default"
    profile = store.get_profile(profile_id)
    assert profile is not None
    assert profile.access_token == "access-123"
    assert profile.refresh_token == "refresh-123"
    assert profile.account_id == "acct-1"
    assert profile.expires_at == "2026-03-22T12:00:00Z"
    assert store.path.exists()


def test_delete_profile(tmp_path: Path):
    auth_store_path = tmp_path / "auth" / "auth-profiles.json"
    codex_auth_path = tmp_path / "codex-auth.json"
    codex_auth_path.write_text('{"tokens": {"access_token": "access-123"}}', encoding="utf-8")

    store = AuthStore(path=auth_store_path)
    profile_id = store.import_codex_cli_auth(profile_label="default", codex_auth_path=codex_auth_path)
    assert store.delete_profile(profile_id) is True
    assert store.get_profile(profile_id) is None
