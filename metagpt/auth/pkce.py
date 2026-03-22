#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class PKCEPair:
    verifier: str
    challenge: str
    state: str


def _urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def generate_pkce() -> PKCEPair:
    verifier = _urlsafe_b64(secrets.token_bytes(32))
    challenge = _urlsafe_b64(hashlib.sha256(verifier.encode("utf-8")).digest())
    state = _urlsafe_b64(secrets.token_bytes(24))
    return PKCEPair(verifier=verifier, challenge=challenge, state=state)
