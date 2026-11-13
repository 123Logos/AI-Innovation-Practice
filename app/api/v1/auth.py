"""认证路由：注册、登录、刷新、登出、改密、当前用户。

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

**`/password` 挂同一条限流**（`ADR-025` §2.4）：它是全仓第二处「有人可以反复猜密码」
的地方。攻击者拿到一条 15 分钟的 access token 之后，唯一能做的破坏性动作就是猜
当前密码并把账号锁成自己的；不限流等于把这道门敞开。用 IP 作键而不是用户 id，
是因为此刻的威胁模型正是「令牌已被换手」。

**批次 B6 收口（`ADR-025` §1.1）后 `/register` 只剩一件事**：建账号。
`organization_code` / `role` 两个字段随旧入口一起下线，路由里那两个 400 分支
（`ORGANIZATION_REQUIRED` / `ORGANIZATION_CODE_INVALID`）也一并删除——
它们已经没有触发条件，留着只会让下一个人以为还有一条带码注册的路。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.error_messages import ErrorMessage
from app.core.rate_limit import AUTH_RATE_LIMIT, limiter
from app.core.responses import success_response
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    UserRead,
)
from app.services.auth_service import (
    AuthService,
    CurrentUserContext,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    InvalidTokenError,
    PasswordMismatchError,
    PasswordUnchangedError,
    PhoneAlreadyExistsError,
    UsernameAlreadyExistsError,
)
from app.services.dependencies import (
    client_ip,
    get_auth_service,
    get_current_context,
    get_current_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CurrentContextDep = Annotated[CurrentUserContext, Depends(get_current_context)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]

#: 刷新/登出失败对外只说这一句：不区分「不存在 / 已撤销 / 类型不符 / 已过期」，
#: 否则这几句差异本身就是一枚「这个令牌是否真实存在过」的探测针。
REFRESH_REJECTED_MESSAGE = "无效或已过期的刷新令牌，请重新登录。"

#: 注册的三句冲突文案在 `ErrorMessage` 里（本仓的 4xx 文案只允许来自那张表，
#: G3 `scripts/check_error_copy.py` 会当场判红）。这里不再各写一遍字面量。


@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit(AUTH_RATE_LIMIT)
def register(
    payload: RegisterRequest,
    request: Request,
    service: AuthServiceDep,
) -> dict[str, Any]:
    """注册：建一个**散人**账号（`ADR-025` §1.1）。

    响应体只有 `user`。B5 时期回显的 `role` 一并删除：注册不再授予任何角色，
    回显一个恒为 `student` 的字段，会让「我注册的是教师」这个误解活到下一次登录。
    角色在 `/auth/me` 里看得到，那是真实来源（`organization_members`）。
    """
    # 注册是「建账号」这条路唯一的写入口，仍然守住「同一 IP 每分钟 5 次」。
    # 限流装饰器放在路由装饰器下面（更靠近函数），这是 slowapi 的要求；
    # B6 收口删掉了组织码那两条 400，但限流不能跟着丢。
    try:
        user = service.register(payload, ip=client_ip(request))
    except UsernameAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.USERNAME_EXISTS,
        ) from error
    except EmailAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.EMAIL_EXISTS,
        ) from error
    except PhoneAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.PHONE_EXISTS,
        ) from error
    return success_response(request, {"user": user.model_dump(mode="json")})


@router.post("/login")
@limiter.limit(AUTH_RATE_LIMIT)
def login(
    payload: LoginRequest,
    request: Request,
    service: AuthServiceDep,
) -> dict[str, Any]:
    """登录：`identifier` 可以是用户名 / 邮箱 / 手机号（`ADR-025` §2.3）。

    失败只有一句「用户名或密码错误」——三种标识共用同一句话，
    否则「这个邮箱注册过吗」就变成一次免费的探测。
    """
    try:
        token = service.login(payload, ip=client_ip(request))
    except InvalidCredentialsError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorMessage.INVALID_CREDENTIALS,
        ) from error
    return success_response(request, token.model_dump(mode="json"))


@router.get("/me")
def read_current_user(request: Request, context: CurrentContextDep) -> dict[str, Any]:
    """当前用户 + 组织 + 角色 + 可用权限（`docs/API.md` §2 的目标契约）。

    这三格**放在 `data` 顶层**，不塞进 `user`：`user` 是「这个人是谁」，
    角色与组织是「他和某个组织的关系」，换组织不改用户（`ADR-021` §2.6）。
    """
    return success_response(request, context.to_response_data().model_dump(mode="json"))


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(AUTH_RATE_LIMIT)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    service: AuthServiceDep,
    current_user: CurrentUserDep,
) -> Response:
    """改密（`ADR-025` §2.4）：成功后**所有** refresh token 全部作废。

    为什么返回 204 而不是一对新令牌：改密不是登录。发一对新令牌等于说
    「只要拿到过一条 access token，就能把账号永久接管」——而改密恰恰是
    用户发现「别处有人」时按的那个按钮。客户端收到 204 后应当清本地会话、
    跳登录页、用新密码登录。

    依赖顺序 `service` 在 `current_user` 之前（与简历路由同一约定）：
    服务装配失败（500）不该被鉴权失败（401）掩盖。
    """
    try:
        service.change_password(
            current_user.id,
            payload,
            ip=client_ip(request),
        )
    except PasswordMismatchError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.PASSWORD_CURRENT_WRONG,
        ) from error
    except PasswordUnchangedError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.PASSWORD_UNCHANGED,
        ) from error
    except InvalidTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorMessage.UNAUTHORIZED,
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
