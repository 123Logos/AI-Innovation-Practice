"""认证路由：注册、登录、刷新、登出、当前用户。

错误码约定见 docs/adr/ADR-004-auth-jwt.md 决策 6。
所有 4xx/5xx 都由 app/main.py 的处理器转成 T6 统一错误模型。

T7.7 起 `/register` 与 `/login` 挂 IP 限流（5 次/分钟）：这两个接口是暴力破解
和「撞库」的唯一入口，且不需要登录态。限流键是客户端 IP，超限由 `main.py` 的
`RateLimitExceeded` 处理器转成 429 + 统一错误模型（`RATE_LIMITED`）。
装饰器必须写在路由装饰器**下面**（更靠近函数），这是 slowapi 的要求。

`/refresh` 与 `/logout` 刻意**不挂**同一条限流：
- `/refresh` 的凭据是 7 天期、每次使用即轮换的随机令牌，猜不出来，
  用「5 次/分钟」挡暴力破解没有意义，反而会误伤「多标签页同时续期」的正常用户；
- `/logout` 是幂等的撤销动作，本来就允许重复调用。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.error_messages import ErrorMessage
from app.core.rate_limit import AUTH_RATE_LIMIT, limiter
from app.core.responses import success_response
from app.schemas.auth import (
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
)
from app.services.auth_service import (
    AuthService,
    CurrentUserContext,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    InvalidOrganizationCodeError,
    InvalidTokenError,
    OrganizationRequiredError,
    UsernameAlreadyExistsError,
)
from app.services.dependencies import (
    client_ip,
    get_auth_service,
    get_current_context,
)

router = APIRouter(prefix="/auth", tags=["auth"])

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CurrentContextDep = Annotated[CurrentUserContext, Depends(get_current_context)]

#: 刷新/登出失败对外只说这一句：不区分「不存在 / 已撤销 / 类型不符 / 已过期」，
#: 否则这几句差异本身就是一枚「这个令牌是否真实存在过」的探测针。
REFRESH_REJECTED_MESSAGE = "无效或已过期的刷新令牌，请重新登录。"

#: 组织码相关两句（`ADR-021` §2.4）：缺什么就说缺什么，且不区分「不存在 / 已停用」。
#: 文案在 `ErrorMessage` 受信目录里，`detail` 直接引用枚举——本仓的 4xx 文案只允许
#: 来自那张表：路由里写字面量会被 `app/main.py` 的处理器替换成通用兜底文案（实测），
#: 而 `scripts/check_error_copy.py`（G3）也会当场判红。


@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit(AUTH_RATE_LIMIT)
def register(
    payload: RegisterRequest,
    request: Request,
    service: AuthServiceDep,
) -> dict[str, Any]:
    try:
        user = service.register(payload, ip=client_ip(request))
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
    except OrganizationRequiredError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.ORGANIZATION_REQUIRED,
        ) from error
    except InvalidOrganizationCodeError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.ORGANIZATION_CODE_INVALID,
        ) from error
    # `role` 回显是「不静默」的另一半：用户能当场看到自己被授予了什么身份，
    # 而不是注册完还要去猜「我那个 teacher 到底生效了没有」。
    return success_response(
        request,
        {"user": user.model_dump(mode="json"), "role": payload.role},
    )


@router.post("/login")
@limiter.limit(AUTH_RATE_LIMIT)
def login(
    payload: LoginRequest,
    request: Request,
    service: AuthServiceDep,
) -> dict[str, Any]:
    try:
        token = service.login(payload, ip=client_ip(request))
    except InvalidCredentialsError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误。",
        ) from error
    return success_response(request, token.model_dump(mode="json"))


@router.get("/me")
def read_current_user(request: Request, context: CurrentContextDep) -> dict[str, Any]:
    """当前用户 + 组织 + 角色 + 可用权限（`docs/API.md` §2 的目标契约）。

    这三格**放在 `data` 顶层**，不塞进 `user`：`user` 是「这个人是谁」，
    角色与组织是「他和某个组织的关系」，换组织不改用户（`ADR-021` §2.6）。
    """
    return success_response(request, context.to_response_data().model_dump(mode="json"))


@router.post("/refresh")
def refresh(
    payload: RefreshTokenRequest,
    request: Request,
    service: AuthServiceDep,
) -> dict[str, Any]:
    """用 refresh token 换一对新令牌（旧的立刻作废）。"""
    try:
        token = service.refresh(payload.refresh_token)
    except InvalidTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=REFRESH_REJECTED_MESSAGE,
        ) from error
    return success_response(request, token.model_dump(mode="json"))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshTokenRequest, service: AuthServiceDep) -> Response:
    """作废当前 refresh token。

    返回 204 且**恒为成功**（哪怕令牌根本不存在）：登出是幂等动作，
    重复调用、或客户端拿着一个早已轮换掉的旧令牌调用，都不该报错。
    """
    service.logout(payload.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
