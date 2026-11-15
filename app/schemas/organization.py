"""组织、成员与审计日志的对外模型（批次 B5，`docs/adr/ADR-021` §2.6）。

三条取舍写在前面，免得后面有人「顺手」改掉：

1. `MemberRead` 里的 `email` **可能为 `null`**：本校管理员能看到成员邮箱
   （这是管理动作的必要信息，PRD §3 的学校管理员职责），但没有邮箱就是没有。
2. `RoleUpdateRequest.role` 用 `Literal` 而不是 `UserRole`：后者会让
   `platform_admin` 成为合法输入，而它只能由运维在库里授予。
3. `AuditLogRead.detail` 原样回传 JSON 字符串，**不在后端解析成对象**：
   服务端不替前端决定「这几种 action 的 detail 各自有什么形状」，
   否则每加一种 action 就要改一次响应模型。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.core.datetime_display import format_shanghai_datetime
from app.core.permissions import UserRole
from app.schemas.auth import OrganizationBrief

#: 管理页允许授出的角色。`platform_admin` 不在其中（理由见模块 docstring 第 2 条）。
AssignableRole = Literal["student", "teacher", "school_admin"]


class OrganizationOverviewData(BaseModel):
    organization: OrganizationBrief
    role: UserRole
    member_count: int


class MemberRead(BaseModel):
    user_id: str
    username: str
    email: str | None = None
    role: UserRole
    #: 加入时间。管理页要能回答「这个人是什么时候进来的」。
    joined_at: datetime

    @field_serializer("joined_at")
    def serialize_joined_at(self, value: datetime) -> str:
        """库里是 naive UTC，返回前转东八区（同 `ProfileRead` / `JobRead` 的口径）。

        少了这一步，前端 `new Date('2026-11-15T07:06:28')` 会按**浏览器本地时区**解析，
        东八区用户看到的入组时间会比真实时间早 8 小时——组织页三个时间字段
        （入组 / 邀请码到期 / 日志时间）都是这个坑，2026-11-15 第 25 轮一并补上。
        """
        return format_shanghai_datetime(value)


class RoleUpdateRequest(BaseModel):
    #: 同样 `extra="forbid"`：多塞一个 `organization_id` 就 422，
    #: 而不是让它被静默忽略、然后后端按 URL 里的那个组织执行。
    model_config = ConfigDict(extra="forbid")

    role: AssignableRole


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor_user_id: str | None = None
    #: 操作人用户名（2026-11-15 第 25 轮）：界面上那句「谁在什么时候做了什么」要的是名字，
    #: 不是一串 UUID。由 `AuditLogRepository.list_recent()` 的 LEFT JOIN 带出；
    #: 系统动作（建组织、被拒绝的越权请求）没有操作人，这里是 `None`。
    actor_username: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    result: str
    detail: str | None = None
    ip: str | None = None
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """同 `MemberRead.serialize_joined_at`：naive UTC → 带 `+08:00` 的绝对时刻。"""
        return format_shanghai_datetime(value)


class AuditLogListData(BaseModel):
    items: list[AuditLogRead] = Field(default_factory=list)
    total: int
