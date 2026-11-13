"""组织邀请码的映射与仓储（批次 B6，`ADR-023` §2.1 / §2.3）。

两条纪律：

1. **写入不 commit**：发码、撤销、兑换都要与审计日志在同一个事务里提交，
   提交点属于服务层（与 `audit_log_repository` / `organization_repository` 同构）。
2. **兑换路径必须行锁**：`get_locked_by_code()` 用 `FOR UPDATE` 锁住邀请行再校验
   `used_count`，否则两个并发请求会同时通过「还有一次」的读判断，一张码被用两次。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from secrets import token_urlsafe
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.datetime_display import utc_now
from app.repositories.audit_log_repository import AuditAction, AuditLog, AuditResult
from app.repositories.user_repository import Base, User

#: 邀请默认有效期与次数上限，与 `app/schemas/invitation.py` 的 Field 约束对齐。
DEFAULT_TTL_DAYS = 7
MAX_USES_LIMIT = 100


class OrganizationInvitation(Base):
    __tablename__ = "organization_invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="student")
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class InvitationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        organization_id: str,
        created_by: str,
        role: str,
        max_uses: int,
        ttl_days: int,
    ) -> OrganizationInvitation:
        now = utc_now()
        invitation = OrganizationInvitation(
            id=str(uuid4()),
            organization_id=organization_id,
            # `token_urlsafe` 是 base64url 字符集；upper 后仍只含 A-Z0-9-_ ，
            # 与 B5 组织码「落库前 strip().upper()」的归一先例一致。
            code=token_urlsafe(12).upper(),
            role=role,
            max_uses=max_uses,
            used_count=0,
            expires_at=now + timedelta(days=ttl_days),
            created_by=created_by,
            revoked_at=None,
            created_at=now,
        )
        self._session.add(invitation)
        self._session.flush()
        return invitation

    def get_locked_by_code(self, code: str) -> OrganizationInvitation | None:
        """按码取邀请并加行锁。**只在兑换路径调用**（`ADR-023` §2.3）。"""
        return self._session.scalar(
            select(OrganizationInvitation)
            .where(OrganizationInvitation.code == code)
            .with_for_update()
        )

    def list_for_organization(self, organization_id: str) -> list[OrganizationInvitation]:
        return list(
            self._session.scalars(
                select(OrganizationInvitation)
                .where(OrganizationInvitation.organization_id == organization_id)
                .order_by(
                    OrganizationInvitation.created_at.desc(),
                    OrganizationInvitation.id.desc(),
                )
            )
        )

    def get_in_organization(
        self, *, organization_id: str, invitation_id: str
    ) -> OrganizationInvitation | None:
        """撤销前的归属校验写在 SQL 谓词里，不靠「先查出来再比一次」。"""
        return self._session.scalar(
            select(OrganizationInvitation).where(
                OrganizationInvitation.id == invitation_id,
                OrganizationInvitation.organization_id == organization_id,
            )
        )

    def revoke(self, invitation: OrganizationInvitation, *, when: datetime) -> None:
        invitation.revoked_at = when
        self._session.flush()

    def consume(self, invitation: OrganizationInvitation) -> None:
        invitation.used_count += 1
        self._session.flush()

    def redeemed_usernames(self, invitation_ids: list[str]) -> dict[str, list[str]]:
        """按邀请码 id 分组，返回兑换过它的用户名（第 18 轮裁定 ④）。

        来源是 `invitation.redeem` 审计行，不是邀请表上的新列：一张码可以
        `max_uses > 1`，兑换者天然是一对多，写进一列只能记住最后一个人。
        被拒绝的尝试（`result=denied`）不算兑换，不展示给发码人。
        """
        if not invitation_ids:
            return {}
        rows = self._session.execute(
            select(AuditLog.target_id, User.username)
            .join(User, User.id == AuditLog.actor_user_id)
            .where(
                AuditLog.action == str(AuditAction.INVITATION_REDEEM),
                AuditLog.result == str(AuditResult.OK),
                AuditLog.target_id.in_(invitation_ids),
            )
            .order_by(AuditLog.id)
        ).all()
        grouped: dict[str, list[str]] = {}
        for invitation_id, username in rows:
            if invitation_id is not None:
                grouped.setdefault(invitation_id, []).append(username)
        return grouped

    def commit(self) -> None:
        self._session.commit()
