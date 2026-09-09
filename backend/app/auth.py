"""Small bearer credential boundary for the trusted LAN host application."""

import hashlib
import secrets

from fastapi import HTTPException, Request


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def host_matches(settings, token: str) -> bool:
    expected = settings.host_admin_token.get_secret_value()
    return bool(expected) and secrets.compare_digest(digest(token), digest(expected))


def bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 512:
        raise HTTPException(401, detail={"message": "需要有效的身份凭据", "issues": []})
    return token


def require_host(request: Request) -> None:
    if not host_matches(request.app.state.settings, bearer(request)):
        raise HTTPException(401, detail={"message": "主机密钥无效或尚未配置", "issues": []})
