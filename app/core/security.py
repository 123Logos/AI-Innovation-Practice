"""口令哈希与 JWT 签发/校验。

设计依据：docs/adr/ADR-004-auth-jwt.md
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

ACCESS_TOKEN_TYPE = "access"

_password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenError(Exception):
    """JWT 缺失、格式错误、签名不匹配或已过期。"""


def hash_password(password: str) -> str:
    """返回 bcrypt 哈希，供 `users.password_hash` 持久化。"""
    return _password_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """校验明文口令与库中哈希；哈希格式非法时返回 False，不抛出。"""
    try:
        return _password_context.verify(plain_password, password_hash)
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject: str,
    expires_minutes: int | None = None,
    now: datetime | None = None,
) -> str:
    """签发 access token；`subject` 为用户 id。"""
    settings = get_settings()
    secret_key = settings.jwt_secret_key
    if not secret_key:
        raise RuntimeError("jwt_secret_key must be configured before issuing tokens")
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(
        minutes=settings.jwt_expire_minutes if expires_minutes is None else expires_minutes
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "type": ACCESS_TOKEN_TYPE,
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(payload, secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """校验并返回载荷；任何失败都转成 `TokenError`。"""
    settings = get_settings()
    secret_key = settings.jwt_secret_key
    if not secret_key:
        raise RuntimeError("jwt_secret_key must be configured before decoding tokens")
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as error:
        raise TokenError("token is invalid or expired") from error
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise TokenError("token type is not access")
    if not payload.get("sub"):
        raise TokenError("token subject is missing")
    return payload
