"""`student_profiles` / `skills` / `experiences` / `educations` 的映射与仓储。

表结构由 Alembic 迁移建立（`20261023_0001_initial_phase1` 建前四张，
`20261102_0002_educations` 建教育经历，`20261103_0007_multi_profiles` 放开
`user_id` 唯一约束并新增 `title` / `is_default` / `deleted_at`），本模块只做映射。

**D4.1 起一个用户可以有多份档案**（ADR-006 方案 C）：`student_profiles` 的每一行
就是一份简历，`skills` / `experiences` / `educations` / `resumes` 仍挂
`student_profile_id`，所以「一个用户多份简历」不需要新增任何表。

两条写入口径：

- 定位一份简历**必须**同时带 `user_id` 与 `profile_id`（`get_by_id`），
  把水平越权写进函数签名里，而不是指望调用方记得再比一次；
- 内容写入仍是「父表字段覆盖 + 子表先删后插」，语义对齐 PUT。

软删除（回收站）用 `deleted_at` 表达；`purge_expired()` 负责惰性物理清理
超过保留期的行（连带子表）。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    SmallInteger,
    String,
    Text,
    bindparam,
    delete,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.datetime_display import utc_now

#: 回收站保留天数：`deleted_at` 早于「现在 - 30 天」的记录会被惰性物理删除。
TRASH_RETENTION_DAYS = 30

#: 挂在 `student_profiles` 下的子表。外键全是 `ON DELETE RESTRICT`，
#: 物理删除父行前必须按这个顺序把子行清干净，否则 MySQL 报 1451。
_CHILD_TABLES = ("resumes", "educations", "experiences", "skills")


def _staggered(now: datetime, index: int) -> datetime:
    """给同一批插入的子行错开 1 微秒。

    `list_skills()` 按 `created_at, id` 排序，而一批子行共享同一个 `utc_now()`：
    微秒相同时 MySQL 只能退化到按随机 UUID 排序，「保存时的技能顺序」就变成
    每次读都不一样（`test_profile_contract` 断言的是保存顺序，会随机红）。
    错开 1 微秒即可让插入顺序稳定复现；技能上限 8 条，不存在溢出风险。
    """
    return now + timedelta(microseconds=index)


class ProfileBase(DeclarativeBase):
    """档案域的 declarative base；`resumes` 表复用同一 registry（见 resume_repository）。"""


class StudentProfile(ProfileBase):
    __tablename__ = "student_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    #: D4.1 起**不再唯一**：一个用户可以有 N 份简历。
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 这份简历的名字（「投后端」「投算法」）；新建时由服务层递增生成。
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 默认简历：`GET /profiles/me` 与不带 `profile_id` 的生成都取它。
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 非空即「在回收站里」；`purge_expired()` 到期后物理删除。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 简历上展示的邮箱；登录邮箱在 `users.email`，两者互不影响。
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    # T7.6：`major` 列已随迁移删除，专业只存在于 `educations.major`。
    grade: Mapped[str] = mapped_column(String(50), nullable=False)
    contact: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Skill(ProfileBase):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    student_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    self_rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Experience(ProfileBase):
    __tablename__ = "experiences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    student_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    responsibility: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Education(ProfileBase):
    __tablename__ = "educations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    student_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    school: Mapped[str] = mapped_column(String(200), nullable=False)
    major: Mapped[str] = mapped_column(String(100), nullable=False)
    degree: Mapped[str] = mapped_column(String(50), nullable=False)
    edu_start: Mapped[date] = mapped_column(Date, nullable=False)
    edu_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


@dataclass(frozen=True)
class ProfileDraft:
    """仓储入参：不依赖 Pydantic，避免仓储层感知 API 模型。

    T7.5 起不含 `grade`：年级字段已从产品下架，列保留但不再写业务值。
    T7.6 起不含 `major`：专业由 `educations.major` 表达，主表列已删除。
    """

    full_name: str
    email: str | None
    contact: str


@dataclass(frozen=True)
class SkillDraft:
    name: str
    self_rating: int


@dataclass(frozen=True)
class ExperienceDraft:
    title: str
    start_date: date
    end_date: date | None
    description: str
    responsibility: str


@dataclass(frozen=True)
class EducationDraft:
    school: str
    major: str
    degree: str
    edu_start: date
    edu_end: date | None


class StudentProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # 定位：一份简历

    def get_by_id(
        self,
        user_id: str,
        profile_id: str,
        include_deleted: bool = False,
    ) -> StudentProfile | None:
        """按「用户 + 简历 id」取一份简历。

        **必须同时带 `user_id`**：这是防水平越权的唯一落点，不能先按 id 查出来
        再在服务层比 user_id —— 那样任何一处漏判就是别人的简历被读/改/删。

        `include_deleted=True` 只在回收站场景（恢复、彻底删除）使用。
        """
        statement = select(StudentProfile).where(
            StudentProfile.id == profile_id,
            StudentProfile.user_id == user_id,
        )
        if not include_deleted:
            statement = statement.where(StudentProfile.deleted_at.is_(None))
        return self._session.scalar(statement)

    def get_by_user_id(self, user_id: str) -> StudentProfile | None:
        """取「当前用户的默认简历」，没有默认时退回最新的一份未删除简历。

        兼容入口：`GET/PUT /profiles/me` 与不带 `profile_id` 的生成都走这里。
        """
        statement = (
            select(StudentProfile)
            .where(
                StudentProfile.user_id == user_id,
                StudentProfile.deleted_at.is_(None),
            )
            .order_by(
                StudentProfile.is_default.desc(),
                StudentProfile.created_at.desc(),
                StudentProfile.id.desc(),
            )
            .limit(1)
        )
        return self._session.scalar(statement)

    def list_by_user(
        self,
        user_id: str,
        include_deleted: bool = False,
        order_by: str = "updated_at",
        descending: bool = True,
    ) -> list[StudentProfile]:
        """列表；默认排除回收站里的记录。

        排序（D4.3）：`order_by` 只接受 `updated_at` / `created_at`，
        由 API 层的 `Literal` 先做白名单校验，这里再用 if 分支取列——不做字符串拼 SQL。
        默认「最近修改的在前」；`descending=False` 时主键与 tie-breaker 一起反向，
        保证 `asc` 恰好是 `desc` 的倒序（前端切换箭头时顺序可预期）。

        开头顺带跑一次 `purge_expired()`：回收站没有后台定时任务，惰性清理挂在
        读路径上，用户下次打开列表时过期记录就消失了。
        """
        self.purge_expired()
        statement = select(StudentProfile).where(StudentProfile.user_id == user_id)
        if not include_deleted:
            statement = statement.where(StudentProfile.deleted_at.is_(None))
        # 主键是 uuid4，仅作确定性 tie-breaker：同一毫秒创建的简历顺序也要稳定。
        column = (
            StudentProfile.created_at if order_by == "created_at" else StudentProfile.updated_at
        )
        id_column = StudentProfile.id
        ordering = (
            (column.desc(), id_column.desc()) if descending else (column.asc(), id_column.asc())
        )
        return list(self._session.scalars(statement.order_by(*ordering)))

    def list_trash(self, user_id: str) -> list[StudentProfile]:
        """回收站：只返回软删除的记录，最近删除的排前面。"""
        self.purge_expired()
        statement = (
            select(StudentProfile)
            .where(
                StudentProfile.user_id == user_id,
                StudentProfile.deleted_at.is_not(None),
            )
            .order_by(StudentProfile.deleted_at.desc(), StudentProfile.id.desc())
        )
        return list(self._session.scalars(statement))

    def has_default(self, user_id: str) -> bool:
        """当前用户是否还持有一份「未删除的默认简历」。"""
        statement = (
            select(StudentProfile.id)
            .where(
                StudentProfile.user_id == user_id,
                StudentProfile.is_default.is_(True),
                StudentProfile.deleted_at.is_(None),
            )
            .limit(1)
        )
        return self._session.scalar(statement) is not None

    # 子表读取

    def list_skills(self, student_profile_id: str) -> list[Skill]:
        statement = (
            select(Skill)
            .where(Skill.student_profile_id == student_profile_id)
            .order_by(Skill.created_at, Skill.id)
        )
        return list(self._session.scalars(statement))

    def list_experiences(self, student_profile_id: str) -> list[Experience]:
        statement = (
            select(Experience)
            .where(Experience.student_profile_id == student_profile_id)
            .order_by(Experience.start_date.desc(), Experience.id)
        )
        return list(self._session.scalars(statement))

    def list_educations(self, student_profile_id: str) -> list[Education]:
        """按入学时间倒序：最近的一段学历排在最前（与简历渲染顺序一致）。"""
        statement = (
            select(Education)
            .where(Education.student_profile_id == student_profile_id)
            .order_by(Education.edu_start.desc(), Education.id)
        )
        return list(self._session.scalars(statement))

    # 写：新建

    def create_profile(self, user_id: str, title: str, is_default: bool) -> StudentProfile:
        """建一份空简历（基本信息与子表都为空，等用户填）。

        `full_name` / `grade` / `contact` 在库里是 `NOT NULL`，但产品允许空草稿，
        所以统一落空串 —— 与 `replace_profile_content()` 的口径一致。
        """
        now = utc_now()
        profile = StudentProfile(
            id=str(uuid4()),
            user_id=user_id,
            title=title,
            is_default=is_default,
            deleted_at=None,
            full_name="",
            email=None,
            grade="",
            contact="",
            created_at=now,
            updated_at=now,
        )
        self._session.add(profile)
        self._session.commit()
        self._session.refresh(profile)
        return profile

    def duplicate_profile(
        self,
        source: StudentProfile,
        user_id: str,
        title: str,
        is_default: bool,
    ) -> StudentProfile:
        """服务端深拷贝：基本信息 + 全部技能 / 经历 / 教育，一次事务写完。

        子行全部重新分配 id，所以「改副本」不会回写到源简历。
        `resumes`（已生成物）不复制 —— 新简历应当是「未生成」状态。
        """
        now = utc_now()
        target = StudentProfile(
            id=str(uuid4()),
            user_id=user_id,
            title=title,
            is_default=is_default,
            deleted_at=None,
            full_name=source.full_name,
            email=source.email,
            grade=source.grade,
            contact=source.contact,
            created_at=now,
            updated_at=now,
        )
        self._session.add(target)
        self._session.flush()

        for index, skill in enumerate(self.list_skills(source.id)):
            self._session.add(
                Skill(
                    id=str(uuid4()),
                    student_profile_id=target.id,
                    name=skill.name,
                    self_rating=skill.self_rating,
                    created_at=_staggered(now, index),
                    updated_at=now,
                )
            )
        for index, experience in enumerate(self.list_experiences(source.id)):
            self._session.add(
                Experience(
                    id=str(uuid4()),
                    student_profile_id=target.id,
                    title=experience.title,
                    start_date=experience.start_date,
                    end_date=experience.end_date,
                    description=experience.description,
                    responsibility=experience.responsibility,
                    created_at=_staggered(now, index),
                    updated_at=now,
                )
            )
        for index, education in enumerate(self.list_educations(source.id)):
            self._session.add(
                Education(
                    id=str(uuid4()),
                    student_profile_id=target.id,
                    school=education.school,
                    major=education.major,
                    degree=education.degree,
                    edu_start=education.edu_start,
                    edu_end=education.edu_end,
                    created_at=_staggered(now, index),
                    updated_at=now,
                )
            )
        self._session.commit()
        self._session.refresh(target)
        return target

    # 写：覆盖

    def replace_profile_content(
        self,
        profile: StudentProfile,
        draft: ProfileDraft,
        skills: Sequence[SkillDraft],
        experiences: Sequence[ExperienceDraft],
        educations: Sequence[EducationDraft],
    ) -> StudentProfile:
        """父表字段覆盖 + 子表先删后插，整体一次提交（PUT 语义）。

        `grade` 已从产品中下架：列在库里保留（NOT NULL），但前后端都不再写入业务值，
        统一落空串，避免「一个没人维护的字段」继续漂移。
        `title` / `is_default` / `deleted_at` 不在这里改 —— 它们属于简历元信息，
        由创建 / 设默认 / 软删除各自的方法负责。
        """
        now = utc_now()
        profile.full_name = draft.full_name
        profile.email = draft.email
        profile.contact = draft.contact
        profile.updated_at = now
        self._session.flush()

        self._session.execute(delete(Skill).where(Skill.student_profile_id == profile.id))
        self._session.execute(delete(Experience).where(Experience.student_profile_id == profile.id))
        self._session.execute(delete(Education).where(Education.student_profile_id == profile.id))
        for index, skill in enumerate(skills):
            self._session.add(
                Skill(
                    id=str(uuid4()),
                    student_profile_id=profile.id,
                    name=skill.name,
                    self_rating=skill.self_rating,
                    created_at=_staggered(now, index),
                    updated_at=now,
                )
            )
        for index, experience in enumerate(experiences):
            self._session.add(
                Experience(
                    id=str(uuid4()),
                    student_profile_id=profile.id,
                    title=experience.title,
                    start_date=experience.start_date,
                    end_date=experience.end_date,
                    description=experience.description,
                    responsibility=experience.responsibility,
                    created_at=_staggered(now, index),
                    updated_at=now,
                )
            )
        for index, education in enumerate(educations):
            self._session.add(
                Education(
                    id=str(uuid4()),
                    student_profile_id=profile.id,
                    school=education.school,
                    major=education.major,
                    degree=education.degree,
                    edu_start=education.edu_start,
                    edu_end=education.edu_end,
                    created_at=_staggered(now, index),
                    updated_at=now,
                )
            )
        self._session.commit()
        return profile

    def set_default(self, user_id: str, profile_id: str) -> None:
        """把某一份设为默认，同时把该用户其它未删除简历的 `is_default` 清零。

        MySQL 没有「部分唯一索引」，所以「每人至多一条默认」只能由这里保证：
        先全清再置一，同一个事务里完成。
        """
        self._session.execute(
            update(StudentProfile)
            .where(
                StudentProfile.user_id == user_id,
                StudentProfile.deleted_at.is_(None),
            )
            .values(is_default=False)
        )
        self._session.execute(
            update(StudentProfile)
            .where(
                StudentProfile.id == profile_id,
                StudentProfile.user_id == user_id,
            )
            .values(is_default=True)
        )
        self._session.commit()

    # 写：元信息（标题）

    def title_exists(self, user_id: str, title: str, exclude_id: str | None = None) -> bool:
        """同一用户名下是否已有同名简历（D4.3：简历不能重名）。

        只在**未删除**的简历里比较：回收站那份不占名字，否则「删掉的简历」会
        永久锁住一个标题，用户想改回原名反而被拒。
        排序规则由库的 `utf8mb4_0900_ai_ci` 决定（大小写不敏感），这正是我们要的语义。
        """
        statement = select(StudentProfile.id).where(
            StudentProfile.user_id == user_id,
            StudentProfile.deleted_at.is_(None),
            StudentProfile.title == title,
        )
        if exclude_id is not None:
            statement = statement.where(StudentProfile.id != exclude_id)
        return self._session.scalar(statement) is not None

    def update_title(self, profile_id: str, title: str) -> None:
        """改标题，并顺带推进 `updated_at`（重命名也是一次修改）。

        调用方**必须**先用 `get_by_id(user_id, ...)` 校验归属与重名。
        """
        self._session.execute(
            update(StudentProfile)
            .where(StudentProfile.id == profile_id)
            .values(title=title, updated_at=utc_now())
        )
        self._session.commit()

    # 写：回收站

    def soft_delete(self, profile_id: str) -> None:
        """软删除（进回收站）。调用方**必须**先用 `get_by_id(user_id, ...)` 校验归属。"""
        self._session.execute(
            update(StudentProfile)
            .where(StudentProfile.id == profile_id)
            .values(deleted_at=utc_now())
        )
        self._session.commit()

    def restore(self, profile_id: str) -> None:
        """从回收站恢复。调用方**必须**先校验归属。"""
        self._session.execute(
            update(StudentProfile).where(StudentProfile.id == profile_id).values(deleted_at=None)
        )
        self._session.commit()

    def hard_delete(self, profile_id: str) -> None:
        """物理删除：先清四张子表再删父行。调用方**必须**先校验归属。"""
        self._delete_rows([profile_id])
        self._session.commit()

    def purge_expired(self, retention_days: int = TRASH_RETENTION_DAYS) -> int:
        """物理清理进回收站超过保留期的简历，返回清理条数。

        由 `list_by_user()` / `list_trash()` 在读取前顺带触发（惰性清理），
        因此没有后台任务也不会让回收站无限增长。
        """
        cutoff = utc_now() - timedelta(days=retention_days)
        expired = list(
            self._session.scalars(
                select(StudentProfile.id).where(
                    StudentProfile.deleted_at.is_not(None),
                    StudentProfile.deleted_at < cutoff,
                )
            )
        )
        if not expired:
            return 0
        self._delete_rows(expired)
        self._session.commit()
        return len(expired)

    def _delete_rows(self, profile_ids: Sequence[str]) -> None:
        """按 `_CHILD_TABLES` 顺序清子行，再删父行（外键 RESTRICT，顺序不能颠倒）。"""
        ids = list(profile_ids)
        if not ids:
            return
        for table in _CHILD_TABLES:
            self._session.execute(
                text(f"DELETE FROM {table} WHERE student_profile_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
        self._session.execute(
            text("DELETE FROM student_profiles WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": ids},
        )
