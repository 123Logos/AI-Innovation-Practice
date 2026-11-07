"""简历业务逻辑：两条**互不调用**的路径（ADR-010）。

- `generate()`：按登录用户取指定那份简历 → 从库里读事实 → 纯模板渲染 → 落 `resumes`。
  不碰 AI，因此不需要 `X-DeepSeek-Key`。
- `polish()`：收前端草稿态的经历文案 → 调 DeepSeek 按档位润色 → 洗一遍结果 → 返回。
  不读库也不写库，采纳与保存都由用户在表单侧手动完成。

与 T5 的差别：生成不再接收请求体里的档案数据，也不再写内存仓储。
与 D4.1 之前的差别：一个用户有多份简历，`ResumeGenerateRequest.profile_id`
指定用哪一份生成；不传则用默认简历（兼容只发 `title` 的旧前端）。

`_sanitize_copy()` 的五条规则是这套架构的安全边界：模型无法新增技能、无法删段、
无法改标题与时间，`expand` 的字数上限也由服务端而不是 Prompt 保证。
"""

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
    PolishedExperience,
    ProfileInput,
    ResumeCopy,
    ResumeGenerateRequest,
    ResumePolishRequest,
    ResumeRead,
    SkillInput,
)
from app.services.profile_service import ProfileNotFoundError


class ProfileIncompleteError(Exception):
    """当前用户还没有可用于生成简历的档案（或缺技能/经历）。"""


class ResumeApiKeyMissingError(Exception):
    """要润色却没带 Key（ADR-010 里只可能来自 `/resumes/polish`）：由客户端负责补。"""


class ResumeCopyRejectedError(Exception):
    """DeepSeek 判定 Key 无效/欠费：透传 401，用户需要的是修 Key，而不是重试。"""


class ResumeCopyUnavailableError(Exception):
    """上游超时/限流/5xx 或返回内容不合契约：标记为可重试，让用户自己决定要不要再点一次。

    ADR-010 起**不再有模板降级**这条路：生成接口本来就是纯模板，
    润色失败时「改用模板」是个无意义的选项。
    """


def _skill_key(name: str) -> str:
    """技能名的比对键：折叠空白 + 转小写。

    模型常见的是把 `PyTorch` 写成 `Pytorch`、或在名字里塞个多余空格。按键比对
    再回贴**库里原名**，既容忍这种抖动，也保证生成物里永远只出现用户真正填过的名字。
    """
    return " ".join(name.split()).casefold()


def _sanitize_copy(copy: ResumeCopy, request: ResumePolishRequest) -> ResumeCopy:
    """把模型输出洗成「可以放心交给用户确认」的结果。五条规则缺一不可
    （前四条继承自 ADR-009，第五条是 ADR-010 为 `expand` 新增的服务端硬保证）。

    1. **技能不新增也不丢**：模型给的每个名字都必须能对上输入里的技能（否则丢弃），
       模型漏掉的按原顺序补在后面，回给前端的永远是用户自己写过的那个名字。
       AI 因此无法把「不会的技术」塞进简历。
    2. **经历必须一一对应**：数量和 `index` 集合都要等于输入的 `range(n)`，
       否则视为上游不合契约——静默少一段就等于悄悄改了用户的事实。
    3. **只替换文案**：标题与时间是既有事实，`index` 就是前后端对齐的唯一锚点。
    4. **空文本回落原值**：模型给出纯空白时保留用户原文，不制造空洞。
    5. **`expand` 的字数上限由服务端兜**：Prompt 里的要求只是建议，
       模型超了就必须截——用户要的是「不超过」，不是「尽量不超过」。
    """
    catalog: dict[str, SkillInput] = {}
    for skill in request.skills:
        catalog.setdefault(_skill_key(skill.name), skill)

    ordered: list[str] = []
    seen: set[str] = set()
    for name in copy.skills:
        key = _skill_key(name)
        matched = catalog.get(key)
        if matched is not None and key not in seen:
            seen.add(key)
            ordered.append(matched.name)
    for skill in request.skills:
        key = _skill_key(skill.name)
        if key not in seen:
            seen.add(key)
            ordered.append(skill.name)

    originals = list(request.experiences)
    if len(copy.experiences) != len(originals) or {item.index for item in copy.experiences} != set(
        range(len(originals))
    ):
        raise ResumeCopyUnavailableError("AI 返回的经历与输入不一致。")

    polished_items = sorted(copy.experiences, key=lambda item: item.index)
    experiences: list[PolishedExperience] = []
    for item in polished_items:
        original = originals[item.index]
        description = item.description.strip() or original.description
        responsibility = item.responsibility.strip() or original.responsibility
        if request.mode == "expand" and request.max_words is not None:
            description, responsibility = _cap_pair(description, responsibility, request.max_words)
        experiences.append(
            PolishedExperience(
                index=item.index,
                description=description,
                responsibility=responsibility,
            )
        )
    return ResumeCopy(skills=ordered, experiences=experiences)


#: 截断时给 `responsibility` 留的最小空间：再短就会把一句职责切成分号前面的半个词。
_RESPONSIBILITY_FLOOR = 10
_SENTENCE_ENDS = ("。", "！", "？", "；", "\n")


def _cap_text(text: str, limit: int) -> str:
    """把文本截到 `limit` 个字符：优先在句末边界收刀，边界太靠后（不足半程）才硬切。"""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for mark in _SENTENCE_ENDS:
        position = window.rfind(mark)
        if position >= limit // 2:
            return window[: position + 1]
    return window.rstrip()


def _cap_pair(description: str, responsibility: str, max_words: int) -> tuple[str, str]:
    """让**一段**（描述 + 职责合计）不超过 `max_words` 字。

    先给职责留一份预算（不超过其自身长度、且不少于下限），剩下的给描述；
    描述截短之后，再把省下来的额度还给职责。两个字段都保有不小于
    `_RESPONSIBILITY_FLOOR` 的空间，避免截成空串撞上 `min_length=1`。
    """
    if len(description) + len(responsibility) <= max_words:
        return description, responsibility

    responsibility_budget = max(_RESPONSIBILITY_FLOOR, min(len(responsibility), max_words // 3))
    capped_description = _cap_text(
        description, max(_RESPONSIBILITY_FLOOR, max_words - responsibility_budget)
    )
    capped_responsibility = _cap_text(
        responsibility,
        max(_RESPONSIBILITY_FLOOR, max_words - len(capped_description)),
    )
    return capped_description, capped_responsibility


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
        skill_inputs = [SkillInput(name=skill.name) for skill in skills]
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

        # ADR-010：生成路径**不再调用 AI**。文案就是库里的事实，
        # 想润色要走 `POST /resumes/polish`，由用户确认后写进表单再保存。
        content_html = self._generator.generate(
            profile=profile_input,
            skills=skill_inputs,
            experiences=experience_inputs,
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

    def polish(self, request: ResumePolishRequest) -> ResumeCopy:
        """ADR-010：纯润色。不读库、不写库，结果只回给前端草稿态。

        异常映射留在服务层，是为了让 API 层只见服务异常、不接触 integrations
        （import-linter 的 “API cannot depend on integrations” 契约）。
        """
        if self._copy_writer is None:
            raise ResumeApiKeyMissingError()
        try:
            copy = self._copy_writer.write(
                request.profile,
                request.skills,
                request.experiences,
                request.mode,
                request.max_words,
            )
        except CopyAuthError as error:
            raise ResumeCopyRejectedError(str(error)) from error
        except CopyUnavailableError as error:
            raise ResumeCopyUnavailableError(str(error)) from error
        return _sanitize_copy(copy, request)

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
