"""档案业务逻辑：读取当前用户档案、整体保存（PUT 语义）。

本层不接触 HTTP：未填写档案返回 None，由 API 层决定用空结构还是状态码表达。

T7.5：教育经历与 skills / experiences 一样走「先删后插」的全量替换，
不新增表外的任何状态；`grade` 已下架，不参与保存。
T7.6：主表 `major` 已删除，专业随每段教育经历保存。
"""

from app.repositories.student_profile_repository import (
    EducationDraft,
    ExperienceDraft,
    ProfileDraft,
    SkillDraft,
    StudentProfile,
    StudentProfileRepository,
)
from app.schemas.profile import (
    EducationRead,
    ExperienceRead,
    ProfileRead,
    ProfileUpsertRequest,
    SkillRead,
)


class ProfileService:
    def __init__(self, repository: StudentProfileRepository) -> None:
        self._repository = repository

    def get_profile(self, user_id: str) -> ProfileRead | None:
        profile = self._repository.get_by_user_id(user_id)
        if profile is None:
            return None
        return self._to_read(profile)

    def save_profile(self, user_id: str, payload: ProfileUpsertRequest) -> ProfileRead:
        self._repository.replace_profile(
            user_id,
            ProfileDraft(
                full_name=payload.full_name,
                email=payload.email,
                # 年级列仍在库里但产品已移除该字段：不再写入任何业务值（见仓储）。
                contact=payload.contact,
            ),
            [
                SkillDraft(name=skill.name, self_rating=skill.self_rating)
                for skill in payload.skills
            ],
            [
                ExperienceDraft(
                    title=experience.title,
                    start_date=experience.start_date,
                    end_date=experience.end_date,
                    description=experience.description,
                    responsibility=experience.responsibility,
                )
                for experience in payload.experiences
            ],
            [
                EducationDraft(
                    school=education.school,
                    major=education.major,
                    degree=education.degree,
                    edu_start=education.edu_start,
                    edu_end=education.edu_end,
                )
                for education in payload.educations
            ],
        )
        saved = self._repository.get_by_user_id(user_id)
        if saved is None:  # pragma: no cover - 写入成功时不可能发生
            raise RuntimeError("student profile was not persisted")
        return self._to_read(saved)

    def _to_read(self, profile: StudentProfile) -> ProfileRead:
        return ProfileRead(
            id=profile.id,
            user_id=profile.user_id,
            full_name=profile.full_name,
            email=profile.email,
            grade=profile.grade,
            contact=profile.contact,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            skills=[
                SkillRead.model_validate(skill)
                for skill in self._repository.list_skills(profile.id)
            ],
            experiences=[
                ExperienceRead.model_validate(experience)
                for experience in self._repository.list_experiences(profile.id)
            ],
            educations=[
                EducationRead.model_validate(education)
                for education in self._repository.list_educations(profile.id)
            ],
        )
