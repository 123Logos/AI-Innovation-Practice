"""`interview_sessions` / `interview_turns` 的映射与仓储（FR-07 / B11a，ADR-022）。

表结构由 Alembic 迁移建立（`20261112_0019_interview_sessions`），本模块只做映射。
本仓没有 `models/` 目录，ORM 声明与仓储实现同文件，与 `job_repository.py` 形状一致。

**两张表为什么不拆成两个文件**：一条面试会话的全部读路径（列表、详情回放、追加轮次）
都要一起走，拆开只会把一次事务拆成两处。

归属判定**写进函数签名**（`user_id` / `viewer_id` 必带），而不是指望调用方再比一次——
与 `job_repository` 的 `get_owned` / `get_readable` 同一做法：水平越权在 SQL 层就被挡死，
不会出现「先按 id 查出来再在服务层比一次」这种漏判就泄露别人会话的写法。

本映射**不声明** CHECK / 外键 / 唯一约束：本仓不用 `create_all`，DDL 的真相只有 Alembic
迁移（AGENTS.md §4.2）。在映射里重抄一份约束，将来只会和迁移漂移。
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, delete, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.datetime_display import utc_now

#: 会话状态（两态）。`active` 进行中，`ended` 已结束。
STATUS_ACTIVE = "active"
STATUS_ENDED = "ended"

#: 发言方。
SPEAKER_INTERVIEWER = "interviewer"
SPEAKER_CANDIDATE = "candidate"

#: 单轮上下文送入模型的最大轮数（ADR-022 (d)）。超出部分截断，首轮系统背景不计入。
MAX_CONTEXT_TURNS = 10


class InterviewBase(DeclarativeBase):
    """面试域的 declarative base；与 `JobBase` / `ProfileBase` / `Base` 互不干扰。"""


class InterviewSession(InterviewBase):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    #: 所有者。由 `get_current_user` 写入，绝不采信请求体。
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 可选绑定的目标岗位；可空，FK 指向 jobs.id（RESTRICT，见迁移）。
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_ACTIVE)
    #: 用户填写的目标岗位名（可空）；与 job_id 二选一或互补，决定出题方向。
    target_role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InterviewTurn(InterviewBase):
    __tablename__ = "interview_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 该会话内从 1 递增的序号；详情按它顺序回放。
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `interviewer` 或 `candidate`。
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 文本正文。
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: AI 失败时这一轮 interviewer 内容由本地降级模板产出，此处记录原因；
    #: 非空即代表「此轮非真 AI」（ADR-022 (b) 的数据层证据）。
    ai_degrade_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class InterviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------ 写

    def create_session(
        self,
        user_id: str,
        job_id: str | None,
        target_role: str | None,
    ) -> InterviewSession:
        """新建一场进行中的会话；子表由调用方随后追加轮次。"""
        now = utc_now()
        session = InterviewSession(
            id=str(uuid4()),
            user_id=user_id,
            job_id=job_id,
            status=STATUS_ACTIVE,
            target_role=target_role,
            created_at=now,
            updated_at=now,
            ended_at=None,
        )
        self._session.add(session)
        self._session.flush()
        return session

    def add_turn(
        self,
        session_id: str,
        sequence: int,
        speaker: str,
        content: str,
        ai_degrade_reason: str | None = None,
    ) -> InterviewTurn:
        now = utc_now()
        turn = InterviewTurn(
            id=str(uuid4()),
            session_id=session_id,
            sequence=sequence,
            speaker=speaker,
            content=content,
            ai_degrade_reason=ai_degrade_reason,
            created_at=now,
        )
        self._session.add(turn)
        self._session.flush()
        return turn

    def next_sequence(self, session_id: str) -> int:
        """下一个序号：现有最大序号 + 1；还没有轮次则为 1。"""
        current = self._session.scalar(
            select(func.coalesce(func.max(InterviewTurn.sequence), 0)).where(
                InterviewTurn.session_id == session_id
            )
        )
        return int(current or 0) + 1

    def touch(self, session_id: str) -> None:
        """更新会话的 `updated_at`（追加轮次 / 结束时调用）。"""
        self._session.execute(
            InterviewSession.__table__.update()  # type: ignore[attr-defined]
            .where(InterviewSession.id == session_id)
            .values(updated_at=utc_now())
        )

    def end_session(self, session_id: str, user_id: str) -> None:
        """结束会话；调用方**必须**先 `get_owned()` 校验归属。

        `WHERE user_id = :user_id` 再兜一层：就算调用方漏判，别人的会话也改不动。
        """
        self._session.execute(
            InterviewSession.__table__.update()  # type: ignore[attr-defined]
            .where(InterviewSession.id == session_id, InterviewSession.user_id == user_id)
            .values(status=STATUS_ENDED, ended_at=utc_now(), updated_at=utc_now())
        )

    # ------------------------------------------------------------------ 读

    def get_owned(self, session_id: str, user_id: str) -> InterviewSession | None:
        """按「用户 + 会话 id」取会话：详情 / 追加 / 结束的前置校验。

        **必须同时带 `user_id`**：防水平越权的唯一落点，不能先按 id 查出来再在服务层比一次。
        非所有者走到这里返回 `None`，服务层抛 404（不是 403，见 ADR-022 / `job_repository`）。
        """
        return self._session.scalar(
            select(InterviewSession).where(
                InterviewSession.id == session_id, InterviewSession.user_id == user_id
            )
        )

    def list_owned(self, user_id: str, offset: int, limit: int) -> list[InterviewSession]:
        """当前用户自己的会话列表，按最近更新倒序。"""
        return list(
            self._session.scalars(
                select(InterviewSession)
                .where(InterviewSession.user_id == user_id)
                .order_by(InterviewSession.updated_at.desc(), InterviewSession.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )

    def count_owned(self, user_id: str) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(InterviewSession)
                .where(InterviewSession.user_id == user_id)
            )
            or 0
        )

    def list_turns(self, session_id: str) -> list[InterviewTurn]:
        """按序号顺序回放一轮面试。"""
        return list(
            self._session.scalars(
                select(InterviewTurn)
                .where(InterviewTurn.session_id == session_id)
                .order_by(InterviewTurn.sequence)
            )
        )

    def list_turns_recent(self, session_id: str, limit: int) -> list[InterviewTurn]:
        """取最近 `limit` 轮（上下文截断用，ADR-022 (d)）。

        先按序号倒序取最近 N 条，再在内存里正序排回，避免子查询里的 `ORDER BY ... LIMIT`。
        """
        recent = list(
            self._session.scalars(
                select(InterviewTurn)
                .where(InterviewTurn.session_id == session_id)
                .order_by(InterviewTurn.sequence.desc())
                .limit(limit)
            )
        )
        return list(reversed(recent))

    def delete_session_turns(self, session_id: str) -> None:
        """结束/归档时若需整体清理轮次（本批未启用；保留以备后续 B11b 重开会话）。"""
        self._session.execute(delete(InterviewTurn).where(InterviewTurn.session_id == session_id))
