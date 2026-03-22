#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from metagpt.auth.models import AuthFile, AuthProfile
from metagpt.auth.openai_codex import (
    derive_account_id,
    format_expiry_timestamp,
    is_expiring_soon,
    refresh_openai_codex_tokens,
)
from metagpt.const import CONFIG_ROOT


class AuthStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or (CONFIG_ROOT / "auth" / "auth-profiles.json")).expanduser()

    @classmethod
    def default(cls) -> "AuthStore":
        return cls()

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> AuthFile:
        if not self.exists():
            return AuthFile()
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AuthFile.model_validate(data)

    def save(self, auth_file: AuthFile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="auth-profiles-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(auth_file.model_dump(), f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    def get_profile(self, profile_id: str) -> Optional[AuthProfile]:
        return self.load().profiles.get(profile_id)

    def upsert_profile(self, profile_id: str, profile: AuthProfile) -> None:
        auth_file = self.load()
        auth_file.profiles[profile_id] = profile
        order = auth_file.order.setdefault(profile.provider, [])
        if profile_id not in order:
            order.append(profile_id)
        self.save(auth_file)

    def delete_profile(self, profile_id: str) -> bool:
        auth_file = self.load()
        profile = auth_file.profiles.pop(profile_id, None)
        if profile is None:
            return False
        order = auth_file.order.get(profile.provider, [])
        auth_file.order[profile.provider] = [item for item in order if item != profile_id]
        self.save(auth_file)
        return True

    def list_profiles(self, provider: Optional[str] = None) -> dict[str, AuthProfile]:
        profiles = self.load().profiles
        if not provider:
            return profiles
        return {profile_id: profile for profile_id, profile in profiles.items() if profile.provider == provider}

    def ensure_valid_profile(
        self,
        profile_id: str,
        within_seconds: int = 300,
        force: bool = False,
    ) -> Optional[AuthProfile]:
        auth_file = self.load()
        profile = auth_file.profiles.get(profile_id)
        if profile is None:
            return None
        if profile.provider != "openai-codex" or not profile.refresh_token:
            return profile
        if not force and not is_expiring_soon(profile.expires_at, within_seconds=within_seconds):
            return profile

        refreshed = refresh_openai_codex_tokens(profile.refresh_token)
        profile.access_token = refreshed["access_token"]
        profile.refresh_token = refreshed["refresh_token"]
        profile.expires_at = refreshed["expires_at"]
        profile.id_token = refreshed.get("id_token") or profile.id_token
        profile.account_id = refreshed.get("account_id") or profile.account_id or derive_account_id(profile.access_token)
        profile.meta = {
            **(profile.meta or {}),
            "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        auth_file.profiles[profile_id] = profile
        self.save(auth_file)
        return profile

    def import_codex_cli_auth(
        self,
        profile_label: str = "default",
        codex_auth_path: Optional[Path] = None,
        overwrite: bool = True,
    ) -> str:
        codex_auth_path = Path(codex_auth_path or Path.home() / ".codex" / "auth.json").expanduser()
        if not codex_auth_path.exists():
            raise FileNotFoundError(f"Codex auth file not found: {codex_auth_path}")

        with open(codex_auth_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        tokens = raw.get("tokens") or {}
        access_token = tokens.get("access_token") or raw.get("access_token")
        refresh_token = tokens.get("refresh_token") or raw.get("refresh_token")
        account_id = tokens.get("account_id") or raw.get("account_id")
        expires_at = (
            tokens.get("expires_at")
            or tokens.get("expires")
            or raw.get("expires_at")
            or raw.get("expires")
        )
        id_token = tokens.get("id_token") or raw.get("id_token")

        if not access_token:
            raise ValueError("Codex auth file does not contain tokens.access_token")

        profile_id = f"openai-codex:{profile_label}"
        if not overwrite and self.get_profile(profile_id):
            raise ValueError(f"Profile already exists: {profile_id}")

        normalized_expires_at = expires_at if isinstance(expires_at, str) and "T" in expires_at else format_expiry_timestamp(expires_at) or (str(expires_at) if expires_at else None)

        profile = AuthProfile(
            provider="openai-codex",
            label=profile_label,
            access_token=access_token,
            refresh_token=refresh_token,
            account_id=account_id or derive_account_id(access_token),
            expires_at=normalized_expires_at,
            id_token=id_token,
            meta={"source": "codex-cli-cache", "path": str(codex_auth_path)},
        )
        self.upsert_profile(profile_id, profile)
        return profile_id

    def import_openclaw_codex_auth(
        self,
        profile_label: str = "default",
        openclaw_auth_path: Optional[Path] = None,
        source_profile_id: str = "openai-codex:default",
        overwrite: bool = True,
    ) -> str:
        openclaw_auth_path = Path(
            openclaw_auth_path or Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
        ).expanduser()
        if not openclaw_auth_path.exists():
            raise FileNotFoundError(f"OpenClaw auth file not found: {openclaw_auth_path}")

        with open(openclaw_auth_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        profiles = raw.get("profiles") or {}
        source = profiles.get(source_profile_id)
        if not source:
            raise ValueError(f"OpenClaw auth profile not found: {source_profile_id}")

        access_token = source.get("access")
        refresh_token = source.get("refresh")
        account_id = source.get("accountId")
        expires_at = source.get("expires")
        provider = source.get("provider") or "openai-codex"

        if not access_token:
            raise ValueError(f"OpenClaw auth profile does not contain access token: {source_profile_id}")

        profile_id = f"openai-codex:{profile_label}"
        if not overwrite and self.get_profile(profile_id):
            raise ValueError(f"Profile already exists: {profile_id}")

        profile = AuthProfile(
            provider=provider,
            label=profile_label,
            access_token=access_token,
            refresh_token=refresh_token,
            account_id=account_id or derive_account_id(access_token),
            expires_at=format_expiry_timestamp(expires_at) or (str(expires_at) if expires_at is not None else None),
            id_token=source.get("idToken") or source.get("id_token"),
            meta={
                "source": "openclaw-auth-profiles",
                "path": str(openclaw_auth_path),
                "source_profile_id": source_profile_id,
            },
        )
        self.upsert_profile(profile_id, profile)
        return profile_id
