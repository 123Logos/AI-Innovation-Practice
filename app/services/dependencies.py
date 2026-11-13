"""应用依赖装配。

注意：`AGENTS.md` 4.1 与 import-linter 的 “Services cannot depend on FastAPI”
contract 要求本层不依赖 `fastapi`，因此 `get_current_user` 只使用 `starlette`
的 `Request` / `HTTPException`（`fastapi.HTTPException` 是后者子类，
`app/main.py` 已注册的 `StarletteHTTPException` 处理器会把它转成统一错误模型）。
取舍记录见 docs/adr/ADR-004-auth-jwt.md。

T7.2 起 `get_resume_service` 装配真实 MySQL 仓储；`FakeResumeRepository` 保留在
`app/repositories/fake_resume_repository.py` 供历史参考，但不再被正式路由引用。
"""

from collections.abc import Callable, Iterator

from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from starlette.requests import Request

from app.core.error_messages import ErrorMessage
from app.integrations.copy_writer import DeepSeekCopyWriter
from app.integrations.evidence_scorer import DeepSeekEvidenceScorer
from app.integrations.interview_coach import DeepSeekInterviewCoach
from app.integrations.resume_generator import TemplateResumeGenerator
from app.integrations.skill_extractor import DeepSeekJobSkillExtractor
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.interview_repository import InterviewRepository
from app.repositories.invitation_repository import InvitationRepository
from app.repositories.job_repository import JobRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.skill_alias_repository import SkillAliasRepository
from app.repositories.student_profile_repository import StudentProfileRepository
from app.repositories.user_repository import UserRepository, session_scope
from app.schemas.auth import UserRead
from app.services.auth_service import (
    AuthError,
    AuthService,
    CurrentUserContext,
)
from app.services.interview_service import InterviewService
from app.services.invitation_service import InvitationService
from app.services.job_service import JobService
from app.services.match_service import MatchService
from app.services.organization_service import OrganizationService
from app.services.profile_service import ProfileService
from app.services.resume_service import ResumeService

UNAUTHORIZED_MESSAGE = "无效或已过期的访问令牌。"

#: BYOK 请求头（ADR-009）：Key 由客户端每次带来，服务端只在本次请求的内存里持有。
DEEPSEEK_KEY_HEADER = "X-DeepSeek-Key"


def get_auth_service() -> Iterator[AuthService]:
    with session_scope() as session:
        yield _build_auth_service(session)


def _build_auth_service(session: Session) -> AuthService:
    """一处装配，两个入口共用（`get_auth_service` 与 `_context_from_request`）。

    注入口径必须只有一个：注册那笔要「用户 + 成员关系 + 审计」同事务，
    任何一处漏装一个仓储，都会变成一个静默的「没有记审计」。
    """
    return AuthService(
        UserRepository(session),
        RefreshTokenRepository(session),
        OrganizationRepository(session),
        AuditLogRepository(session),
    )


def get_current_user(request: Request) -> UserRead:
    return _context_from_request(request).user


def get_current_context(request: Request) -> CurrentUserContext:
    """「我是谁 + 我在哪个组织 + 我能做什么」。供 `/me` 与权限依赖使用。"""
    return _context_from_request(request)


def require_permission(permission: str) -> Callable[[Request], CurrentUserContext]:
    """端点级权限闸（`ADR-021` §2.3）。

    前端拿到的 `permissions` 与这里判的同源，所以不会出现「前端给了按钮、后端 403」。
    但**前端那份不是边界**：每个需要权限的端点必须自己挂一次这个依赖。
    """

    def guard(request: Request) -> CurrentUserContext:
        context = _context_from_request(request)
        if permission not in context.permissions:
            raise HTTPException(status_code=403, detail=ErrorMessage.FORBIDDEN)
        return context

    return guard


def _context_from_request(request: Request) -> CurrentUserContext:
    """一次请求只查一次库：结果挂在 `request.state` 上，后面的依赖直接复用。

    没有这层缓存，`/me` 与每个带权限的端点都会查两遍「用户 + 成员关系」。
    """
    cached = getattr(request.state, "current_user_context", None)
    if isinstance(cached, CurrentUserContext):
        return cached
    token = _bearer_token(request)
    with session_scope() as session:
        service = _build_auth_service(session)
        try:
            context = service.get_current_context(token)
        except AuthError as error:
            raise HTTPException(status_code=401, detail=UNAUTHORIZED_MESSAGE) from error
    # 给限流用的一次性标记（ADR-013 §3）：`/jobs/{id}/match` 的键按用户而不是 IP。
    # 写在依赖里而不是在路由里，是为了让「谁都能改这个请求的限流归属」这件事不可能发生。
    request.state.rate_limit_user_id = context.user.id
    request.state.current_user_context = context
    return context


def client_ip(request: Request) -> str | None:
    """审计里的来源 IP。

    只取 `request.client`（直连对端），**不信任** `X-Forwarded-For`：本仓还没有任何
    反向代理配置（`Dockerfile.*` 里是 uvicorn 直出），此时采信那个头等于允许客户端
    在审计日志里伪造自己的来源。真上了代理再写 ADR 加白名单。
    """
    return request.client.host if request.client is not None else None


def get_profile_service() -> Iterator[ProfileService]:
    with session_scope() as session:
        yield ProfileService(StudentProfileRepository(session))


def get_organization_service() -> Iterator[OrganizationService]:
    """组织服务：组织仓储 + 审计仓储共享同一个 session。

    两者必须同 session：改角色与写审计要在一次提交里（`ADR-021` §2.5），
    分两个 session 就会出现「角色改了、审计没写」的窗口。
    """
    with session_scope() as session:
        yield OrganizationService(OrganizationRepository(session), AuditLogRepository(session))


def get_invitation_service() -> Iterator[InvitationService]:
    """装配邀请服务（B6 / ADR-023）。

    三个仓储必须共享同一个 session：兑换要「加成员 + 占用次数 + 写审计」
    在一次提交里生效，分三个 session 就会出现「人进来了、次数没扣」的窗口。
    """
    with session_scope() as session:
        yield InvitationService(
            InvitationRepository(session),
            OrganizationRepository(session),
            AuditLogRepository(session),
        )


def get_job_service(request: Request) -> Iterator[JobService]:
    """装配岗位服务（Phase 4 · B1，C1 起带上提取通道）。

    读、写、回收站那几条路径与 `get_profile_service()` 同构：一个 session、一个仓储、一个服务。
    C1 起还要装配 JD 技能提取的适配器，**Key 的取法与 `get_resume_service` 完全同构**：
    在这里取而不是在路由里取（路由拿不到 `JobService` 内部的 extractor），
    且「缺鉴权材料就 4xx」本来就是本文件的既有分工。

    「有没有 Key」只决定 extractor 装不装：`GET/POST/PUT/DELETE /jobs` 都不看这个头，
    只有 `/jobs/extract-skills` 在拿不到 Key 时由服务层抛 `ResumeApiKeyMissingError`（400）。
    这里不提前 4xx，理由与润色相同——不让不需要 Key 的路径付任何成本。

    Key 只活在这一次请求的内存里：不落库、不写日志、不进异常消息（ADR-009/011 2.B）。
    """
    api_key = request.headers.get(DEEPSEEK_KEY_HEADER, "").strip()
    with session_scope() as session:
        yield JobService(
            repository=JobRepository(session),
            extractor=DeepSeekJobSkillExtractor(api_key) if api_key else None,
            # 审核流（`ADR-024`）起，岗位服务也要写审计：仓储与审计必须同一个 session，
            # 否则「状态改了、审计没写」就成了一种可能（`ADR-021` §2.5）。
            audit=AuditLogRepository(session),
        )


def get_match_service(request: Request) -> Iterator[MatchService]:
    """装配匹配服务（ADR-013 §10 第 7c 步的**结构约束**）。

    一个 session、**三个**仓储、一个 scorer —— 这三样必须在同一次 `session_scope()` 里装好：
    复用 `get_job_service()` + `get_profile_service()` 会开两条连接、拿到两个快照，
    而「岗位与档案是同一时刻的状态」是分数可复现的前提（§13 第 8 条）。
    第三个仓储（`SkillAliasRepository`，B1）是同一句话的延伸而不是新增的例外：
    别名词表也是这次判定的输入，它必须与岗位、档案同一个快照，否则「昨天灰的今天绿了」
    连是不是同一次读数都说不清。三个仓储共享一条连接 ⇒ `_gather` 里那次
    `load_index()` 天然就是**一次**（`ADR-016` §3 不许 N+1 的结构面保证）。

    Key 的取法与 `get_job_service` 完全同构：空 Key 时 scorer 装 `None`，
    由服务层翻译成 `degrade_reason="key_missing"`，**不在这里 4xx**——
    匹配没有 Key 也要给分（§6.5），这是它与润色/提取的唯一语义差别。
    """
    api_key = request.headers.get(DEEPSEEK_KEY_HEADER, "").strip()
    with session_scope() as session:
        yield MatchService(
            job_repository=JobRepository(session),
            profile_repository=StudentProfileRepository(session),
            alias_repository=SkillAliasRepository(session),
            scorer=DeepSeekEvidenceScorer(api_key) if api_key else None,
        )


def get_interview_service(request: Request) -> Iterator[InterviewService]:
    """装配面试服务（FR-07 / B11a，ADR-022）。

    一个 session、**三个**仓储（会话 / 岗位 / 档案）、一个 coach，必须在同一次
    `session_scope()` 里装好：复用别的 `get_*_service()` 会开多条连接、拿到多个快照，
    而「这条面试基于的简历/岗位是同一时刻的状态」是出题可被复现的前提。

    Key 的取法与 `get_match_service` 完全同构：空 Key 时 coach 装 `None`，
    由服务层翻译成 `ai_degrade_reason="key_missing"`，**不在这里 4xx**——
    面试没有 Key 也要能进行（离线降级），这是它与润色/提取的唯一语义差别。
    """
    api_key = request.headers.get(DEEPSEEK_KEY_HEADER, "").strip()
    with session_scope() as session:
        yield InterviewService(
            session_repository=InterviewRepository(session),
            job_repository=JobRepository(session),
            profile_repository=StudentProfileRepository(session),
            coach=DeepSeekInterviewCoach(api_key) if api_key else None,
        )


def get_resume_service(request: Request) -> Iterator[ResumeService]:
    """装配简历服务。

    Key 在这里取而不是在路由里取，有两个既有依据：路由拿不到 `ResumeService` 内部的
    generator（它在依赖里被 new 出来），而「缺鉴权材料就 4xx」也已经是本文件的既有分工
    ——`get_current_user` 就是在依赖里抛 401 的。

    ADR-010 起「有没有 Key」只决定 `copy_writer` 装不装：`/generate` 走纯模板，
    不看这个头；`/polish` 拿不到 Key 时由服务层抛 `ResumeApiKeyMissingError`（400）。
    在这里不提前 4xx，是为了让「不需要 Key 的生成路径」保持零成本。
    """
    api_key = request.headers.get(DEEPSEEK_KEY_HEADER, "").strip()
    with session_scope() as session:
        yield ResumeService(
            profile_repository=StudentProfileRepository(session),
            resume_repository=ResumeRepository(session),
            generator=TemplateResumeGenerator(),
            copy_writer=DeepSeekCopyWriter(api_key) if api_key else None,
        )


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    token = credentials.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail=UNAUTHORIZED_MESSAGE)
    return token
