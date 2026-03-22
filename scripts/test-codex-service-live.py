#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8787"
SERVICE = ROOT_DIR / "scripts" / "metagpt-codex-service.sh"
WRAPPER = ROOT_DIR / "scripts" / "metagpt-codex.sh"
METAGPT_LOCAL = ROOT_DIR / "scripts" / "metagpt-local.sh"
OPENCLAW_AUTH = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"


class CheckFailure(RuntimeError):
    pass



def run(cmd: list[str], env: dict[str, str] | None = None, timeout: float = 240.0) -> str:
    completed = subprocess.run(
        cmd,
        cwd=str(ROOT_DIR),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise CheckFailure(f"Command failed ({completed.returncode}): {' '.join(cmd)}\n{output}")
    return output



def parse_json_from_output(output: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for idx, char in enumerate(output):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(output[idx:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise CheckFailure(f"Failed to parse JSON from output:\n{output}")



def http_request(path: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 120.0):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method="POST" if payload is not None else "GET",
        headers=headers or {},
    )
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, dict(response.headers), body
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")



def expect(condition: bool, message: str):
    if not condition:
        raise CheckFailure(message)



def auth_access_token() -> str:
    auth_path = Path.home() / ".metagpt" / "auth" / "auth-profiles.json"
    data = json.loads(auth_path.read_text(encoding="utf-8"))
    return data["profiles"]["openai-codex:default"]["access_token"]



def check_service_setup(report: list[dict[str, Any]]):
    output = run([str(SERVICE), "setup"])
    parsed = parse_json_from_output(output)
    expect(parsed["profile_id"] == "openai-codex:default", "setup profile_id mismatch")
    expect(parsed["profile"]["present"] is True, "setup did not produce auth profile")
    report.append({"name": "service_setup", "ok": True, "result": parsed})



def check_service_ensure(report: list[dict[str, Any]]):
    output = run([str(SERVICE), "ensure"])
    parsed = parse_json_from_output(output)
    expect(parsed["setup"]["profile_id"] == "openai-codex:default", "ensure profile_id mismatch")
    expect(parsed["bridge"]["health"]["ok"] is True, "ensure did not leave bridge healthy")
    report.append({"name": "service_ensure", "ok": True, "result": parsed})



def check_service_status(report: list[dict[str, Any]]):
    output = run([str(SERVICE), "status"])
    parsed = parse_json_from_output(output)
    expect(parsed["pid_running"] is True, "service status says pid is not running")
    expect(parsed["health"]["ok"] is True, "service health is not ok")
    expect(parsed["config_exists"] is True, "~/.metagpt/config2.yaml missing")
    expect(parsed["profile"]["present"] is True, "oauth profile missing")
    report.append({"name": "service_status", "ok": True, "result": parsed})



def check_auth_status(report: list[dict[str, Any]]):
    output = run([str(METAGPT_LOCAL), "auth", "status"])
    expect("openai-codex:default" in output, "auth status missing openai-codex:default")
    report.append({"name": "auth_status", "ok": True, "snippet": output[-500:]})



def check_auth_cli_import_in_temp_home(report: list[dict[str, Any]]):
    with tempfile.TemporaryDirectory(prefix="metagpt-auth-cli-") as tmp:
        env = os.environ.copy()
        env["HOME"] = tmp
        import_output = run(
            [
                str(METAGPT_LOCAL),
                "auth",
                "import-openclaw-codex",
                "default",
                str(OPENCLAW_AUTH),
                "openai-codex:default",
            ],
            env=env,
        )
        status_output = run([str(METAGPT_LOCAL), "auth", "status"], env=env)
        expect("Imported OpenClaw Codex auth into profile" in import_output, "auth import did not report success")
        expect("openai-codex:default" in status_output, "temp-home auth status missing imported profile")
        report.append(
            {
                "name": "auth_cli_import_temp_home",
                "ok": True,
                "import_snippet": import_output[-300:],
                "status_snippet": status_output[-300:],
            }
        )



def check_logs(report: list[dict[str, Any]]):
    output = run([str(SERVICE), "logs", "--lines", "20"])
    report.append({"name": "service_logs", "ok": True, "snippet": output[-800:]})



def check_root_and_health(report: list[dict[str, Any]]):
    root_status, _, root_body = http_request("/")
    health_status, _, health_body = http_request("/health")
    health = json.loads(health_body)
    expect(root_status == 404, f"expected root 404, got {root_status}")
    expect(health_status == 200 and health.get("ok") is True, "health endpoint not healthy")
    report.append(
        {
            "name": "http_root_and_health",
            "ok": True,
            "root_status": root_status,
            "health": health,
            "root_snippet": root_body[:120],
        }
    )



def check_smoke(report: list[dict[str, Any]]):
    output = run([str(SERVICE), "smoke"])
    parsed = parse_json_from_output(output)
    expect(parsed["ok"] is True, "smoke did not report ok")
    expect(bool(parsed["content"]), "smoke content empty")
    report.append({"name": "service_smoke", "ok": True, "result": parsed})



def check_nonstream_completion(report: list[dict[str, Any]]):
    status, _, body = http_request(
        "/v1/chat/completions",
        {
            "model": "gpt-5.4",
            "messages": [
                {"role": "system", "content": "Answer in exactly three words."},
                {"role": "user", "content": "Say hello to Brownie."},
            ],
            "stream": False,
        },
    )
    parsed = json.loads(body)
    content = parsed["choices"][0]["message"]["content"]
    expect(status == 200, f"non-stream completion returned {status}")
    expect(bool(content), "non-stream content empty")
    report.append({"name": "http_nonstream", "ok": True, "content": content, "finish_reason": parsed["choices"][0]["finish_reason"]})



def check_explicit_bearer(report: list[dict[str, Any]]):
    status, _, body = http_request(
        "/v1/chat/completions",
        {
            "model": "gpt-5.4",
            "messages": [
                {"role": "system", "content": "Answer in exactly two words."},
                {"role": "user", "content": "Say hi."},
            ],
            "stream": False,
        },
        headers={"Authorization": f"Bearer {auth_access_token()}"},
    )
    parsed = json.loads(body)
    content = parsed["choices"][0]["message"]["content"]
    expect(status == 200, f"explicit bearer completion returned {status}")
    expect(bool(content), "explicit bearer content empty")
    report.append({"name": "http_explicit_bearer", "ok": True, "content": content})



def check_stream(report: list[dict[str, Any]]):
    payload = {
        "model": "gpt-5.4",
        "messages": [
            {"role": "system", "content": "Answer in exactly four words."},
            {"role": "user", "content": "Introduce the bridge."},
        ],
        "stream": True,
    }
    request = urllib.request.Request(BASE_URL + "/v1/chat/completions", data=json.dumps(payload).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=120) as response:
        lines = []
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            lines.append(line)
            if line == "data: [DONE]":
                break
    expect(len(lines) >= 2, "stream returned too few SSE lines")
    expect(lines[-1] == "data: [DONE]", "stream did not end with [DONE]")
    report.append({"name": "http_stream", "ok": True, "line_count": len(lines), "first": lines[0], "last": lines[-1]})



def check_tool_call(report: list[dict[str, Any]]):
    payload = {
        "model": "gpt-5.4",
        "messages": [
            {"role": "system", "content": "You must call the function with valid JSON only."},
            {"role": "user", "content": "Create a tiny python snippet that prints hi."},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "emit_code",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "language": {"type": "string"},
                            "code": {"type": "string"},
                        },
                        "required": ["language", "code"],
                    },
                },
            }
        ],
        "stream": False,
    }
    status, _, body = http_request("/v1/chat/completions", payload)
    parsed = json.loads(body)
    choice = parsed["choices"][0]
    tool_call = choice["message"]["tool_calls"][0]
    args = json.loads(tool_call["function"]["arguments"])
    expect(status == 200, f"tool-call request returned {status}")
    expect(choice["finish_reason"] == "tool_calls", "finish_reason is not tool_calls")
    expect(tool_call["function"]["name"] == "emit_code", "tool name mismatch")
    expect(args.get("language") == "python", "tool arguments missing python language")
    expect("print" in args.get("code", ""), "tool arguments missing code body")
    report.append({"name": "http_tool_call", "ok": True, "tool_args": args})



def check_stop_restart_cycle(report: list[dict[str, Any]]):
    stop_once = parse_json_from_output(run([str(SERVICE), "stop"]))
    expect(stop_once.get("stopped") is True, "first stop did not stop the service")

    try:
        urllib.request.urlopen(BASE_URL + "/health", timeout=3)
        raise CheckFailure("health still reachable after stop")
    except Exception:
        pass

    stop_twice = parse_json_from_output(run([str(SERVICE), "stop"]))
    expect(stop_twice.get("message") == "not running", "second stop was not idempotent")

    ensure_after = parse_json_from_output(run([str(SERVICE), "ensure"]))
    expect(ensure_after["bridge"]["health"]["ok"] is True, "ensure after stop did not recover service")
    report.append({"name": "service_stop_restart_cycle", "ok": True, "first_stop": stop_once, "second_stop": stop_twice, "ensure_after": ensure_after})



def check_wrapper_user_flow(report: list[dict[str, Any]]):
    parse_json_from_output(run([str(SERVICE), "stop"]))
    try:
        urllib.request.urlopen(BASE_URL + "/health", timeout=3)
        raise CheckFailure("health still reachable before wrapper autostart test")
    except Exception:
        pass

    output = run(
        [
            str(WRAPPER),
            "startup",
            "Return exactly one line: print(42)",
            "--n-round",
            "1",
            "--code-review",
            "false",
            "--run-tests",
            "false",
            "--implement",
            "false",
            "--project-name",
            "codex_wrapper_autostart_test",
        ],
        timeout=420.0,
    )
    expect("print(42)" in output, "wrapper user flow did not produce expected result")
    status = parse_json_from_output(run([str(SERVICE), "status"]))
    expect(status["health"]["ok"] is True, "wrapper user flow did not leave service healthy")
    report.append({"name": "wrapper_user_flow", "ok": True, "output_snippet": output[-800:], "status": status})



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run live end-to-end checks for the local MetaGPT Codex OAuth service flow.")
    parser.parse_args(argv)

    report: list[dict[str, Any]] = []
    checks = [
        check_service_setup,
        check_service_ensure,
        check_service_status,
        check_auth_status,
        check_auth_cli_import_in_temp_home,
        check_logs,
        check_root_and_health,
        check_smoke,
        check_nonstream_completion,
        check_explicit_bearer,
        check_stream,
        check_tool_call,
        check_stop_restart_cycle,
        check_wrapper_user_flow,
    ]

    try:
        for check in checks:
            check(report)
    except Exception as exc:
        failed_name = checks[len(report)].__name__ if len(report) < len(checks) else "unknown"
        print(
            json.dumps(
                {
                    "ok": False,
                    "failed_check": failed_name,
                    "completed": len(report),
                    "report": report,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(json.dumps({"ok": True, "checks": len(report), "report": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
