"""认证业务逻辑：注册、登录、刷新、登出、改密、令牌换用户。

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

**批次 B6 收口（`ADR-025`）后本文件的两处结构性变化**：

1. `register` 不再解析组织码、不再接受角色：注册就是「当散人」，一条 INSERT + 一条审计。
   角色只由邀请码在加入组织时给定（`ADR-023`）。旧的 `_resolve_organization`
   与两个组织码异常随「注册带码」入口一起删除（`ADR-025` §1.1）。
2. `login` 的入参是一个 `identifier`，按 `ADR-025` §2.3 的优先级解析：
   **用户名 > 邮箱 > 手机号**。用户名优先不是为了好看——存量库里可能真的有人叫 `a@b`，
   按形状先判邮箱会让这个人从上线那天起再也登不进来。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn

from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.datetime_display import utc_now
from app.core.permissions import UserRole, permissions_for
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
from app.repositories.audit_log_repository import AuditAction, AuditLogRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.refresh_token_repository import RefreshToken, RefreshTokenRepository
from app.repositories.user_repository import User, UserRepository
from app.schemas.auth import (
    ChangePasswordRequest,
    CurrentUserData,
    LoginRequest,
    OrganizationBrief,
    RegisterRequest,
    TokenResponse,
    UserRead,
    normalize_phone,
)


class AuthError(Exception):
    """认证域错误基类。"""


class UsernameAlreadyExistsError(AuthError):
    """用户名已被占用。"""


class EmailAlreadyExistsError(AuthError):
    """邮箱已被占用。"""


class PhoneAlreadyExistsError(AuthError):
    """手机号已被占用（`ADR-025` §2.3）。与邮箱同档：三个登录标识都必须唯一。"""


class InvalidCredentialsError(AuthError):
    """标识不存在或口令错误（对外不区分，避免账号枚举）。"""


class InvalidTokenError(AuthError):
    """令牌无效、已过期、类型不符、已被撤销或指向不存在的用户。"""


class PasswordMismatchError(AuthError):
    """改密时「当前密码」不对（`ADR-025` §2.4）。

    与 `InvalidCredentialsError` 分开：改密发生在**已登录**状态下，
    此刻不存在账号枚举的问题，而「当前密码错了」和「登录失败」是两件需要
    不同下一步动作的事（前者重输一次，后者去登录）。
    """


class PasswordUnchangedError(AuthError):
    """新密码与旧密码相同（`ADR-025` §2.4）：「改密成功但没改」是最坏的一种成功。"""


@dataclass(frozen=True)
class CurrentUserContext:
    """一次请求里的「我是谁 + 我在哪个组织 + 我能做什么」。

    单独成型而不是塞进 `UserRead`：`user` 只来自 `users` 表，角色与组织来自
    `organization_members`——两种来源合成一个可校验的模型，`model_validate(orm_user)`
    就再也对不上了（`ADR-021` §2.6）。
    """

    user: UserRead
    role: UserRole
    organization: OrganizationBrief | None
    permissions: list[str]

    def to_response_data(self) -> CurrentUserData:
        return CurrentUserData(
            user=self.user,
            role=self.role,
            organization=self.organization,
            permissions=self.permissions,
        )


class AuthService:
    def __init__(
        self,
        repository: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        organizations: OrganizationRepository,
        audit: AuditLogRepository,
    ) -> None:
        self._repository = repository
        self._refresh_tokens = refresh_tokens
        self._organizations = organizations
        self._audit = audit

    def register(self, payload: RegisterRequest, *, ip: str | None = None) -> UserRead:
        """注册：**只建账号**，不建任何组织关系（`ADR-025` §1.1）。

        归一化必须发生在任何读写之前：查重和落库都用同一个值，
        否则会先按原样查不到、再按原样插进一条大写记录。

        三件事同生共死（`ADR-021` §2.5）：用户行 + 注册审计 + 提交点。
        `commit=False` 让提交留在本方法末尾，任何一步抛异常都由 `session_scope`
        整体回滚——包括「审计没写进去」这种最容易被忽略的半截状态。
        """
        username = payload.username.strip().lower()
        if self._repository.get_by_username(username) is not None:
            raise UsernameAlreadyExistsError(username)
        password_hash = hash_password(payload.password)
        try:
            user = self._repository.create_user(
                username=username,
                password_hash=password_hash,
                email=payload.email,
                phone=payload.phone,
                commit=False,
            )
            # 注册不再产生任何角色：`role="student"` 在这里不是「授予」，而是
            # 「还没有组织关系」时 `/auth/me` 的既定口径（见 `get_current_context`）。
            # 把它写进 detail 会让人以为注册时给过一个角色。
            self._audit.record(
                action=AuditAction.USER_REGISTER,
                actor_user_id=user.id,
                organization_id=None,
                target_type="user",
                target_id=user.id,
                detail='{"organization":null}',
                ip=ip,
            )
            self._repository.commit()
        except IntegrityError as error:
            self._raise_conflict(error)
        return UserRead.model_validate(user)

    def login(self, payload: LoginRequest, *, ip: str | None = None) -> TokenResponse:
        """三种标识共用一个入口（`ADR-025` §2.1–§2.3）。"""
        user = self._resolve_identifier(payload.identifier)
        if user is None or not verify_password(payload.password, user.password_hash):
            # 标识不存在与密码错误共用一句：两者对调用方是同一件事，
            # 而区分它们等于送出一个「这个账号存不存在」的探针。
            raise InvalidCredentialsError(payload.identifier)
        self._audit.record(
            action=AuditAction.USER_LOGIN,
            actor_user_id=user.id,
            organization_id=self._organization_id_of(user.id),
            target_type="user",
            target_id=user.id,
            ip=ip,
        )
        # 登录本身没有别的业务写入，所以这一次提交只提交审计。
        self._repository.commit()
        return self._issue_token_pair(user.id)

    def change_password(
        self,
        user_id: str,
        payload: ChangePasswordRequest,
        *,
        ip: str | None = None,
    ) -> int:
        """改密（`ADR-025` §2.4）：换哈希 + 撤销全部 refresh token + 写审计，一次提交。

        返回被撤销的会话条数（调用方用不上，但它是**测试能断言的事实**：
        「撤销真的发生了」不该只由「旧令牌 401」间接证明——那条链路里还有
        验签、轮换、过期三道闸，任何一道都能让测试在撤销没生效时照样变绿）。

        当前 access token 不会被主动作废（无状态 JWT 的既有契约，`ADR-004`），
        所以客户端拿到的下一件事是「改密成功 → 跳登录页 → 用新密码登录」。
        """
        user = self._repository.get_by_id(user_id)
        if user is None:
            # 令牌合法但用户已被删：与 `refresh` 同一口径（`InvalidTokenError` → 401）。
            raise InvalidTokenError("token subject does not exist")
        if not verify_password(payload.current_password, user.password_hash):
            raise PasswordMismatchError(user.id)
        if verify_password(payload.new_password, user.password_hash):
            raise PasswordUnchangedError(user.id)
        self._repository.update_password(user, hash_password(payload.new_password))
        revoked = self._refresh_tokens.revoke_all_for_user(user.id)
        self._audit.record(
            action=AuditAction.PASSWORD_CHANGE,
            actor_user_id=user.id,
            organization_id=self._organization_id_of(user.id),
            target_type="user",
            target_id=user.id,
            detail=f'{{"revoked_sessions":{revoked}}}',
            ip=ip,
        )
        self._repository.commit()
        return revoked

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
        return UserRead.model_validate(self._load_user(token))

    def get_current_context(self, token: str) -> CurrentUserContext:
        """「我是谁 + 我在哪个组织 + 我能做什么」，一次请求只算一次。

        角色来自 `organization_members`；**没有成员关系时按 `student` 处理**，
        而不是报错——学生可以暂不绑定组织（`ADR-021` §2.4 的边界）。
        注册入口收口之后，**所有新注册账号**都从这里出发：`role=student`、
        `organization=null`，直到有人用邀请码把他们加进组织。
        """
        user = self._load_user(token)
        member = self._organizations.get_member(user.id)
        role = UserRole(member.role) if member is not None else UserRole.STUDENT
        organization = (
            self._organizations.get_by_id(member.organization_id) if member is not None else None
        )
        return CurrentUserContext(
            user=UserRead.model_validate(user),
            role=role,
            organization=(
                OrganizationBrief.model_validate(organization) if organization is not None else None
            ),
            permissions=permissions_for(role),
        )

    def _resolve_identifier(self, identifier: str) -> User | None:
        """一个字符串 → 一行用户（`ADR-025` §2.3）。

        顺序**写死**在代码里，不靠「哪个查得到用哪个」：

        1. 先按用户名查（小写归一）。存量库里可能有人叫 `a@b`、甚至叫 `13800000000`，
           用户名优先是唯一不伤存量用户的顺序；
        2. 含 `@` 才查邮箱——不含 `@` 的字符串在邮箱列里不可能命中，
           多查一次只是白跑一条 SQL；
        3. 能归一成大陆手机号才查手机号。

        第 2、3 步**只在第 1 步落空时执行**：这不是优化，而是语义——
        「用户名命中了就别再看别的列」，否则同一个字符串在两个列里各有一行时，
        命中哪一行取决于查询顺序，而那种不确定性会在生产里表现为「今天能登、明天不能」。
        """
        raw = identifier.strip().lower()
        if not raw:
            return None
        user = self._repository.get_by_username(raw)
        if user is not None:
            return user
        if "@" in raw:
            return self._repository.get_by_email(raw)
        try:
            phone = normalize_phone(raw)
        except ValueError:
            return None
        return self._repository.get_by_phone(phone)

    def _load_user(self, token: str) -> User:
        try:
            payload = decode_token(token)
        except TokenError as error:
            raise InvalidTokenError(str(error)) from error
        user: User | None = self._repository.get_by_id(str(payload["sub"]))
        if user is None:
            raise InvalidTokenError("token subject does not exist")
        return user

    def _organization_id_of(self, user_id: str) -> str | None:
        member = self._organizations.get_member(user_id)
        return member.organization_id if member is not None else None

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
        """唯一键冲突 → 精确到「哪个标识被占了」（`ADR-025` §2.3 末）。

        三个标识各有一句不同的文案：说「用户名重复」而实际撞的是手机号，
        会让用户改十次用户名也过不去。
        """
        detail = str(error.orig).lower()
        if "phone" in detail:
            raise PhoneAlreadyExistsError("phone already registered") from error
        if "email" in detail:
            raise EmailAlreadyExistsError("email already registered") from error
        raise UsernameAlreadyExistsError("username already registered") from error
