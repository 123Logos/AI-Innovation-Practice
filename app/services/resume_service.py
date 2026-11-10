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
    PrivacyCategory,
    PrivacyItem,
    ProfileInput,
    ResumeCopy,
    ResumeGenerateRequest,
    ResumePolishRequest,
    ResumePrivacyCheck,
    ResumePrivacyCheckRequest,
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
        profile = self._resolve_profile(user_id, payload.profile_id)
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

    def privacy_check(self, user_id: str, payload: ResumePrivacyCheckRequest) -> ResumePrivacyCheck:
        """导出前隐私检查（批次 B2 / PRD FR-06）：纯计算，读一份已保存的档案，不写任何东西。

        形态与 `polish()` 是同一条路子（ADR-010 §2、ADR-011 议题 2.A）：不落库、不加列、
        不建表，结果只回给界面。区别只在数据来源——`polish()` 收的是前端草稿，本方法**读库**，
        因为「会被带出去什么」只由「生成物里有什么」决定，而生成物读的就是这份档案
        （`generate()` 走同一个 `_resolve_profile`、同一批 `list_*`）。两头各判一次，
        检查结果就会对着一份不存在的简历说话，那比不检查更坏。

        FR-06 点名的四类里今天真能核对的只有两类：作品链接与授权范围在数据模型里没有任何
        对应的列或表（取证与措辞见下面那两条常量），这两格如实返回 `not_available`，
        不为了让清单看起来更满而造字段或建表。
        """
        profile = self._resolve_profile(user_id, payload.profile_id)
        return _build_privacy_check(
            profile_id=profile.id,
            full_name=profile.full_name,
            contact=profile.contact,
            email=profile.email,
            target_role=profile.target_role,
            skill_count=len(self._profile_repository.list_skills(profile.id)),
            experience_count=len(self._profile_repository.list_experiences(profile.id)),
            education_count=len(self._profile_repository.list_educations(profile.id)),
        )

    def _resolve_profile(self, user_id: str, profile_id: str | None) -> StudentProfile:
        """定位要处理的那份简历（生成与隐私检查共用这一条判断）。

        - 传了 `profile_id`：必须是**当前用户的、未删除的**那份，否则 404
          （查不到与不属于自己共用同一个 404，避免探测他人 id）；
        - 没传：退回默认简历，一份都没有则 400「档案不完整」。
        """
        if profile_id:
            profile = self._profile_repository.get_by_id(user_id, profile_id)
            if profile is None:
                raise ProfileNotFoundError(profile_id)
            return profile
        profile = self._profile_repository.get_by_user_id(user_id)
        if profile is None:
            raise ProfileIncompleteError(user_id)
        return profile


# --------------------------------------------------------------------------- #
# 导出前隐私检查的组装（批次 B2 / PRD FR-06）。
#
# 写在文件**末尾**是位置约束：ADR-006 引 `resume_service.py:34-47`、ADR-013 引 `:196`
# （`SkillInput` 构造点），在上面插行会把那些指针推到别的代码上，而
# `scripts/check_docs_citations.py` 只验「那一行非空」，漂了不会报红。
#
# 为什么不复用 `generate()` 的那四个 `list_*`：本函数**读的就是同一批数据**，
# 由调用处（`ResumeService.privacy_check`）用同一个 `_resolve_profile` 定位同一份简历。
# 两处各判一次「这一格有没有」就会对出一份不存在的简历，那比不检查更坏。
#
# FR-06 点名的四类里，今天真能核对的只有两类。「作品链接」与「授权范围」在数据模型里
# 没有任何对应的列或表（取证：`student_profiles` 全部列 = id/user_id/title/is_default/
# deleted_at/paper_size/theme/layout_config/full_name/email/grade/contact/target_role/
# created_at/updated_at；全仓 `consent`/`authoriz`/`visibility`/`is_public` 零命中）。
# 这两格如实返回 `not_available`，不为了让清单看起来更满而造字段或建表。
# --------------------------------------------------------------------------- #


def _build_privacy_check(
    *,
    profile_id: str,
    full_name: str,
    contact: str,
    email: str | None,
    target_role: str | None,
    skill_count: int,
    experience_count: int,
    education_count: int,
) -> ResumePrivacyCheck:
    """把「会被带出去什么」组装成四类清单。纯计算，不写任何东西。"""

    has_contact = bool(contact.strip())
    has_email = bool(email and email.strip())

    contact_category = PrivacyCategory(
        key="contact",
        label="联系方式",
        status="present" if has_contact or has_email else "missing",
        items=[
            PrivacyItem(
                key="phone",
                label="手机号",
                present=has_contact,
                included=True,
                note=None if has_contact else "没填手机号，纸面上的联系方式只剩姓名。",
            ),
            PrivacyItem(
                key="email",
                label="邮箱",
                present=has_email,
                included=has_email,
                # 邮箱没填时纸面上**不留标记也不留分隔点**（既有契约
                # `test_generate_omits_email_span_when_profile_has_none`），
                # 所以「少了这一栏」只能在这里说——这就是本格存在的理由。
                note=None
                if has_email
                else "没填邮箱：简历上不会出现这一栏，HR 也无法用邮箱联系你。",
            ),
        ],
    )

    personal_items = [
        PrivacyItem(
            key="full_name",
            label="姓名",
            present=bool(full_name.strip()),
            included=True,
        ),
        PrivacyItem(
            key="skills",
            label=f"技能（{skill_count} 条）",
            present=skill_count > 0,
            included=skill_count > 0,
        ),
        PrivacyItem(
            key="experiences",
            label=f"项目经历（{experience_count} 段）",
            present=experience_count > 0,
            included=experience_count > 0,
        ),
        PrivacyItem(
            key="educations",
            label=f"教育经历（{education_count} 段）",
            present=education_count > 0,
            included=education_count > 0,
            note=(
                None
                if education_count > 0
                else "一段教育经历都没有：这一节整节不渲染，不会留一个空标题。"
            ),
        ),
        PrivacyItem(
            key="target_role",
            label="目标岗位偏好",
            present=bool(target_role and target_role.strip()),
            # 「存在但不会被带出去」必须与「不存在」分得开：这一格就是为它而设的。
            included=False,
            note="只在人岗匹配里用，不写进简历正文。",
        ),
    ]

    return ResumePrivacyCheck(
        profile_id=profile_id,
        categories=[
            contact_category,
            PrivacyCategory(
                key="portfolio",
                label="作品链接",
                status="not_available",
                note="这份产品今天还没有「作品链接」这一栏（档案里没有这一列），所以它不会被带进简历；等这一栏能填了，这里才会真的检查。",
                items=[],
            ),
            PrivacyCategory(
                key="personal",
                label="个人信息",
                status="present",
                items=personal_items,
            ),
            PrivacyCategory(
                key="authorization",
                label="授权范围",
                status="not_available",
                note="今天没有「授权给谁看」这一层：这份简历只有登录的你自己能读到，教师端与推荐流程还没有对应的授权记录（那属于批次 B5）。",
                items=[],
            ),
        ],
    )
