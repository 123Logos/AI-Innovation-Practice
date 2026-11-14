"""教师端工具的只读仓储（FR-08 / B10a、B10b，`ADR-029`）。

**只读**：本模块没有 add / update / delete。看板不产生业务数据；导出和下载也不改库，
唯一的写入是服务层那边的一条审计（走 `AuditLogRepository`）。

跨三套 declarative base 取数（`ProfileBase` / `JobBase` / `AssessmentBase`）时用显式
join 条件，不依赖 ORM relationship：本仓的 DDL 真相在 Alembic，映射里刻意不声明
关系（`AGENTS.md` §4.2），靠 relationship 会把「哪张表是父」再写一份。

归属判定**收在 SQL 里**：`organization_members.organization_id` 是本批唯一的范围边界，
服务层不自己比一遍「这个学生是不是我们组织的」——比漏一次就是跨组织泄露，
而那种泄露不会报错。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repositories.assessment_repository import ProfileTag
from app.repositories.job_repository import STATUS_PUBLISHED, Job, JobSkill
from app.repositories.organization_repository import OrganizationMember
from app.repositories.resume_repository import Resume
from app.repositories.student_profile_repository import Skill, StudentProfile


@dataclass(frozen=True)
class ResumeDocument:
    """一条可导出的简历版本（含拼文件名与打印尺寸所需的最小信息）。"""

    resume_id: str
    profile_id: str
    title: str
    full_name: str
    version_no: int
    paper_size: str
    content_html: str


class TeacherToolsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------ 范围

    def student_profile_ids(self, organization_id: str) -> list[str]:
        """本组织学生（role='student'）的**未删除**档案 id。

        教师 / 管理员本人也可能有档案，他们不算「学生」，不进聚合分母。
        """
        statement = (
            select(StudentProfile.id)
            .join(
                OrganizationMember,
                OrganizationMember.user_id == StudentProfile.user_id,
            )
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.role == "student",
                StudentProfile.deleted_at.is_(None),
            )
            .distinct()
        )
        return list(self._session.scalars(statement))

    # ------------------------------------------------------------------ 看板

    def tag_rows(self, profile_ids: list[str]) -> list[tuple[str, str, str]]:
        """`(tag, status, student_profile_id)`；只取本组织范围内的档案。"""
        if not profile_ids:
            return []
        statement = select(ProfileTag.tag, ProfileTag.status, ProfileTag.student_profile_id).where(
            ProfileTag.student_profile_id.in_(profile_ids)
        )
        return [tuple(row) for row in self._session.execute(statement).all()]  # type: ignore[misc]

    def skills_by_profile(self, profile_ids: list[str]) -> dict[str, list[str]]:
        """每个档案的**活跃**技能名（软删除的历史行不算）。"""
        if not profile_ids:
            return {}
        statement = select(Skill.student_profile_id, Skill.name).where(
            Skill.student_profile_id.in_(profile_ids),
            Skill.deleted_at.is_(None),
        )
        grouped: dict[str, list[str]] = {}
        for profile_id, name in self._session.execute(statement).all():
            grouped.setdefault(str(profile_id), []).append(str(name))
        return grouped

    def published_jobs_with_skills(self, organization_id: str) -> list[dict[str, Any]]:
        """本组织已发布岗位 + 每条岗位要求的技能名。

        跨组织的岗位不进概况：岗位广场是公开的，但「我们班和它的匹配度」不是。
        """
        jobs = list(
            self._session.scalars(
                select(Job)
                .where(
                    Job.organization_id == organization_id,
                    Job.status == STATUS_PUBLISHED,
                )
                .order_by(Job.created_at, Job.id)
            )
        )
        if not jobs:
            return []
        job_ids = [job.id for job in jobs]
        skill_rows = self._session.execute(
            select(JobSkill.job_id, JobSkill.name).where(JobSkill.job_id.in_(job_ids))
        ).all()
        grouped: dict[str, list[str]] = {}
        for job_id, name in skill_rows:
            grouped.setdefault(str(job_id), []).append(str(name))
        return [
            {
                "job_id": str(job.id),
                "title": str(job.title),
                "required_skills": grouped.get(str(job.id), []),
                "no_requirements": not grouped.get(str(job.id)),
            }
            for job in jobs
        ]

    # ------------------------------------------------------------------ 导出

    def _documents(
        self, organization_id: str, resume_ids: list[str] | None
    ) -> list[ResumeDocument]:
        """按 id 取简历，**在 SQL 层**钉死「属于本组织学生」。

        不在服务层先查再比：那会先把别人的简历对象读进内存，任何一次漏判都会把
        `content_html` 直接带到响应里。
        """
        if resume_ids is not None and not resume_ids:
            return []
        statement = (
            select(
                Resume.id,
                Resume.student_profile_id,
                Resume.title,
                Resume.version_no,
                Resume.content_html,
                StudentProfile.full_name,
                StudentProfile.paper_size,
            )
            .join(StudentProfile, StudentProfile.id == Resume.student_profile_id)
            .join(
                OrganizationMember,
                OrganizationMember.user_id == StudentProfile.user_id,
            )
        )
        conditions = [
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == "student",
            StudentProfile.deleted_at.is_(None),
        ]
        if resume_ids is not None:
            conditions.insert(0, Resume.id.in_(resume_ids))
        # 不在 SQL 里 `ORDER BY resumes.created_at`：`SELECT DISTINCT` 的列里没有它，
        # MySQL 8 会报 3065（ORDER BY 表达式不在 SELECT 列表）。批次清单本来就会
        # 按调用方给的 id 顺序重排，这里的库内顺序不构成契约。
        statement = statement.where(*conditions).distinct()
        return [
            ResumeDocument(
                resume_id=str(row[0]),
                profile_id=str(row[1]),
                title=str(row[2]),
                version_no=int(row[3]),
                content_html=str(row[4]),
                full_name=str(row[5]),
                paper_size=str(row[6]),
            )
            for row in self._session.execute(statement).all()
        ]

    def one_document(self, organization_id: str, resume_id: str) -> ResumeDocument | None:
        found = self._documents(organization_id, [resume_id])
        return next((item for item in found if item.resume_id == resume_id), None)

    def all_documents(self, organization_id: str) -> list[ResumeDocument]:
        """本组织所有可导出的简历版本（看板页的下载清单，不含正文）。"""
        return self._documents(organization_id, None)

    def documents(
        self, organization_id: str, resume_ids: list[str]
    ) -> tuple[list[ResumeDocument], list[str]]:
        """返回 `(可导出的, 被跳过的 id)`；顺序按请求的 `resume_ids`，不按库返回顺序。

        调用方要把跳过项写进 manifest 与审计，而不是当成「没发生」。
        """
        found = {item.resume_id: item for item in self._documents(organization_id, resume_ids)}
        available: list[ResumeDocument] = []
        skipped: list[str] = []
        for resume_id in resume_ids:
            item = found.get(resume_id)
            if item is None:
                skipped.append(resume_id)
            else:
                available.append(item)
        return available, skipped
