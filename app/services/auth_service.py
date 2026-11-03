"""认证业务逻辑：注册、登录、令牌换用户。

本层不接触 HTTP：错误以域异常抛出，由 API 层映射为状态码。

用户名归一（Phase 2 · D1，见 docs/adr/ADR-005）：
`register` / `login` 一进来就把用户名归一成 `strip().lower()`，其余逻辑只用归一后的值。
在此之前，大小写不敏感是 MySQL 的 `utf8mb4_0900_ai_ci` 隐式给的——
代码里没有一行表达这个意图，换排序规则就会静默翻转，且没有任何测试守护。
现在归一写在业务层，数据库里存的是小写，比较也是小写对小写，行为不再依赖 collation。
"""

from typing import NoReturn

from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.security import (
    TokenError,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.repositories.user_repository import User, UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserRead


class AuthError(Exception):
    """认证域错误基类。"""


class UsernameAlreadyExistsError(AuthError):
    """用户名已被占用。"""


class EmailAlreadyExistsError(AuthError):
    """邮箱已被占用。"""


class InvalidCredentialsError(AuthError):
    """用户名不存在或口令错误（对外不区分，避免账号枚举）。"""


class InvalidTokenError(AuthError):
    """令牌无效、已过期或指向不存在的用户。"""


class AuthService:
    def __init__(self, repository: UserRepository) -> None:
        self._repository = repository

    def register(self, payload: RegisterRequest) -> UserRead:
        # 归一化必须发生在任何读写之前：查重和落库都用同一个值，
        # 否则会先按原样查不到、再按原样插进一条大写记录。
        username = payload.username.strip().lower()
        if self._repository.get_by_username(username) is not None:
            raise UsernameAlreadyExistsError(username)
        password_hash = hash_password(payload.password)
        try:
            user = self._repository.create_user(
                username=username,
                password_hash=password_hash,
                email=payload.email,
            )
        except IntegrityError as error:
            self._raise_conflict(error)
        return UserRead.model_validate(user)

    def login(self, payload: LoginRequest) -> TokenResponse:
        # 与 `register` 同口径：老的大写记录由迁移回填成小写，
        # 这里再归一一次，用户敲大写/小写都能命中同一行。
        username = payload.username.strip().lower()
        user = self._repository.get_by_username(username)
        if user is None or not verify_password(payload.password, user.password_hash):
            raise InvalidCredentialsError(username)
        settings = get_settings()
        return TokenResponse(
            access_token=create_access_token(user.id),
            expires_in=settings.jwt_expire_seconds,
        )

    def get_current_user(self, token: str) -> UserRead:
        try:
            payload = decode_token(token)
        except TokenError as error:
            raise InvalidTokenError(str(error)) from error
        user: User | None = self._repository.get_by_id(str(payload["sub"]))
        if user is None:
            raise InvalidTokenError("token subject does not exist")
        return UserRead.model_validate(user)

    def _raise_conflict(self, error: IntegrityError) -> NoReturn:
        detail = str(error.orig).lower()
        if "email" in detail:
            raise EmailAlreadyExistsError("email already registered") from error
        raise UsernameAlreadyExistsError("username already registered") from error
