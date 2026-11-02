# ADR-004：注册/登录采用 JWT 无状态鉴权

- 状态：已接受
- 日期：2026-11-02
- 决策人：本项目人类负责人
- 关联任务：T7.1（后端注册/登录与 JWT 鉴权）

## 背景

T1–T7.4 已交付：Alembic 基线（`users` 表已存在）、fake 简历切片、import-linter 架构约束、
统一错误模型、配置 fail-fast、前端三栏编辑器。但后端没有任何认证能力，`users 表`
只有建表迁移，没有读写路径。T7.1 需要在不动简历业务逻辑、不动前端的前提下，
补全注册、登录与受保护路由的鉴权基建。

约束来自既有文档：

1. `AGENTS.md` 4.1 规定依赖方向 `api/ → services/ → repositories/ → integrations/`，
   且由 import-linter 的 5 个 contract 强制（含 “Services cannot depend on FastAPI”）。
2. `AGENTS.md` 4.3 规定所有错误响应走统一模型（`code` / `message` / `request_id`）。
3. `AGENTS.md` 4.6 规定新增依赖前必须先写 ADR——本轮新增 `pyjwt` 与 `passlib[bcrypt]`。
4. `docs/adr/ADR-001` 已定 MVP 为响应式 Web，前后端分离（Vue3 + Vite 独立部署），
   不支持 Cookie 同站假设之外的隐式身份传递。

## 决策

### 1. 认证方式：JWT（Bearer Token），不使用服务端 Session

- 签发：`POST /api/v1/auth/login` 校验用户名/口令后签发 HS256 JWT，
  载荷固定为 `sub`（用户 id）、`iat`、`exp`、`type="access"`。
- 携带：客户端用 `Authorization: Bearer <token>` 访问受保护路由。
- 校验：`GET /api/v1/auth/me` 依赖 `get_current_user`，解码令牌 → 查库 → 返回用户信息。
- 有效期：`jwt_expire_minutes`，默认 15 分钟。
- 令牌内容不含任何权限位、不含口令哈希，只用于标识“是谁”。

### 2. 配置 fail-fast

`app/core/config.py` 新增 `jwt_secret_key` / `jwt_algorithm`（默认 `HS256`）/
`jwt_expire_minutes`。

- `environment=production` 时：`jwt_secret_key` 必须存在且长度 ≥ 32，
  否则构造 `Settings` 即抛 `ValueError`，进程起不来（与 T6 的 `database_url` fail-fast 同机制）。
- 其他环境：缺省使用开发用弱密钥，保证本地与 CI 零配置可跑。

### 3. 口令存储：`passlib[bcrypt]`

> **补记（2026-11-02，人类负责人批准）**：`uv add "passlib[bcrypt]"` 默认解析到
> `bcrypt==5.0.0`，而 `passlib 1.7.4`（2020-10 后未再发版）初始化 bcrypt 后端时会在
> `detect_wrap_bug()` 里用 255 字节口令做自检；bcrypt 5.0 对 >72 字节口令改为抛
> `ValueError`（不再静默截断），后端加载失败，`CryptContext.hash()` 完全不可用。
> 该不兼容与操作系统无关，在 Linux 上同样复现。经人类负责人裁定，
> **保留 `passlib[bcrypt]`，额外约束 `bcrypt<5`**（实际解析到 4.3.0，
> 哈希形如 `$2b$12$...`，校验正常；运行时仅多一条无害的
> `(trapped) error reading bcrypt version` 日志，原因是 bcrypt 4.1 起移除了 `__about__`）。
> 未更换库，未引入 passlib/bcrypt 之外的实现。

- 只保存 bcrypt 哈希，写入 `users.password_hash`（`varchar(255)`，与既有迁移一致）。
- 任何 API 响应体都不包含 `password_hash`：对外只暴露 `UserRead`
  （`id` / `username` / `email` / `created_at`），映射在 service 层完成，
  避免“靠调用方自觉”漏字段。
- bcrypt 有 72 字节上限，`RegisterRequest` 在 schema 层按 UTF-8 字节数校验并拒绝超长口令，
  不静默截断。

### 4. 鉴权依赖放在 `app/services/dependencies.py`

`get_current_user` 与既有的 `get_resume_service` 放在同一 DI 模块。
为避免破坏 import-linter 的 “Services cannot depend on FastAPI” contract，
该函数只依赖 `starlette` 的 `Request` / `HTTPException`，不 import `fastapi`
（`fastapi.HTTPException` 是 `starlette.HTTPException` 的子类，`main.py` 已注册的
`StarletteHTTPException` 处理器会把它转成统一错误模型，401 状态码保持不变）。

### 5. 仓储直接使用 SQLAlchemy 连接真实 MySQL

`app/repositories/user_repository.py` 内定义 `User` 映射（表已由迁移建立，
本轮**不新增表、不新增迁移**）、懒加载 `Engine` 与 `Session` 工厂，
向上暴露 `create_user` / `get_by_username` / `get_by_id`。
连接串来自 `settings.database_url`，测试通过 `TEST_DATABASE_URL` 注入，
不使用 SQLite 替代。

### 6. 状态码约定

| 场景 | 状态码 | 说明 |
|---|---|---|
| 注册成功 | 201 | 返回 `user` |
| 用户名/邮箱已存在 | 400 | 走统一错误模型，`code=CLIENT_ERROR` |
| 登录成功 | 200 | 返回 `access_token` / `token_type` / `expires_in` |
| 用户名或口令错误 | 401 | 不区分“用户不存在”和“口令错误”，避免账号枚举 |
| 缺失/无效/过期令牌 | 401 | 受保护路由 |
| 请求体非法 | 422 | 既有 `VALIDATION_ERROR` |

## 后果

- 后端具备完整认证闭环，T7 的“真实用户验证”可以开始要求登录态。
- 无状态令牌无法在服务端即时吊销；本轮不做 refresh token 与黑名单，
  `jwt_expire_minutes` 取短值（15 分钟）作为缓解。登出在客户端丢弃令牌即可。
- `pyproject.toml` / `uv.lock` 新增 `pyjwt`、`passlib[bcrypt]` 两个依赖，
  并额外约束 `bcrypt<5`（见决策 3 补记）；需同步到 CI（CI 使用 `uv sync`，无需改 workflow）。
- `app/services/dependencies.py` 由此依赖 `starlette`。这是对
  `AGENTS.md` 4.1 “services 层不接触 HTTP 对象”的一次受控让步：
  import-linter 的 contract 仍然全绿（它只禁止 `fastapi`），
  但语义上 DI 模块已感知 HTTP 请求对象。若后续收紧，正确做法是把
  `get_current_user` 下移到 `app/api/`，并新增 `app.api` 专门的 `deps` 模块；
  该重构不在 T7.1 范围。
- `main.py` 的 `StarletteHTTPException` 处理器不会透传 `WWW-Authenticate` 头，
  401 响应缺少该 RFC 6750 建议头。补齐需要改 `main.py`，不在本轮授权范围内，
  已登记为遗留问题。
- `tests/test_auth.py` 会真实建表/删表（仅 `users`）。它按文件名顺序先于
  `tests/test_migrations.py` 执行，退出时 `drop_all`，因此不影响后续
  “空库 → upgrade head → downgrade base”的既有断言。
- 前端尚未接入登录态（T7.1 明确不改前端），`frontend/` 仍以匿名方式调用
  `POST /api/v1/resumes/generate`。

## 替代方案

- **服务端 Session + Cookie**：不采用。前端是独立域名的 Vite 应用，跨站 Cookie 需要
  `SameSite=None; Secure` 与 CSRF 双提交，本地 `http://localhost` 无法验证 Secure；
  且未来水平扩容要引入共享 Session 存储（Redis），而 Redis 在 Phase 1 冻结清单内。
- **JWT 存在 HttpOnly Cookie（服务端只下发 Cookie）**：不采用。同样受跨站 Cookie 与
  CSRF 约束，且调试期无法用 curl/Postman 直接复现，与 AGENTS.md 要求的
  “人类可按给定步骤复现”冲突。
- **OAuth2 Password Flow + `python-jose`**：不采用。`python-jose` 维护活跃度低、
  历史上多次 CVE；FastAPI 文档的 OAuth2 示例同样只需 `pyjwt`，
  选 `pyjwt` 可减少依赖面。前端本轮不接入，也不需要 `OAuth2PasswordBearer`
  带来的表单编码约束。
- **`bcrypt` 裸库（不经过 passlib）**：不采用。人类已拍板 `passlib[bcrypt]`；
  且 passlib 的 `CryptContext` 提供算法升级与 `deprecated="auto"` 语义，
  后续换 argon2 只需加 scheme。
- **自研 HMAC 令牌**：不采用。等同于重新实现 JWT 且没有成熟库的
  `exp` / `alg` 校验与算法混淆防护。
- **在本轮同时实现 refresh token / 令牌黑名单**：不采用。超出 T7.1 范围，
  且需要新增表（违反“不新增表”约束）。
