"""简历生成业务逻辑：按登录用户取**指定那份**简历 → AI 润色文案 → 套模板 → 落库。

与 T5 的差别：不再接收请求体里的档案数据，也不再写内存仓储。
与 D4.1 之前的差别：一个用户有多份简历，`ResumeGenerateRequest.profile_id`
指定用哪一份生成；不传则用默认简历（兼容只发 `title` 的旧前端）。

ADR-009 插入了一步「文案润色」：AI 只改写项目经历的描述与职责、给出技能排序，
排版仍由 `TemplateResumeGenerator` 负责。因此服务层负责把两者**合并**，
合并规则是这套架构的安全边界，见 `_apply_copy()` 的注释。
"""

from collections.abc import Sequence

from app.core.document_style import normalize_theme, resolve_layout
from app.integrations.copy_writer import (
    CopyAuthError,
    CopyUnavailableError,
    ResumeCopyWriter,
)
from app.integrations.resume_generator import ResumeGenerator
from app.repositories.resume_repository import ResumeRepository
from app.repositories.student_profile_repository import StudentProfile, StudentProfileRepository
from app.schemas.profile import EducationInput
from app.schemas.resume import (
    ExperienceInput,
    ProfileInput,
    ResumeCopy,
    ResumeGenerateRequest,
    ResumeRead,
    SkillInput,
)
from app.services.profile_service import ProfileNotFoundError


class ProfileIncompleteError(Exception):
    """当前用户还没有可用于生成简历的档案（或缺技能/经历）。"""


class ResumeApiKeyMissingError(Exception):
    """要走 AI 却没带 Key：由客户端负责补，服务端不回退到模板。"""


class ResumeCopyRejectedError(Exception):
    """DeepSeek 判定 Key 无效/欠费：透传 401，用户需要的是修 Key，不是一份模板简历。"""


class ResumeCopyUnavailableError(Exception):
    """上游超时/限流/5xx 或返回内容不合契约：可重试，前端据此提议模板降级。"""


def _skill_key(name: str) -> str:
    """技能名的比对键：折叠空白 + 转小写。

    模型常见的是把 `PyTorch` 写成 `Pytorch`、或在名字里塞个多余空格。按键比对
    再回贴**库里原名**，既容忍这种抖动，也保证生成物里永远只出现用户真正填过的名字。
    """
    return " ".join(name.split()).casefold()


def _apply_copy(
    copy: ResumeCopy,
    skills: Sequence[SkillInput],
    experiences: Sequence[ExperienceInput],
) -> tuple[list[SkillInput], list[ExperienceInput]]:
    """把 AI 文案合并回原始输入，规则缺一不可：

    1. **技能不新增也不丢**：模型给的每个名字都必须能对上库里的技能（否则丢弃），
       模型漏掉的按原顺序补在后面。AI 因此无法把「不会的技术」写进简历。
    2. **经历必须一一对应**：数量和 `index` 集合都要等于输入的 `range(n)`，
       否则视为上游不合契约 —— 静默少一段就等于悄悄改了用户的事实。
    3. **只替换文案**：标题与起止时间是库里的既有事实，模型无权改动。
    4. **空文本回落原值**：模型给出空串/纯空白时保留用户原文，不制造空洞。
    """
    catalog: dict[str, SkillInput] = {}
    for skill in skills:
        catalog.setdefault(_skill_key(skill.name), skill)

    ordered: list[SkillInput] = []
    seen: set[str] = set()
    for name in copy.skills:
        key = _skill_key(name)
        matched = catalog.get(key)
        if matched is not None and key not in seen:
            seen.add(key)
            ordered.append(matched)
    for skill in skills:
        key = _skill_key(skill.name)
        if key not in seen:
            seen.add(key)
            ordered.append(skill)

    if len(copy.experiences) != len(experiences) or {
        item.index for item in copy.experiences
    } != set(range(len(experiences))):
        raise ResumeCopyUnavailableError("AI 返回的经历与档案不一致。")

    merged_experiences: list[ExperienceInput] = []
    for position, original in enumerate(experiences):
        polished = next(item for item in copy.experiences if item.index == position)
        merged_experiences.append(
            original.model_copy(
                update={
                    "description": polished.description.strip() or original.description,
                    "responsibility": polished.responsibility.strip() or original.responsibility,
                }
            )
        )
    return ordered, merged_experiences


class ResumeService:
    def __init__(
        self,
        profile_repository: StudentProfileRepository,
        resume_repository: ResumeRepository,
        generator: ResumeGenerator,
        copy_writer: ResumeCopyWriter | None = None,
    ) -> None:
        self._profile_repository = profile_repository
        self._resume_repository = resume_repository
        self._generator = generator
        # `None` = 这次请求没有可用的 DeepSeek Key。用它而不是「有没有 writer」来判
        # 断，比在依赖层塞一个抛 NotImplementedError 的占位实现更诚实。
        self._copy_writer = copy_writer

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

        profile_input = ProfileInput(
            full_name=profile.full_name,
            contact=profile.contact,
            email=profile.email,
        )
        skill_inputs = [
            SkillInput(name=skill.name, self_rating=skill.self_rating) for skill in skills
        ]
        experience_inputs = [
            ExperienceInput(
                title=experience.title,
                start_date=experience.start_date,
                end_date=experience.end_date,
                description=experience.description,
                responsibility=experience.responsibility,
            )
            for experience in experiences
        ]
        polished_skills, polished_experiences = self._polish(
            payload, profile_input, skill_inputs, experience_inputs
        )

        content_html = self._generator.generate(
            profile=profile_input,
            skills=polished_skills,
            experiences=polished_experiences,
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
            # 顺序与标题同样跟着这份简历走（D5.3）。`resolve_layout()` 是唯一入口：
            # 库里是 NULL 或脏数据时它一律给出「3 段、合法 id、标题非空」的顺序，
            # 所以生成器拿到的永远是可渲染的列表，不需要自己判空。
            layout=resolve_layout(profile.layout_config),
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

    def _polish(
        self,
        payload: ResumeGenerateRequest,
        profile_input: ProfileInput,
        skills: list[SkillInput],
        experiences: list[ExperienceInput],
    ) -> tuple[list[SkillInput], list[ExperienceInput]]:
        """AI 润色一步。`use_fallback=True` 时**原样返回**，即用户点出来的模板降级。"""
        if payload.use_fallback:
            return skills, experiences
        if self._copy_writer is None:
            raise ResumeApiKeyMissingError()
        try:
            copy: ResumeCopy = self._copy_writer.write(profile_input, skills, experiences)
        except CopyAuthError as error:
            raise ResumeCopyRejectedError(str(error)) from error
        except CopyUnavailableError as error:
            raise ResumeCopyUnavailableError(str(error)) from error
        return _apply_copy(copy, skills, experiences)

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
