import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from metagpt.auth.store import AuthStore
from metagpt.configs.llm_config import LLMConfig
from metagpt.provider.openai_api import OpenAILLM


def test_oauth_profile_completes_against_local_openai_compatible_server(tmp_path: Path, monkeypatch):
    auth_store_path = tmp_path / "auth" / "auth-profiles.json"
    codex_auth_path = tmp_path / "codex-auth.json"
    codex_auth_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "oauth-access-e2e",
                    "refresh_token": "refresh-e2e",
                    "account_id": "acct-e2e",
                    "expires_at": "2026-12-31T23:59:59Z",
                }
            }
        ),
        encoding="utf-8",
    )

    store = AuthStore(path=auth_store_path)
    profile_id = store.import_codex_cli_auth(profile_label="default", codex_auth_path=codex_auth_path)
    monkeypatch.setattr(AuthStore, "default", classmethod(lambda cls: cls(path=auth_store_path)))

    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            captured["path"] = self.path
            captured["authorization"] = self.headers.get("Authorization")
            captured["content_type"] = self.headers.get("Content-Type")
            captured["body"] = json.loads(raw) if raw else None

            payload = {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "created": 1711111111,
                "model": "gpt-5.4",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "mock-bridge-ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            }
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, fmt, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        config = LLMConfig(
            api_type="openai_codex",
            auth_mode="oauth_profile",
            auth_profile=profile_id,
            api_key="",
            base_url=f"http://127.0.0.1:{port}/v1",
            model="gpt-5.4",
        )

        llm = OpenAILLM(config)
        text = asyncio.run(
            llm.acompletion_text(
                [{"role": "user", "content": "Say hello from MetaGPT auth smoke test."}],
                stream=False,
            )
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert text == "mock-bridge-ok"
    assert captured["path"] == "/v1/chat/completions"
    assert captured["authorization"] == "Bearer oauth-access-e2e"
    assert captured["content_type"] == "application/json"
    assert captured["body"]["model"] == "gpt-5.4"
    assert captured["body"]["messages"][0]["content"] == "Say hello from MetaGPT auth smoke test."
