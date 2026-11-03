"""认证路由：注册、登录、当前用户。

错误码约定见 docs/adr/ADR-004-auth-jwt.md 决策 6。
所有 4xx/5xx 都由 app/main.py 的处理器转成 T6 统一错误模型。

T7.7 起 `/register` 与 `/login` 挂 IP 限流（5 次/分钟）：这两个接口是暴力破解
和「撞库」的唯一入口，且不需要登录态。限流键是客户端 IP，超限由 `main.py` 的
`RateLimitExceeded` 处理器转成 429 + 统一错误模型（`RATE_LIMITED`）。
装饰器必须写在路由装饰器**下面**（更靠近函数），这是 slowapi 的要求。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.rate_limit import AUTH_RATE_LIMIT, limiter
from app.core.responses import success_response
from app.schemas.auth import LoginRequest, RegisterRequest, UserRead
from app.services.auth_service import (
    AuthService,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    UsernameAlreadyExistsError,
)
from app.services.dependencies import get_auth_service, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]


@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit(AUTH_RATE_LIMIT)
def register(
    payload: RegisterRequest,
    request: Request,
    service: AuthServiceDep,
) -> dict[str, Any]:
    try:
        user = service.register(payload)
    except UsernameAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已被注册。",
        ) from error
    except EmailAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="邮箱已被注册。",
        ) from error
    return success_response(request, {"user": user.model_dump(mode="json")})


@router.post("/login")
@limiter.limit(AUTH_RATE_LIMIT)
def login(
    payload: LoginRequest,
    request: Request,
    service: AuthServiceDep,
) -> dict[str, Any]:
    try:
        token = service.login(payload)
    except InvalidCredentialsError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误。",
        ) from error
    return success_response(request, token.model_dump(mode="json"))


@router.get("/me")
def read_current_user(request: Request, current_user: CurrentUserDep) -> dict[str, Any]:
    return success_response(request, {"user": current_user.model_dump(mode="json")})
