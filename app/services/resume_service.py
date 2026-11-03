"""简历生成业务逻辑：按登录用户从库中取档案 → 交给生成器 → 落库。

与 T5 的差别：不再接收请求体里的档案数据，也不再写内存仓储。
"""

from app.integrations.resume_generator import ResumeGenerator
from app.repositories.resume_repository import ResumeRepository
from app.repositories.student_profile_repository import StudentProfileRepository
from app.schemas.profile import EducationInput
from app.schemas.resume import (
    ExperienceInput,
    ProfileInput,
    ResumeGenerateRequest,
    ResumeRead,
    SkillInput,
)


class ProfileIncompleteError(Exception):
    """当前用户还没有可用于生成简历的档案（或缺技能/经历）。"""


class ResumeService:
    def __init__(
        self,
        profile_repository: StudentProfileRepository,
        resume_repository: ResumeRepository,
        generator: ResumeGenerator,
    ) -> None:
        self._profile_repository = profile_repository
        self._resume_repository = resume_repository
        self._generator = generator

    def generate(self, user_id: str, payload: ResumeGenerateRequest) -> ResumeRead:
        profile = self._profile_repository.get_by_user_id(user_id)
        if profile is None:
            raise ProfileIncompleteError(user_id)
        skills = self._profile_repository.list_skills(profile.id)
        experiences = self._profile_repository.list_experiences(profile.id)
        educations = self._profile_repository.list_educations(profile.id)
        # 保存是草稿式的（`ProfileUpsertRequest.full_name` / `contact` 都允许空），
        # 所以「基本信息填全了没有」必须在这里判，否则 pydantic 会在下面抛
        # ValidationError，冒到接口层变成 500 —— 而它其实是一个「档案不完整」的 400。
        if not profile.full_name.strip() or not profile.contact.strip():
            raise ProfileIncompleteError(user_id)
        if not skills or not experiences:
            raise ProfileIncompleteError(user_id)

        content_html = self._generator.generate(
            profile=ProfileInput(
                full_name=profile.full_name,
                contact=profile.contact,
                email=profile.email,
            ),
            skills=[
                SkillInput(name=skill.name, self_rating=skill.self_rating) for skill in skills
            ],
            experiences=[
                ExperienceInput(
                    title=experience.title,
                    start_date=experience.start_date,
                    end_date=experience.end_date,
                    description=experience.description,
                    responsibility=experience.responsibility,
                )
                for experience in experiences
            ],
            title=payload.title,
            educations=[
                EducationInput(
                    school=education.school,
                    major=education.major,
                    degree=education.degree,  # type: ignore[arg-type]
                    edu_start=education.edu_start,
                    edu_end=education.edu_end,
                )
                for education in educations
            ],
        )
        resume = self._resume_repository.create_resume(
            student_profile_id=profile.id,
            title=payload.title,
            content_html=content_html,
        )
        return ResumeRead(
            resume_id=resume.id,
            title=resume.title,
            content_html=resume.content_html,
            created_at=resume.created_at,
        )
