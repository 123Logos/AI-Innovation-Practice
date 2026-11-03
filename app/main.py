from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import router
from app.core.config import get_settings
from app.core.errors import error_response
from app.core.rate_limit import limiter

#: 前端实际用到的 HTTP 方法，显式列出而不放通配符 `*`。
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
#: 前端实际会带的请求头：JWT、JSON、以及 `X-Request-ID`（前端可透传追踪 id）。
CORS_ALLOW_HEADERS = ["Authorization", "Content-Type", "X-Request-ID"]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.database_url:
        with create_engine(settings.database_url).connect() as connection:
            connection.execute(text("SELECT 1"))
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
