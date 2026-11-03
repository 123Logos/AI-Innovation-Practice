"""口令哈希与 JWT 签发/校验。

设计依据：docs/adr/ADR-004-auth-jwt.md；双 Token 见 ADR-005（D2 方案 A-1）。

两套令牌各司其职：

- access token：15 分钟，随每个请求走 `Authorization`，**不落库**，泄露窗口小；
- refresh token：7 天，只在 `/auth/refresh` 用一次就换新（轮换），
  库里只存它的 SHA-256 哈希，用于「撤销 / 复用拦截」这一层能力。

`type` 声明是防错配的关键：拿到 refresh token 冒充 access token 时，
`decode_token` 会因为 `expected_type` 不匹配而直接拒绝，不必依赖调用方自觉。
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

#: Refresh Token 有效期（天）。
#:
#: 刻意没有提升为 `Settings` 项：它和「轮换 + 撤销」的语义绑在一起，
#: 长度变了要同步考虑 D2.2 的续期策略，运维侧没有单独调它而不动代码的需求。
REFRESH_TOKEN_EXPIRE_DAYS = 7

_password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenError(Exception):
    """JWT 缺失、格式错误、签名不匹配、已过期或类型不符。"""


def hash_password(password: str) -> str:
    """返回 bcrypt 哈希，供 `users.password_hash` 持久化。"""
    return _password_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """校验明文口令与库中哈希；哈希格式非法时返回 False，不抛出。"""
    try:
        return _password_context.verify(plain_password, password_hash)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    """令牌的入库形态：SHA-256 十六进制（64 字符），与 `refresh_tokens.token_hash` 同宽。

    为什么不存明文：JWT 自带签名但**不是**口令，它等价于一张可直接使用的门票。
    库一旦被读走（备份、慢查询日志、越权查询），明文 token 立刻可冒用；
    存哈希后攻击者只能看到一串不可逆的摘要。

    为什么不加盐：输入是签名保护的随机串（JWT 签名段），字典/彩虹表无从下手；
    而加盐会让「按哈希查一行」退化成全表遍历，得不偿失。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(
    subject: str,
    expires_minutes: int | None = None,
    now: datetime | None = None,
) -> str:
    """签发 access token；`subject` 为用户 id。"""
    settings = get_settings()
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(
        minutes=settings.jwt_expire_minutes if expires_minutes is None else expires_minutes
    )
    return _encode(subject, ACCESS_TOKEN_TYPE, issued_at, expires_at)


def create_refresh_token(
    subject: str,
    expires_days: int | None = None,
    now: datetime | None = None,
) -> str:
    """签发 refresh token（明文 JWT）。

    落库前请务必用 `token_hash()` 摘要，**不要**把返回值直接写进数据库。

    载荷里带一个随机 `jti`：JWT 的时间声明精度只到**秒**，若同一秒内为同一个
    用户签发两次（登录 → 立刻刷新、或并发登录），`sub/type/iat/exp` 全相同，
    生成的令牌会**逐字节一样**，SHA-256 也就一样——落库时直接撞
    `token_hash` 的唯一索引，接口变成 500。`jti` 让每条令牌天然唯一。
    """
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS if expires_days is None else expires_days
    )
    return _encode(subject, REFRESH_TOKEN_TYPE, issued_at, expires_at)


def refresh_token_expires_at(
    expires_days: int | None = None,
    now: datetime | None = None,
) -> datetime:
    """与 `create_refresh_token` 同一口径的过期时刻，供 `refresh_tokens.expires_at` 落库。

    返回 **UTC naive**（MySQL DATETIME 无时区，全仓统一按 UTC 的 naive 时间存，
    见 `app/core/datetime_display.utc_now`）。传入同一个 `now`，两者算出的 `exp`
    才会与库里的 `expires_at` 严格一致，避免 JWT 还没过期、数据库却先判过期。
    """
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS if expires_days is None else expires_days
    )
    return expires_at.replace(tzinfo=None)


def decode_token(token: str, expected_type: str = ACCESS_TOKEN_TYPE) -> dict[str, Any]:
    """校验并返回载荷；任何失败都转成 `TokenError`。

    `expected_type` 是必设的防错配闸门：`/auth/me` 传默认的 `access`，
    `/auth/refresh` 传 `refresh`。少传一次就等于把「7 天有效的 refresh token」
    当成「15 分钟的 access token」放行，且不会再有任何一层拦得住。
    """
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
    if payload.get("type") != expected_type:
        raise TokenError(f"token type is not {expected_type}")
    if not payload.get("sub"):
        raise TokenError("token subject is missing")
    return payload


def _encode(
    subject: str,
    token_type: str,
    issued_at: datetime,
    expires_at: datetime,
) -> str:
    settings = get_settings()
    secret_key = settings.jwt_secret_key
    if not secret_key:
        raise RuntimeError("jwt_secret_key must be configured before issuing tokens")
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        # 每条令牌都有唯一 `jti`。不只是为了让 refresh token 落库不撞唯一索引，
        # 也是为了让「换发的新令牌」真的是新字符串——否则同一秒内的两次签发
        # 会给出逐字节相同的 JWT（`iat`/`exp` 只到秒），日志和测试里都分不清。
        "jti": str(uuid4()),
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(payload, secret_key, algorithm=settings.jwt_algorithm)
