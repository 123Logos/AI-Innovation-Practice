"""岗位库业务逻辑（Phase 4 · B1 + C1，ADR-011 1.D / 1.F / 2.A / 2.D / 2.G）。

本层不接触 HTTP（AGENTS.md §4.1）：**不存在 / 越权 / 已删除** 一律抛同一个领域异常
`JobNotFoundError`，由 `app/api/v1/jobs.py` 翻译成 404。

为什么三者共用一个异常（1.F 的三条硬约束）：
403 会暴露「该 id 存在」，而「存在但无权」本身就是一条信息通道 ——
岗位 id 是 UUID 看似猜不到，但列表接口泄露的 id 会被二次枚举。
收敛成一个异常，api 层就没有「区分两者」的机会，也就没有写错的机会。

四条服务层规则：

1. **过期判定只在这一层用 `datetime.date.today()`**（2.G）。
   不用东八区的 `shanghai_today()`，也不用数据库侧的当日函数：
   全仓时间列都是 `DATETIME(6)` + 应用进程时钟写入，DB 服务器时区一旦与应用
   不一致，`CURDATE()` 给出的「今天」就和写入时用的不是同一个日子，
   而且这种不一致在本地永远测不出来。ADR-011 2.G 定的口径是应用时钟，照此执行。
   过期**不写库、不改状态**：没有 `expired` 状态（1.B），只有读侧判定。
2. **创建恒为 `draft`**：发布是显式动作（1.F #6），不是创建或编辑的副作用。
3. **技能条目的 `origin` 在这一层派生**（1.D）：`evidence_text` 经 §2.D.1 的归一化函数
   处理后是否是 `description` 同法归一文本的子串，是则 `jd_verifiable`，否则 `unverified`。
   客户端提交的 `origin` 在请求模型那一层就整键丢掉，到不了这里；
   `confidence` / `evidence_text` 到得了这里（ADR-011 v9 放开），但只对派生**负责**、
   不对派生**结果**有发言权。三列提取溯源戳同样在这一层盖（v9 第 ② 条）。
4. **提取（C1）在本层做核验，不在适配器里做**：与润色的 `_sanitize_copy` 同一个分层理由
   —— 模型输出的语义级校验是业务规则，而适配器只负责「把字节换成人能读的东西」。
   七条失败语义里属于上游的三条（第 3/4/6/7）由适配器的异常类区分，本层只做映射；
   第 1 条（没 Key）在第 0 格判，第 2 条（Key 被拒）由适配器的鉴权异常翻译，
   第 5 条（无一可回溯）在这里判。
"""

import json
import logging
from collections.abc import Sequence
from datetime import date, datetime

from app.core import deadline
from app.core.action_gate import (
    ActionRequiresOrganization,
    OrganizationAction,
    require_organization,
)
from app.core.datetime_display import utc_now
from app.core.permissions import JOB_REVIEW, UserRole
from app.core.skill_names import skill_name_key
from app.integrations.skill_extractor import (
    DEEPSEEK_MODEL,
    EXTRACT_PROMPT_VERSION,
    MAX_EVIDENCE_CHARS,
    MAX_EXTRACTED_SKILLS,
    MAX_SKILL_NAME_CHARS,
    ExtractedSkill,
    JobSkillExtractor,
    SkillExtractAuthError,
    SkillExtraction,
    SkillExtractorError,
    SkillExtractTruncatedError,
    is_traceable,
)
from app.repositories.audit_log_repository import (
    AuditAction,
    AuditLogRepository,
    AuditResult,
)
from app.repositories.job_repository import (
    ORIGIN_JD_VERIFIABLE,
    ORIGIN_UNVERIFIED,
    STATUS_DRAFT,
    STATUS_PENDING_REVIEW,
    STATUS_PUBLISHED,
    STATUS_REJECTED,
    Job,
    JobDraft,
    JobRepository,
    JobSkillDraft,
)
from app.schemas.job import (
    DEFAULT_PAGE_SIZE,
    ExtractedSkillRead,
    ExtractSkillsMeta,
    ExtractSkillsRequest,
    ExtractSkillsResult,
    JobRead,
    JobSkillRead,
    JobSummary,
    JobWriteRequest,
)
from app.services.auth_service import CurrentUserContext
from app.services.resume_service import (
    ResumeApiKeyMissingError,
    ResumeCopyRejectedError,
    ResumeCopyUnavailableError,
)

logger = logging.getLogger(__name__)

#: 列表卡片只展示前 6 条技能（ADR-011 议题 3.B 的卡片规格）。
SUMMARY_SKILL_LIMIT = 6

#: `GET /jobs` 缺省只看已发布（1.F #1：自己的草稿要显式 `?status=draft` 才出现）。
DEFAULT_LIST_STATUS = STATUS_PUBLISHED

#: 2.D 第 5 条的白名单：与 `ck_job_skills_type` 三值一致，
#: 枚举外的 `type` **保留该条、把值置 NULL**（所以 1.D 的 `type` 可空），
#: 而不是丢掉整条——用户看得见的那条技能仍然有用，只是分类要他自己选。
EXTRACTED_SKILL_TYPES = frozenset({"hard", "soft", "domain"})


class JobNotFoundError(Exception):
    """岗位不存在、不可见，或不属于当前用户。

    三种情况刻意返回同一个异常：对外都是 404，避免拿 id 探测「别人有没有这个岗位」。
    与 `app/services/profile_service.py:47` 的 `ProfileNotFoundError` 同族。
    """

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"job {job_id} not found")


class JobOrganizationRequiredError(Exception):
    """没有组织归属的账号不能提交审核（`ADR-024` §2.3、第 18 轮裁定 ①）。

    「发布岗位」是裁定点名的、必须依托组织才能完成的动作：这里不静默降级、
    不自动建组织，只拦下并提示绑定。
    """

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        super().__init__(f"user {user_id} has no organization")


class JobStateConflictError(Exception):
    """当前状态不接受这个动作（例如审核一条已经审完的岗位）。

    与 `JobNotFoundError` 分开：状态冲突时用户**有权知道**这条岗位存在——
    他本来就是创建者或审核人，藏着只会让人反复点。对外 409。
    """

    def __init__(self, job_id: str, action: str, status: str) -> None:
        self.job_id = job_id
        self.action = action
        self.status = status
        super().__init__(f"job {job_id} cannot {action} in status {status}")


class JobSelfReviewError(Exception):
    """不能审核自己提交的岗位（`ADR-024` §2.2）。

    教师角色可由邀请码获得（`ADR-023` §2.1），若不挡这一层，一个既是教师又是录入者的
    账号点一下「通过」就把自己的岗位放进广场——审核流当场退化成一次点击。
    与 B5 的「不能改自己的角色」（`SELF_ROLE_CHANGE`）同一个理由：
    审自己等于没有审。
    """

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"job {job_id} cannot be reviewed by its creator")


class JobExpiredError(Exception):
    """岗位已过截止时间，审核动作（以及提交审核）被 `is_expired` 拦下。

    `ADR-017` §5 把「动作层必须先过同一个判据」记成开工约束，
    `ADR-024` §2.4 是它第一次有实现物——判据只来自 `app/core/deadline.is_expired`，
    本模块里**没有**任何一处自己比较日期。
    """

    def __init__(self, job_id: str, action: str) -> None:
        self.job_id = job_id
        self.action = action
        super().__init__(f"job {job_id} is expired, cannot {action}")


class JobSkillExtractTruncatedError(ResumeCopyUnavailableError):
    """失败语义第 7 情形：输出被服务端自己设的输出 token 上限切断（`finish_reason == "length"`）。

    它是 `ResumeCopyUnavailableError` 的子类而不是新的错误种类，这一点是刻意的：
    `app/main.py` 的处理器按 MRO 查找，所以**即使路由忘了 catch 它**，
    对外也仍然是一条 `AI_SERVICE_UNAVAILABLE`（502，`retryable`）——
    「别新建错误码」这条约束因此在类型层面就守住了，不靠纪律。

    之所以还要单独有类型：它比第 4 情形多一个必须回给用户的信息
    （`meta.finish_reason == "length"`）和一条不同的下一步动作，
    而这两样都由 api 层消费。
    """

    def __init__(self, message: str, *, finish_reason: str | None) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason


class JobService:
    def __init__(
        self,
        repository: JobRepository,
        extractor: JobSkillExtractor | None = None,
        audit: AuditLogRepository | None = None,
    ) -> None:
        self._repository = repository
        # `None` = 这次请求没有可用的 DeepSeek Key。与 `ResumeService._copy_writer` 同一口径：
        # 用它而不是「塞一个会 raise NotImplementedError 的占位实现」更诚实，
        # 而且让「不需要 Key 的读写路径」保持零成本（`get_job_service` 每次请求都会装配本服务）。
        self._extractor = extractor
        # 审计与岗位仓储**必须同一个 session**（`ADR-021` §2.5：业务与审计同事务）。
        # 允许 `None` 只为让不碰审核的单测不被迫造一个仓储；审核动作在缺它时直接报错，
        # 不会出现「悄悄不写审计」的情况。
        self._audit = audit

    # ------------------------------------------------------------------ 读

    def list_jobs(
        self,
        user_id: str,
        *,
        status: str = DEFAULT_LIST_STATUS,
        keyword: str | None = None,
        include_expired: bool = False,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[list[JobSummary], int]:
        """广场（`status=published`）或本人的草稿（`status=draft`）列表。

        返回 `(当页条目, 符合筛选的总数)`：`total` 让前端能算页数，
        而筛选（关键词/过期）之后 `total` 必须跟着变，否则翻页会翻进空页。
        """
        today = date.today()
        offset = (page - 1) * page_size
        jobs = self._repository.list_jobs(
            viewer_id=user_id,
            status=status,
            keyword=keyword,
            today=today,
            include_expired=include_expired,
            offset=offset,
            limit=page_size,
        )
        total = self._repository.count_jobs(
            viewer_id=user_id,
            status=status,
            keyword=keyword,
            today=today,
            include_expired=include_expired,
        )
        return [self._to_summary(job, today) for job in jobs], total

    def list_trash(self, user_id: str) -> list[JobSummary]:
        """回收站（1.F #8）：只含**自己**软删除的岗位，过滤在 SQL 里做。"""
        today = date.today()
        return [self._to_summary(job, today) for job in self._repository.list_trash(user_id)]

    def get_job(self, context: CurrentUserContext, job_id: str) -> JobRead:
        """详情（1.F #2 + `ADR-024` §2.2）。

        `published` 任何登录用户可读，其余仅创建者；`pending_review` 额外给审核人读
        （同组织 / 平台管理员）。可见性仍然只在仓储的一个 SQL 谓词里，
        本层只负责把「有没有审核权、范围多大」翻译成两个参数。
        """
        reviewer_org, sees_all = self._review_scope(context)
        job = self._repository.get_readable(
            job_id,
            context.user.id,
            reviewer_organization_id=reviewer_org,
            reviewer_sees_all=sees_all,
        )
        if job is None:
            raise JobNotFoundError(job_id)
        return self._to_read(job)

    # ------------------------------------------------------------------ 提取

    def extract_skills(self, payload: ExtractSkillsRequest, request_id: str) -> ExtractSkillsResult:
        """`POST /jobs/extract-skills`：纯计算，**不读库也不写库**（2.A）。

        与 `/resumes/polish` 同构：结果回到前端草稿态，由用户逐条删改，
        点「保存岗位」才由 `POST/PUT /jobs` 落库。AI 永远无法只凭一次点击改动数据库。

        异常映射留在本层，是为了让 api 层只见服务异常、不接触 `integrations`
        （import-linter 的 “API cannot depend on integrations” 契约）。
        三个错误码沿用润色那套（2.F：`AI_KEY_MISSING` / `AI_KEY_INVALID` /
        `AI_SERVICE_UNAVAILABLE`），本层不新建码——`app/main.py` 的处理器按 MRO 命中，
        所以第 7 情形的子类对外仍是 502 `AI_SERVICE_UNAVAILABLE`。
        """
        if self._extractor is None:
            raise ResumeApiKeyMissingError()
        try:
            extraction = self._extractor.extract(payload.description, payload.title)
        except SkillExtractAuthError as error:
            raise ResumeCopyRejectedError(str(error)) from error
        except SkillExtractTruncatedError as error:
            raise JobSkillExtractTruncatedError(
                str(error), finish_reason=error.finish_reason
            ) from error
        except SkillExtractorError as error:
            raise ResumeCopyUnavailableError(str(error)) from error
        return self._verify_extraction(extraction, payload.description, request_id)

    def _verify_extraction(
        self, extraction: SkillExtraction, description: str, request_id: str
    ) -> ExtractSkillsResult:
        """2.D 的七条服务端强制（**不指望 Prompt**）+ 重名合并，逐条对应 ADR 的编号。

        1. 锚点核验：`evidence` 与 `description` 各过 §2.D.1 的归一化函数，
           前者必须是后者的子串；不是子串 → **丢弃该条**（第 2 条）。
        2. `name` 空或 > 40 字 → 丢弃；`evidence` 空或 > 60 字 → 丢弃（第 6 条）。
        3. `type` 不在 `hard/soft/domain` 内 → **保留该条**、`type` 置 NULL（第 5 条）。
        4. 条数 > 15 → 截断到 15，`meta.truncated = true`（第 4 条）。
        5. 一条都不剩 → 抛 502，**绝不返回空列表**（第 3 条，也是七条失败语义的第 5 情形）。
        6. `received_count` / `dropped_count` / `deduped_count` 进响应，同时进日志——
           日志里只有这几个计数、`finish_reason` 与 `request_id`，
           **没有 Key、没有 JD 正文、没有 evidence 内容**
           （2.D「核验计数必须可见」+ 附录的脱敏要求）。
        7. **重名合并**（ADR-011 2.D 没写、C1 之后才暴露的缺口）：模型常把同一个技能拆成
           `Python` / `python` / `Ｐython` 两条以上。这里不合并，后果不是难看而是必败——
           用户点「保存岗位」时 `JobWriteRequest.validate_unique_skill_names` 会报 422
           「技能名称重复」，库的 `UNIQUE (job_id, name)` 也会拦，等于我们把一个
           注定失败的 payload 交给了用户。合并键用 `skill_name_key`（与写侧同一个口径），
           **不是** §2.D.1 那个四步函数（理由写在 `app/schemas/job.py` 那里）。
           重名的那条**丢弃后来者、保留第一次出现的**：模型自己给的优先级顺序和
           原始大小写都在第一条上，后来者只是同一种东西的另一种写法。

        先核验、后截断是有意的：`dropped_count` 要对**全部** received 条数说话
        （前端文案是「AI 给出 20 条，其中 14 条无法回溯，已剔除」），
        先截断会让这个数变成「前 15 条里有几条不合格」，与用户看到的那句话不一致。
        重名合并同样在截断**之前**：放在后面，`deduped_count` 说的就变成
        「返回的那 15 条里有几条重名」，而封顶切掉的条目根本没进结果——两个数会互相吃掉，
        前端那两句计数文案也就都成了假话。
        """
        received = len(extraction.skills)
        kept: list[ExtractedSkillRead] = []
        seen: set[str] = set()
        dropped = 0
        deduped = 0
        for item in extraction.skills:
            verified = self._verify_item(item, description)
            if verified is None:
                dropped += 1
                continue
            name_key = skill_name_key(verified.name)
            if name_key in seen:
                # 只对**已通过核验**的条目判重名：核验失败的条目落在 `dropped_count` 里，
                # 不进这个集合、也不增加 `deduped_count`——两个数各说各的，互不串味。
                deduped += 1
                continue
            seen.add(name_key)
            kept.append(verified)

        truncated = len(kept) > MAX_EXTRACTED_SKILLS
        survivors = kept[:MAX_EXTRACTED_SKILLS]
        if not survivors:
            # 七条失败语义里唯一由**服务端规则**（而不是上游）触发的 502：
            # 模型答了，但一句都回溯不到 JD 原文。返回空列表等于把「AI 没干活」
            # 伪装成「这个岗位不要技能」，用户既无从纠正也无从重试。
            logger.warning(
                "岗位技能提取无一可回溯（request_id=%s, received=%s, dropped=%s, finish_reason=%s）",
                request_id,
                received,
                dropped,
                extraction.finish_reason,
            )
            raise ResumeCopyUnavailableError("AI 给出的技能条目都无法回溯到 JD 原文。")

        logger.info(
            "岗位技能提取完成（request_id=%s, received=%s, dropped=%s, deduped=%s, "
            "truncated=%s, finish_reason=%s）",
            request_id,
            received,
            dropped,
            deduped,
            truncated,
            extraction.finish_reason,
        )
        return ExtractSkillsResult(
            skills=survivors,
            meta=ExtractSkillsMeta(
                model=DEEPSEEK_MODEL,
                prompt_version=EXTRACT_PROMPT_VERSION,
                received_count=received,
                dropped_count=dropped,
                deduped_count=deduped,
                truncated=truncated,
                finish_reason=extraction.finish_reason,
            ),
        )

    @staticmethod
    def _verify_item(item: ExtractedSkill, description: str) -> ExtractedSkillRead | None:
        """单条核验：通不过就返回 `None`（= 丢弃该条），通不过的原因已经在计数里了。"""
        name = item.name.strip()
        evidence = item.evidence.strip()
        if not name or len(name) > MAX_SKILL_NAME_CHARS:
            return None
        # 空锚点与超长锚点都不能用：前者是任何文本的子串（会把幻觉判成「可回溯」），
        # 后者要求模型逐字复现的跨度过长，一个全半角差异就整条作废（2.B 收到 60 字的主因）。
        if not evidence or len(evidence) > MAX_EVIDENCE_CHARS:
            return None
        if not is_traceable(evidence, description):
            return None
        skill_type: str | None = (
            item.type if isinstance(item.type, str) and item.type in EXTRACTED_SKILL_TYPES else None
        )
        return ExtractedSkillRead(
            name=name,
            type=skill_type,  # type: ignore[arg-type]
            evidence=evidence,
            confidence=item.confidence,
        )

    # ------------------------------------------------------------------ 写

    def create_job(self, user_id: str, payload: JobWriteRequest) -> JobRead:
        """录入一个岗位：一律落 `status='draft'`，`created_by_user_id` 只来自 JWT。

        请求体里即便塞了 `status` / `created_by_user_id` / `origin` / 三列溯源戳，
        它们在 Pydantic 默认 `extra="ignore"` 那一层就掉了，到不了这里（1.F 的写侧规则）。
        岗位与技能由仓储在**一个事务**里写完（`JobRepository.create_job`）：
        分两次提交时，子表撞唯一键会留下一条「没有技能的岗位」。
        """
        skills = self._to_skill_drafts(payload)
        created = self._repository.create_job(
            created_by_user_id=user_id,
            draft=self._to_draft(payload, skills),
            status=STATUS_DRAFT,
            skills=skills,
        )
        return self._reload(created)

    def update_job(self, user_id: str, job_id: str, payload: JobWriteRequest) -> JobRead:
        """整体覆盖（1.F #4，仅创建者）：字段覆盖 + 技能子表先删后插，一次提交。

        `status` 原样保留：PUT 改的是内容，发布/下架是独立动作，
        否则「编辑草稿」会顺带把岗位推上广场。

        **唯一例外（`ADR-024` §2.5）**：编辑一份 `pending_review` 的岗位会把它退回
        `draft`。审核人批的必须是最终内容——不改状态的话，审的是旧文本、发的是新文本。
        退回方向只会更安全，反方向（编辑变发布）在这里没有任何入口。

        三列溯源戳**跟着这一次提交重算**（见 `_extract_stamp`）。
        """
        job = self._repository.get_owned(job_id, user_id)
        if job is None:
            raise JobNotFoundError(job_id)
        skills = self._to_skill_drafts(payload)
        status_override = STATUS_DRAFT if job.status == STATUS_PENDING_REVIEW else None
        self._repository.replace_job(
            job,
            self._to_draft(payload, skills),
            skills,
            status_override=status_override,
        )
        return self._reload(job)

    def submit_for_review(
        self,
        context: CurrentUserContext,
        job_id: str,
        *,
        ip: str | None = None,
    ) -> JobRead:
        """提交审核（`ADR-024` §2.3 / §2.4）：`draft` / `rejected` → `pending_review`。

        三道门，每一道都对应一条已裁定的口径：

        1. **归属**：只认创建者（非创建者 404，不留 403 的信息通道）；
        2. **组织**：没有组织归属就拦下并提示绑定（第 18 轮裁定 ①，「发布岗位」
           是它点名的动作）。这一步同时把 `organization_id` 写进岗位行——
           审核范围从此不靠猜；
        3. **过期**：`is_expired` 判据（裁定 ③ 的延伸）。否则队列里堆的全是
           「审核人只能驳回」的岗位。
        """
        job = self._repository.get_owned(job_id, context.user.id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status not in (STATUS_DRAFT, STATUS_REJECTED):
            raise JobStateConflictError(job_id, "submit_for_review", job.status)
        organization_id = self._require_organization(context, job, "submit_for_review", ip)
        self._ensure_not_expired(context, job, "submit_for_review", ip)

        # 先记审计、再由仓储那一次 commit 把「审计 + 状态」一起落库（ADR-021 §2.5）。
        self._record(
            AuditAction.JOB_SUBMIT_REVIEW,
            context,
            job,
            ip=ip,
            detail={"from": job.status},
        )
        self._repository.submit_for_review(job.id, context.user.id, organization_id, utc_now())
        return self._reload(job)

    def review(
        self,
        context: CurrentUserContext,
        job_id: str,
        *,
        decision: str,
        note: str | None = None,
        ip: str | None = None,
    ) -> JobRead:
        """审核（`ADR-024` §2.2 / §2.4）：`pending_review` → `published` / `rejected`。

        审核范围由 `_review_scope()` 给出：同组织的教师/校长，或平台管理员（不限组织）。
        范围外的岗位与「不存在」一样返回 404 —— 教师不该知道别校有一条待审岗位。

        **过期拦截是裁定 ③ 的本体**：通过和驳回都改变世界状态，都先过
        `deadline.is_expired`；被拦下的尝试写 `denied` 审计，方便以后回答
        「为什么这条岗位一直没人批」。
        """
        organization_id, sees_all = self._review_scope(context)
        # 「有权限但算不出范围」（教师没有组织）必须当成看不见，而不是把 `None`
        # 当成「不限组织」传下去——`get_for_review(organization_id=None)` 的语义是
        # **平台管理员**，两者混淆就是一个跨组织审核的漏洞。
        if organization_id is None and not sees_all:
            self._deny(context, AuditAction.JOB_APPROVE, None, "no_review_scope", ip)
            raise JobNotFoundError(job_id)
        job = self._repository.get_for_review(
            job_id, organization_id=None if sees_all else organization_id
        )
        if job is None:
            audit_action = (
                AuditAction.JOB_APPROVE if decision == "approve" else AuditAction.JOB_REJECT
            )
            self._deny(context, audit_action, None, "not_in_review_scope", ip)
            raise JobNotFoundError(job_id)
        if job.status != STATUS_PENDING_REVIEW:
            raise JobStateConflictError(job_id, decision, job.status)
        if job.created_by_user_id == context.user.id:
            audit_action = (
                AuditAction.JOB_APPROVE if decision == "approve" else AuditAction.JOB_REJECT
            )
            self._deny(context, audit_action, job, "self_review", ip)
            raise JobSelfReviewError(job.id)
        self._ensure_not_expired(context, job, decision, ip)

        approved = decision == "approve"
        status = STATUS_PUBLISHED if approved else STATUS_REJECTED
        self._record(
            AuditAction.JOB_APPROVE if approved else AuditAction.JOB_REJECT,
            context,
            job,
            ip=ip,
            detail={"note": note} if note else None,
        )
        # 驳回理由只在驳回时存；通过时把上一轮的旧理由清掉（`submit` 已清一次，
        # 这里再清是因为「通过」必须让前端拿到的 `review_note` 是空的，而不是残留）。
        self._repository.set_review(job.id, status, None if approved else note, utc_now())
        return self._reload(job)

    def list_review_queue(
        self,
        context: CurrentUserContext,
        *,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[list[JobSummary], int]:
        """待审队列（`ADR-024` §2.6）：只出本组织的 `pending_review`，先来先审。

        权限由路由层判（`require_permission(JOB_REVIEW)`）；本层只算范围。
        有权限但没有组织（理论上构造不出来）给空列表，而不是把全平台的岗位倒出来。
        """
        organization_id, sees_all = self._review_scope(context)
        if organization_id is None and not sees_all:
            return [], 0
        scope = None if sees_all else organization_id
        offset = (page - 1) * page_size
        jobs = self._repository.list_review_queue(
            organization_id=scope, keyword=keyword, offset=offset, limit=page_size
        )
        total = self._repository.count_review_queue(organization_id=scope, keyword=keyword)
        today = date.today()
        return [self._to_summary(job, today) for job in jobs], total

    def unpublish_job(self, user_id: str, job_id: str) -> JobRead:
        """下架回草稿（1.F #6，仅创建者）。

        `ADR-024` §2.6：下架保留，但**重新上架必须重新过审** —— 下架不是「撤销发布」
        的同义词，它把岗位变回一份需要重新提交的草稿。
        """
        job = self._repository.get_owned(job_id, user_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != STATUS_PUBLISHED:
            raise JobStateConflictError(job_id, "unpublish", job.status)
        self._repository.update_status(job.id, user_id, STATUS_DRAFT)
        return self._reload(job)

    def delete_job(self, user_id: str, job_id: str) -> None:
        """软删除进回收站（1.F #5，仅创建者）。

        只打 `deleted_at` 标记，`job_skills` 一行不动 —— 回收站里的岗位必须能原样恢复。
        """
        job = self._repository.get_owned(job_id, user_id)
        if job is None:
            raise JobNotFoundError(job_id)
        self._repository.soft_delete(job.id, user_id)

    def restore_job(self, user_id: str, job_id: str) -> JobRead:
        """从回收站恢复（1.F #9，仅创建者；对外是 `PUT /jobs/{job_id}/restore`）。

        两道门都在这里：`get_owned(..., include_deleted=True)` 把非创建者挡成 `None`，
        `job.deleted_at is None` 把「岗位存在但不在回收站」挡成同一个
        `JobNotFoundError` —— 两种情况对外都是 404，不给「存在但无权 / 存在但没被删」
        留任何信息通道（1.F 的 404 而非 403）。

        `status` 原样保留：恢复只是把 `deleted_at` 清回 `NULL`，
        删掉一个已发布岗位再恢复，它必须还是已发布，不该顺手退回草稿。
        """
        job = self._repository.get_owned(job_id, user_id, include_deleted=True)
        if job is None or job.deleted_at is None:
            raise JobNotFoundError(job_id)
        self._repository.restore(job.id, user_id)
        return self._reload(job)

    # ----------------------------------------------------------------- 内部

    def _review_scope(self, context: CurrentUserContext) -> tuple[str | None, bool]:
        """审核范围 → `(组织 id, 是否跨组织)`（`ADR-024` §2.2）。

        - 没有 `JOB_REVIEW`：`(None, False)`，即「读不到任何待审岗位」；
        - `platform_admin`：`(None, True)`，全平台；
        - 其余审核人：`(本组织 id, False)`；没有组织时是 `(None, False)`。
        """
        if JOB_REVIEW not in context.permissions:
            return None, False
        if context.role is UserRole.PLATFORM_ADMIN:
            return None, True
        return (context.organization.id if context.organization is not None else None), False

    def _require_organization(
        self,
        context: CurrentUserContext,
        job: Job,
        action: str,
        ip: str | None,
    ) -> str:
        """取提交者的组织；没有就拦下并提示绑定（第 18 轮裁定 ①）。

        判据本身在 `app/core/action_gate.require_organization`（`ADR-025` §2.5）：
        「哪些动作必须依托组织」是一张可枚举的表，不是散在服务里的 `if`。
        本方法只负责两件本地的事——记一条 `denied` 审计、把自己的域异常抛出去。

        返回值直接写进 `jobs.organization_id`：范围在**提交那一刻**定格，
        审核人据此判定「该不该我审」，而不是审核时再去猜提交者属于谁。
        """
        try:
            return require_organization(
                context.organization.id if context.organization is not None else None,
                action=OrganizationAction.PUBLISH_JOB,
            )
        except ActionRequiresOrganization as error:
            self._deny(context, AuditAction.JOB_SUBMIT_REVIEW, job, "no_organization", ip)
            raise JobOrganizationRequiredError(context.user.id) from error

    def _ensure_not_expired(
        self,
        context: CurrentUserContext,
        job: Job,
        action: str,
        ip: str | None,
    ) -> None:
        """动作层的唯一过期判据（`ADR-017` §5 / `ADR-024` §2.4）。

        判据只有 `app/core/deadline.is_expired` 一处；本文件**没有**任何一处
        自己写 `job.deadline < date.today()`，也没有 `deadline is None` 的特殊分支——
        「没有截止时间」由那个函数定义成「不过期」，在这里再判一次就是第二份真相。
        """
        if not deadline.is_expired(job.deadline, date.today()):
            return
        audit_action = {
            "submit_for_review": AuditAction.JOB_SUBMIT_REVIEW,
            "approve": AuditAction.JOB_APPROVE,
            "reject": AuditAction.JOB_REJECT,
        }[action]
        self._deny(context, audit_action, job, "job_expired", ip)
        raise JobExpiredError(job.id, action)

    def _record(
        self,
        action: AuditAction,
        context: CurrentUserContext,
        job: Job,
        *,
        ip: str | None,
        detail: dict[str, object] | None = None,
    ) -> None:
        """写一条成功审计。**不 commit**：跟着紧随其后的业务提交一起落库。"""
        if self._audit is None:  # pragma: no cover - 只有裸构图的服务才会走到
            raise RuntimeError("job review requires an audit repository")
        self._audit.record(
            action=action,
            actor_user_id=context.user.id,
            organization_id=job.organization_id
            or (context.organization.id if context.organization is not None else None),
            target_type="job",
            target_id=job.id,
            detail=json.dumps(detail, ensure_ascii=False) if detail else None,
            ip=ip,
        )

    def _deny(
        self,
        context: CurrentUserContext,
        action: AuditAction,
        job: Job | None,
        reason: str,
        ip: str | None,
    ) -> None:
        """被拒绝的尝试单独记 `denied` 并**立即提交**（业务没有任何写入，单独提交安全）。"""
        if self._audit is None:  # pragma: no cover
            return
        self._audit.record(
            action=action,
            actor_user_id=context.user.id,
            organization_id=(
                job.organization_id
                if job is not None and job.organization_id
                else (context.organization.id if context.organization is not None else None)
            ),
            target_type="job",
            target_id=job.id if job is not None else None,
            result=AuditResult.DENIED,
            detail=json.dumps({"reason": reason}, ensure_ascii=False),
            ip=ip,
        )
        self._audit.commit()

    def _reload(self, job: Job) -> JobRead:
        """写完重新读一遍：`create_job()` 会先提交父行，`updated_at` 也要取库里的值。

        用 `get_readable` 而不是 `get_owned`：调用方就是创建者本人，两条谓词对他等价，
        而复用同一个读入口意味着「刚写完的岗位」和「下次打开的岗位」永远长一样。
        """
        reloaded = self._repository.get_readable(job.id, job.created_by_user_id)
        if reloaded is None:  # pragma: no cover - 刚写完的岗位不可能读不到
            raise JobNotFoundError(job.id)
        return self._to_read(reloaded)

    @staticmethod
    def _to_draft(payload: JobWriteRequest, skills: Sequence[JobSkillDraft]) -> JobDraft:
        # 一次算好三列再拆开赋值：调三次 `_extract_stamp()` 会取三次 `utc_now()`，
        # 虽然只用得上其中一个，但「同一个派生算三遍」读起来就像三列可能互不相干。
        extract_model, extract_prompt_version, extracted_at = JobService._extract_stamp(skills)
        return JobDraft(
            company_name=payload.company_name,
            title=payload.title,
            location=payload.location,
            employment_type=payload.employment_type,
            description=payload.description,
            salary_min=payload.salary_min,
            salary_max=payload.salary_max,
            experience_required=payload.experience_required,
            deadline=payload.deadline,
            # 三列溯源戳由本次保存的技能条目派生，不来自请求体（v9 第 ② 条）。
            extract_model=extract_model,
            extract_prompt_version=extract_prompt_version,
            extracted_at=extracted_at,
        )

    @staticmethod
    def _extract_stamp(
        skills: Sequence[JobSkillDraft],
    ) -> tuple[str | None, str | None, datetime | None]:
        """PRD FR-03 L105 那四个字段的落点：本次保存里只要有任何一条能回溯原文就盖戳。

        为什么按「有任何一条」而不是「全部」：这一列要回答的是
        「这批数据里有没有来自模型的部分」，只要有一条锚点成立，它就必须可考；
        而一条都没有时留 `NULL` 比留一个假戳更诚实——库里那三列此刻确实没有对应的产出。

        `extracted_at` 用应用时钟的 UTC naive 值（`utc_now()`），与全仓时间写入口径一致
        （2.G 的「以应用进程时钟为准」）。它与同一行的 `created_at` 可能差几微秒：
        仓储自己取一次 `utc_now()`。这不是 bug——两列回答的是不同的问题
        （「这行什么时候建的」/「这批技能是什么时候从 JD 里出来的」）。
        """
        if not any(skill.origin == ORIGIN_JD_VERIFIABLE for skill in skills):
            return None, None, None
        return DEEPSEEK_MODEL, EXTRACT_PROMPT_VERSION, utc_now()

    @staticmethod
    def _to_skill_drafts(payload: JobWriteRequest) -> list[JobSkillDraft]:
        """把请求体的技能转成仓储入参：`origin` 在这里按 1.D 现场派生（模块 docstring 第 3 条）。

        派生用的是 §2.D.1 的**同一个**归一化函数（`is_traceable`），不是它的近似：
        提取时判「这句话在 JD 里出现过」用它，落库时决定徽标也用它，
        两处一旦各写一份，就会出现「提取时说可回溯、存完变未标注出处」这种没法解释的抖动。

        `confidence` / `evidence_text` 原样带给仓储（ADR-011 v9 放开）：
        越界与超长已经在 `JobSkillInput` 报成 422，走不到这里。
        """
        return [
            JobSkillDraft(
                name=skill.name,
                type=skill.type,
                origin=(
                    ORIGIN_JD_VERIFIABLE
                    if skill.evidence_text is not None
                    and is_traceable(skill.evidence_text, payload.description)
                    else ORIGIN_UNVERIFIED
                ),
                confidence=skill.confidence,
                evidence_text=skill.evidence_text,
            )
            for skill in payload.skills
        ]

    @staticmethod
    def is_expired(job: Job, today: date) -> bool:
        """2.G 的唯一判据，实现在 `app/core/deadline.py::is_expired`。

        这里只保持既有签名不变（调用点很多），比较本身**不在此处**：
        ADR-013 §7.5 要求匹配的「剩余投递时间」与列表页的「已过期」走同一个函数，
        两处各写一份 `deadline < today` 迟早漂移。
        """
        return deadline.is_expired(job.deadline, today)

    def _to_read(self, job: Job) -> JobRead:
        return JobRead(
            id=job.id,
            company_name=job.company_name,
            title=job.title,
            location=job.location,
            employment_type=job.employment_type,
            description=job.description,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            experience_required=job.experience_required,
            deadline=job.deadline,
            status=job.status,  # type: ignore[arg-type]
            created_by_user_id=job.created_by_user_id,
            # 驳回理由：只有 `rejected` 时仓储里才有值，其余状态恒为 None（ADR-024 §2.6）。
            review_note=job.review_note,
            extract_model=job.extract_model,
            extract_prompt_version=job.extract_prompt_version,
            extracted_at=job.extracted_at,
            deleted_at=job.deleted_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
            skills=[
                JobSkillRead.model_validate(skill) for skill in self._repository.list_skills(job.id)
            ],
            is_expired=self.is_expired(job, date.today()),
        )

    def _to_summary(self, job: Job, today: date) -> JobSummary:
        """列表项：不带 `description`（JD 最长 8000 字），技能只给前 6 条。"""
        skills = [
            JobSkillRead.model_validate(skill) for skill in self._repository.list_skills(job.id)
        ]
        return JobSummary(
            id=job.id,
            company_name=job.company_name,
            title=job.title,
            location=job.location,
            employment_type=job.employment_type,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            deadline=job.deadline,
            status=job.status,  # type: ignore[arg-type]
            created_by_user_id=job.created_by_user_id,
            created_at=job.created_at,
            updated_at=job.updated_at,
            deleted_at=job.deleted_at,
            skills=skills[:SUMMARY_SKILL_LIMIT],
            is_expired=self.is_expired(job, today),
        )
