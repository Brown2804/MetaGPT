#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Optional

from metagpt.auth.models import AuthFile, AuthProfile
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

        profile = AuthProfile(
            provider="openai-codex",
            label=profile_label,
            access_token=access_token,
            refresh_token=refresh_token,
            account_id=account_id,
            expires_at=expires_at,
            id_token=id_token,
            meta={"source": "codex-cli-cache", "path": str(codex_auth_path)},
        )
        self.upsert_profile(profile_id, profile)
        return profile_id
