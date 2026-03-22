import json
from pathlib import Path

from metagpt.auth.store import AuthStore


def test_import_openclaw_codex_auth(tmp_path: Path):
    auth_store_path = tmp_path / "auth" / "auth-profiles.json"
    openclaw_auth_path = tmp_path / "openclaw-auth-profiles.json"
    openclaw_auth_path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "openai-codex:default": {
                        "type": "oauth",
                        "provider": "openai-codex",
                        "access": "access-123",
                        "refresh": "refresh-123",
                        "expires": 1774366484411,
                        "accountId": "acct-1",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = AuthStore(path=auth_store_path)
    profile_id = store.import_openclaw_codex_auth(
        profile_label="default",
        openclaw_auth_path=openclaw_auth_path,
        source_profile_id="openai-codex:default",
    )

    assert profile_id == "openai-codex:default"
    profile = store.get_profile(profile_id)
    assert profile is not None
    assert profile.access_token == "access-123"
    assert profile.refresh_token == "refresh-123"
    assert profile.account_id == "acct-1"
    assert profile.expires_at == "1774366484411"
    assert profile.meta["source"] == "openclaw-auth-profiles"
