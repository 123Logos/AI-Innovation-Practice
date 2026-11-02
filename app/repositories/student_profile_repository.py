"""`student_profiles` / `skills` / `experiences` 的 SQLAlchemy 映射与仓储。

表结构由 `alembic/versions/20261023_0001_initial_phase1.py` 建立，本模块只做映射。
写入采用「父表 upsert + 子表全量替换」，语义对齐 PUT。
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime, SmallInteger, String, Text, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class ProfileBase(DeclarativeBase):
    """档案域的 declarative base；`resumes` 表复用同一 registry（见 resume_repository）。"""


def utc_now() -> datetime:
    """MySQL DATETIME 无时区，统一存 UTC 的 naive 时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


class StudentProfile(ProfileBase):
    __tablename__ = "student_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    major: Mapped[str] = mapped_column(String(100), nullable=False)
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


@dataclass(frozen=True)
class ProfileDraft:
    """仓储入参：不依赖 Pydantic，避免仓储层感知 API 模型。"""

    full_name: str
    major: str
    grade: str
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

    def replace_profile(
        self,
        user_id: str,
        profile: ProfileDraft,
        skills: Sequence[SkillDraft],
        experiences: Sequence[ExperienceDraft],
    ) -> StudentProfile:
        """父表不存在则建、存在则改；子表先删后插，整体一次提交。"""
        now = utc_now()
        target = self.get_by_user_id(user_id)
        if target is None:
            target = StudentProfile(
                id=str(uuid4()),
                user_id=user_id,
                created_at=now,
                updated_at=now,
                **asdict(profile),
            )
            self._session.add(target)
            self._session.flush()
        else:
            target.full_name = profile.full_name
            target.major = profile.major
            target.grade = profile.grade
            target.contact = profile.contact
            target.updated_at = now
            self._session.flush()

        self._session.execute(delete(Skill).where(Skill.student_profile_id == target.id))
        self._session.execute(
            delete(Experience).where(Experience.student_profile_id == target.id)
        )
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
        self._session.commit()
        return target
