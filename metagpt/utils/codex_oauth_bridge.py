#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from metagpt.auth.openai_codex import (
    derive_account_id,
    is_expiring_soon,
    refresh_openai_codex_tokens,
)


def extract_text_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"text", "input_text"}:
                text = item.get("text") or item.get("value") or ""
                if text:
                    chunks.append(str(text))
        return "".join(chunks)
    return str(content)


def render_messages_as_prompt(messages: list[dict[str, Any]], tool_name: str | None = None) -> str:
    lines: list[str] = []
    if tool_name:
        lines.extend(
            [
                "You are operating behind an OpenAI tool-call compatibility bridge.",
                f"Return ONLY a JSON object containing arguments for the tool '{tool_name}'.",
                "Do not include markdown fences, prose, or any extra text.",
                "Use the conversation below to infer the correct arguments.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "You are answering through an OpenAI-compatible chat-completions bridge.",
                "Use the conversation below and answer the latest user request directly.",
                "",
            ]
        )

    for msg in messages:
        role = msg.get("role", "user")
        text = extract_text_content(msg.get("content"))
        if not text:
            continue
        lines.append(f"[{role}]\n{text}\n")
    return "\n".join(lines).strip()


def _pad_b64url(value: str) -> str:
    return value + "=" * ((4 - len(value) % 4) % 4)


def decode_jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("Token is not a JWT")
    payload = base64.urlsafe_b64decode(_pad_b64url(parts[1]).encode("utf-8"))
    return json.loads(payload.decode("utf-8"))


def derive_chatgpt_account_id(access_token: str) -> str:
    claims = decode_jwt_claims(access_token)
    auth = claims.get("https://api.openai.com/auth") or {}
    account_id = auth.get("chatgpt_account_id")
    if not account_id:
        raise ValueError("Access token does not include https://api.openai.com/auth.chatgpt_account_id")
    return str(account_id)


def derive_chatgpt_plan_type(access_token: str) -> str | None:
    claims = decode_jwt_claims(access_token)
    auth = claims.get("https://api.openai.com/auth") or {}
    plan_type = auth.get("chatgpt_plan_type")
    return str(plan_type) if plan_type else None


def normalize_json_arguments(text: str) -> str:
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()

    try:
        parsed = json.loads(candidate)
        return json.dumps(parsed, ensure_ascii=False)
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = candidate[start : end + 1]
        parsed = json.loads(snippet)
        return json.dumps(parsed, ensure_ascii=False)

    raise ValueError(f"Model output is not valid JSON arguments: {text!r}")



def normalize_output_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [normalize_output_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    normalized = {key: normalize_output_schema(value) for key, value in schema.items()}
    if normalized.get("type") == "object":
        normalized.setdefault("additionalProperties", False)
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["properties"] = {key: normalize_output_schema(value) for key, value in properties.items()}
    if normalized.get("type") == "array" and "items" in normalized:
        normalized["items"] = normalize_output_schema(normalized["items"])
    return normalized


@dataclass
class UsageSummary:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class BridgeSettings:
    host: str = "127.0.0.1"
    port: int = 8787
    codex_command: str = "codex"
    cwd: str = str(Path.cwd())
    model: str = "gpt-5.4"
    sandbox: str = "read-only"
    approval_policy: str = "never"
    personality: str = "pragmatic"
    openclaw_auth_path: str = str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json")
    openclaw_profile: str = "openai-codex:default"


class CodexAppServerClient:
    def __init__(self, settings: BridgeSettings):
        self.settings = settings
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self.notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            self.settings.codex_command,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stdout_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        await self.rpc(
            "initialize",
            {
                "clientInfo": {"name": "metagpt-codex-bridge", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.notify("initialized", {})

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        for task in (self._stdout_task, self._stderr_task):
            if task and not task.done():
                task.cancel()

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue
            if "id" in message:
                future = self._pending.pop(message["id"], None)
                if future and not future.done():
                    future.set_result(message)
            elif message.get("method"):
                await self.notifications.put(message)

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break

    async def _write(self, message: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def rpc(self, method: str, params: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        response = await asyncio.wait_for(future, timeout=timeout)
        if "error" in response:
            raise RuntimeError(f"{method} failed: {response['error']}")
        return response["result"]

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def login_with_tokens(self, access_token: str, account_id: str, plan_type: str | None = None) -> None:
        params: dict[str, Any] = {
            "type": "chatgptAuthTokens",
            "accessToken": access_token,
            "chatgptAccountId": account_id,
        }
        if plan_type:
            params["chatgptPlanType"] = plan_type
        await self.rpc("account/login/start", params)

    async def start_thread(self, model: str) -> tuple[str, dict[str, Any]]:
        result = await self.rpc(
            "thread/start",
            {
                "cwd": self.settings.cwd,
                "approvalPolicy": self.settings.approval_policy,
                "sandbox": self.settings.sandbox,
                "model": model,
                "personality": self.settings.personality,
            },
        )
        return result["thread"]["id"], result

    async def start_turn(
        self,
        thread_id: str,
        prompt: str,
        model: str,
        output_schema: dict[str, Any] | None = None,
    ) -> str:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "model": model,
        }
        if output_schema is not None:
            params["outputSchema"] = output_schema
        result = await self.rpc("turn/start", params)
        return result["turn"]["id"]


def _atomic_write_json(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="openclaw-auth-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)



def load_openclaw_profile(settings: BridgeSettings) -> tuple[str, str, str | None]:
    auth_path = Path(settings.openclaw_auth_path).expanduser()
    data = json.loads(auth_path.read_text(encoding="utf-8"))
    profile = data["profiles"][settings.openclaw_profile]

    refresh_token = profile.get("refresh")
    expires_at = profile.get("expires")
    if refresh_token and is_expiring_soon(expires_at, within_seconds=300):
        refreshed = refresh_openai_codex_tokens(refresh_token)
        profile["access"] = refreshed["access_token"]
        profile["refresh"] = refreshed["refresh_token"]
        profile["expires"] = refreshed["expires_at_ms"]
        profile["accountId"] = profile.get("accountId") or refreshed.get("account_id") or derive_account_id(refreshed["access_token"])
        if refreshed.get("id_token"):
            profile["idToken"] = refreshed["id_token"]
        data["profiles"][settings.openclaw_profile] = profile
        _atomic_write_json(auth_path, data)

    access_token = profile["access"]
    account_id = profile.get("accountId") or derive_chatgpt_account_id(access_token)
    plan_type = derive_chatgpt_plan_type(access_token)
    return access_token, account_id, plan_type


def resolve_request_auth(request: web.Request, settings: BridgeSettings) -> tuple[str, str, str | None]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        access_token = auth_header.split(" ", 1)[1].strip()
        if access_token.count(".") >= 2:
            try:
                account_id = derive_chatgpt_account_id(access_token)
                plan_type = derive_chatgpt_plan_type(access_token)
                return access_token, account_id, plan_type
            except Exception:
                pass
    return load_openclaw_profile(settings)


def format_usage(token_usage: UsageSummary | None) -> dict[str, int] | None:
    if token_usage is None:
        return None
    return {
        "prompt_tokens": token_usage.prompt_tokens,
        "completion_tokens": token_usage.completion_tokens,
        "total_tokens": token_usage.total_tokens,
    }


def build_chat_completion_response(model: str, text: str, usage: UsageSummary | None) -> dict[str, Any]:
    response: dict[str, Any] = {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }
    if usage:
        response["usage"] = format_usage(usage)
    return response


def build_tool_call_response(
    model: str,
    tool_name: str,
    arguments_json: str,
    usage: UsageSummary | None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": tool_name, "arguments": arguments_json},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    if usage:
        response["usage"] = format_usage(usage)
    return response


def build_stream_chunk(model: str, delta: str | None = None, finish_reason: str | None = None, include_role: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": ({"role": "assistant"} if include_role else {}),
                "finish_reason": finish_reason,
            }
        ],
    }
    if delta is not None:
        payload["choices"][0]["delta"]["content"] = delta
    return payload


async def execute_codex_turn(
    settings: BridgeSettings,
    access_token: str,
    account_id: str,
    plan_type: str | None,
    prompt: str,
    model: str,
    output_schema: dict[str, Any] | None = None,
):
    client = CodexAppServerClient(settings)
    final_text = ""
    usage: UsageSummary | None = None
    try:
        await client.start()
        await client.login_with_tokens(access_token=access_token, account_id=account_id, plan_type=plan_type)
        thread_id, _ = await client.start_thread(model=model)
        turn_id = await client.start_turn(thread_id=thread_id, prompt=prompt, model=model, output_schema=output_schema)
        while True:
            event = await asyncio.wait_for(client.notifications.get(), timeout=180)
            method = event.get("method")
            params = event.get("params", {})
            if method == "item/agentMessage/delta" and params.get("threadId") == thread_id and params.get("turnId") == turn_id:
                final_text += params.get("delta", "")
                yield {"type": "delta", "delta": params.get("delta", "")}
            elif method == "item/completed" and params.get("threadId") == thread_id and params.get("turnId") == turn_id:
                item = params.get("item") or {}
                if item.get("type") == "agentMessage":
                    final_text = item.get("text") or final_text
            elif method == "codex/event/task_complete" and params.get("conversationId") == thread_id:
                msg = params.get("msg") or {}
                final_text = msg.get("last_agent_message") or final_text
            elif method == "thread/tokenUsage/updated" and params.get("threadId") == thread_id and params.get("turnId") == turn_id:
                token_usage = params.get("tokenUsage") or {}
                total = token_usage.get("total") or {}
                usage = UsageSummary(
                    prompt_tokens=int(total.get("inputTokens") or 0),
                    completion_tokens=int(total.get("outputTokens") or 0),
                    total_tokens=int(total.get("totalTokens") or 0),
                )
            elif method == "turn/completed" and params.get("threadId") == thread_id:
                turn = params.get("turn") or {}
                if turn.get("id") == turn_id:
                    status = turn.get("status")
                    if status != "completed":
                        raise RuntimeError(f"Codex turn failed: {turn}")
                    break
        yield {"type": "completed", "text": final_text, "usage": usage}
    finally:
        await client.close()


async def handle_health(request: web.Request) -> web.Response:
    settings: BridgeSettings = request.app["settings"]
    return web.json_response(
        {
            "ok": True,
            "bridge": "codex-oauth-chat-completions",
            "host": settings.host,
            "port": settings.port,
            "cwd": settings.cwd,
            "model": settings.model,
            "openclaw_auth_path": settings.openclaw_auth_path,
            "openclaw_profile": settings.openclaw_profile,
        }
    )


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    settings: BridgeSettings = request.app["settings"]
    body = await request.json()
    model = body.get("model") or settings.model
    messages = body.get("messages") or []
    tools = body.get("tools") or []
    stream = bool(body.get("stream")) and not tools

    access_token, account_id, plan_type = resolve_request_auth(request, settings)

    tool_name = None
    output_schema = None
    if tools:
        function = (tools[0] or {}).get("function") or {}
        tool_name = function.get("name") or "execute"
        output_schema = normalize_output_schema(function.get("parameters") or {"type": "object"})

    prompt = render_messages_as_prompt(messages, tool_name=tool_name)

    if stream:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        role_sent = False
        async for event in execute_codex_turn(
            settings=settings,
            access_token=access_token,
            account_id=account_id,
            plan_type=plan_type,
            prompt=prompt,
            model=model,
        ):
            if event["type"] == "delta":
                payload = build_stream_chunk(
                    model=model,
                    delta=event["delta"],
                    finish_reason=None,
                    include_role=not role_sent,
                )
                role_sent = True
                await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
            elif event["type"] == "completed":
                payload = build_stream_chunk(model=model, finish_reason="stop")
                await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
                await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    final_text = ""
    usage: UsageSummary | None = None
    async for event in execute_codex_turn(
        settings=settings,
        access_token=access_token,
        account_id=account_id,
        plan_type=plan_type,
        prompt=prompt,
        model=model,
        output_schema=output_schema,
    ):
        if event["type"] == "completed":
            final_text = event["text"]
            usage = event["usage"]

    if tool_name:
        arguments_json = normalize_json_arguments(final_text)
        return web.json_response(build_tool_call_response(model=model, tool_name=tool_name, arguments_json=arguments_json, usage=usage))

    return web.json_response(build_chat_completion_response(model=model, text=final_text, usage=usage))


def build_app(settings: BridgeSettings) -> web.Application:
    app = web.Application()
    app["settings"] = settings
    app.router.add_get("/health", handle_health)
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    return app


def parse_args(argv: list[str] | None = None) -> BridgeSettings:
    parser = argparse.ArgumentParser(description="Run a local OpenAI chat-completions bridge backed by Codex OAuth.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--cwd", default=str(Path.cwd()))
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--sandbox", default="read-only", choices=["read-only", "workspace-write", "danger-full-access"])
    parser.add_argument("--approval-policy", default="never", choices=["untrusted", "on-failure", "on-request", "never"])
    parser.add_argument("--personality", default="pragmatic", choices=["none", "friendly", "pragmatic"])
    parser.add_argument(
        "--openclaw-auth-path",
        default=str(Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"),
    )
    parser.add_argument("--openclaw-profile", default="openai-codex:default")
    args = parser.parse_args(argv)
    return BridgeSettings(
        host=args.host,
        port=args.port,
        codex_command=args.codex_command,
        cwd=args.cwd,
        model=args.model,
        sandbox=args.sandbox,
        approval_policy=args.approval_policy,
        personality=args.personality,
        openclaw_auth_path=args.openclaw_auth_path,
        openclaw_profile=args.openclaw_profile,
    )


async def run_server(settings: BridgeSettings) -> None:
    app = build_app(settings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=settings.host, port=settings.port)
    await site.start()
    print(f"Codex OAuth bridge listening on http://{settings.host}:{settings.port}")
    while True:
        await asyncio.sleep(3600)


def main(argv: list[str] | None = None) -> None:
    settings = parse_args(argv)
    asyncio.run(run_server(settings))


if __name__ == "__main__":
    main()
