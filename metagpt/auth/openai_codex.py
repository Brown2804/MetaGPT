#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CODEX_REFRESH_EARLY_SECONDS = 300
JWT_CLAIM_PATH = "https://api.openai.com/auth"


def _pad_b64url(value: str) -> str:
    return value + "=" * ((4 - len(value) % 4) % 4)



def decode_jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("Token is not a JWT")
    payload = base64.urlsafe_b64decode(_pad_b64url(parts[1]).encode("utf-8"))
    return json.loads(payload.decode("utf-8"))



def derive_account_id(access_token: str) -> str | None:
    try:
        claims = decode_jwt_claims(access_token)
    except Exception:
        return None
    auth = claims.get(JWT_CLAIM_PATH) or {}
    account_id = auth.get("chatgpt_account_id")
    return str(account_id) if account_id else None



def derive_plan_type(access_token: str) -> str | None:
    try:
        claims = decode_jwt_claims(access_token)
    except Exception:
        return None
    auth = claims.get(JWT_CLAIM_PATH) or {}
    plan_type = auth.get("chatgpt_plan_type")
    return str(plan_type) if plan_type else None



def parse_expiry_timestamp(expires_at: Any) -> float | None:
    if expires_at in {None, "", 0, "0"}:
        return None

    if isinstance(expires_at, (int, float)):
        value = float(expires_at)
        return value / 1000.0 if value > 10_000_000_000 else value

    text = str(expires_at).strip()
    if not text:
        return None

    try:
        value = float(text)
        return value / 1000.0 if value > 10_000_000_000 else value
    except ValueError:
        pass

    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()



def format_expiry_timestamp(expires_at_seconds: float | int | str | None) -> str | None:
    if expires_at_seconds in {None, 0, "", "0"}:
        return None
    timestamp = parse_expiry_timestamp(expires_at_seconds)
    if timestamp is None:
        return None
    dt = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")



def is_expiring_soon(expires_at: Any, within_seconds: int = OPENAI_CODEX_REFRESH_EARLY_SECONDS) -> bool:
    if expires_at in {0, "0"}:
        return True
    timestamp = parse_expiry_timestamp(expires_at)
    if timestamp is None:
        return False
    return time.time() >= timestamp - max(within_seconds, 0)



def refresh_openai_codex_tokens(refresh_token: str) -> dict[str, Any]:
    if not refresh_token:
        raise ValueError("refresh_token is required")

    body = urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OPENAI_CODEX_CLIENT_ID,
        }
    ).encode("utf-8")
    request = Request(
        OPENAI_CODEX_TOKEN_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"OpenAI Codex token refresh failed: {exc.code} {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI Codex token refresh network error: {exc}") from exc

    access_token = payload.get("access_token")
    new_refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if not access_token or not new_refresh_token or not isinstance(expires_in, (int, float)):
        raise RuntimeError(f"OpenAI Codex token refresh response missing fields: {payload}")

    expires_at_seconds = time.time() + float(expires_in)
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "id_token": payload.get("id_token"),
        "expires_in": int(expires_in),
        "expires_at": format_expiry_timestamp(expires_at_seconds),
        "expires_at_seconds": int(expires_at_seconds),
        "expires_at_ms": int(expires_at_seconds * 1000),
        "account_id": derive_account_id(access_token),
        "plan_type": derive_plan_type(access_token),
    }
