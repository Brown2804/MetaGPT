#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass

from metagpt.auth.store import AuthStore
from metagpt.configs.llm_config import LLMConfig


@dataclass(frozen=True)
class ResolvedCredentials:
    api_key: str
    base_url: str
    auth_profile: str | None = None


def resolve_openai_credentials(config: LLMConfig) -> ResolvedCredentials:
    if config.auth_mode == "oauth_profile":
        profile_id = config.auth_profile or "openai-codex:default"
        profile = AuthStore.default().get_profile(profile_id)
        if profile is None:
            raise ValueError(
                f"OAuth profile '{profile_id}' not found. Run `metagpt auth import-codex` or configure a valid auth_profile."
            )
        return ResolvedCredentials(
            api_key=profile.access_token,
            base_url=config.base_url,
            auth_profile=profile_id,
        )

    return ResolvedCredentials(api_key=config.api_key, base_url=config.base_url)
