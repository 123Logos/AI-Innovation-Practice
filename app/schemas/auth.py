"""认证相关 Pydantic 模型：请求体与对外响应体。

对外模型 `UserRead` 刻意不包含 `password_hash`，
映射在 service 层完成，避免任何调用方误把哈希返回给客户端。

**批次 B6 收口（`ADR-025`）后有两条结构性变化**，都在本文件里看得见：

1. `RegisterRequest` 只剩「用户名 / 密码 / 邮箱 / 手机号」。`organization_code` 与
   `role` 一起下线——注册不再是一条「加入组织」的路，统一成「注册当散人」，
   角色只能由邀请码在加入组织时给定（`ADR-023` §2.1）。`extra="forbid"` 让还在发
   这两个字段的客户端当场 422，而不是让它静默降级（`ADR-021` §2.2 的同一条理由）。
2. `LoginRequest` 从一个字段变成三种标识共用一个字段 `identifier`（邮箱 / 手机号 /
   用户名），优先级与归一化规则写在 `ADR-025` §2.2–§2.3。
"""

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.core.permissions import UserRole

BCRYPT_MAX_PASSWORD_BYTES = 72

#: 中国大陆手机号。与 `app/schemas/profile.py::PHONE_PATTERN` **同一个正则**：
#: 档案里展示的手机号与账号登录手机号如果口径不同，用户会在两个地方被两种规则拦住。
PHONE_PATTERN = r"^1[3-9]\d{9}$"

#: 手机号归一化时要剥掉的前缀。顺序即优先级：先试长的，再试短的。
_PHONE_PREFIXES = ("+86", "86")


def normalize_phone(value: str) -> str:
    """把用户可能敲出的各种写法收敛成 11 位数字，或抛 `ValueError`。

    接受：`13800000000` / `138 0000 0000` / `138-0000-0000` / `+8613800000000` / `8613800000000`。
    拒绝：其它一切（空串、座机、国际号码、少一位多一位）。

    三条口径写死：

    1. **只存 11 位数字**（`ADR-025` §2.2）：存两种写法等于让唯一键只挡住一半的重复；
    2. 剥离前缀只做一次，`+8686138…` 这种明显错拼不该被「多剥几次」救活；
    3. 剥完必须重新匹配 `PHONE_PATTERN`，不做「长度到了就放过」的宽松判断。
    """
    candidate = "".join(value.split()).replace("-", "")
    for prefix in _PHONE_PREFIXES:
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    if not _phone_matches(candidate):
        raise ValueError("phone must be a mainland China mobile number")
    return candidate


def _phone_matches(value: str) -> bool:
    """不引 `re` 的等价判定：`1` + `3-9` + 9 位数字，共 11 位。

    手写而不是 `re.fullmatch`：本模块被每个注册 / 登录请求调用一次，
    而这条规则只有两个条件（首位与第二位范围）。正则版本在 `app/schemas/profile.py`
    里仍保留一份作为档案侧的对照，两处的**规则**必须一致，实现可以不同。
    """
    if len(value) != 11 or not value.isdigit():
        return False
    return value[0] == "1" and "3" <= value[1] <= "9"


class RegisterRequest(BaseModel):
    """注册请求体。

    **`extra="forbid"` 是批次 B5 的行为变更**（`docs/adr/ADR-021` §2.2）：在这之前，
    客户端多塞的任何字段（拼错的 `organiztion_code`、自以为能提权的 `is_admin`）
    都被 Pydantic 静默丢掉，而响应是成功——「静默降级比明确拒绝更坏」。
    现在这类请求直接 422，错误位置精确到字段名。

    **B6 收口（`ADR-025` §1.1）后 `extra="forbid"` 多守一件事**：还在发 `role` 或
    `organization_code` 的客户端会 422。这是刻意的——留着它们、只允许 `student`，
    会让「我注册成了教师」变成一句用户在 `/auth/me` 里才发现的谎话。
    """

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    #: 账号手机号（≠ 简历上展示的 `student_profiles.contact`），可空。
    #: 一旦填了就**默认**是登录标识之一（`ADR-025` §2.1）。
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        """只定「长什么样」，不挑大小写；大小写归一在下面 `lower()` 一次做掉。

        Phase 2 · D1（docs/adr/ADR-005）：用户名是**大小写不敏感**的标识符，
        统一以小写存储。归一放在模型层，是因为它决定了落库的值；
        `AuthService` 入口还会再归一一次（幂等），保证不经 schema 的内部调用也安全。
        """
        candidate = value.strip().lower()
        # 归一后重新判长度：某些字符小写化后长度会变（如 'İ' → 'i̇'），
        # 只信 `Field(max_length=100)` 会让超长值一路落到数据库再炸成 500。
        if not 3 <= len(candidate) <= 100:
            raise ValueError("username must contain between 3 and 100 characters")
        if any(character.isspace() for character in candidate):
            raise ValueError("username must not contain whitespace")
        # B6 收口（ADR-025 §2.3）：用户名长得像邮箱或手机号时，登录时的
        # 「用户名 > 邮箱 > 手机号」优先级会让**别人**登不进来（他的标识被截胡）。
        # 存量无法约束，新数据从这里起不再制造新的碰撞。
        if _looks_like_email(candidate) or _phone_matches(_strip_phone_prefix(candidate)):
            raise ValueError("username must not look like an email or a mobile number")
        return candidate

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_bytes(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip().lower()
        if not candidate:
            return None
        local_part, separator, domain = candidate.partition("@")
        if not separator or not local_part or not domain or "@" in domain:
            raise ValueError("email format is invalid")
        return candidate

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        """空串与 `None` 同等（= 没填），其余必须能归一成 11 位数字。"""
        if value is None:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        return normalize_phone(candidate)


def validate_password_bytes(value: str) -> str:
    """注册与改密**共用**的一条密码规则（`ADR-025` §2.4）。

    单独成函数而不是复制两遍校验器：改密如果放宽了上限，用户就能把密码改成一个
    注册时根本不可能设置的值，而下次登录照样能过——这类「两个入口两套规则」的
    漏洞只会被运气发现。
    """
    # bcrypt 只处理前 72 字节；超过就拒绝，绝不静默截断。
    if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError("password must not exceed 72 bytes when encoded as UTF-8")
    return value


def _looks_like_email(value: str) -> bool:
    """与 `RegisterRequest.validate_email` 同一形状判定，但不做归一。

    只作为「用户名不许长得像邮箱」的判据使用：`a@b` 这种没有点号的也会命中，
    而它正是历史库里真实存在过的形态（`ADR-025` §2.3 讲的存量碰撞）。
    """
    local_part, separator, domain = value.partition("@")
    return bool(separator) and bool(local_part) and bool(domain) and "@" not in domain


def _strip_phone_prefix(value: str) -> str:
    for prefix in _PHONE_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


class LoginRequest(BaseModel):
    """登录请求体：一个 `identifier`，三种形状（`ADR-025` §2.1）。

    `validation_alias` 让旧客户端继续发 `{"username": …}`：这是**输入别名**，
    不是第二套语义——两者进同一个解析流程，行为完全一致。等旧客户端清零后
    删掉别名即可，服务层一行都不用改。
    """

    identifier: str = Field(
        min_length=1,
        max_length=254,
        validation_alias=AliasChoices("identifier", "username"),
    )
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    """`POST /auth/password` 的请求体（`ADR-025` §2.4）。

    `extra="forbid"`：改密是安全动作，多塞一个 `user_id` 之类的字段不该被静默忽略——
    虽然服务层只认 JWT 里的用户，但「发了什么就处理什么」比「悄悄丢掉」更容易排障。
    """

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_bytes(value)


class RefreshTokenRequest(BaseModel):
    """`POST /auth/refresh` 与 `POST /auth/logout` 共用的请求体。

    `max_length` 只做防御：JWT 一般 300–500 字符，给到 4096 是为了容纳
    未来可能的更长声明，同时挡住「往这个接口灌 MB 级字符串」这种廉价消耗。
    这里**不做** JWT 结构校验——那不是 schema 的职责，且校验结果不该
    通过 422/401 的差异泄露给攻击者（统一在 service 层判 401）。
    """

    refresh_token: str = Field(min_length=1, max_length=4096)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str | None = None
    #: 登录标识之一的手机号（`ADR-025` §2.1）。可空：存量账号都没有。
    phone: str | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    """登录与刷新共用同一响应结构，前端可以无差别处理。

    `expires_in` 依然只描述 **access token** 的寿命（秒）：
    它决定前端什么时候该去刷新；refresh token 的 7 天寿命属于服务端状态
    （可被撤销、可被轮换），不通过响应体对外承诺。
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class OrganizationBrief(BaseModel):
    """`GET /me` 里的组织摘要（`docs/adr/ADR-021` §2.6）。

    只给「我是谁的下属」必须的三格：`code` 是邀请码（管理页要展示给用户转发），
    `name` 是显示名，`kind` 决定前端文案（学校 / 学院 / 企业）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    kind: str


class CurrentUserData(BaseModel):
    """`GET /me` 的 `data`。

    **角色与组织放在顶层、不塞进 `user`**（ADR-021 §2.6）：`user` 是「这个人是谁」，
    角色/组织是「他和某个组织的关系」，两者生命周期不同（换组织不改用户）。
    塞在一起会让 `UserRead` 同时承担两种来源的数据，`model_validate(orm_user)` 就再也对不上。

    `permissions` 与后端判定同源（都读 `app/core/permissions.py::ROLE_PERMISSIONS`），
    前端据此决定菜单；但它**不是**权限边界，每个端点自己再判一次。
    """

    user: UserRead
    role: UserRole
    organization: OrganizationBrief | None = None
    permissions: list[str]
