#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AuthProfile(BaseModel):
    provider: str
    mode: Literal["oauth"] = "oauth"
    label: str = "default"
    access_token: str
    refresh_token: Optional[str] = None
    account_id: Optional[str] = None
    expires_at: Optional[str] = None
    id_token: Optional[str] = None
    meta: Dict[str, str] = Field(default_factory=dict)


class AuthFile(BaseModel):
    profiles: Dict[str, AuthProfile] = Field(default_factory=dict)
    order: Dict[str, List[str]] = Field(default_factory=dict)
