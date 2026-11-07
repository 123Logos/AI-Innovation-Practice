"""应用依赖装配。

注意：`AGENTS.md` 4.1 与 import-linter 的 “Services cannot depend on FastAPI”
contract 要求本层不依赖 `fastapi`，因此 `get_current_user` 只使用 `starlette`
的 `Request` / `HTTPException`（`fastapi.HTTPException` 是后者子类，
`app/main.py` 已注册的 `StarletteHTTPException` 处理器会把它转成统一错误模型）。
取舍记录见 docs/adr/ADR-004-auth-jwt.md。

T7.2 起 `get_resume_service` 装配真实 MySQL 仓储；`FakeResumeRepository` 保留在
`app/repositories/fake_resume_repository.py` 供历史参考，但不再被正式路由引用。
"""

from collections.abc import Iterator

from starlette.exceptions import HTTPException
from starlette.requests import Request

from app.integrations.copy_writer import DeepSeekCopyWriter
from app.integrations.evidence_scorer import DeepSeekEvidenceScorer
from app.integrations.resume_generator import TemplateResumeGenerator
from app.integrations.skill_extractor import DeepSeekJobSkillExtractor
from app.repositories.job_repository import JobRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.student_profile_repository import StudentProfileRepository
from app.repositories.user_repository import UserRepository, session_scope
from app.schemas.auth import UserRead
from app.services.auth_service import AuthError, AuthService
from app.services.job_service import JobService
from app.services.match_service import MatchService
from app.services.profile_service import ProfileService
from app.services.resume_service import ResumeService

UNAUTHORIZED_MESSAGE = "无效或已过期的访问令牌。"

#: BYOK 请求头（ADR-009）：Key 由客户端每次带来，服务端只在本次请求的内存里持有。
DEEPSEEK_KEY_HEADER = "X-DeepSeek-Key"


def get_auth_service() -> Iterator[AuthService]:
    with session_scope() as session:
        yield AuthService(UserRepository(session), RefreshTokenRepository(session))


def get_current_user(request: Request) -> UserRead:
    token = _bearer_token(request)
    with session_scope() as session:
        service = AuthService(UserRepository(session), RefreshTokenRepository(session))
        try:
            user = service.get_current_user(token)
        except AuthError as error:
            raise HTTPException(status_code=401, detail=UNAUTHORIZED_MESSAGE) from error
    # 给限流用的一次性标记（ADR-013 §3）：`/jobs/{id}/match` 的键按用户而不是 IP。
    # 写在依赖里而不是在路由里，是为了让「谁都能改这个请求的限流归属」这件事不可能发生。
    request.state.rate_limit_user_id = user.id
    return user


def get_profile_service() -> Iterator[ProfileService]:
    with session_scope() as session:
        yield ProfileService(StudentProfileRepository(session))


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
        )


def get_match_service(request: Request) -> Iterator[MatchService]:
    """装配匹配服务（ADR-013 §10 第 7c 步的**结构约束**）。

    一个 session、两个仓储、一个 scorer —— 这三样必须在同一次 `session_scope()` 里装好：
    复用 `get_job_service()` + `get_profile_service()` 会开两条连接、拿到两个快照，
    而「岗位与档案是同一时刻的状态」是分数可复现的前提（§13 第 8 条）。

    Key 的取法与 `get_job_service` 完全同构：空 Key 时 scorer 装 `None`，
    由服务层翻译成 `degrade_reason="key_missing"`，**不在这里 4xx**——
    匹配没有 Key 也要给分（§6.5），这是它与润色/提取的唯一语义差别。
    """
    api_key = request.headers.get(DEEPSEEK_KEY_HEADER, "").strip()
    with session_scope() as session:
        yield MatchService(
            job_repository=JobRepository(session),
            profile_repository=StudentProfileRepository(session),
            scorer=DeepSeekEvidenceScorer(api_key) if api_key else None,
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
