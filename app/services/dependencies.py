"""应用依赖装配。

注意：`AGENTS.md` 4.1 与 import-linter 的 “Services cannot depend on FastAPI”
contract 要求本层不依赖 `fastapi`，因此 `get_current_user` 只使用 `starlette`
的 `Request` / `HTTPException`（`fastapi.HTTPException` 是后者子类，
`app/main.py` 已注册的 `StarletteHTTPException` 处理器会把它转成统一错误模型）。
取舍记录见 docs/adr/ADR-004-auth-jwt.md。
"""

from collections.abc import Iterator

from starlette.exceptions import HTTPException
from starlette.requests import Request

from app.integrations.resume_generator import TemplateResumeGenerator
from app.repositories.fake_resume_repository import (
    FakeResumeRepository,
    get_fake_resume_repository,
)
from app.repositories.user_repository import UserRepository, session_scope
from app.schemas.auth import UserRead
from app.services.auth_service import AuthError, AuthService
from app.services.resume_service import ResumeService

UNAUTHORIZED_MESSAGE = "无效或已过期的访问令牌。"


def get_resume_service() -> ResumeService:
    repository: FakeResumeRepository = get_fake_resume_repository()
    return ResumeService(
        repository=repository,
        generator=TemplateResumeGenerator(),
    )


def get_auth_service() -> Iterator[AuthService]:
    with session_scope() as session:
        yield AuthService(UserRepository(session))


def get_current_user(request: Request) -> UserRead:
    token = _bearer_token(request)
    with session_scope() as session:
        service = AuthService(UserRepository(session))
        try:
            return service.get_current_user(token)
        except AuthError as error:
            raise HTTPException(status_code=401, detail=UNAUTHORIZED_MESSAGE) from error


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    token = credentials.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail=UNAUTHORIZED_MESSAGE)
    return token
