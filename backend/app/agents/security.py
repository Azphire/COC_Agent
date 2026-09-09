"""Credential removal shared by model inputs and persistent Agent outputs."""

import re

from app.auth import digest

FORBIDDEN_KEYS = {
    "token",
    "member_token",
    "invite_code",
    "invite_hash",
    "token_hash",
    "host_admin_token",
    "model_api_key",
    "api_key",
    "authorization",
    "thinking",
    "reasoning",
    "chain_of_thought",
}


def scrub(value, secrets=(), hashes=()):
    if isinstance(value, dict):
        return {
            k: scrub(v, secrets, hashes)
            for k, v in value.items()
            if k.lower() not in FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [scrub(v, secrets, hashes) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret and len(secret) >= 6:
                value = value.replace(secret, "[已隐藏凭据]")
        value = re.sub(
            r"[A-Za-z0-9_-]{24,128}",
            lambda m: "[已隐藏凭据]" if digest(m[0]) in hashes else m[0],
            value,
        )
        value = re.sub(r"<think>.*?(</think>|$)", "", value, flags=re.I | re.S)
    return value
