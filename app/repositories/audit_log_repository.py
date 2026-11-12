"""审计日志的映射与仓储（批次 B5，`docs/adr/ADR-021` §2.5）。

**只追加**：本模块没有 update / delete 方法，表上也没有 `updated_at`——可改的审计日志
不是审计日志。

写入**不 commit**，由服务层与业务同一个事务提交：若审计写失败而业务成功，
就会出现一次不可追溯的角色变更，那正是审计要防的事。代价是审计表不可用时业务也不可用，
这个代价刻意接受（同一库同一实例，它挂了业务本来也活不了）。

`action` 是白名单枚举而不是自由字符串：自由字符串会在半年后长成 40 种拼法，
而「按操作类型统计」这件事从此不可做。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.datetime_display import utc_now
from app.repositories.user_repository import Base


class AuditAction(StrEnum):
    """本批允许写入的 action。新增动作必须同时加进这里，不许在调用点写字面量。"""

    USER_REGISTER = "user.register"
    USER_LOGIN = "user.login"
    ORGANIZATION_CREATE = "organization.create"
    MEMBER_JOIN = "member.join"
    MEMBER_ROLE_CHANGE = "member.role_change"
    # 批次 B6（ADR-023）：邀请的发码 / 兑换 / 撤销。
    INVITATION_CREATE = "invitation.create"
    INVITATION_REDEEM = "invitation.redeem"
    INVITATION_REVOKE = "invitation.revoke"


class AuditResult(StrEnum):
    OK = "ok"
    #: 被拒绝的越权尝试**也要记**：这是「谁在什么时候试图做什么」的唯一证据。
    DENIED = "denied"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result: Mapped[str] = mapped_column(String(10), nullable=False, default="ok")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AuditLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        action: AuditAction,
        actor_user_id: str | None = None,
        organization_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        result: AuditResult = AuditResult.OK,
        detail: str | None = None,
        ip: str | None = None,
    ) -> AuditLog:
        """写一条审计。**不提交**——提交点属于调用它的业务事务。"""
        entry = AuditLog(
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            action=str(action),
            target_type=target_type,
            target_id=target_id,
            result=str(result),
            detail=detail,
            ip=ip,
            created_at=utc_now(),
        )
        self._session.add(entry)
        self._session.flush()
        return entry

    def list_recent(
        self, *, organization_id: str | None, limit: int = 50, offset: int = 0
    ) -> list[AuditLog]:
        """按时间倒序翻页；`organization_id=None` 表示跨组织（仅平台管理员用得上）。

        `id` 作为第二排序键：同一毫秒内写入的多条记录（批量邀请）否则顺序不稳定，
        翻页时会出现「第二页看到第一页的最后一条」。
        """
        statement = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        if organization_id is not None:
            statement = statement.where(AuditLog.organization_id == organization_id)
        return list(self._session.scalars(statement.limit(limit).offset(offset)))

    def count(self, *, organization_id: str | None) -> int:
        statement = select(func.count()).select_from(AuditLog)
        if organization_id is not None:
            statement = statement.where(AuditLog.organization_id == organization_id)
        return int(self._session.scalar(statement) or 0)

    def commit(self) -> None:
        """只给「被拒绝的操作」用：那时业务没有任何写入，单独提交审计是安全的。

        正常路径不要调它——审计必须与业务同一次提交（ADR-021 §2.5），
        提前提交会让「业务回滚了、审计却留下了」变成可能。
        """
        self._session.commit()
