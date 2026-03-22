from pathlib import Path

from metagpt.auth.openai_codex import format_expiry_timestamp, is_expiring_soon, parse_expiry_timestamp
from metagpt.auth.store import AuthStore


def test_parse_expiry_timestamp_supports_epoch_millis_and_iso8601():
    millis_value = 1774180800000
    iso_value = "2026-03-21T12:00:00Z"

    assert int(parse_expiry_timestamp(millis_value)) == 1774180800
    assert int(parse_expiry_timestamp(iso_value)) == 1774094400
    assert format_expiry_timestamp(millis_value) == "2026-03-22T12:00:00Z"



def test_is_expiring_soon_detects_near_expiry():
    assert is_expiring_soon("1970-01-01T00:00:00Z", within_seconds=300) is True
    assert is_expiring_soon("2999-01-01T00:00:00Z", within_seconds=300) is False



def test_auth_store_ensure_valid_profile_refreshes_openai_codex_profile(tmp_path: Path, monkeypatch):
    auth_store_path = tmp_path / "auth" / "auth-profiles.json"
    openclaw_auth_path = tmp_path / "openclaw-auth.json"
    openclaw_auth_path.write_text(
        '{"profiles": {"openai-codex:default": {"provider": "openai-codex", "access": "access-old", "refresh": "refresh-old", "accountId": "acct-old", "expires": 0}}}',
        encoding="utf-8",
    )

    store = AuthStore(path=auth_store_path)
    profile_id = store.import_openclaw_codex_auth(
        profile_label="default",
        openclaw_auth_path=openclaw_auth_path,
        source_profile_id="openai-codex:default",
    )

    monkeypatch.setattr(
        "metagpt.auth.store.refresh_openai_codex_tokens",
        lambda refresh_token: {
            "access_token": "access-new",
            "refresh_token": "refresh-new",
            "expires_at": "2030-01-01T00:00:00Z",
            "account_id": "acct-new",
            "id_token": "id-new",
        },
    )

    profile = store.ensure_valid_profile(profile_id)

    assert profile is not None
    assert profile.access_token == "access-new"
    assert profile.refresh_token == "refresh-new"
    assert profile.account_id == "acct-new"
    assert profile.expires_at == "2030-01-01T00:00:00Z"
    assert profile.id_token == "id-new"
    assert profile.meta["last_refresh"].endswith("Z")
