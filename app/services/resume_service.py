"""简历业务逻辑：两条**互不调用**的路径（ADR-010）。

- `generate()`：按登录用户取指定那份简历 → 从库里读事实 → 纯模板渲染 → 落 `resumes`。
  不碰 AI，因此不需要 `X-DeepSeek-Key`。
- `polish()`：收前端草稿态的经历文案 → 调 DeepSeek 按档位润色 → 洗一遍结果 → 返回。
  不读库也不写库，采纳与保存都由用户在表单侧手动完成。

与 T5 的差别：生成不再接收请求体里的档案数据，也不再写内存仓储。
与 D4.1 之前的差别：一个用户有多份简历，`ResumeGenerateRequest.profile_id`
指定用哪一份生成；不传则用默认简历（兼容只发 `title` 的旧前端）。

`_sanitize_copy()` 的五条规则是这套架构的安全边界：模型无法新增技能、无法删段、
无法改标题与时间。字数口径自 ADR-018 起是「目标 + 超限标注」：`expand` 的
`max_words` 只进提示词与 `over_limit_by` 的账，**不再砍尾**；唯一的硬边是响应契约
（1200/600），撞到时段级截断并标 `truncated=true`，不再整单 502。

B8（ADR-015 §2.3）起新增 `get_current_resume()`：`resumes` 的第一条读路径，
「当前简历」只读版本指针，指针落空显式报错，绝不按 `created_at` 猜。

B8 剩余（`ADR-026`）起新增三条：`list_versions()` / `diff_versions()` /
`customize_for_job()`。三者共守一条不变式——**除了 `generate()`，没有任何路径
允许移动链头**：定制版插进历史但不改写 `current_version_id`（§2.2）。
"""

from collections.abc import Sequence
from datetime import date

from app.core import deadline
from app.core.document_style import LAYOUT_VERSION, normalize_theme, resolve_layout
from app.core.error_messages import ErrorMessage
from app.core.skill_names import skill_name_key
from app.integrations.copy_writer import (
    CopyAuthError,
    CopyUnavailableError,
    ResumeCopyWriter,
)
from app.integrations.resume_generator import ResumeGenerator
from app.repositories.job_repository import STATUS_PUBLISHED, Job, JobRepository, JobSkill
from app.repositories.resume_repository import ORIGIN_CUSTOMIZED, Resume, ResumeRepository
from app.repositories.student_profile_repository import (
    Experience,
    Skill,
    StudentProfile,
    StudentProfileRepository,
)
from app.schemas.profile import CertificateInput, EducationInput, PortfolioLinkInput
from app.schemas.resume import (
    ExperienceInput,
    PolishedExperience,
    PrivacyCategory,
    PrivacyItem,
    ProfileInput,
    ResumeCopy,
    ResumeCustomizeFocus,
    ResumeCustomizeRequest,
    ResumeCustomizeResult,
    ResumeDiff,
    ResumeDiffField,
    ResumeDiffItem,
    ResumeDiffSection,
    ResumeDiffSummary,
    ResumeGenerateRequest,
    ResumePolishRequest,
    ResumePrivacyCheck,
    ResumePrivacyCheckRequest,
    ResumeRead,
    ResumeVersionList,
    ResumeVersionOrigin,
    ResumeVersionSummary,
    SkillInput,
    clamp_to_polish_contract,
)
from app.services.profile_service import ProfileNotFoundError
from app.services.resume_diff import DocumentDiff, diff_documents, parse_snapshot

#: 生成侧拦旧布局的文案（ADR-015 §2.2）：必须是一句**修得动**的话。
#: 静默回落会把用户排好的顺序换成默认顺序而他看不出来——这比报错更糟。
#: 句子本身住在 `app/core/error_messages.py`（批次 F1c 起所有上屏文案只有一个来源），
#: 这里只留一个同义名字给服务层与路由层共用，避免两处各写一遍再慢慢漂移。
LAYOUT_VERSION_MISMATCH_MESSAGE = ErrorMessage.LAYOUT_VERSION


class ProfileIncompleteError(Exception):
    """当前用户还没有可用于生成简历的档案（或缺技能/经历）。"""


class ResumeLayoutVersionError(Exception):
    """档案里存的排版配置不是当前版本，生成新简历前必须重新保存一次（ADR-015 §2.2）。

    只拦「要产出新数据」这一刻：读侧照旧静默回落，已存在简历的快照永不受影响。
    """


class ResumeNotFoundError(Exception):
    """这份档案还没有生成过任何简历（`resumes` 一行都没有）。"""


class ResumePointerBrokenError(Exception):
    """有简历行但链头指针落空（链头被物理删除，ON DELETE SET NULL 置空了其余行）。

    ADR-015 §2.3：此时必须显式报错，**不许回落到「挑一条看起来最新的」**——
    回落就是把这条 ADR 作废。用户动作是重新生成一份。
    """


class ResumeVersionNotFoundError(Exception):
    """要对比的版本不存在、不属于当前用户，或两个 id 不属于同一份档案（`ADR-026` §2.6）。

    四种情形共用同一句 404：区分「不存在」与「不是你的」等于送出一个
    「这个版本 id 存不存在」的探针（与 `_resolve_profile` 同一口径）。
    """


class ResumeCustomizeJobNotFoundError(Exception):
    """定制的目标岗位不可读（不存在 / 非创建者且未发布 / 已删）。

    与 `jobs` 域的 `JOB_NOT_FOUND` 同一句文案：定制本质上是一次「按这条岗位渲染」，
    岗位看不见就没有定制的依据。
    """


class ResumeCustomizeJobExpiredError(Exception):
    """目标岗位已过截止时间（`ADR-026` §2.6，动作层 `is_expired`）。

    「为一条已经截止的岗位定制简历」是能省则省的无效劳动，而它会**落一行新版本**，
    属于动作层而不是读侧——所以它必须过同一个 `app/core/deadline.is_expired`。
    """


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
    （前四条继承自 ADR-009，第五条自 ADR-018 起是「目标 + 超限标注」）。

    1. **技能不新增也不丢**：模型给的每个名字都必须能对上输入里的技能（否则丢弃），
       模型漏掉的按原顺序补在后面，回给前端的永远是用户自己写过的那个名字。
       AI 因此无法把「不会的技术」塞进简历。
    2. **经历必须一一对应**：数量和 `index` 集合都要等于输入的 `range(n)`，
       否则视为上游不合契约——静默少一段就等于悄悄改了用户的事实。
    3. **只替换文案**：标题与时间是既有事实，`index` 就是前后端对齐的唯一锚点。
    4. **空文本回落原值**：模型给出纯空白时保留用户原文，不制造空洞。
    5. **字数是目标不是命令**（ADR-018 §2）：`expand` 不再按 `max_words` 砍尾，
       超出目标的部分用 `target_words` + `over_limit_by` 报数，让用户自己减字；
       只有撞到响应契约（1200/600）才段级截断并标 `truncated=true`，
       其余段照常返回——「坏的那段坏，好的那段能用」，两端都不许静默。
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

    # `max_words` 只对 expand 生效（校验层已把其它档置空）；它是目标，不是截断线。
    target_words = request.max_words if request.mode == "expand" else None

    polished_items = sorted(copy.experiences, key=lambda item: item.index)
    experiences: list[PolishedExperience] = []
    for item in polished_items:
        original = originals[item.index]
        description = item.description.strip() or original.description
        responsibility = item.responsibility.strip() or original.responsibility
        # 契约级截断（ADR-018 §2.4 / §6）：适配器已对模型原文截过一次，
        # 这里兜的是「空文本回落原值」那一支——用户原文最长 2000，也可能超契约。
        description, responsibility, contract_truncated = clamp_to_polish_contract(
            description, responsibility
        )
        # 超目标必须有数：len(返回文字) − target_words，不超为 0（ADR-018 §6 机械判据）。
        over_limit_by = (
            max(0, len(description) + len(responsibility) - target_words)
            if target_words is not None
            else 0
        )
        experiences.append(
            PolishedExperience(
                index=item.index,
                description=description,
                responsibility=responsibility,
                target_words=target_words,
                over_limit_by=over_limit_by,
                truncated=item.truncated or contract_truncated,
            )
        )
    return ResumeCopy(skills=ordered, experiences=experiences)


class ResumeService:
    def __init__(
        self,
        profile_repository: StudentProfileRepository,
        resume_repository: ResumeRepository,
        generator: ResumeGenerator,
        copy_writer: ResumeCopyWriter | None = None,
        job_repository: JobRepository | None = None,
    ) -> None:
        self._profile_repository = profile_repository
        self._resume_repository = resume_repository
        self._generator = generator
        # `None` = 这次请求没有可用的 DeepSeek Key。用它而不是「有没有 writer」来判
        # 断，比在依赖层塞一个抛 NotImplementedError 的占位实现更诚实。
        self._copy_writer = copy_writer
        # `None` = 没有装配岗位仓储（历史单测的裸构图）。只有
        # `customize_for_job()` 需要它，缺了就在那一刻报「配置错了」，
        # 而不是让生成/润色这些不需要它的路径跟着装不上。
        self._job_repository = job_repository

    def generate(self, user_id: str, payload: ResumeGenerateRequest) -> ResumeRead:
        profile = self._resolve_profile(user_id, payload.profile_id)
        # ADR-015 §2.2：版本失配只在「要产出新数据」时拦人。旧布局生成新简历 → 422，
        # 文案必须修得动；读侧照旧静默回落，已存在简历的快照永不受影响。
        self._ensure_current_layout(profile)
        skills = self._profile_repository.list_skills(profile.id)
        experiences = self._profile_repository.list_experiences(profile.id)
        # 期望城市**不读**：它不是简历区块（ADR-015 §5 ③），不进 content_html。
        # 保存是草稿式的（`ProfileUpsertRequest.full_name` / `contact` 都允许空），
        # 所以「基本信息填全了没有」必须在这里判，否则 pydantic 会在下面抛
        # ValidationError，冒到接口层变成 500 —— 而它其实是一个「档案不完整」的 400。
        if not profile.full_name.strip() or not profile.contact.strip():
            raise ProfileIncompleteError(user_id)
        if not skills or not experiences:
            raise ProfileIncompleteError(user_id)

        content_html = self._render(
            profile,
            title=payload.title,
            skills=skills,
            experiences=experiences,
        )
        resume = self._resume_repository.create_resume(
            student_profile_id=profile.id,
            title=payload.title,
            content_html=content_html,
        )
        return _read_resume(resume)

    def get_current_resume(self, user_id: str, profile_id: str | None = None) -> ResumeRead:
        """「当前简历」读路径（B8 / ADR-015 §2.3）：**只准读版本指针**。

        三种状态分开报，绝不合并成「挑一条看起来最新的」：

        - 链头在 → 返回它（`is_head = 1` 的那条，自指即链头）；
        - 一行都没有 → `ResumeNotFoundError`（还没生成过，404）；
        - 有行但无链头 → `ResumePointerBrokenError`（链头被删、指针落空，显式报错）。
          回落到 `ORDER BY created_at DESC LIMIT 1` 从此不许出现在任何地方。
        """
        profile = self._resolve_profile(user_id, profile_id)
        head = self._resume_repository.get_head(profile.id)
        if head is None:
            if self._resume_repository.count_by_profile(profile.id) > 0:
                raise ResumePointerBrokenError(profile.id)
            raise ResumeNotFoundError(profile.id)
        return _read_resume(head)

    # ------------------------------------------------------------------ B8 剩余
    # 版本列表 / 两版差异 / 按岗位定制（`ADR-026`）。三条路径里只有第三条写库，
    # 而它写的那一行**不移动链头**——除了 `generate()`，没有第二个动作能改变
    # 「当前简历」是哪一版（§2.2）。

    def list_versions(self, user_id: str, profile_id: str | None = None) -> ResumeVersionList:
        """该档案的全部版本，按 `version_no` 倒序（`ADR-026` §2.6）。

        一份都没生成过时返回**空列表**而不是 404：那个判断属于 `GET /resumes/current`
        （「还没有简历」是它的错误口径），而本端点回答的是「历史上有哪些版本」，
        空历史是一个合法答案。档案本身不存在/越权仍然 404（`_resolve_profile`）。
        """
        profile = self._resolve_profile(user_id, profile_id)
        versions = self._resume_repository.list_versions(profile.id)
        head = next((version for version in versions if version.is_head == 1), None)
        return ResumeVersionList(
            profile_id=profile.id,
            current_version_id=head.id if head is not None else None,
            versions=[_version_summary(version) for version in versions],
        )

    def diff_versions(self, user_id: str, from_version_id: str, to_version_id: str) -> ResumeDiff:
        """两版的结构级差异（`ADR-026` §2.4）。

        两个 id 都必须属于**当前用户的同一份档案**：任一落空或跨档案 → 同一个 404。
        不接受 `profile_id` 参数——那会引入「id 说是 A 档案、参数说是 B 档案」的
        两义输入，而这类输入的最后一次处理一定是在某个 if 里猜。
        """
        old = self._resume_repository.get_version_for_user(from_version_id, user_id)
        new = self._resume_repository.get_version_for_user(to_version_id, user_id)
        if old is None or new is None or old.student_profile_id != new.student_profile_id:
            raise ResumeVersionNotFoundError(from_version_id)
        result = diff_documents(parse_snapshot(old.content_html), parse_snapshot(new.content_html))
        return _to_diff(_version_summary(old), _version_summary(new), result)

    def customize_for_job(
        self, user_id: str, payload: ResumeCustomizeRequest
    ) -> ResumeCustomizeResult:
        """按岗位定制：**只重排，一个字都不改**（`ADR-026` §2.5）。

        五道闸门按顺序过，每一步都对应一个能修得动的错误：

        1. 档案完整 + 排版是当前版本（与 `/generate` 逐字相同）；
        2. 链头必须存在——定制要「基于某一版」，没有就 `ResumeNotFoundError`
           （一行都没有）或 `ResumePointerBrokenError`（有行但指针落空）；
        3. 岗位必须可读且已发布（未发布的岗位只有创建者看得到，拿它定制没有意义）；
        4. 岗位未过期（动作层 `is_expired`，`ADR-017` §5）；
        5. 渲染 → 插一行 `origin='customized'`，`current_version_id` 指向**原链头**。
        """
        profile = self._resolve_profile(user_id, payload.profile_id)
        self._ensure_current_layout(profile)
        skills = self._profile_repository.list_skills(profile.id)
        experiences = self._profile_repository.list_experiences(profile.id)
        if not profile.full_name.strip() or not profile.contact.strip():
            raise ProfileIncompleteError(user_id)
        if not skills or not experiences:
            raise ProfileIncompleteError(user_id)

        head = self._resume_repository.get_head(profile.id)
        if head is None:
            if self._resume_repository.count_by_profile(profile.id) > 0:
                raise ResumePointerBrokenError(profile.id)
            raise ResumeNotFoundError(profile.id)

        job, job_skills = self._load_customize_job(user_id, payload.job_id)
        keys, matched, missing = _focus(job_skills, skills)
        title = _customized_title(profile.title, job.title)
        content_html = self._render(
            profile,
            title=title,
            skills=_order_skills(skills, keys),
            experiences=_order_experiences(experiences, keys),
        )
        resume = self._resume_repository.create_customized_resume(
            profile.id,
            title=title,
            content_html=content_html,
            target_job_id=job.id,
            head_id=head.id,
        )
        return ResumeCustomizeResult(
            resume=_read_resume(resume),
            based_on_version_no=head.version_no,
            focus=ResumeCustomizeFocus(
                job_id=job.id,
                job_title=job.title,
                matched=matched,
                missing=missing,
            ),
        )

    def _load_customize_job(self, user_id: str, job_id: str) -> tuple[Job, list[JobSkill]]:
        """取一条「可以拿来定制」的岗位：已发布、未删、未过期（`ADR-026` §2.6）。"""
        if self._job_repository is None:  # pragma: no cover - 只有裸构图的服务才会走到
            raise RuntimeError("customize_for_job requires a job repository")
        job = self._job_repository.get_readable(job_id, user_id)
        if job is None or job.status != STATUS_PUBLISHED:
            raise ResumeCustomizeJobNotFoundError(job_id)
        # 动作层的唯一过期判据：本文件没有第二处 `deadline < today`。
        if deadline.is_expired(job.deadline, date.today()):
            raise ResumeCustomizeJobExpiredError(job_id)
        return job, self._job_repository.list_skills(job.id)

    def _render(
        self,
        profile: StudentProfile,
        *,
        title: str,
        skills: Sequence[Skill],
        experiences: Sequence[Experience],
    ) -> str:
        """把档案事实渲染成 `content_html`——生成与定制的**唯一**渲染出口。

        参数收的是 ORM 行而不是生成器契约：定制版要先把技能/经历**重排**再渲染，
        若这里收 `SkillInput`，重排逻辑就得同时构造一遍 `SkillInput` 列表，
        两处各写一份「哪些字段参与渲染」的清单，迟早有一处漏字段（例如新加的证书）。

        教育 / 证书 / 作品在内部读，因为它们**不参与定制排序**（`ADR-026` §2.5 第 3 条），
        调用方传进来只会让两个入口各自读一遍同样的三张子表。
        """
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
        educations = self._profile_repository.list_educations(profile.id)
        certificates = self._profile_repository.list_certificates(profile.id)
        portfolio_links = self._profile_repository.list_portfolio_links(profile.id)

        # ADR-010：生成路径**不再调用 AI**。文案就是库里的事实，
        # 想润色要走 `POST /resumes/polish`，由用户确认后写进表单再保存。
        # 定制路径同理：它只改顺序，不碰一个字（`ADR-026` §2.5）。
        return self._generator.generate(
            profile=profile_input,
            skills=skill_inputs,
            experiences=experience_inputs,
            title=title,
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
            # 库里是 NULL 或脏数据时它一律给出「5 段、合法 id、标题非空」的顺序，
            # 所以生成器拿到的永远是可渲染的列表，不需要自己判空。
            layout=resolve_layout(profile.layout_config),
            certificates=[
                CertificateInput(
                    name=certificate.name,
                    issuer=certificate.issuer,
                    issued_on=certificate.issued_on,
                )
                for certificate in certificates
            ],
            portfolio=[
                PortfolioLinkInput(label=link.label, url=link.url) for link in portfolio_links
            ],
        )

    @staticmethod
    def _ensure_current_layout(profile: StudentProfile) -> None:
        """生成前的排版版本闸（ADR-015 §2.2 的「生成」那一格）。

        `NULL` = 从未自定义，按默认顺序生成，不拦；只要存过配置而版本不是当前值
        （含旧版本、未知版本、手工改库改坏的非对象），一律 422 让用户去排版面板
        重新保存——静默回落会把他排好的顺序换成默认顺序，而他看不出来。
        版本相符但 sections 脏的，仍由 `resolve_layout()` 宽松降级（那是数据状态，
        不是「旧版本」）。
        """
        config = profile.layout_config
        if config is None:
            return
        version = config.get("version") if isinstance(config, dict) else None
        if version != LAYOUT_VERSION:
            raise ResumeLayoutVersionError(LAYOUT_VERSION_MISMATCH_MESSAGE)

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

        FR-06 点名的四类里，**批次 B4 之前**只有两类真能核对：作品链接与授权范围在数据模型里
        没有任何对应的列或表，那两格如实返回 `not_available`。B4 把
        `portfolio_links` / `profile_certificates` 建起来之后，「作品链接」这一格第一次
        真的可核（见 `_build_privacy_check`），**只剩授权范围仍是 `not_available`**——
        它要等 B5 的组织与角色。这条判断跟着 ADR-019 §3 的口径走：能核就核，核不了就说核不了，
        绝不为让清单看起来更满而造字段。
        """
        profile = self._resolve_profile(user_id, payload.profile_id)
        portfolio_links = self._profile_repository.list_portfolio_links(profile.id)
        return _build_privacy_check(
            profile_id=profile.id,
            full_name=profile.full_name,
            contact=profile.contact,
            email=profile.email,
            target_role=profile.target_role,
            skill_count=len(self._profile_repository.list_skills(profile.id)),
            experience_count=len(self._profile_repository.list_experiences(profile.id)),
            education_count=len(self._profile_repository.list_educations(profile.id)),
            certificate_count=len(self._profile_repository.list_certificates(profile.id)),
            portfolio_links=[(link.label, link.url) for link in portfolio_links],
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
# FR-06 点名的四类里，「作品链接」自批次 B4 起**真的可核**（`portfolio_links` 表已存在，
# 生成物里也是真实的 `<a href>`），所以它不再返回 `not_available`。「授权范围」仍然
# `not_available`：数据模型里没有任何对应的列或表（取证：`student_profiles` 全部列 =
# id/user_id/title/is_default/deleted_at/paper_size/theme/layout_config/full_name/
# email/grade/contact/target_role/resume_version_seq/created_at/updated_at；
# 全仓 `consent`/`authoriz`/`visibility`/`is_public` 零命中），要等批次 B5 的组织与角色。
# 如实返回，不为了让清单看起来更满而造字段或建表。
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
    certificate_count: int,
    portfolio_links: Sequence[tuple[str, str]],
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
            key="certificates",
            label=f"证书（{certificate_count} 条）",
            present=certificate_count > 0,
            included=certificate_count > 0,
            note=(
                None
                if certificate_count > 0
                else "一条证书都没填：这一节整节不渲染，不会留一个空标题。"
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
                # 批次 B4 起这一格真能核：`portfolio_links` 是真实存在的表，生成物里
                # 每条都是可点的 `<a href>`——「会被带出去」在这里是字面意思。
                status="present" if portfolio_links else "missing",
                note=(
                    None
                    if portfolio_links
                    else "还没填作品链接：简历上这一节整节不渲染，看简历的人也就点不进去。"
                ),
                items=[
                    PrivacyItem(
                        key=f"portfolio-{index}",
                        label=label,
                        present=True,
                        included=True,
                        note=f"会被带出去：{url}",
                    )
                    for index, (label, url) in enumerate(portfolio_links)
                ],
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


# --------------------------------------------------------------------------- #
# B8 剩余（`ADR-026`）：版本摘要、差异装配、按岗位定制的纯函数。
#
# 追加在文件**末尾**是位置约束：本文件上部有多处被文档按行号引用
# （`ADR-013` 引三个异常类所在行、`ADR-018` 引 `_sanitize_copy` 的区间），
# 在它们上面插行会把指针推到别的代码上。
#
# 这一批的函数全部是纯的：不读库、不写库、不认识 `Session`。
# 服务层负责取行与落库，这里只负责「怎么排列」「怎么描述」——
# 于是顺序规则可以单测，而不必先造一个 MySQL。
# --------------------------------------------------------------------------- #


def _read_resume(resume: Resume) -> ResumeRead:
    """ORM 行 → 对外读模型（生成、当前版、定制版三处共用这一份映射）。"""
    return ResumeRead(
        resume_id=resume.id,
        title=resume.title,
        content_html=resume.content_html,
        created_at=resume.created_at,
        version_no=resume.version_no,
    )


def _version_summary(resume: Resume) -> ResumeVersionSummary:
    """ORM 行 → 版本摘要（不含正文，`ADR-026` §2.6）。

    `is_head` 是数据库生成列，值是 `1` / `None`；这里只做布尔折叠，
    再算一次「哪条是链头」就是第二份真相。
    """
    origin: ResumeVersionOrigin = (
        "customized" if resume.origin == ORIGIN_CUSTOMIZED else "generated"
    )
    return ResumeVersionSummary(
        resume_id=resume.id,
        version_no=resume.version_no,
        title=resume.title,
        origin=origin,
        is_head=resume.is_head == 1,
        target_job_id=resume.target_job_id,
        created_at=resume.created_at,
    )


def _to_diff(
    old: ResumeVersionSummary,
    new: ResumeVersionSummary,
    result: DocumentDiff,
) -> ResumeDiff:
    """把 `resume_diff.DocumentDiff` 装进响应模型（`ADR-026` §2.4）。

    两层的字段一一对应，刻意不做「顺手补一句自然语言总结」：总结一旦由代码生成，
    就会在规则变化时变成一句过期的谎话；界面要一句话时应当由计数自己拼。
    """
    sections = [
        ResumeDiffSection(
            key=section.key,  # type: ignore[arg-type]
            label=section.label,
            change=section.change,  # type: ignore[arg-type]
            title_before=section.title_before,
            title_after=section.title_after,
            order_changed=section.order_changed,
            items=[
                ResumeDiffItem(
                    kind=item.kind,  # type: ignore[arg-type]
                    label=item.label,
                    before=item.before,
                    after=item.after,
                    fields=[
                        ResumeDiffField(field=diff.field, before=diff.before, after=diff.after)
                        for diff in item.fields
                    ],
                )
                for item in section.items
            ],
        )
        for section in result.sections
    ]
    return ResumeDiff(
        from_version=old,
        to_version=new,
        comparable=result.comparable,
        note=result.note,
        summary=ResumeDiffSummary(
            added=result.added,
            removed=result.removed,
            changed=result.changed,
            sections_changed=sum(1 for section in result.sections if section.change != "unchanged"),
            section_order_changed=result.section_order_changed,
            item_order_changed=result.item_order_changed,
        ),
        sections=sections,
    )


def _job_skill_keys(job_skills: Sequence[JobSkill]) -> list[str]:
    """岗位技能 → 判定键（去空、去重、保序）。

    用 `skill_name_key`（NFKC + casefold）而不是别名层：别名层是**评分**的输入
    （`ADR-016` §3），把它拉进「排序」会让「为什么这段经历排在前面」无法解释——
    用户看不到那张词表，只看得到顺序变了。
    """
    keys: list[str] = []
    for job_skill in job_skills:
        key = skill_name_key(job_skill.name)
        if key and key not in keys:
            keys.append(key)
    return keys


def _focus(
    job_skills: Sequence[JobSkill],
    skills: Sequence[Skill],
) -> tuple[list[str], list[str], list[str]]:
    """算出 `(判定键, 命中, 缺失)`，三者的顺序都跟着「来源」走（`ADR-026` §2.5）。

    - 判定键：岗位技能顺序（第一个出现的写法胜出）；
    - 命中：**档案**里的技能顺序（因为它是给用户看「你的哪几条中了」）；
    - 缺失：**岗位**里的技能顺序（因为它是给用户看「JD 要什么」）。
    """
    keys = _job_skill_keys(job_skills)
    key_set = set(keys)
    profile_keys = {skill_name_key(skill.name) for skill in skills}
    matched = [skill.name for skill in skills if skill_name_key(skill.name) in key_set]
    missing: list[str] = []
    for job_skill in job_skills:
        key = skill_name_key(job_skill.name)
        if key in key_set and key not in profile_keys and job_skill.name not in missing:
            missing.append(job_skill.name)
    return keys, matched, missing


def _order_skills(skills: Sequence[Skill], keys: Sequence[str]) -> list[Skill]:
    """命中的技能排前面，两组内部保持档案原顺序（稳定排序，`ADR-026` §2.5 第 1 条）。

    稳定是产品要求而不是实现巧合：用户按自己熟悉的顺序填过技能，
    定制版只该把「与这条岗位相关的」提上来，不该顺手把他排好的其余顺序打乱。
    """
    key_set = set(keys)
    matched = [skill for skill in skills if skill_name_key(skill.name) in key_set]
    rest = [skill for skill in skills if skill_name_key(skill.name) not in key_set]
    return matched + rest


def _hit_count(experience: Experience, keys: Sequence[str]) -> int:
    """这段经历的正文里出现了几个岗位技能（**近似**，只用于排序）。

    近似是刻意的：真正的证据打分在 `app/integrations/evidence_scorer.py` 与
    `app/services/match_scoring.py`（`ADR-013`），那套要 Key、要别名层、要可解释的
    分数。这里只需要一个「谁更相关」的序，任何复杂化都会让定制版变成一个
    用户没法理解的排序。
    """
    haystack = (
        f"{experience.title} {experience.description} {experience.responsibility}"
    ).casefold()
    return sum(1 for key in keys if key and key in haystack)


def _order_experiences(experiences: Sequence[Experience], keys: Sequence[str]) -> list[Experience]:
    """按命中条数降序，同分保持原序（`ADR-026` §2.5 第 2 条）。

    `sorted` 是稳定的，但这里仍然显式带下标：`(... , index)` 让「同分保持原序」
    成为一条**可读的规则**而不是一条依赖实现细节的假设。
    """
    indexed = [(index, experience) for index, experience in enumerate(experiences)]
    indexed.sort(key=lambda pair: (-_hit_count(pair[1], keys), pair[0]))
    return [experience for _, experience in indexed]


def _customized_title(profile_title: str, job_title: str) -> str:
    """定制版的标题 = 简历名 + 面向的岗位（`ADR-026` §2.1）。

    标题落进 `resumes.title`（200 字上限）而不是新开一列存岗位标题：
    它是**快照**语义——岗位后来改名，手上这份简历的名字不该跟着变。
    超长时按码点截断（Python 的 `str[:n]` 就是码点级），不切出半个代理对。
    """
    base = profile_title.strip() or "我的简历"
    return f"{base} · 面向「{job_title.strip()}」"[:200]
