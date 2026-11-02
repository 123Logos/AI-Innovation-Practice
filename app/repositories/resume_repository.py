"""`resumes` 表的 SQLAlchemy 映射与仓储（真实 MySQL）。

与 `student_profile_repository` 共用 `ProfileBase` registry，避免同一张库出现多套映射。
本轮只有写入需求（生成即落库），读取接口待简历列表任务一并交付。
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.student_profile_repository import ProfileBase


class Resume(ProfileBase):
    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    student_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # DDL 为 LONGTEXT；映射用 Text，只为读写绑定，不参与 DDL 生成。
    content_html: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResumeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_resume(
        self,
        student_profile_id: str,
        title: str,
        content_html: str,
    ) -> Resume:
        now = datetime.now(UTC).replace(tzinfo=None)
        resume = Resume(
            id=str(uuid4()),
            student_profile_id=student_profile_id,
            title=title,
            content_html=content_html,
            created_at=now,
            updated_at=now,
        )
        self._session.add(resume)
        self._session.commit()
        self._session.refresh(resume)
        return resume
