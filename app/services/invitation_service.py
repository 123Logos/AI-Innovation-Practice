"""组织邀请的业务逻辑（批次 B6，`ADR-023`）。

本层不接触 HTTP：错误以域异常抛出，由 API 层统一翻译（与 `organization_service` 同构）。

**两级判断**（`ADR-021` §2.3 的延续）：发码 / 列表 / 撤销由路由层判权限，
本层只判「这条邀请是不是你这个组织的」；兑换只需登录，边界靠
「码有效 + 该账号尚无组织」两条业务规则，不靠角色。

**审计与业务同事务**：发码、撤销、兑换都在同一个 `session` 里写业务与审计，
由本层统一 `commit()`（`ADR-021` §2.5）。被拒绝的尝试单独记 `denied`，
那时业务没有任何写入，单独提交是安全的。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.datetime_display import utc_now
from app.repositories.audit_log_repository import (
    AuditAction,
    AuditLogRepository,
    AuditResult,
)
from app.repositories.invitation_repository import (
    InvitationRepository,
    OrganizationInvitation,
)
from app.repositories.organization_repository import Organization, OrganizationRepository
from app.services.auth_service import CurrentUserContext


class InvitationError(Exception):
    """邀请域错误基类。"""


class InvitationNotFoundError(InvitationError):
    """撤销时找不到这张邀请（或它不属于该组织）。"""


class InvitationUnusableError(InvitationError):
    """码不存在 / 已撤销 / 已过期 / 已用尽——四种情形**对外同义**。

    分开报就等于给了一枚「猜这张码是否存在」的探针（`ADR-023` §2.3）。
    """


class AlreadyInOrganizationError(InvitationError):
    """当前账号已有组织；`organization_members.user_id` 唯一键不允许二次加入。"""


class OrganizationMissingError(InvitationError):
    """当前账号没有组织，无法发码 / 列邀请 / 撤销。"""


class CrossOrganizationError(InvitationError):
    """目标组织不是当前账号所属的组织。"""


@dataclass(frozen=True)
class RedeemResult:
    organization: Organization
    role: str


class InvitationService:
    def __init__(
        self,
        invitations: InvitationRepository,
        organizations: OrganizationRepository,
        audit: AuditLogRepository,
    ) -> None:
        self._invitations = invitations
        self._organizations = organizations
        self._audit = audit

    # -------------------------------- 管理端 --------------------------------

    def create(
        self,
        context: CurrentUserContext,
        *,
        organization_id: str,
        role: str,
        max_uses: int,
        ttl_days: int,
        ip: str | None = None,
    ) -> OrganizationInvitation:
        self._ensure_same_organization(context, organization_id, ip=ip)
        invitation = self._invitations.create(
            organization_id=organization_id,
            created_by=context.user.id,
            role=role,
            max_uses=max_uses,
            ttl_days=ttl_days,
        )
        self._audit.record(
            action=AuditAction.INVITATION_CREATE,
            actor_user_id=context.user.id,
            organization_id=organization_id,
            target_type="invitation",
            target_id=invitation.id,
            detail=json.dumps(
                {"role": role, "max_uses": max_uses, "ttl_days": ttl_days},
                ensure_ascii=False,
            ),
            ip=ip,
        )
        self._invitations.commit()
        return invitation

    def list_for_organization(
        self,
        context: CurrentUserContext,
        organization_id: str,
        *,
        ip: str | None = None,
    ) -> list[OrganizationInvitation]:
        self._ensure_same_organization(context, organization_id, ip=ip)
        return self._invitations.list_for_organization(organization_id)

    def revoke(
        self,
        context: CurrentUserContext,
        *,
        organization_id: str,
        invitation_id: str,
        ip: str | None = None,
    ) -> OrganizationInvitation:
        self._ensure_same_organization(context, organization_id, ip=ip)
        invitation = self._invitations.get_in_organization(
            organization_id=organization_id, invitation_id=invitation_id
        )
        if invitation is None:
            raise InvitationNotFoundError(invitation_id)
        if invitation.revoked_at is not None:
            # 幂等：已经撤销就不重复写审计（否则审计会被空转刷满）。
            return invitation
        self._invitations.revoke(invitation, when=utc_now())
        self._audit.record(
            action=AuditAction.INVITATION_REVOKE,
            actor_user_id=context.user.id,
            organization_id=organization_id,
            target_type="invitation",
            target_id=invitation.id,
            ip=ip,
        )
        self._invitations.commit()
        return invitation

    # -------------------------------- 兑换端 --------------------------------

    def redeem(
        self,
        context: CurrentUserContext,
        code: str,
        *,
        ip: str | None = None,
    ) -> RedeemResult:
        """凭码加入组织。

        `FOR UPDATE` 锁住邀请行之后再校验次数与加入资格；两个请求同时兑换最后
        一张码时，不加锁会双双通过 `used_count < max_uses` 的读判断。
        """
        normalized = code.strip().upper()
        invitation = self._invitations.get_locked_by_code(normalized)
        now = utc_now()
        if (
            invitation is None
            or invitation.revoked_at is not None
            or invitation.expires_at <= now
            or invitation.used_count >= invitation.max_uses
        ):
            self._deny(context, "invalid_invitation", ip)
            raise InvitationUnusableError("invitation is not usable")

        if self._organizations.get_member(context.user.id) is not None:
            self._deny(context, "already_in_organization", ip)
            raise AlreadyInOrganizationError(context.user.id)

        organization = self._organizations.get_by_id(invitation.organization_id)
        if organization is None:
            # 成员关系还在、组织被删了：数据不一致，如实当成不可用，不猜一个组织。
            self._deny(context, "missing_organization", ip)
            raise InvitationUnusableError("organization missing")

        self._organizations.add_member(
            organization_id=invitation.organization_id,
            user_id=context.user.id,
            role=invitation.role,
        )
        self._invitations.consume(invitation)
        self._audit.record(
            action=AuditAction.INVITATION_REDEEM,
            actor_user_id=context.user.id,
            organization_id=invitation.organization_id,
            target_type="invitation",
            target_id=invitation.id,
            detail=json.dumps({"role": invitation.role}, ensure_ascii=False),
            ip=ip,
        )
        self._invitations.commit()
        return RedeemResult(organization=organization, role=invitation.role)

    # -------------------------------- 内部 ---------------------------------

    def _ensure_same_organization(
        self,
        context: CurrentUserContext,
        organization_id: str,
        *,
        ip: str | None = None,
    ) -> None:
        if context.organization is None:
            self._deny(context, "no_organization", ip)
            raise OrganizationMissingError(context.user.id)
        if context.organization.id != organization_id:
            self._deny(context, "cross_organization", ip)
            raise CrossOrganizationError(organization_id)

    def _deny(
        self,
        context: CurrentUserContext,
        reason: str,
        ip: str | None,
    ) -> None:
        self._audit.record(
            action=AuditAction.INVITATION_REDEEM,
            actor_user_id=context.user.id,
            organization_id=context.organization.id if context.organization else None,
            target_type="invitation",
            result=AuditResult.DENIED,
            detail=json.dumps({"reason": reason}, ensure_ascii=False),
            ip=ip,
        )
        self._audit.commit()
