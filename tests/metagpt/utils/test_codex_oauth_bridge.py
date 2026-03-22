import base64
import json
from pathlib import Path

from metagpt.utils.codex_oauth_bridge import (
    BridgeSettings,
    derive_chatgpt_account_id,
    extract_text_content,
    load_openclaw_profile,
    normalize_json_arguments,
    normalize_output_schema,
    render_messages_as_prompt,
)


def _jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig"



def test_extract_text_content_supports_openai_content_lists():
    content = [
        {"type": "text", "text": "hello"},
        {"type": "input_text", "text": " world"},
        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
    ]

    assert extract_text_content(content) == "hello world"



def test_render_messages_as_prompt_includes_roles():
    prompt = render_messages_as_prompt(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Say hi."},
        ]
    )

    assert "[system]" in prompt
    assert "You are helpful." in prompt
    assert "[user]" in prompt
    assert "Say hi." in prompt



def test_derive_chatgpt_account_id_reads_jwt_claim():
    token = _jwt(
        {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct-123",
                "chatgpt_plan_type": "pro",
            }
        }
    )

    assert derive_chatgpt_account_id(token) == "acct-123"



def test_normalize_json_arguments_strips_markdown_fences():
    text = '```json\n{"language":"python","code":"print(1)"}\n```'

    normalized = normalize_json_arguments(text)

    assert json.loads(normalized) == {"language": "python", "code": "print(1)"}



def test_normalize_output_schema_adds_additional_properties_false():
    schema = {
        "type": "object",
        "properties": {
            "language": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
            },
        },
    }

    normalized = normalize_output_schema(schema)

    assert normalized["additionalProperties"] is False
    assert normalized["properties"]["nested"]["additionalProperties"] is False



def test_load_openclaw_profile_refreshes_expiring_tokens(tmp_path: Path, monkeypatch):
    access_token = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-old", "chatgpt_plan_type": "pro"}})
    refreshed_access_token = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-new", "chatgpt_plan_type": "pro"}})
    auth_path = tmp_path / "auth-profiles.json"
    auth_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "openai-codex:default": {
                        "access": access_token,
                        "refresh": "refresh-old",
                        "accountId": "acct-old",
                        "expires": 0,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "metagpt.utils.codex_oauth_bridge.refresh_openai_codex_tokens",
        lambda refresh_token: {
            "access_token": refreshed_access_token,
            "refresh_token": "refresh-new",
            "expires_at_ms": 1893456000000,
            "account_id": "acct-new",
            "id_token": "id-new",
        },
    )

    settings = BridgeSettings(openclaw_auth_path=str(auth_path), openclaw_profile="openai-codex:default")
    access, account_id, plan_type = load_openclaw_profile(settings)
    updated = json.loads(auth_path.read_text(encoding="utf-8"))

    assert access == refreshed_access_token
    assert account_id == "acct-old"
    assert plan_type == "pro"
    assert updated["profiles"]["openai-codex:default"]["refresh"] == "refresh-new"
    assert updated["profiles"]["openai-codex:default"]["expires"] == 1893456000000
    assert updated["profiles"]["openai-codex:default"]["idToken"] == "id-new"
