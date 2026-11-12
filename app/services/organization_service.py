"""组织与成员的业务逻辑（批次 B5，`docs/adr/ADR-021`）。

本层不接触 HTTP：错误以域异常抛出，由 API 层映射为状态码（与 `auth_service` 同构）。

**两道判断必须分开写，不要合并**：

1. 「你有没有这个权限」由路由层的 `require_permission(...)` 判（`ADR-021` §2.3），
   它读的是角色→权限映射表；
2. 「这个对象是不是你这个组织的」只能在这一层判——权限表里没有、也不该有
   「某一条记录属于谁」的信息。把两者混在一起，就是日后「教师 A 校能改 B 校成员」的漏洞。

**被拒绝的越权尝试也要写审计**（`result=denied`）。那时业务没有任何写入，
所以审计单独提交是安全的（`AuditLogRepository.commit` 的注释里有理由）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.permissions import UserRole
from app.repositories.audit_log_repository import (
    AuditAction,
    AuditLog,
    AuditLogRepository,
    AuditResult,
)
from app.repositories.organization_repository import (
    Organization,
    OrganizationMember,
    OrganizationRepository,
)
from app.repositories.user_repository import User
from app.services.auth_service import CurrentUserContext


class OrganizationError(Exception):
    """组织域错误基类。"""


class OrganizationNotFoundError(OrganizationError):
    """组织不存在（或当前账号不属于任何组织）。"""


class CrossOrganizationError(OrganizationError):
    """目标组织不是当前账号所属的组织。—— 跨组织不可见的唯一执行点。"""


class MemberNotFoundError(OrganizationError):
    """目标用户不是这个组织的成员。"""


class CannotChangeOwnRoleError(OrganizationError):
    """不许改自己的角色。

    这不是洁癖：一个校长把自己降级成学生之后，就没有任何账号能把他改回来，
    也没有任何账号能再管理这个组织——自锁一旦发生只能进数据库手工修。
    """


class InvalidRoleAssignmentError(OrganizationError):
    """不允许通过这个端点授出的角色（`platform_admin`）。"""


@dataclass(frozen=True)
class OrganizationOverview:
    organization: Organization
    role: UserRole
    member_count: int


class OrganizationService:
    def __init__(
        self,
        organizations: OrganizationRepository,
        audit: AuditLogRepository,
    ) -> None:
        self._organizations = organizations
        self._audit = audit

    # ------------------------------ 读：我的组织 ------------------------------

    def overview(self, context: CurrentUserContext) -> OrganizationOverview:
        """当前账号的组织概览。没有组织 → `OrganizationNotFoundError`（不是空对象）。

        返回空对象会让前端画出「组织：（空）」这种界面，用户以为系统坏了；
        明确报错才能让前端说人话：「你的账号还没有加入任何组织」。
        """
        if context.organization is None:
            raise OrganizationNotFoundError("current user has no organization")
        organization = self._organizations.get_by_id(context.organization.id)
        if organization is None:  # 成员关系还在、组织被删了：数据不一致，如实报错
            raise OrganizationNotFoundError(context.organization.id)
        return OrganizationOverview(
            organization=organization,
            role=context.role,
            member_count=self._organizations.count_members(organization.id),
        )

    # ------------------------------ 读：成员列表 ------------------------------

    def list_members(
        self, context: CurrentUserContext, organization_id: str
    ) -> list[tuple[OrganizationMember, User]]:
        """成员列表。跨组织直接拒绝（`CrossOrganizationError` → 403）。"""
        self._ensure_same_organization(context, organization_id)
        return list(self._organizations.list_members(organization_id))

    # ----------------------------- 写：改角色 -------------------------------

    def change_role(
        self,
        context: CurrentUserContext,
        *,
        organization_id: str,
        target_user_id: str,
        new_role: UserRole,
        ip: str | None = None,
    ) -> None:
        self._ensure_same_organization(context, organization_id, ip=ip)
        if target_user_id == context.user.id:
            self._deny(context, organization_id, target_user_id, "self_role_change", ip)
            raise CannotChangeOwnRoleError(target_user_id)
        if new_role is UserRole.PLATFORM_ADMIN:
            # 平台管理员只能由运维在库里授予，任何端点都不许把它发出去。
            self._deny(context, organization_id, target_user_id, "grant_platform_admin", ip)
            raise InvalidRoleAssignmentError(str(new_role))
        member = self._organizations.get_member_in_organization(
            organization_id=organization_id, user_id=target_user_id
        )
        if member is None:
            raise MemberNotFoundError(target_user_id)
        previous = member.role
        if previous == str(new_role):
            # 幂等：不改就是成功，但**不留一条无意义的审计**（否则审计会被空转刷满）。
            return
        self._organizations.set_member_role(member, str(new_role))
        self._audit.record(
            action=AuditAction.MEMBER_ROLE_CHANGE,
            actor_user_id=context.user.id,
            organization_id=organization_id,
            target_type="user",
            target_id=target_user_id,
            detail=f'{{"from":"{previous}","to":"{new_role}"}}',
            ip=ip,
        )
        self._audit.commit()

    # ------------------------------ 读：审计日志 ------------------------------

    def list_audit_logs(
        self,
        context: CurrentUserContext,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[AuditLog], int]:
        """当前组织的审计日志。

        `platform_admin` 没有组织归属，本批也不给它跨组织视图：
        给一个能读全校日志的入口，却没有「谁批准了这次读取」的记录，本身就是风险。
        它要跨组织时先补一篇 ADR（`ADR-021` §3.3）。
        """
        if context.organization is None:
            raise OrganizationNotFoundError("current user has no organization")
        organization_id = context.organization.id
        entries = self._audit.list_recent(
            organization_id=organization_id, limit=limit, offset=offset
        )
        total = self._audit.count(organization_id=organization_id)
        return entries, total

    # -------------------------------- 内部 ---------------------------------

    def _ensure_same_organization(
        self,
        context: CurrentUserContext,
        organization_id: str,
        *,
        ip: str | None = None,
    ) -> None:
        if context.organization is None or context.organization.id != organization_id:
            self._deny(context, organization_id, context.user.id, "cross_organization", ip)
            raise CrossOrganizationError(organization_id)

    def _deny(
        self,
        context: CurrentUserContext,
        organization_id: str,
        target_id: str,
        reason: str,
        ip: str | None,
    ) -> None:
        self._audit.record(
            action=AuditAction.MEMBER_ROLE_CHANGE,
            actor_user_id=context.user.id,
            organization_id=context.organization.id if context.organization else None,
            target_type="user",
            target_id=target_id,
            result=AuditResult.DENIED,
            detail=f'{{"reason":"{reason}","requested_organization":"{organization_id}"}}',
            ip=ip,
        )
        self._audit.commit()
