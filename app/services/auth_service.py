"""认证业务逻辑：注册、登录、刷新、登出、令牌换用户。

本层不接触 HTTP：错误以域异常抛出，由 API 层映射为状态码。

用户名归一（Phase 2 · D1，见 docs/adr/ADR-005）：
`register` / `login` 一进来就把用户名归一成 `strip().lower()`，其余逻辑只用归一后的值。
在此之前，大小写不敏感是 MySQL 的 `utf8mb4_0900_ai_ci` 隐式给的——
代码里没有一行表达这个意图，换排序规则就会静默翻转，且没有任何测试守护。
现在归一写在业务层，数据库里存的是小写，比较也是小写对小写，行为不再依赖 collation。

双 Token（Phase 2 · D2.1，ADR-005 方案 A-1）：
登录签发 access（15 分钟，无状态）+ refresh（7 天，落库可撤销）；
`refresh` 每次都**轮换**——旧记录立刻置 `revoked_at`，换发一对全新的令牌。
这样一条 refresh token 只能用一次，被截获后重放会在第二次直接 401。
"""

from datetime import datetime
from typing import NoReturn

from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.datetime_display import utc_now
from app.core.security import (
    REFRESH_TOKEN_TYPE,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    refresh_token_expires_at,
    token_hash,
    verify_password,
)
from app.repositories.refresh_token_repository import RefreshToken, RefreshTokenRepository
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
    """令牌无效、已过期、类型不符、已被撤销或指向不存在的用户。"""


class AuthService:
    def __init__(
        self,
        repository: UserRepository,
        refresh_tokens: RefreshTokenRepository,
    ) -> None:
        self._repository = repository
        self._refresh_tokens = refresh_tokens

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
        return self._issue_token_pair(user.id)

    def refresh(self, refresh_token: str) -> TokenResponse:
        """用 refresh token 换一对新令牌（旧 refresh 立即作废）。

        四道闸门缺一不可，且都统一抛 `InvalidTokenError`（→ 401）：
        1. 验签 + 过期 + 类型必须是 refresh（挡住拿 access token 来刷）；
        2. 库里必须有这个哈希（挡住自签 / 从别处伪造的合法 JWT）；
        3. 未被撤销（挡住「同一条 token 用第二次」的重放）；
        4. 主体用户仍存在（用户注销后令牌不该继续可用）。
        """
        now = utc_now()
        try:
            payload = decode_token(refresh_token, expected_type=REFRESH_TOKEN_TYPE)
        except TokenError as error:
            raise InvalidTokenError(str(error)) from error

        record = self._refresh_tokens.get_by_hash(token_hash(refresh_token))
        if record is None or record.revoked_at is not None:
            # 不区分「不存在」和「已撤销」：两者对调用方都是「重新登录」，
            # 差异化文案只会告诉攻击者「这条 token 曾经真实存在过」。
            raise InvalidTokenError("refresh token is unknown or already revoked")
        # JWT 的 exp 与库里的 expires_at 同源，这里再判一次是为了不依赖
        # 「PyJWT 一定先拒绝」这个隐含前提（时钟漂移、提前轮换都更稳妥）。
        if record.expires_at <= now:
            raise InvalidTokenError("refresh token has expired")

        user = self._repository.get_by_id(str(payload["sub"]))
        if user is None:
            raise InvalidTokenError("token subject does not exist")
        # 记录里的 user_id 必须与令牌主体一致：防止「A 的 token + B 的 sub」这类
        # 签名合法但语义错位的组合（正常流程下不可能，但校验成本为零）。
        if record.user_id != user.id:
            raise InvalidTokenError("refresh token does not belong to the subject")

        return self._rotate_token_pair(record, user, now)

    def logout(self, refresh_token: str) -> None:
        """作废一条 refresh token（幂等）。

        刻意**不验签**：撤销是「让某个值失效」的动作，即便令牌已过期或签名段
        对不上，只要哈希命中了记录就该作废它。验签反而会让「客户端存了个旧
        token」变成 401，逼用户在登出时看到一条无意义的报错。
        也刻意不区分「记录不存在」——登出接口对任何输入都返回成功，
        避免它变成一个「这个 token 是否有效」的探测接口。
        """
        record = self._refresh_tokens.get_by_hash(token_hash(refresh_token))
        if record is None:
            return
        self._refresh_tokens.revoke(record)

    def get_current_user(self, token: str) -> UserRead:
        try:
            payload = decode_token(token)
        except TokenError as error:
            raise InvalidTokenError(str(error)) from error
        user: User | None = self._repository.get_by_id(str(payload["sub"]))
        if user is None:
            raise InvalidTokenError("token subject does not exist")
        return UserRead.model_validate(user)

    def _issue_token_pair(self, user_id: str, now: datetime | None = None) -> TokenResponse:
        """签发一对令牌，并把 refresh 的哈希落库。"""
        issued_at = now or utc_now()
        refresh_token = create_refresh_token(user_id, now=issued_at)
        self._refresh_tokens.issue(
            user_id=user_id,
            token_hash=token_hash(refresh_token),
            expires_at=refresh_token_expires_at(now=issued_at),
            now=issued_at,
        )
        return self._build_response(user_id, refresh_token, issued_at)

    def _rotate_token_pair(
        self,
        record: RefreshToken,
        user: User,
        now: datetime,
    ) -> TokenResponse:
        """轮换：撤销旧记录 + 插入新记录（同一事务），再返回新的双 Token。"""
        refresh_token = create_refresh_token(user.id, now=now)
        self._refresh_tokens.rotate(
            record,
            token_hash=token_hash(refresh_token),
            expires_at=refresh_token_expires_at(now=now),
            now=now,
        )
        return self._build_response(user.id, refresh_token, now)

    def _build_response(
        self,
        user_id: str,
        refresh_token: str,
        issued_at: datetime,
    ) -> TokenResponse:
        settings = get_settings()
        return TokenResponse(
            access_token=create_access_token(user_id, now=issued_at),
            refresh_token=refresh_token,
            expires_in=settings.jwt_expire_seconds,
        )

    def _raise_conflict(self, error: IntegrityError) -> NoReturn:
        detail = str(error.orig).lower()
        if "email" in detail:
            raise EmailAlreadyExistsError("email already registered") from error
        raise UsernameAlreadyExistsError("username already registered") from error
