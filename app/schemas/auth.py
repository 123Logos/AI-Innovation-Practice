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
        candidate = value.strip()
        if len(candidate) < 3:
            raise ValueError("username must contain at least 3 characters")
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
