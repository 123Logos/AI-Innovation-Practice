"""认证相关 Pydantic 模型：请求体与对外响应体。

对外模型 `UserRead` 刻意不包含 `password_hash`，
映射在 service 层完成，避免任何调用方误把哈希返回给客户端。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

BCRYPT_MAX_PASSWORD_BYTES = 72


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        """只定「长什么样」，不挑大小写；大小写归一在下面 `lower()` 一次做掉。

        Phase 2 · D1（docs/adr/ADR-005）：用户名是**大小写不敏感**的标识符，
        统一以小写存储。归一放在模型层，是因为它决定了落库的值；
        `AuthService` 入口还会再归一一次（幂等），保证不经 schema 的内部调用也安全。
        """
        candidate = value.strip().lower()
        # 归一后重新判长度：某些字符小写化后长度会变（如 'İ' → 'i̇'），
        # 只信 `Field(max_length=100)` 会让超长值一路落到数据库再炸成 500。
        if not 3 <= len(candidate) <= 100:
            raise ValueError("username must contain between 3 and 100 characters")
        if any(character.isspace() for character in candidate):
            raise ValueError("username must not contain whitespace")
        return candidate

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        # bcrypt 只处理前 72 字节；超过就拒绝，绝不静默截断。
        if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
            raise ValueError("password must not exceed 72 bytes when encoded as UTF-8")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip().lower()
        if not candidate:
            return None
        local_part, separator, domain = candidate.partition("@")
        if not separator or not local_part or not domain or "@" in domain:
            raise ValueError("email format is invalid")
        return candidate


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
