#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

from metagpt.auth.openai_codex import parse_expiry_timestamp
from metagpt.auth.store import AuthStore

ROOT_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".metagpt" / "codex-service"
PID_PATH = STATE_DIR / "bridge.pid"
LOG_PATH = STATE_DIR / "bridge.log"
CONFIG_PATH = Path.home() / ".metagpt" / "config2.yaml"
OPENCLAW_AUTH_PATH = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
BRIDGE_SCRIPT = ROOT_DIR / "scripts" / "codex-oauth-bridge.py"
DEFAULT_PROFILE_LABEL = "default"
DEFAULT_PROFILE_ID = "openai-codex:default"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def build_health_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/health"



def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method=method.upper())
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)



def pid_is_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False



def read_pid() -> int | None:
    if not PID_PATH.exists():
        return None
    try:
        return int(PID_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return None



def write_pid(pid: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(f"{pid}\n", encoding="utf-8")



def clear_pid() -> None:
    if PID_PATH.exists():
        PID_PATH.unlink()



def health_status(host: str, port: int, timeout: float = 3.0) -> dict[str, Any] | None:
    try:
        return http_json("GET", build_health_url(host, port), timeout=timeout)
    except Exception:
        return None



def wait_for_health(host: str, port: int, timeout: float = 20.0) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = health_status(host, port, timeout=2.0)
        if last and last.get("ok"):
            return last
        time.sleep(0.5)
    return last



def load_yaml_doc(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}



def save_yaml_doc(path: Path, doc: dict[str, Any]) -> tuple[bool, Path | None]:
    serialized = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    old_text = path.read_text(encoding="utf-8") if path.exists() else None
    if old_text == serialized:
        return False, None

    backup_path = None
    if path.exists():
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}.bak-{timestamp}")
        path.replace(backup_path)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")
    return True, backup_path



def configure_metagpt_home(model: str, host: str, port: int, profile_id: str) -> tuple[bool, Path | None, dict[str, Any]]:
    doc = load_yaml_doc(CONFIG_PATH)
    llm = dict(doc.get("llm") or {})
    llm.update(
        {
            "api_type": "openai_codex",
            "auth_mode": "oauth_profile",
            "auth_profile": profile_id,
            "base_url": f"http://{host}:{port}/v1",
            "api_key": "",
            "model": model,
            "stream": True,
        }
    )
    doc["llm"] = llm
    changed, backup_path = save_yaml_doc(CONFIG_PATH, doc)
    return changed, backup_path, doc



def ensure_auth_profile(
    profile_label: str,
    source_profile_id: str,
    openclaw_auth_path: Path,
) -> tuple[AuthStore, str, Any]:
    store = AuthStore.default()
    profile_id = store.import_openclaw_codex_auth(
        profile_label=profile_label,
        openclaw_auth_path=openclaw_auth_path,
        source_profile_id=source_profile_id,
        overwrite=True,
    )
    profile = store.ensure_valid_profile(profile_id)
    return store, profile_id, profile



def start_bridge(host: str, port: int, model: str, openclaw_auth_path: Path, source_profile_id: str, cwd: Path) -> dict[str, Any]:
    existing_pid = read_pid()
    current_health = health_status(host, port)
    if current_health and current_health.get("ok"):
        if existing_pid and pid_is_running(existing_pid):
            return {
                "started": False,
                "pid": existing_pid,
                "health": current_health,
                "message": "already running",
            }
        return {
            "started": False,
            "pid": existing_pid,
            "health": current_health,
            "message": "already running (unmanaged)",
        }

    if existing_pid and not pid_is_running(existing_pid):
        clear_pid()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "ab") as log_handle:
        process = subprocess.Popen(
            [
                sys.executable,
                str(BRIDGE_SCRIPT),
                "--host",
                host,
                "--port",
                str(port),
                "--cwd",
                str(cwd),
                "--model",
                model,
                "--openclaw-auth-path",
                str(openclaw_auth_path),
                "--openclaw-profile",
                source_profile_id,
            ],
            cwd=str(ROOT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    write_pid(process.pid)
    health = wait_for_health(host, port)
    if not health or not health.get("ok"):
        raise RuntimeError(f"Bridge failed to become healthy. Check logs: {LOG_PATH}")
    return {
        "started": True,
        "pid": process.pid,
        "health": health,
        "message": "started",
    }



def stop_bridge() -> dict[str, Any]:
    pid = read_pid()
    if not pid:
        return {"stopped": False, "message": "not running"}
    if not pid_is_running(pid):
        clear_pid()
        return {"stopped": False, "message": "stale pid removed", "pid": pid}

    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 10
    while time.time() < deadline:
        if not pid_is_running(pid):
            clear_pid()
            return {"stopped": True, "pid": pid}
        time.sleep(0.25)

    os.kill(pid, signal.SIGKILL)
    clear_pid()
    return {"stopped": True, "pid": pid, "forced": True}



def profile_status(profile: Any) -> dict[str, Any]:
    if profile is None:
        return {"present": False}
    expires_ts = parse_expiry_timestamp(profile.expires_at)
    return {
        "present": True,
        "provider": profile.provider,
        "label": profile.label,
        "account_id": profile.account_id,
        "expires_at": profile.expires_at,
        "expires_in_seconds": int(expires_ts - time.time()) if expires_ts else None,
        "source": (profile.meta or {}).get("source"),
    }



def run_setup(args: argparse.Namespace) -> dict[str, Any]:
    _, profile_id, profile = ensure_auth_profile(
        profile_label=args.profile_label,
        source_profile_id=args.source_profile_id,
        openclaw_auth_path=Path(args.openclaw_auth_path).expanduser(),
    )
    changed, backup_path, _ = configure_metagpt_home(
        model=args.model,
        host=args.host,
        port=args.port,
        profile_id=profile_id,
    )
    return {
        "profile_id": profile_id,
        "config_path": str(CONFIG_PATH),
        "config_changed": changed,
        "config_backup": str(backup_path) if backup_path else None,
        "profile": profile_status(profile),
        "bridge_base_url": f"http://{args.host}:{args.port}/v1",
    }



def run_ensure(args: argparse.Namespace) -> dict[str, Any]:
    setup_result = run_setup(args)
    start_result = start_bridge(
        host=args.host,
        port=args.port,
        model=args.model,
        openclaw_auth_path=Path(args.openclaw_auth_path).expanduser(),
        source_profile_id=args.source_profile_id,
        cwd=Path(args.cwd).expanduser(),
    )
    return {"setup": setup_result, "bridge": start_result}



def run_status(args: argparse.Namespace) -> dict[str, Any]:
    store = AuthStore.default()
    profile = store.get_profile(DEFAULT_PROFILE_ID)
    return {
        "pid": read_pid(),
        "pid_running": pid_is_running(read_pid()),
        "health": health_status(args.host, args.port),
        "log_path": str(LOG_PATH),
        "config_path": str(CONFIG_PATH),
        "config_exists": CONFIG_PATH.exists(),
        "profile": profile_status(profile),
    }



def run_logs(args: argparse.Namespace) -> int:
    if not LOG_PATH.exists():
        print(f"No log file yet: {LOG_PATH}")
        return 0
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-args.lines :]:
        print(line)
    return 0



def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "Return a very short plain-text answer."},
            {"role": "user", "content": args.prompt},
        ],
        "stream": False,
    }
    response = http_json(
        "POST",
        f"http://{args.host}:{args.port}/v1/chat/completions",
        payload=payload,
        timeout=args.timeout,
    )
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {
        "ok": True,
        "model": response.get("model"),
        "content": message.get("content"),
        "finish_reason": choice.get("finish_reason"),
        "usage": response.get("usage"),
    }



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a locally usable MetaGPT + Codex OAuth bridge service.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default=DEFAULT_HOST)
    common.add_argument("--port", type=int, default=DEFAULT_PORT)
    common.add_argument("--model", default=DEFAULT_MODEL)
    common.add_argument("--cwd", default=str(ROOT_DIR))
    common.add_argument("--openclaw-auth-path", default=str(OPENCLAW_AUTH_PATH))
    common.add_argument("--source-profile-id", default=DEFAULT_PROFILE_ID)
    common.add_argument("--profile-label", default=DEFAULT_PROFILE_LABEL)

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("setup", parents=[common])
    subparsers.add_parser("ensure", parents=[common])
    subparsers.add_parser("start", parents=[common])
    subparsers.add_parser("status", parents=[common])
    subparsers.add_parser("stop")

    logs_parser = subparsers.add_parser("logs")
    logs_parser.add_argument("--lines", type=int, default=80)

    smoke_parser = subparsers.add_parser("smoke", parents=[common])
    smoke_parser.add_argument("--prompt", default="Say hello from the bridge in one sentence.")
    smoke_parser.add_argument("--timeout", type=float, default=90.0)
    return parser



def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "setup":
            print(json.dumps(run_setup(args), ensure_ascii=False, indent=2))
            return 0
        if args.command == "ensure":
            print(json.dumps(run_ensure(args), ensure_ascii=False, indent=2))
            return 0
        if args.command == "start":
            result = start_bridge(
                host=args.host,
                port=args.port,
                model=args.model,
                openclaw_auth_path=Path(args.openclaw_auth_path).expanduser(),
                source_profile_id=args.source_profile_id,
                cwd=Path(args.cwd).expanduser(),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "status":
            print(json.dumps(run_status(args), ensure_ascii=False, indent=2))
            return 0
        if args.command == "stop":
            print(json.dumps(stop_bridge(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "logs":
            return run_logs(args)
        if args.command == "smoke":
            print(json.dumps(run_smoke(args), ensure_ascii=False, indent=2))
            return 0
    except URLError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
