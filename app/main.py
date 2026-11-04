import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import router
from app.core.config import Settings, get_settings
from app.core.errors import error_response
from app.core.rate_limit import limiter
from app.services.resume_service import (
    ResumeApiKeyMissingError,
    ResumeCopyRejectedError,
    ResumeCopyUnavailableError,
)

logger = logging.getLogger(__name__)

# BYOK 架构下 Key 只允许在本次请求的内存里存在（ADR-009）。`openai` SDK 在 DEBUG 级别
# 会把整个 request options —— 包含 `Authorization: Bearer <key>` —— 打出来，一旦有人
# 用 `--log-level debug` 起服务，用户自带的 Key 就落进了日志文件。在这里按库压级，
# 比在业务代码里小心更可靠：它挡住的是第三方库的日志，不是我们的。
for _sdk_logger in ("openai", "httpx2", "httpcore", "httpx"):
    logging.getLogger(_sdk_logger).setLevel(logging.WARNING)

#: 前端实际用到的 HTTP 方法，显式列出而不放通配符 `*`。
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
#: 前端实际会带的请求头：JWT、JSON、`X-Request-ID`（追踪 id），以及 BYOK 的
#: `X-DeepSeek-Key`。少列一个，浏览器预检就会在跨源部署（手机走局域网 IP 访问）时
#: 直接拒掉这个头；本地 Vite 代理同源，测不出来。
CORS_ALLOW_HEADERS = ["Authorization", "Content-Type", "X-Request-ID", "X-DeepSeek-Key"]

#: 迁移脚本在项目根目录的 `alembic/`，用绝对路径解析，避免从别的工作目录启动时找不到。
ALEMBIC_INI_PATH = Path(__file__).resolve().parents[1] / "alembic.ini"
MIGRATION_FIX_COMMAND = "uv run alembic upgrade head"

_ANSI_RESET = "\x1b[0m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_RED = "\x1b[31m"


class DatabaseSchemaOutOfDateError(RuntimeError):
    """生产环境数据库迁移版本与代码不一致：不阻止启动就等于带病上线。"""


def _supports_color() -> bool:
    """只在真终端里上色；重定向到文件/CI 时输出纯文本，日志仍可读。"""
    return sys.stderr.isatty() and not os.environ.get("NO_COLOR")


def _emit_startup_banner(lines: list[str], color: str) -> None:
    """把告警打成带分隔线的色块。

    不走 `logging` 是因为 uvicorn 会把 warning 压成一行普通日志，夹在满屏的
    reload/access 日志里几乎看不见；这里直接写 stderr 保证「醒目」。
    """
    border = "!" * 78
    prefix, suffix = (color, _ANSI_RESET) if _supports_color() else ("", "")
    for line in (border, *lines, border):
        print(f"{prefix}{line}{suffix}", file=sys.stderr, flush=True)


def expected_migration_revisions() -> list[str]:
    """代码里 Alembic 的最新版本（存在多个 head 时全部返回）。"""
    if not ALEMBIC_INI_PATH.exists():
        return []
    script = ScriptDirectory.from_config(AlembicConfig(str(ALEMBIC_INI_PATH)))
    return list(script.get_heads())


def applied_migration_revisions(connection: Connection) -> list[str]:
    """数据库当前版本；库还没执行过任何迁移（无 `alembic_version` 表）时返回空列表。

    不先 `inspect().has_table()` 探测：多一次往返，而且「表不存在」在 MySQL 侧
    本来就是一条明确的 1146 错误，就地捕获即可。
    """
    try:
        rows = connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
    except SQLAlchemyError as error:
        logger.debug("读取 alembic_version 失败，按「未执行任何迁移」处理：%s", error)
        return []
    return [revision for revision in rows if revision]


def check_migration_state(connection: Connection, settings: Settings) -> None:
    """启动时比对「数据库已执行版本」与「代码期望版本」，实现 Fail-fast。

    背景：Phase 2 · D2.1 新增 `refresh_tokens` 表，开发库没跑迁移时后端能正常
    起来，直到用户点「登录」才 500（`Table 'refresh_tokens' doesn't exist`），
    排查成本高。这里把问题提前到启动阶段暴露：
    - `development` / `test`：只打印醒目告警，不阻塞本地开发；
    - `production`：直接抛异常让 uvicorn 启动失败，避免带病上线。
    """
    expected = expected_migration_revisions()
    if not expected:
        logger.debug("跳过迁移版本检查：未找到 alembic.ini 或没有任何迁移脚本。")
        return

    applied = applied_migration_revisions(connection)
    if set(applied) == set(expected):
        return

    expected_text = ", ".join(sorted(expected))
    applied_text = ", ".join(sorted(applied)) or "（空，数据库尚未执行任何迁移）"
    detail = (
        f"代码期望版本 {expected_text}，数据库当前版本 {applied_text}；"
        f"请先执行 `{MIGRATION_FIX_COMMAND}` 同步数据库结构。"
    )
    lines = [
        f"⚠️  数据库结构与代码版本不一致，请先执行 `{MIGRATION_FIX_COMMAND}`",
        f"    代码期望版本：{expected_text}",
        f"    数据库当前版本：{applied_text}",
        "    常见原因：拉取新代码后没有跑迁移（如 D2.1 的 refresh_tokens 表）。",
    ]
    if settings.environment == "production":
        # 生产环境必须阻断启动：继续跑下去就是运行期 500。
        _emit_startup_banner([*lines, "    ENVIRONMENT=production：已阻止启动。"], _ANSI_RED)
        raise DatabaseSchemaOutOfDateError(f"数据库结构与代码版本不一致：{detail}")

    _emit_startup_banner(
        [*lines, "    该检查在 ENVIRONMENT=production 下会直接阻止启动。"], _ANSI_YELLOW
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.database_url:
        engine = create_engine(settings.database_url)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                check_migration_state(connection, settings)
        finally:
            engine.dispose()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI 职业发展和面试指导平台 API",
        version="0.1.0",
        docs_url=None,
        redoc_url="/redoc" if settings.enable_api_docs else None,
        openapi_url="/openapi.json" if settings.enable_api_docs else None,
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=CORS_ALLOW_METHODS,
        allow_headers=CORS_ALLOW_HEADERS,
    )

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, _: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_response(
                request,
                "INTERNAL_ERROR",
                "服务暂时不可用，请稍后重试。",
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if exc.status_code == 404:
            code = "NOT_FOUND"
            message = "资源不存在。"
        elif 400 <= exc.status_code < 500:
            code = "CLIENT_ERROR"
            message = str(exc.detail)
        else:
            code = "INTERNAL_ERROR"
            message = "服务暂时不可用，请稍后重试。"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(request, code, message, details={"detail": exc.detail}),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = jsonable_encoder(exc.errors())
        body = error_response(
            request,
            "VALIDATION_ERROR",
            "请求参数校验失败。",
            details=details,
        )
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_exceeded(
        request: Request,
        exc: RateLimitExceeded,
    ) -> JSONResponse:
        """限流命中也走 T6 统一错误模型，前端不需要为 429 写特例。

        `Retry-After` 取 60 秒：当前规则是「每分钟 N 次」，最坏情况等满一个
        窗口即可重试；真要精确到秒级得读 slowapi 的 limit 对象，MVP 不值当。
        """
        return JSONResponse(
            status_code=429,
            content=error_response(
                request,
                "RATE_LIMITED",
                "请求过于频繁，请稍后再试。",
                details={"limit": str(exc.detail)},
                retryable=True,
            ),
            headers={"Retry-After": "60"},
        )

    # ADR-009 的三个 AI 分支。走专用处理器而不是在路由里 raise HTTPException，
    # 是因为通用 4xx 处理器会把 code 统一压成 CLIENT_ERROR，而前端需要按
    # `AI_KEY_MISSING` / `AI_KEY_INVALID` / `AI_SERVICE_UNAVAILABLE` 区分动作：
    # 补 Key、修 Key、还是问用户要不要改用模板。
    # 消息一律用固定文案，不拼 `str(exc)`：上游异常文本可能带 request_id、
    # 甚至带用户自己的 Key，不该原样回显到浏览器。

    @app.exception_handler(ResumeApiKeyMissingError)
    async def resume_api_key_missing(_: Request, __: ResumeApiKeyMissingError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=error_response(_, "AI_KEY_MISSING", "请先在设置中配置 DeepSeek API Key。"),
        )

    @app.exception_handler(ResumeCopyRejectedError)
    async def resume_copy_rejected(_: Request, __: ResumeCopyRejectedError) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content=error_response(
                _,
                "AI_KEY_INVALID",
                "您的 DeepSeek API Key 无效或已欠费，请前往「AI 设置」检查后重试。",
            ),
        )

    @app.exception_handler(ResumeCopyUnavailableError)
    async def resume_copy_unavailable(_: Request, __: ResumeCopyUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content=error_response(
                _,
                "AI_SERVICE_UNAVAILABLE",
                "AI 服务当前不可用（超时或上游异常），你可以重试，或改用基础模板生成。",
                retryable=True,
            ),
        )

    app.include_router(router, prefix="/api/v1")

    @app.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "message": "API 服务运行正常，请访问 /docs 查看接口文档。",
            "health": "/api/v1/health",
        }

    @app.get("/health", include_in_schema=False)
    async def health_alias() -> dict[str, str]:
        return {"status": "ok", "service": "api"}

    if settings.enable_api_docs:

        @app.get("/docs", include_in_schema=False)
        async def custom_docs() -> object:
            return get_swagger_ui_html(
                openapi_url=app.openapi_url or "/openapi.json",
                title=f"{app.title} - Swagger UI",
                swagger_js_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js",
                swagger_css_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css",
            )

    return app


app = create_app()
