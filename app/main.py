from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse

from app.api.router import router
from app.core.config import get_settings
from app.core.responses import error_response


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, _: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_response(request, "INTERNAL_ERROR", "服务暂时不可用，请稍后重试。"),
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
