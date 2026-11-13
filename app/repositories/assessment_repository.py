"""测评域的四张表映射与仓储（FR-04 / B9，`ADR-028`）。

表结构由 Alembic 迁移建立（`20261113_0026_assessments`），本模块只做映射。
本仓没有 `models/` 目录，ORM 声明与仓储实现同文件（与 `interview_repository.py` 形状一致）。

**四张表为什么不拆成四个文件**：一次提交要同时读题目、写答案、写会话、写标签，
拆开只会把一次事务拆成四处。

归属判定**写进函数签名**（`user_id` 必带），而不是让调用方再比一次：
与 `job_repository.get_owned` / `InterviewRepository.get_session` 同一做法——
水平越权在 SQL 层挡死，不会出现「先按 id 查出来再在服务层比一次」这种漏判就泄露别人会话的写法。

本映射**不声明** CHECK / 外键 / 唯一约束：本仓不用 `create_all`，DDL 的真相只有 Alembic
迁移（AGENTS.md §4.2）。在映射里重抄一份约束，将来只会和迁移漂移。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.datetime_display import utc_now

#: 会话状态（`ADR-028` §2.2）。三条迁移全部单向，终态任何写入都是 409。
STATUS_IN_PROGRESS = "in_progress"
STATUS_SUBMITTED = "submitted"
STATUS_ABANDONED = "abandoned"

#: 摘要来源（`ADR-028` §2.5）。`fallback` 必须能被前端与排障一眼看出来，
#: 所以它是一列而不是「文案长得像模板」这种推断。
SUMMARY_SOURCE_AI = "ai"
SUMMARY_SOURCE_FALLBACK = "fallback"

#: 标签来源。今天只有测评会写；`project` / `teacher` 是留位（`ADR-028` §2.4）。
TAG_SOURCE_ASSESSMENT = "assessment"


class AssessmentBase(DeclarativeBase):
    """测评域的 declarative base；与 `ProfileBase` / `JobBase` 等互不干扰。"""


class AssessmentQuestion(AssessmentBase):
    __tablename__ = "assessment_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    #: 归一后的技能键（`skill_name_key`）：与 B7 的技能同名口径一致，
    #: 「Python」「python」在同一组题里只算一个技能。
    skill_key: Mapped[str] = mapped_column(String(100), nullable=False)
    #: 展示用的原始写法（`Python` / `MySQL`）。
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt: Mapped[str] = mapped_column(String(500), nullable=False)
    #: `[{"key": "A", "label": "…"}, …]`。选项是「这道题的数据」不是独立实体，
    #: 没有任何跨题查询需要它（`ADR-028` §2.1 第 1 条），所以不单开表。
    options: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    #: 标准答案的 option key。**永远不进读响应**（`ADR-028` §2.1 第 2 条）。
    answer_key: Mapped[str] = mapped_column(String(4), nullable=False)
    bank_version: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 停用不删：历史会话仍要能解释「当时考的是什么」。
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AssessmentSession(AssessmentBase):
    __tablename__ = "assessment_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 画像挂**档案**而不是用户（`ADR-028` §2.4）：技能属于档案，一人多档案时
    #: 挂 user_id 会让「这份画像说的是哪份简历」无法回答。
    student_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 开考时的题库版本。题库会改，会话记下当时的版本，否则三个月前那份测评
    #: 在题库换过之后无法解释（`ADR-028` §2.1 第 3 条）。
    bank_version: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 这场会话**实际出过**的题 id（顺序即出题顺序）。
    #:
    #: `ADR-028` §2.2 原本只写了 `question_count`，实现时补了这一列：只存数量的话，
    #: 判分时就得「按当前题库重新取一遍题」，而题库随时可能改——那会让同一场会话
    #: 的分数取决于「什么时候点的提交」。存 id 之后，出过的题是**事实**，
    #: 数量也仍然可读（`len`）。
    question_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 判分结果：`in_progress` 时为 NULL（`ADR-028` §2.2）。
    correct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: AI 或降级模板写的一段话。
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `ai` / `fallback` / NULL（从未生成）。降级不许伪装成 AI（`ADR-028` §2.5）。
    ai_summary_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ai_summary_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AssessmentAnswer(AssessmentBase):
    __tablename__ = "assessment_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    question_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chosen_key: Mapped[str] = mapped_column(String(4), nullable=False)
    #: 提交时算的真假值。`NULL` = 还没判分（`in_progress`）。
    #: 存下来而不是每次现算：重复读同一场已提交的会话不该依赖题库。
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    answered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ProfileTag(AssessmentBase):
    __tablename__ = "profile_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    student_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 标签文本 = 技能名（原写法）。`UNIQUE(student_profile_id, tag)`：
    #: 重考应当**覆盖**同一标签，而不是让画像越长越胖（`ADR-028` §2.4）。
    tag: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 支撑这个标签的题数（今天 = 该技能的题数）。
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 这条标签来自哪一场会话：重考后覆盖成新会话的 id，排障时能顺回原始记录。
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AssessmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()

    # ------------------------------------------------------------------ 题库

    def latest_bank_version(self, active_only: bool = True) -> str | None:
        """当前题库版本：按 `created_at` 取最近一条（种子是 `v1`，扩库时新版本更晚）。

        为什么不按字符串排序（`v10` < `v2`）：版本号是给人读的，不是给字典序读的。
        """
        statement = select(AssessmentQuestion.bank_version).order_by(
            AssessmentQuestion.created_at.desc()
        )
        if active_only:
            statement = statement.where(AssessmentQuestion.is_active.is_(True))
        return self._session.scalars(statement.limit(1)).first()

    def list_questions(self, bank_version: str) -> list[AssessmentQuestion]:
        """某个版本的全部启用题，按「技能名 → 题 id」排序，保证出题顺序稳定。

        顺序必须稳定：同一场会话再次读取时题目顺序变了，用户会以为自己看错了题。
        """
        statement = (
            select(AssessmentQuestion)
            .where(
                AssessmentQuestion.bank_version == bank_version,
                AssessmentQuestion.is_active.is_(True),
            )
            .order_by(AssessmentQuestion.skill_name, AssessmentQuestion.id)
        )
        return list(self._session.scalars(statement))

    def questions_by_ids(self, question_ids: Sequence[str]) -> dict[str, AssessmentQuestion]:
        """按 id 批量取题（含已停用的：会话里出过的题要能解释，`ADR-028` §2.1 第 4 条）。"""
        if not question_ids:
            return {}
        rows = self._session.scalars(
            select(AssessmentQuestion).where(AssessmentQuestion.id.in_(list(question_ids)))
        )
        return {row.id: row for row in rows}

    # ------------------------------------------------------------------ 会话

    def create_session(
        self,
        *,
        user_id: str,
        student_profile_id: str,
        bank_version: str,
        question_ids: Sequence[str],
    ) -> AssessmentSession:
        now = utc_now()
        session = AssessmentSession(
            id=str(uuid4()),
            user_id=user_id,
            student_profile_id=student_profile_id,
            status=STATUS_IN_PROGRESS,
            bank_version=bank_version,
            question_ids=list(question_ids),
            question_count=len(question_ids),
            correct_count=None,
            score=None,
            started_at=now,
            submitted_at=None,
            abandoned_at=None,
            ai_summary=None,
            ai_summary_source=None,
            ai_summary_at=None,
        )
        self._session.add(session)
        return session

    def get_session(self, user_id: str, session_id: str) -> AssessmentSession | None:
        """按「本人 + id」取会话；别人的会话对当前用户与不存在同义（→ 404）。"""
        return self._session.scalars(
            select(AssessmentSession).where(
                AssessmentSession.id == session_id,
                AssessmentSession.user_id == user_id,
            )
        ).first()

    def latest_submitted(self, student_profile_id: str) -> AssessmentSession | None:
        """这份档案最近一次**已提交**的会话：画像摘要取它（`ADR-028` §2.4）。

        只看 `submitted`：`in_progress` 没有分，`abandoned` 没有标签。
        """
        return self._session.scalars(
            select(AssessmentSession)
            .where(
                AssessmentSession.student_profile_id == student_profile_id,
                AssessmentSession.status == STATUS_SUBMITTED,
            )
            .order_by(AssessmentSession.submitted_at.desc(), AssessmentSession.started_at.desc())
            .limit(1)
        ).first()

    # ------------------------------------------------------------------ 答案

    def list_answers(self, session_id: str) -> list[AssessmentAnswer]:
        return list(
            self._session.scalars(
                select(AssessmentAnswer)
                .where(AssessmentAnswer.session_id == session_id)
                .order_by(AssessmentAnswer.answered_at, AssessmentAnswer.id)
            )
        )

    def replace_answers(
        self, session_id: str, answers: Sequence[tuple[str, str]]
    ) -> list[AssessmentAnswer]:
        """批量覆盖答案（`ADR-028` §2.3：`UNIQUE(session_id, question_id)`，等价 UPSERT）。

        实现是「先删这次提交涉及的题，再插入」：两次点保存不会留下两行，
        而「改答案」与「首次作答」走同一条路径，少一个分支就少一个漏判。
        只删本次涉及的题——用户上一轮答过、这一轮没提交的题保持原样，
        因为批量覆盖的语义是「把这几题设成这些值」，不是「清空整张卷」。
        """
        question_ids = [question_id for question_id, _ in answers]
        now = utc_now()
        if question_ids:
            self._session.execute(
                delete(AssessmentAnswer).where(
                    AssessmentAnswer.session_id == session_id,
                    AssessmentAnswer.question_id.in_(question_ids),
                )
            )
        rows = [
            AssessmentAnswer(
                id=str(uuid4()),
                session_id=session_id,
                question_id=question_id,
                chosen_key=chosen_key,
                is_correct=None,
                answered_at=now,
            )
            for question_id, chosen_key in answers
        ]
        self._session.add_all(rows)
        return rows

    def grade_answers(self, session_id: str, correct_by_question: dict[str, bool]) -> None:
        """把判分结果写回答案行（提交时一次性完成）。"""
        for answer in self.list_answers(session_id):
            is_correct = correct_by_question.get(answer.question_id)
            if is_correct is not None:
                answer.is_correct = is_correct

    # ------------------------------------------------------------------ 标签

    def list_tags(self, student_profile_id: str) -> list[ProfileTag]:
        return list(
            self._session.scalars(
                select(ProfileTag)
                .where(ProfileTag.student_profile_id == student_profile_id)
                .order_by(ProfileTag.tag)
            )
        )

    def upsert_tags(
        self,
        *,
        student_profile_id: str,
        tags: Sequence[dict[str, object]],
        session_id: str,
    ) -> None:
        """按 `(student_profile_id, tag)` 覆盖写（重考不追加，`ADR-028` §2.4）。

        逐条查再改/插，而不是 `INSERT … ON DUPLICATE KEY UPDATE`：本仓没有方言级
        upsert 的其它用法，混进一种会让「写入路径只有一条」这件事失效；
        标签数量是个位数，多一次查询不影响任何事。
        """
        existing = {tag.tag: tag for tag in self.list_tags(student_profile_id)}
        now = utc_now()
        for item in tags:
            name = str(item["tag"])
            row = existing.get(name)
            if row is None:
                self._session.add(
                    ProfileTag(
                        id=str(uuid4()),
                        student_profile_id=student_profile_id,
                        tag=name,
                        status=str(item["status"]),
                        score=int(item["score"]),  # type: ignore[call-overload]
                        source=TAG_SOURCE_ASSESSMENT,
                        evidence_count=int(item["evidence_count"]),  # type: ignore[call-overload]
                        session_id=session_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
                continue
            row.status = str(item["status"])
            row.score = int(item["score"])  # type: ignore[call-overload]
            row.source = TAG_SOURCE_ASSESSMENT
            row.evidence_count = int(item["evidence_count"])  # type: ignore[call-overload]
            row.session_id = session_id
            row.updated_at = now

    # ------------------------------------------------------------------ 状态

    def mark_submitted(self, session: AssessmentSession, *, correct_count: int, score: int) -> None:
        now = utc_now()
        session.status = STATUS_SUBMITTED
        session.correct_count = correct_count
        session.score = score
        session.submitted_at = now

    def mark_abandoned(self, session: AssessmentSession) -> None:
        session.status = STATUS_ABANDONED
        session.abandoned_at = utc_now()

    def set_summary(self, session: AssessmentSession, *, text: str, source: str) -> None:
        session.ai_summary = text
        session.ai_summary_source = source
        session.ai_summary_at = utc_now()
