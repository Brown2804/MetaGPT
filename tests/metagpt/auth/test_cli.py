from pathlib import Path

from typer.testing import CliRunner

from metagpt.auth.cli import app
from metagpt.auth.store import AuthStore

runner = CliRunner()


def test_auth_cli_import_codex_and_status(tmp_path: Path):
    auth_store_path = tmp_path / "auth" / "auth-profiles.json"
    codex_auth_path = tmp_path / "codex-auth.json"
    codex_auth_path.write_text(
        '{"tokens": {"access_token": "oauth-access", "account_id": "acct-1", "expires_at": "2026-03-22T12:00:00Z"}}',
        encoding="utf-8",
    )

    original_default = AuthStore.default
    AuthStore.default = classmethod(lambda cls: cls(path=auth_store_path))
    try:
        result = runner.invoke(app, ["import-codex", "smoke", str(codex_auth_path)])
        assert result.exit_code == 0, result.output
        assert "openai-codex:smoke" in result.output

        status_result = runner.invoke(app, ["status"])
        assert status_result.exit_code == 0, status_result.output
        assert "Auth store:" in status_result.output
        assert "openai-codex:smoke" in status_result.output
        assert "acct-1" in status_result.output
    finally:
        AuthStore.default = original_default


def test_auth_cli_logout(tmp_path: Path):
    auth_store_path = tmp_path / "auth" / "auth-profiles.json"
    codex_auth_path = tmp_path / "codex-auth.json"
    codex_auth_path.write_text('{"tokens": {"access_token": "oauth-access"}}', encoding="utf-8")

    store = AuthStore(path=auth_store_path)
    profile_id = store.import_codex_cli_auth(profile_label="smoke", codex_auth_path=codex_auth_path)

    original_default = AuthStore.default
    AuthStore.default = classmethod(lambda cls: cls(path=auth_store_path))
    try:
        result = runner.invoke(app, ["logout", profile_id])
        assert result.exit_code == 0, result.output
        assert f"Removed profile: {profile_id}" in result.output
        assert store.get_profile(profile_id) is None
    finally:
        AuthStore.default = original_default
