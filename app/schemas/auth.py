"""认证相关 Pydantic 模型：请求体与对外响应体。

对外模型 `UserRead` 刻意不包含 `password_hash`，
映射在 service 层完成，避免任何调用方误把哈希返回给客户端。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.permissions import UserRole

BCRYPT_MAX_PASSWORD_BYTES = 72


class RegisterRequest(BaseModel):
    """注册请求体。

    **`extra="forbid"` 是批次 B5 的行为变更**（`docs/adr/ADR-021` §2.2）：在这之前，
    客户端多塞的任何字段（拼错的 `organiztion_code`、自以为能提权的 `is_admin`）
    都被 Pydantic 静默丢掉，而响应是成功——「静默降级比明确拒绝更坏」。
    现在这类请求直接 422，错误位置精确到字段名。
    """

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    #: 自助注册只认这三种角色。**不含** `platform_admin`：能自己注册成平台管理员就是提权漏洞。
    #: 用 Literal 而不是 `UserRole`：后者会把 `platform_admin` 也当成合法输入，
    #: 而它在自助注册里根本不该被接受。
    role: Literal["student", "teacher", "school_admin"] = "student"
    #: 组织码 = 邀请码。教师/管理员必须带（业务校验在 `AuthService.register`，
    #: 那里能给出 400 + 人话；schema 只负责「形状」）。
    organization_code: str | None = Field(default=None, max_length=50)

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


class RefreshTokenRequest(BaseModel):
    """`POST /auth/refresh` 与 `POST /auth/logout` 共用的请求体。

    `max_length` 只做防御：JWT 一般 300–500 字符，给到 4096 是为了容纳
    未来可能的更长声明，同时挡住「往这个接口灌 MB 级字符串」这种廉价消耗。
    这里**不做** JWT 结构校验——那不是 schema 的职责，且校验结果不该
    通过 422/401 的差异泄露给攻击者（统一在 service 层判 401）。
    """

    refresh_token: str = Field(min_length=1, max_length=4096)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    """登录与刷新共用同一响应结构，前端可以无差别处理。

    `expires_in` 依然只描述 **access token** 的寿命（秒）：
    它决定前端什么时候该去刷新；refresh token 的 7 天寿命属于服务端状态
    （可被撤销、可被轮换），不通过响应体对外承诺。
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class OrganizationBrief(BaseModel):
    """`GET /me` 里的组织摘要（`docs/adr/ADR-021` §2.6）。

    只给「我是谁的下属」必须的三格：`code` 是邀请码（管理页要展示给用户转发），
    `name` 是显示名，`kind` 决定前端文案（学校 / 学院 / 企业）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    kind: str


class CurrentUserData(BaseModel):
    """`GET /me` 的 `data`。

    **角色与组织放在顶层、不塞进 `user`**（ADR-021 §2.6）：`user` 是「这个人是谁」，
    角色/组织是「他和某个组织的关系」，两者生命周期不同（换组织不改用户）。
    塞在一起会让 `UserRead` 同时承担两种来源的数据，`model_validate(orm_user)` 就再也对不上。

    `permissions` 与后端判定同源（都读 `app/core/permissions.py::ROLE_PERMISSIONS`），
    前端据此决定菜单；但它**不是**权限边界，每个端点自己再判一次。
    """

    user: UserRead
    role: UserRole
    organization: OrganizationBrief | None = None
    permissions: list[str]
