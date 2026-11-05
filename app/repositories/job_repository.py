"""`jobs` / `job_skills` 的映射与仓储（Phase 4 · B1，ADR-011 1.B / 1.D）。

表结构由 Alembic 迁移建立（`20261105_0011_jobs`、`20261105_0012_job_skills`），
本模块只做映射。本仓没有 `models/` 目录，ORM 声明与仓储实现同文件，
与 `student_profile_repository.py` / `resume_repository.py` 的形状一致。

**两张表为什么不拆成两个文件**：`job_skills` 是岗位的组成部分，它的每一条读路径
都要跟着 `jobs` 一起走（详情要带技能、PUT 要先删后插），拆开只会把一次事务拆成两处。

两条写入口径：

- 归属判定**写进函数签名**（`viewer_id` / `user_id` 必带），而不是指望调用方
  再比一次 —— 与 `student_profile_repository.get_by_id()` 同一做法；
- 内容写入是「父表字段覆盖 + 子表先删后插」，语义对齐 PUT。

读侧的两个入口对应 ADR-011 1.F 的两类权限：

- `get_readable()`：`published` 人人可读、`draft` 仅创建者，全部落在**一个 SQL 谓词**里。
  这里刻意不做「先按 id 查出来再在服务层比 status/user_id」——那等于把
  「漏判一次就泄露别人的草稿」留给服务层的纪律来兜，而纪律不是约束；
- `get_owned()`：改 / 删 / 发布 / 下架 / 回收站，只有创建者，SQL 直接带 `created_by_user_id`。

`origin` 与 `status` 都不由调用方决定：`status` 由服务层给（创建恒 `draft`），
`origin` 由服务层按 ADR-011 1.D 派生（`evidence_text` 归一化后是否是 JD 原文的子串），
两者都到不了请求体。三列提取溯源戳同样由服务层盖（1.F v9 第 ② 条），
本文件只负责把它们写进对应的列。

过期判定**不在这里**：SQL 里不出现数据库侧的当日函数（`CURDATE`/`CURRENT_DATE`），
「今天」由服务层用应用时钟算好后作为绑定参数传进来（ADR-011 2.G）。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    ColumnElement,
    Date,
    DateTime,
    Numeric,
    String,
    Text,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.datetime_display import utc_now

#: 岗位状态（1.B 的两态）。`draft` 不进广场，`published` 全量可读。
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"

#: 技能条目的「能否回溯到 JD 原文」标记（1.D 只有两个值；v7 值域改名，方案 a）。
#:
#: **写哪个值由服务层按 1.D 的派生规则决定**（`evidence_text` 经 §2.D.1 归一化后
#: 是否是 `description` 归一化文本的子串），既不采信客户端提交的 `origin`，
#: 也不采信模型自报的任何标记。C1 之前本轮任何写入都恒为 `ORIGIN_UNVERIFIED`，
#: 是因为那时还没有可核验的锚点（`evidence_text` 写不进来）。
#: 之所以只有两个值的 CHECK：这一列的语义（「这句话能否回溯到 JD 原文」）从建表那天起
#: 就是完整的，而第三个值 `user_edited` 已被 v7 删除（客户端可伪造的标记等于没有标记）。
#: 值名刻意不带「谁写的」含义：这一列只能证明「在 JD 原文里出现过」，
#: 旧的「AI 提取 / 手动」两个值名会把逐字粘贴原文的手动条目误标成 AI 产出（1.D v7）。
ORIGIN_UNVERIFIED = "unverified"
ORIGIN_JD_VERIFIABLE = "jd_verifiable"

#: 单个岗位的技能条目上限。与 ADR-011 2.B 的「AI 输出条数上限 15」同一数字：
#: 手动录入与 AI 提取共用一个上限，避免出现「AI 能存 15 条、人只能存 10 条」这类
#: 无法解释的差异。超出由请求模型（`app/schemas/job.py`）报 422。
MAX_SKILLS_PER_JOB = 15


def _staggered(now: datetime, index: int) -> datetime:
    """给同一批插入的子行错开 1 微秒。

    `list_skills()` 按 `created_at, id` 排序，而一批子行共享同一个 `utc_now()`：
    微秒相同时 MySQL 只能退化到按随机 UUID 排序，「录入时的技能顺序」每次读都不一样。
    与 `student_profile_repository._staggered()` 同一做法（上限 15 条，不会溢出）。
    """
    return now + timedelta(microseconds=index)


class JobBase(DeclarativeBase):
    """岗位域的 declarative base；与 `ProfileBase` / `Base` 互不干扰。"""


class Job(JobBase):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    #: 创建者。由 `get_current_user` 写入，绝不采信请求体（1.F）。
    created_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 存而不筛（1.G）。
    location: Mapped[str | None] = mapped_column(String(160), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: JD 原文；应用层上限 8000 字（2.F，由请求模型拦成 422）。
    description: Mapped[str] = mapped_column(Text, nullable=False)
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: 自然语言（「3 年以上后端经验」），本轮不筛（1.G）。
    experience_required: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: 截止时间。`NULL` = 长期有效，永远出现在默认列表里（2.G）。
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_DRAFT)
    # 三列提取溯源戳：由服务层在保存时盖（ADR-011 v9 第 ② 条），客户端提交不到这里。
    extract_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    extract_prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 软删除时间戳：`NULL` = 正常，非 `NULL` = 回收站（与 0007 同口径）。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # 本映射**不声明** CHECK / 外键 / 唯一约束：本仓不用 `create_all`，DDL 的真相
    # 只有 Alembic 迁移（AGENTS.md §4.2）。在映射里重抄一份约束，将来只会和迁移漂移。


class JobSkill(JobBase):
    __tablename__ = "job_skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: 可空：AI 的分级不可信，宁可为空让用户填（1.D）。
    type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: 服务端派生，禁止出现在请求体（1.D / 1.F）。
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default=ORIGIN_UNVERIFIED)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    #: JD 原文片段，§2.D 的核验锚点，也是 `origin` 派生的输入（60 字上限见 2.B）。
    evidence_text: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


@dataclass(frozen=True)
class JobDraft:
    """仓储入参：不依赖 Pydantic，避免仓储层感知 API 模型。

    **不含 `status` / `created_by_user_id`**：前者由服务层显式给（创建恒 `draft`、
    发布/下架是独立动作），后者只能来自 `get_current_user`。
    放在这里是为了让「请求体够不到这两列」变成类型层面的事实。

    **含 `extract_model` / `extract_prompt_version` / `extracted_at`（ADR-011 v9 第 ② 条）**：
    这三列 C1 起真的会被写，但写什么由服务层算好交下来（「本次保存里只要有任何一条
    `jd_verifiable` 就盖戳，否则三列 `NULL`」是业务规则，不是存储细节），
    而请求体里根本没有这三格（`JobWriteRequest` 靠 `extra="ignore"` 整键丢掉）。
    也就是说：仓储拿得到它们，客户端拿不到——这正是 v9 要的分工。
    """

    company_name: str
    title: str
    location: str | None
    employment_type: str | None
    description: str
    salary_min: Decimal | None
    salary_max: Decimal | None
    experience_required: str | None
    deadline: date | None
    #: 三列提取溯源戳；服务层在没有可回溯条目时传 `None`。
    extract_model: str | None
    extract_prompt_version: str | None
    extracted_at: datetime | None


@dataclass(frozen=True)
class JobSkillDraft:
    """一条岗位技能。

    `origin` 由**服务层按 1.D 派生**后传下来（`evidence_text` 归一化后是否是 JD 原文的子串），
    请求模型里没有这一格，客户端提交的 `origin` 一律整键丢掉——「可回溯原文」这个徽标
    因此不可伪造。ADR-011 v6 曾把它写死成 `ORIGIN_UNVERIFIED`（那时还没有可核验的锚点），
    C1 起派生成立，写死的理由随之消失。

    `confidence` / `evidence_text` **依据 ADR-011 v9 放开**：它们跟着用户保存的
    提取结果落库（列宽 60 与 `MAX_EVIDENCE_CHARS` 同一个数，越界已在 Pydantic 层报成 422）。
    """

    name: str
    type: str | None
    origin: str
    confidence: Decimal | None
    evidence_text: str | None


class JobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------ 读

    def get_readable(self, job_id: str, viewer_id: str) -> Job | None:
        """按「1.F #2 的可见性」取一行：`published` 人人可读，`draft` 仅创建者。

        回收站里的行（`deleted_at` 非空）一律不可读 —— 它与「不存在」同样返回 404。
        """
        return self._session.scalar(
            select(Job)
            .where(Job.id == job_id, Job.deleted_at.is_(None))
            .where(self._visibility_clause(viewer_id))
        )

    def get_owned(
        self,
        job_id: str,
        user_id: str,
        include_deleted: bool = False,
    ) -> Job | None:
        """按「用户 + 岗位 id」取一行：改 / 删 / 发布 / 下架 / 恢复 的前置校验。

        **必须同时带 `user_id`**：这是防水平越权的唯一落点，不能先按 id 查出来
        再在服务层比一次。非创建者走到这里返回 `None`，服务层抛
        `JobNotFoundError`，对外 404（不是 403，见 1.F）。
        """
        statement = select(Job).where(Job.id == job_id, Job.created_by_user_id == user_id)
        if not include_deleted:
            statement = statement.where(Job.deleted_at.is_(None))
        return self._session.scalar(statement)

    def list_jobs(
        self,
        *,
        viewer_id: str,
        status: str,
        keyword: str | None,
        today: date,
        include_expired: bool,
        offset: int,
        limit: int,
    ) -> list[Job]:
        """广场/草稿列表。`status` 决定看到谁的岗位（1.F #1），`today` 来自应用时钟（2.G）。

        - `status=published`：全体创作者的已发布岗位；
        - `status=draft`：**仅本人**的草稿（别人的草稿永远不出现在你的列表里）；
        - 默认隐藏 `deadline < today` 的过期岗位，`include_expired=True` 时一起返回；
          `deadline IS NULL` 视为长期有效，永远出现。
          比较值由服务层传入，SQL 里只有绑定参数，没有 `CURDATE`/`CURRENT_DATE`。

        排序：`created_at DESC`，主键 uuid4 作 tie-breaker（同一秒录入的岗位顺序也要稳定）。
        """
        conditions = self._filters(
            viewer_id=viewer_id,
            status=status,
            keyword=keyword,
            today=today,
            include_expired=include_expired,
            # 列表不掺回收站：软删的行由 `list_trash()` 单独回答。
            include_deleted=False,
        )
        statement = (
            select(Job).where(*conditions).order_by(Job.created_at.desc(), Job.id.desc())
        )
        return list(self._session.scalars(statement.offset(offset).limit(limit)))

    def count_jobs(
        self,
        *,
        viewer_id: str,
        status: str,
        keyword: str | None,
        today: date,
        include_expired: bool,
    ) -> int:
        """与 `list_jobs()` 完全同谓词的总数（分页 `meta.total`）。

        两者共用 `_filters()`，避免出现「列表有 3 条、total 说 5 条」这种
        只有翻页才发现的漂移。
        """
        conditions = self._filters(
            viewer_id=viewer_id,
            status=status,
            keyword=keyword,
            today=today,
            include_expired=include_expired,
            include_deleted=False,
        )
        total = self._session.scalar(select(func.count()).select_from(Job).where(*conditions))
        return int(total or 0)

    def list_trash(self, user_id: str) -> list[Job]:
        """回收站：只返回**自己的**软删岗位，最近删除的排前面（1.F #8）。

        过滤在 SQL 里做（`created_by_user_id = :user_id`），不是「查出全部再前端过滤」。
        """
        return list(
            self._session.scalars(
                select(Job)
                .where(Job.created_by_user_id == user_id, Job.deleted_at.is_not(None))
                .order_by(Job.deleted_at.desc(), Job.id.desc())
            )
        )

    def list_skills(self, job_id: str) -> list[JobSkill]:
        """技能条目按录入顺序返回（`created_at` 由 `_staggered()` 保证稳定）。"""
        return list(
            self._session.scalars(
                select(JobSkill)
                .where(JobSkill.job_id == job_id)
                .order_by(JobSkill.created_at, JobSkill.id)
            )
        )

    # ------------------------------------------------------------------ 写

    def create_job(
        self,
        created_by_user_id: str,
        draft: JobDraft,
        status: str,
        skills: Sequence[JobSkillDraft],
    ) -> Job:
        """岗位 + 技能子行**一次事务**写完。

        为什么不在这里分两次提交：分两次的话，子表撞 `uq_job_skills_job_name`（1062）
        时岗位父行已经落库，用户看到一条 500、库里留下一条「没有技能的岗位」，
        而那条脏数据只有直接操作数据库才能清（`MVP_SCOPE.md` §9 没有后台）。
        父行 `flush()` 拿 id、子行 add 完再一起 `commit()`，失败整体回滚。

        `deleted_at=None`：新建一定不在回收站。
        """
        now = utc_now()
        job = Job(
            id=str(uuid4()),
            created_by_user_id=created_by_user_id,
            company_name=draft.company_name,
            title=draft.title,
            location=draft.location,
            employment_type=draft.employment_type,
            description=draft.description,
            salary_min=draft.salary_min,
            salary_max=draft.salary_max,
            experience_required=draft.experience_required,
            deadline=draft.deadline,
            status=status,
            # 三列溯源戳由服务层算好交下来：没有可回溯条目时就是三个 None。
            extract_model=draft.extract_model,
            extract_prompt_version=draft.extract_prompt_version,
            extracted_at=draft.extracted_at,
            deleted_at=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(job)
        self._session.flush()
        self._add_skills(job.id, skills, now)
        self._session.commit()
        self._session.refresh(job)
        return job

    def replace_job(
        self,
        job: Job,
        draft: JobDraft,
        skills: Sequence[JobSkillDraft],
    ) -> Job:
        """父表字段覆盖 + 子表先删后插，整体一次提交（PUT 语义）。

        `status` / `deleted_at` 不在这里改：发布与下架是独立动作（1.F #6），
        创建与编辑都不是它们的副作用。把它们混进 PUT，
        就会让「编辑一份草稿」顺带把岗位推上广场。

        先删后插（而不是 diff）与 `replace_profile_content()` 一致：
        技能条目没有稳定业务主键，diff 的复杂度换不来可验证的收益。

        三列溯源戳**跟着这一次提交重算**（服务层给值），不是「只在有值时覆盖」：
        PUT 会把技能子表整批换掉，若上一次保存盖过戳而这一次一条都不可回溯，
        留着旧戳就是在为一批已经不在库里的技能声称「出自 JD 原文」。
        """
        now = utc_now()
        job.company_name = draft.company_name
        job.title = draft.title
        job.location = draft.location
        job.employment_type = draft.employment_type
        job.description = draft.description
        job.salary_min = draft.salary_min
        job.salary_max = draft.salary_max
        job.experience_required = draft.experience_required
        job.deadline = draft.deadline
        job.extract_model = draft.extract_model
        job.extract_prompt_version = draft.extract_prompt_version
        job.extracted_at = draft.extracted_at
        job.updated_at = now
        self._session.execute(delete(JobSkill).where(JobSkill.job_id == job.id))
        self._add_skills(job.id, skills, now)
        self._session.commit()
        return job

    # ---------------------------------------------------------------- 内部

    def _add_skills(self, job_id: str, skills: Sequence[JobSkillDraft], now: datetime) -> None:
        """写技能子行；**不提交**，由调用方决定事务边界。

        `origin` 由服务层按 1.D 派生后传下来（见 `JobSkillDraft`），这里只落值：
        本文件不认识请求体，所以「客户端伪造可回溯徽标」在这层根本没有入口。

        `confidence` / `evidence_text` 依据 ADR-011 v9 放开，同样来自 `JobSkillDraft`
        （B1 那一轮把这两格写死成 `NULL`，注释里标的是「本轮写死」；提取链路接通后
        该前提不再成立）。越界值不会走到这里：`JobSkillInput` 已按 422 拦下，
        而 `ck_job_skills_confidence` 是最后一道数据库闸门。
        """
        for index, skill in enumerate(skills):
            self._session.add(
                JobSkill(
                    id=str(uuid4()),
                    job_id=job_id,
                    name=skill.name,
                    type=skill.type,
                    origin=skill.origin,
                    confidence=skill.confidence,
                    evidence_text=skill.evidence_text,
                    created_at=_staggered(now, index),
                    updated_at=now,
                )
            )

    def update_status(self, job_id: str, user_id: str, status: str) -> None:
        """发布 / 下架。调用方**必须**先用 `get_owned(job_id, user_id)` 校验归属。

        `WHERE created_by_user_id = :user_id` 再兜一层：就算调用方漏判，
        别人的岗位也改不动（写侧的第二道锁）。
        """
        self._session.execute(
            update(Job)
            .where(Job.id == job_id, Job.created_by_user_id == user_id)
            .values(status=status, updated_at=utc_now())
        )
        self._session.commit()

    def soft_delete(self, job_id: str, user_id: str) -> None:
        """软删除（进回收站）。调用方**必须**先 `get_owned()` 校验归属。

        子表不动：`job_skills` 与岗位一起留在库里，恢复后内容必须还在。
        """
        self._session.execute(
            update(Job)
            .where(Job.id == job_id, Job.created_by_user_id == user_id)
            .values(deleted_at=utc_now())
        )
        self._session.commit()

    def restore(self, job_id: str, user_id: str) -> None:
        """从回收站恢复（`deleted_at` 回到 NULL）。同上，调用方先校验归属。"""
        self._session.execute(
            update(Job)
            .where(Job.id == job_id, Job.created_by_user_id == user_id)
            .values(deleted_at=None, updated_at=utc_now())
        )
        self._session.commit()

    # ---------------------------------------------------------------- 内部

    def _visibility_clause(self, viewer_id: str) -> ColumnElement[bool]:
        """1.F #1/#2 的可见性谓词：已发布的谁都能看，草稿只有创建者能看。"""
        return or_(
            Job.status == STATUS_PUBLISHED,
            Job.created_by_user_id == viewer_id,
        )

    def _filters(
        self,
        *,
        viewer_id: str,
        status: str,
        keyword: str | None,
        today: date,
        include_expired: bool,
        include_deleted: bool,
    ) -> list[ColumnElement[bool]]:
        """列表与计数共用的谓词（同一条规则只写一次）。"""
        conditions: list[ColumnElement[bool]] = [Job.status == status]
        if status == STATUS_DRAFT:
            # 草稿是私有数据：只加创建者自己的，别人的草稿不进任何列表。
            conditions.append(Job.created_by_user_id == viewer_id)
        if not include_deleted:
            conditions.append(Job.deleted_at.is_(None))
        if keyword and keyword.strip():
            pattern = keyword.strip()
            conditions.append(
                or_(
                    Job.company_name.contains(pattern, autoescape=True),
                    Job.title.contains(pattern, autoescape=True),
                )
            )
        if not include_expired:
            # 2.G：`deadline IS NULL` 视为长期有效；`deadline >= 今天` 尚未过期。
            conditions.append(or_(Job.deadline.is_(None), Job.deadline >= today))
        return conditions
