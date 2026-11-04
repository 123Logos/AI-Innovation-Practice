"""简历生成业务逻辑：按登录用户取**指定那份**简历 → 交给生成器 → 落库。

与 T5 的差别：不再接收请求体里的档案数据，也不再写内存仓储。
与 D4.1 之前的差别：一个用户有多份简历，`ResumeGenerateRequest.profile_id`
指定用哪一份生成；不传则用默认简历（兼容只发 `title` 的旧前端）。
"""

from app.core.document_style import normalize_theme
from app.integrations.resume_generator import ResumeGenerator
from app.repositories.resume_repository import ResumeRepository
from app.repositories.student_profile_repository import StudentProfile, StudentProfileRepository
from app.schemas.profile import EducationInput
from app.schemas.resume import (
    ExperienceInput,
    ProfileInput,
    ResumeGenerateRequest,
    ResumeRead,
    SkillInput,
)
from app.services.profile_service import ProfileNotFoundError


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
        profile = self._resolve_profile(user_id, payload)
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
            skills=[SkillInput(name=skill.name, self_rating=skill.self_rating) for skill in skills],
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
            # 配色跟着**这份简历**走（D5.2）：生成物把主题写成内联 CSS 变量，
            # 导出 / 分享出去也带着配色，不依赖页面样式表。
            theme=normalize_theme(profile.theme),
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

    def _resolve_profile(self, user_id: str, payload: ResumeGenerateRequest) -> StudentProfile:
        """定位要生成的那份简历。

        - 传了 `profile_id`：必须是**当前用户的、未删除的**那份，否则 404
          （查不到与不属于自己共用同一个 404，避免探测他人 id）；
        - 没传：退回默认简历，一份都没有则 400「档案不完整」。
        """
        if payload.profile_id:
            profile = self._profile_repository.get_by_id(user_id, payload.profile_id)
            if profile is None:
                raise ProfileNotFoundError(payload.profile_id)
            return profile
        profile = self._profile_repository.get_by_user_id(user_id)
        if profile is None:
            raise ProfileIncompleteError(user_id)
        return profile
