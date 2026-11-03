"""`student_profiles` / `skills` / `experiences` / `educations` 的映射与仓储。

表结构由 Alembic 迁移建立（`20261023_0001_initial_phase1` 建前四张，
`20261102_0002_educations` 建教育经历），本模块只做映射。
写入采用「父表 upsert + 子表全量替换」，语义对齐 PUT。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime, SmallInteger, String, Text, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.datetime_display import utc_now


class ProfileBase(DeclarativeBase):
    """档案域的 declarative base；`resumes` 表复用同一 registry（见 resume_repository）。"""


class StudentProfile(ProfileBase):
    __tablename__ = "student_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
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

    def get_by_user_id(self, user_id: str) -> StudentProfile | None:
        return self._session.scalar(
            select(StudentProfile).where(StudentProfile.user_id == user_id)
        )

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

    def replace_profile(
        self,
        user_id: str,
        profile: ProfileDraft,
        skills: Sequence[SkillDraft],
        experiences: Sequence[ExperienceDraft],
        educations: Sequence[EducationDraft],
    ) -> StudentProfile:
        """父表不存在则建、存在则改；子表先删后插，整体一次提交。

        `grade` 已从产品中下架：列在库里保留（NOT NULL），但前后端都不再写入业务值，
        统一落空串，避免「一个没人维护的字段」继续漂移。
        `major` 列已在 T7.6 删除，专业改由 `educations` 承载。
        """
        now = utc_now()
        target = self.get_by_user_id(user_id)
        if target is None:
            target = StudentProfile(
                id=str(uuid4()),
                user_id=user_id,
                created_at=now,
                updated_at=now,
                full_name=profile.full_name,
                email=profile.email,
                grade="",
                contact=profile.contact,
            )
            self._session.add(target)
            self._session.flush()
        else:
            target.full_name = profile.full_name
            target.email = profile.email
            target.contact = profile.contact
            target.updated_at = now
            self._session.flush()

        self._session.execute(delete(Skill).where(Skill.student_profile_id == target.id))
        self._session.execute(
            delete(Experience).where(Experience.student_profile_id == target.id)
        )
        self._session.execute(delete(Education).where(Education.student_profile_id == target.id))
        for skill in skills:
            self._session.add(
                Skill(
                    id=str(uuid4()),
                    student_profile_id=target.id,
                    name=skill.name,
                    self_rating=skill.self_rating,
                    created_at=now,
                    updated_at=now,
                )
            )
        for experience in experiences:
            self._session.add(
                Experience(
                    id=str(uuid4()),
                    student_profile_id=target.id,
                    title=experience.title,
                    start_date=experience.start_date,
                    end_date=experience.end_date,
                    description=experience.description,
                    responsibility=experience.responsibility,
                    created_at=now,
                    updated_at=now,
                )
            )
        for education in educations:
            self._session.add(
                Education(
                    id=str(uuid4()),
                    student_profile_id=target.id,
                    school=education.school,
                    major=education.major,
                    degree=education.degree,
                    edu_start=education.edu_start,
                    edu_end=education.edu_end,
                    created_at=now,
                    updated_at=now,
                )
            )
        self._session.commit()
        return target
