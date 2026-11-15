"""组织邀请的对外模型（批次 B6，`ADR-023`）。

三条取舍：

1. `InvitationCreateRequest.role` 用 `Literal["student", "teacher"]`，
   **不含 `school_admin`**：一张可转发的码不该能造出第二个校长。
2. 请求体一律 `extra="forbid"`：多塞一个 `organization_id` 就 422，
   而不是被静默忽略、后端仍按 URL 里的那个组织执行（与 B5 `RoleUpdateRequest` 同口径）。
3. `expires_at` / `created_at` 是 `datetime`；库里是 naive UTC，**返回前转东八区**。
   B6 当时的写法是「展示口径交给前端 `utils/datetime.ts`」，2026-11-15 第 25 轮实测发现
   那句话不成立：naive 串没有偏移量，`new Date('2026-11-22T07:06:28')` 会按**浏览器本地
   时区**解析，东八区用户看到的到期时间比真实时间早 8 小时。前端无法靠格式化修好一个
   缺了时区的事实，所以口径改回后端（与 `JobRead` / `ProfileRead` / `MemberRead` 一致）：
   带 `+08:00` 的绝对时刻，前端怎么解析都不会差。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.core.datetime_display import format_shanghai_datetime
from app.core.permissions import UserRole
from app.schemas.auth import OrganizationBrief

#: 邀请能授予的角色。校长本来可以改成员角色，但那要经过端点并留审计。
InvitableRole = Literal["student", "teacher"]


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: InvitableRole = "student"
    max_uses: int = Field(default=1, ge=1, le=100)
    ttl_days: int = Field(default=7, ge=1, le=30)


class InvitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    code: str
    role: str
    max_uses: int
    used_count: int
    expires_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime
    #: 兑换过这张码的用户名；来自 `invitation.redeem` 审计，不是表列。
    redeemed_by: list[str] = Field(default_factory=list)

    @field_serializer("expires_at", "created_at")
    def serialize_shanghai(self, value: datetime) -> str:
        return format_shanghai_datetime(value)

    @field_serializer("revoked_at")
    def serialize_revoked_at(self, value: datetime | None) -> str | None:
        return None if value is None else format_shanghai_datetime(value)


class InvitationListData(BaseModel):
    items: list[InvitationRead] = Field(default_factory=list)


class InvitationCreateData(BaseModel):
    invitation: InvitationRead


class InvitationRedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)


class InvitationRedeemData(BaseModel):
    organization: OrganizationBrief
    role: UserRole
