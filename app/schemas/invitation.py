"""组织邀请的对外模型（批次 B6，`ADR-023`）。

三条取舍：

1. `InvitationCreateRequest.role` 用 `Literal["student", "teacher"]`，
   **不含 `school_admin`**：一张可转发的码不该能造出第二个校长。
2. 请求体一律 `extra="forbid"`：多塞一个 `organization_id` 就 422，
   而不是被静默忽略、后端仍按 URL 里的那个组织执行（与 B5 `RoleUpdateRequest` 同口径）。
3. `expires_at` / `created_at` 是 `datetime`，与 B5 `AuditLogRead` 一致；
   库里是 naive UTC，展示口径由前端按 `utils/datetime.ts` 处理，不在 schema 里另造一套。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
