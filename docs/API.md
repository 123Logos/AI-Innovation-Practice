> 【范围声明】本文件为 API 设计参考，非 Phase 1 实现依据。Phase 1 API 实现范围以 docs/MVP_SCOPE.md 为准；本文件中属于 MVP_SCOPE.md 冻结清单的接口，Phase 1 不实现、不排期、不建表。与 docs/MVP_SCOPE.md 或 AGENTS.md 冲突时，以后两者为准；本文件不扩大 Phase 1 范围。

# 接口文档

## 1. 基础约定

- Base URL：`/api/v1`
- Content-Type：`application/json`
- 时间：ISO 8601 UTC，例如 `2026-10-22T09:00:00Z`
- ID：UUID 字符串
- 鉴权：`Authorization: Bearer <access_token>`
- 分页：`page` 从 1 开始，`page_size` 默认 20，最大 100

### 1.1 统一响应

成功：

```json
{"data": {}, "meta": {"request_id": "req_123"}}
```

分页：

```json
{"data": [], "meta": {"page": 1, "page_size": 20, "total": 42, "request_id": "req_123"}}
```

失败：

```json
{"error": {"code": "NOT_FOUND", "message": "资源不存在。", "details": {}}, "meta": {"request_id": "req_123"}}
```

实际由代码发出的错误码（以 `app/` 全量 grep 这些字面量为准，不含测试）：`VALIDATION_ERROR`（`app/main.py::validation_exception`）、`RATE_LIMITED`（`app/main.py::rate_limit_exceeded`）、`NOT_FOUND`（`app/main.py::http_exception`）、`CLIENT_ERROR`（同上，`http_exception` 的兜底档）、`INTERNAL_ERROR`（`app/main.py::unhandled_exception`，以及 `::http_exception` 的 5xx 档）、`AI_KEY_MISSING`（`app/main.py::resume_api_key_missing`）、`AI_KEY_INVALID`（`app/main.py::resume_copy_rejected`）、`AI_SERVICE_UNAVAILABLE`（`app/main.py::resume_copy_unavailable`、`app/api/v1/jobs.py` 的提取端点）。三个 `AI_*` 码只在 `/resumes/polish` 与 `/jobs/extract-skills` 上可达（`app/main.py:239-240` 的注释写明这一沿革；`POST /jobs/{job_id}/match` **不在这个集合里**——它 AI 失败也返回 `200`，见 §4.10；`GET /jobs/{job_id}/requirement-match` 同样不在——它按定义不调 AI，见 §4.10.1），且处理器文案要求作用域中性——不得出现「润色」或「简历」（`docs/adr/ADR-011-job-board-and-skill-extraction.md` 2.F）。

以下码目前**只是本文件的设计意图**，`app/` 下没有任何一处产生它们：`AUTH_REQUIRED`、`AUTH_INVALID_CREDENTIALS`、`AUTH_FORBIDDEN`、`RESOURCE_NOT_FOUND`、`CONSENT_REQUIRED`、`CONFLICT`。未登录与令牌失效实际走 `HTTPException(401)`（`app/services/dependencies.py::_context_from_request`、`app/services/dependencies.py::_bearer_token`），被通用 4xx 处理器压成 `CLIENT_ERROR`（`app/main.py:192-194`），message 取 `UNAUTHORIZED_MESSAGE`（`app/services/dependencies.py::UNAUTHORIZED_MESSAGE`）。

`MEDIA_UNAVAILABLE` 属模拟面试，仍在冻结清单（AGENTS.md §3.2），未启用、无代码位置。

已删除的两个码（依 ADR-011 修订清单第 8 条）：`AI_JOB_FAILED` 依附于「异步 AI 任务」模型，该模型被 ADR-011 2.A 否决且从未实现；`AI_OUTPUT_UNSUPPORTED` 并入 `AI_SERVICE_UNAVAILABLE`——ADR-011 2.F 规定提取端点只复用润色那三码，一个都不新增。

错误响应必须包含稳定的 `code`、面向用户的 `message`、可供日志关联的 `request_id`；不得返回堆栈、SQL、密钥、供应商原始错误或其他租户数据。测试编号、预期 HTTP 状态、输出和处理规则见 [测试文档](TESTING.md)。

## 2. 认证与用户

> **本节的状态（2026-11-13 批次 B6 收口之后）**：注册**不再收角色与组织码**，登录标识从一个字段变成三种。
> 口径写在 `docs/adr/ADR-025`，四句话能说完：
>
> 1. `POST /auth/register` 只剩 `username`（必填，3–100）+ `password` + 可选 `email` + 可选 `phone`。**注册就是建一个「散人」账号**，不授予任何角色——角色只能由邀请码在加入组织时给定（`ADR-023` §2.1）；
> 2. `role` / `organization_code` 两个字段**已下线**。`RegisterRequest` 仍是 `extra="forbid"`：还在发这两个字段的客户端当场 **422**，而不是被静默降级成学生（`ADR-025` §1.1）；
> 3. `POST /auth/login` 收一个 `identifier`，可以是用户名 / 邮箱 / 手机号；优先级 **用户名 > 邮箱 > 手机号**（`ADR-025` §2.3）；
> 4. `POST /auth/password` 是新增的**改密**端点：成功返回 `204`，并作废该账号**全部** refresh token（`ADR-025` §2.4）。
>
> **仍未实现的部分**：「账号必须绑定组织」的**全局**强制力、院系 / 班级层级。
> 组织只在「必须依托组织的动作」上拦截——今天只有「把岗位提交审核」一处（§4.7），
> 个人功能（档案、草稿、简历、面试）对没有组织的账号**完全可用**。
> 这是 `ADR-025` §2.7 的「渐进式强制」，不是遗漏：用户可以在没有组织的情况下用完整产品，
> 只有在他要做「代表某个组织」的事情时才被要求先绑定。
>
> 已删除的错误码：`ORGANIZATION_REQUIRED` / `ORGANIZATION_CODE_INVALID`——触发它们的路由分支随旧注册入口一起下线了，
> 留着只会让下一个人以为还有一条「带码注册」的路（`app/api/v1/auth.py` 模块注释）。

### POST `/auth/register`（批次 B6 收口：只建账号）

请求（`app/schemas/auth.py::RegisterRequest`）：

```json
{"username":"zhangsan","password":"Strong-password-1","email":"student@example.com",
 "phone":"+86 138 0000 0000"}
```

返回 `201`：`{"data":{"user":{"id":"uuid","username":"zhangsan","email":"student@example.com","phone":"13800000000","created_at":"…"}}}`

字段与错误（**`error.code` 只有 `CLIENT_ERROR` / `VALIDATION_ERROR` 两种**；具体原因在 `details.reason`，见 §1.1）：

| 情形 | 结果 |
|---|---|
| `email` / `phone` 都不填 | 201，之后只能用用户名登录 |
| `phone` 写成 `+86 138 0000 0000` / `138-0000-0000` / `13800000000` | 201，库里**只存 11 位数字** `13800000000` |
| `phone` 不是大陆手机号（座机、国际号、位数不对） | `422 VALIDATION_ERROR`「手机号格式不正确，请填写中国大陆 11 位手机号。」（`ErrorMessage.CONTACT_FORMAT`，与档案里的 `contact` **同一个正则**） |
| 用户名已被占用 | `400 CLIENT_ERROR`，`details.reason="USERNAME_EXISTS"`，文案「用户名已被注册，请更换用户名或直接登录。」 |
| 邮箱已被占用 | `400 CLIENT_ERROR`，`details.reason="EMAIL_EXISTS"`，文案「邮箱已被注册，请更换邮箱或使用原账号登录。」 |
| 手机号已被占用（含同一号码的不同写法） | `400 CLIENT_ERROR`，`details.reason="PHONE_EXISTS"`，文案「这个手机号已被注册，请更换手机号或直接用手机号登录。」 |
| 用户名长得像邮箱（`a@b`）或手机号（11 位数字） | `422 VALIDATION_ERROR`，字段级 `message` 取自 `USERNAME_NOT_AN_IDENTIFIER`：「用户名不能是邮箱或手机号：邮箱请填在「邮箱」一栏，手机号请填在「手机号」一栏。」 |
| 请求体里有 `role` / `organization_code`，或任何未知字段 | `422 VALIDATION_ERROR`（`extra="forbid"`） |

**手机号归一化是口径，不是格式偏好**（`ADR-025` §2.2）：`normalize_phone` 剥掉空白与短横、剥掉一次 `+86` / `86` 前缀、
再按 `^1[3-9]\d{9}$` 重新判定。只存一种写法，唯一键才挡得住重复——存两种写法等于让同一个人能用两个号注册两次。
档案里展示的 `contact` 与这个账号手机号是**两件事**，前者在 `student_profiles`，后者是登录凭据。

**用户名不许长得像邮箱或手机号**，是登录优先级的直接后果（`ADR-025` §2.3）：登录时用户名优先匹配，
若允许新账号叫 `13800000000`，那个手机号的真实主人就再也登不进来（标识被截胡）。
存量账号无法约束（`a@b` 这种历史用户名真实存在），所以规则只对新数据生效，且登录侧用「用户名优先」保护它们。

**注册不发令牌**（响应里没有 `access_token`）：流程仍是「注册成功 → 回登录页登录」，
令牌签发只有 `/auth/login` 一个入口（`docs/adr/ADR-004`）。响应里也**没有 `role`**：
注册不授予任何角色，回显一个恒为 `student` 的字段只会让「我注册的是教师」这个误解活到下一次登录。

### POST `/auth/login`

请求（`app/schemas/auth.py::LoginRequest`）：

```json
{"identifier":"student@example.com","password":"..."}
```

`identifier` 可以是**用户名 / 邮箱 / 手机号**，三种形状走同一条解析路径。匹配优先级（`ADR-025` §2.3）：

1. **先按用户名**（大小写归一：`strip().lower()`）；
2. 用户名没命中，再按 `email` 精确匹配（邮箱在注册时已归一为小写）；
3. 最后按归一化后的 `phone` 匹配。

**用户名优先是为了保护存量**：历史库里有 `a@b` 形状的用户名，先按邮箱匹配会让这类账号永久登不进来。
新注册的用户名已经不允许长得像邮箱或手机号，所以这条优先级只会影响存量，不会影响新数据。

`{"username":"zhangsan","password":"..."}` 仍然可用：`LoginRequest.identifier` 上挂了
`validation_alias=AliasChoices("identifier","username")`，它是**输入别名**而非第二套语义——
两者进同一个解析流程，行为完全一致。旧客户端清零后删掉别名即可，服务层一行都不用改。

登录失败统一 `401`（`error.code` 是 `CLIENT_ERROR`），文案恒为 `ErrorMessage.INVALID_CREDENTIALS`「用户名或密码错误，请检查输入后重新登录。」，
**三种标识共用同一句话**：否则「这个邮箱注册过吗」就变成一次免费探测。
登录成功写一条 `user.login` 审计（`ADR-021` §2.5）。返回 `TokenResponse`（`access_token` / `refresh_token` / `token_type` / `expires_in`）。

### POST `/auth/password` — 改密（批次 B6 收口新增）

请求（`app/schemas/auth.py::ChangePasswordRequest`，`extra="forbid"`）：

```json
{"current_password":"old-password","new_password":"new-password-1"}
```

需要登录（Bearer access token）。成功返回 **`204`，无响应体**。失败：

| 情形 | 状态码（`error.code` + `details.reason`） | 文案 |
|---|---|---|
| 当前密码不正确 | `400` `CLIENT_ERROR`，`PASSWORD_CURRENT_WRONG` | 「当前密码不正确，请重新输入后再改。」 |
| 新密码与当前密码相同 | `400` `CLIENT_ERROR`，`PASSWORD_UNCHANGED` | 「新密码与当前密码相同，请换一个没在用的密码。」 |
| 未登录 / 令牌失效 | `401` `CLIENT_ERROR`，`UNAUTHORIZED` | 「登录已失效，请重新登录。」 |
| 新密码超过 72 字节（bcrypt 上限） | `422` `VALIDATION_ERROR`，字段级 `message` 取自 `PASSWORD_BYTES` | 与注册**同一条规则**（`validate_password_bytes`），不会出现「注册设不进去、改密能设进去」 |
| 多塞任何字段 | `422` `VALIDATION_ERROR` | `extra="forbid"` |

三条口径必须连着读（`ADR-025` §2.4）：

1. **改密不是登录，所以不发令牌**。返回一对新令牌等于说「只要拿到过一条 access token，就能把账号永久接管」——
   而改密恰恰是用户发现「别处有人」时按的那个按钮。客户端收到 `204` 后应**清本地会话、跳登录页、用新密码登录**。
2. **作废全部 refresh token**，不只是当前会话。被换手的旧会话必须当场失效，否则改密只挡住了「下次登录」，挡不住「已经登录的人」。
3. **挂与 `/register`、`/login` 同一条 IP 限流**（5 次/分钟）：这是全仓第二处「有人可以反复猜密码」的地方，
   不限流等于把这道门敞开。用 IP 作键而不是用户 id，因为威胁模型正是「令牌已被换手」。

### POST `/auth/refresh` / POST `/auth/logout`

刷新令牌轮换；注销使当前 refresh token 失效，返回 `204`。
两者都不挂限流：`/refresh` 的凭据是 7 天期、每次使用即轮换的随机令牌，猜不出来；
`/logout` 是幂等撤销动作，本来就允许重复调用。

### GET `/me`（批次 B5 起返回组织与权限）

返回：

```json
{"data":{"user":{"id":"…","username":"zhangsan","email":null,"phone":null,"created_at":"…"},
         "role":"teacher",
         "organization":{"id":"…","code":"AI2026","name":"示例大学","kind":"school"},
         "permissions":["interview:own","job:read","job:write","org:member:read","profile:read","profile:write","resume:read","resume:write"]}}
```

- `role` / `organization` / `permissions` 在 **`data` 顶层**，不塞进 `user`（`ADR-021` §2.6）：`user` 是「这个人是谁」，角色与组织是「他和某个组织的关系」；
- 没有组织的账号 `organization` 为 `null`，`role` 为 `student`；
- **`role` 的唯一来源是 `organization_members`**（批次 B6 收口后注册不再授予角色）：注册拿到的账号在 `/me` 里一定是 `student` + `organization: null`，
  直到用邀请码加入某个组织（§8 组织邀请）才变成 `teacher` / `school_admin`。`platform_admin` 只能由运维在库里授予，API 不可达（`test_platform_admin_cannot_be_granted_through_the_api`）；
- `user.phone` 是登录标识之一（`ADR-025`），与档案里的 `contact` 不是同一格——前者是凭据，后者是要印在纸上的联系方式。
  存量账号 `phone` 为 `null`（`alembic/versions/20261113_0023_user_phone.py` 加列时未回填）；
- `permissions` 与后端判定**同源**（都读 `app/core/permissions.py` 的 `ROLE_PERMISSIONS`），前端据此决定菜单，但它**不是**权限边界。

## 3. 学生档案与技能

> **本节 2026-11-07 重写过一次。** 它原先描述的是 `GET/PATCH /students/me/profile`
> （字段 `name/school/major/grade/city_preference/target_roles/bio`）、`/students/me/skills`
> （`skill_id/level/source/evidence_ids/last_used_at`）、`/students/me/evidence`、`/students/me/consents`
> 以及一句「删除只解除学生关联，不删除公共技能字典」——**这些端点、这些字段、那张技能字典从未实现过**，
> OpenAPI 里一个都查不到（`app/api/v1/` 下没有 `students` 路由，`skills` 表也没有 `level`/`source` 列）。
> 旧文本是设计期词汇漏进了契约文档，读它的人会以为 `target_roles` 已经存在（ADR-013 §5 才刚决定加它）。
> 下面按代码重写，每条都给了出处。

### 3.1 路由（`app/api/v1/profiles.py`，前缀 `/profiles`）

| 方法与路径 | 作用 | 出处 |
|---|---|---|
| `GET /profiles` | 列出我的档案（不含回收站） | `:48` |
| `POST /profiles` | 新建档案，**201** | `:72` |
| `GET /profiles/trash` | 回收站列表 | `:94` |
| `GET /profiles/me` | **兼容入口**：默认档案；没有默认档案时返回 `{"profile": null}`，**不是 404** | `:108` |
| `PUT /profiles/me` | **兼容入口**：整体覆盖默认档案；一份都没有时先建一份再写 | `:122` |
| `GET /profiles/{profile_id}` | 读单份档案 | `:134` |
| `PUT /profiles/{profile_id}` | 覆盖指定档案 | `:149` |
| `PUT /profiles/{profile_id}/default` | 设为默认 | `:165` |
| `PUT /profiles/{profile_id}/title` | 改档案名 | `:180` |
| `DELETE /profiles/{profile_id}` | 软删（进回收站） | `:206` |
| `PUT /profiles/{profile_id}/restore` | 从回收站恢复 | `:221` |
| `DELETE /profiles/{profile_id}/permanent` | **永久删除**——档案有这条，岗位没有（ADR-011 1.D 缺口②） | `:236` |

### 3.2 `PUT /profiles/me` 请求体（`ProfileUpsertRequest`，`app/schemas/profile.py::ProfileUpsertRequest`）

| 字段 | 约束 | 备注 |
|---|---|---|
| `full_name` | `str`，默认 `""`，≤100 | 必填的是**生成简历**时，不是保存时 |
| `email` | `str \| None`，≤254 | 空串在校验器里折成 `None` |
| `grade` | `str \| None`，≤50 | UI 已不采集（ADR-013 的反歧视条款禁止它进入匹配特征） |
| `contact` | `str`，默认 `""`，≤255 | 两端空白裁掉 |
| `target_role` | `str \| None`，≤200 | 2026-11-07 新增（ADR-013 §5）。目标岗位偏好，自由文本、不建枚举；纯空白折成 `NULL`。**`PUT` 是整体覆盖，不带这一格 = 清空**，与 `email` 同语义（`paper_size`/`theme` 那组才是「缺省不改动」） |
| `paper_size` | `PaperSize \| None` | A4 等，见 ADR-008 之后的实现 |
| `theme` | `Theme \| None` | 主题，`fe21c8b` |
| `layout_config` | `LayoutConfigInput \| None` | 排版，`a7a1fe2` |
| `skills` | `list[SkillInput]`，**≤8** | 见 §3.3 |
| `experiences` | `list[ExperienceInput]`，**≤3** | `title`/`start_date`/`end_date`/`description`/`responsibility` |
| `educations` | `list[EducationInput]`，**≤5** | `d02b176` |
| `certificates` | `list[CertificateInput]`，**≤5** | B4 / `ADR-015` §2.1：`name`（必填，≤100）、`issuer`（选填，≤100）、`issued_on`（选填，`YYYY-MM-DD`）。空白的 `issuer` 折成 `None`；进简历渲染（区块「证书」） |
| `portfolio_links` | `list[PortfolioLinkInput]`，**≤5** | B4：`label`（必填，≤100）、`url`（必填，≤500，**只收 `http://` / `https://`**——放开 scheme 等于开一条 `javascript:` 的存储型 XSS 通道）。进简历渲染（区块「作品链接」）与导出前隐私检查 |
| `city_preferences` | `list[CityPreferenceInput]`，**≤5** | B4：`city`（必填，≤50）。**不是简历区块**：不进 `content_html`，只作投递元数据 |

**三格（以及原有的 `skills` / `experiences` / `educations`）的写路径只有一套口径**（`ADR-015` §2.1）：`PUT` 时先给该档案**当前活跃行**（`deleted_at IS NULL`）盖 `deleted_at = now`，再插入本次提交的行——不再物理 `DELETE`。由此有三条必须知道的性质：

1. **没传就等于清空**：这是「整体覆盖」语义的展开，与 `target_role` 一致（想保留就把当前值原样带上）；
2. **上限只数活跃行**：历史行不参与计数，所以「存几次之后就永久加不进东西」不会发生；
3. **空行会 422**：`name` / `label` / `url` / `city` 都是 `min_length=1`，前端必须先剔除空行再提交（`frontend/src/stores/resume.ts::toUpsertPayload` 负责这件事）。

`GET /profiles/me` 与 `GET /profiles/{profile_id}` 的响应里三组都以**活跃行**返回（`CertificateRead` / `PortfolioLinkRead` / `CityPreferenceRead`，各带 `id`），顺序即用户录入顺序。

### 3.3 `skills[]` 每条的名字规矩（2026-11-07 起）

`SkillInput`（`app/schemas/resume.py:32`）= 只有 `name`（1–100）一格。

- **`name` 两端空白由服务端裁掉**，纯空白名 → 422（以前 `"   "` 能过 `min_length=1`，会往库里存一条空白技能，在简历里渲染成空条目）。这与岗位侧 `JobSkillInput.normalize_name` 同规矩，目的是让两张技能表只有一种规范化程度。
- **重名判定用 `skill_name_key` = NFKC + casefold**（`app/core/skill_names.py`，与提取端点、岗位写侧同一个函数），不是裸相等。改之前 `Python` 与 `python` 能过校验、走到 INSERT，再由 `uq_skills_profile_name`（建在大小写不敏感的 `utf8mb4_0900_ai_ci` 上）抛 `IntegrityError 1062`——用户看到的是内部错误，正确答案是「技能名称重复」的 422。全角 `Ｐython` 同理。
- 判定键**不删标点、不去内部空白**，所以 `C#` 与 `C`、`Web␣␣开发` 与 `Web␣开发` 仍是两个技能；理由与实测对照表见 ADR-011 §2.D.2。
- `self_rating` **已于 2026-11-07 删除**（迁移 `20261107_0013`；它此前在 UI 已不采集、渲染不使用、库里实测恒为 `{2:1, 3:23, 4:1}`，是一个零信息常量，理由见 ADR-013 §4）。旧客户端多发的那个键不会报错也不会入库（Pydantic 丢弃未知键），但**不要继续写它**。


## 4. 岗位

> 本章是 Phase 4（B1 / B1 补正 / B2 / B3 / C1）**已实现**的后端契约，取代本文件旧版的异步岗位分析口径。
> 代码位置：端点 `app/api/v1/jobs.py`，请求/响应模型 `app/schemas/job.py`，业务规则 `app/services/job_service.py`，ORM 与仓储 `app/repositories/job_repository.py`，DeepSeek 适配器 `app/integrations/skill_extractor.py`；表结构 `alembic/versions/20261105_0011_jobs.py`（`jobs`）、`20261105_0012_job_skills.py`（`job_skills`）与审核流两列 `alembic/versions/20261113_0022_job_review_workflow.py`。设计依据 `docs/adr/ADR-011-job-board-and-skill-extraction.md`，审核流以 `docs/adr/ADR-024-job-review-workflow.md` 为准。
> 旧版的 `POST /jobs` 返回 `202` + `ai_job_id`、以及 `POST /jobs/{job_id}/analyze` 异步任务形态**从未实现**，并被 ADR-011 2.A 明确否决（队列依赖 Redis/ARQ/Celery，仍在 AGENTS.md §3.2 冻结清单内）。

### 4.0 状态码从哪里来（改错误契约前必读）

运行时 `/openapi.json` 里，本章 **12 个操作的 `responses` 只声明成功码与 `422`**（实测导出，只读、不连库）：

| 操作 | OpenAPI summary | schema 里声明的 responses |
|---|---|---|
| `GET /api/v1/jobs` | List Jobs | `200`、`422` |
| `POST /api/v1/jobs` | Create Job | `201`、`422` |
| `POST /api/v1/jobs/extract-skills` | Extract Job Skills | `200`、`422` |
| `GET /api/v1/jobs/trash` | List Job Trash | `200` |
| `GET /api/v1/jobs/review-queue` | List Review Queue | `200`、`403` |
| `GET /api/v1/jobs/{job_id}` | Read Job | `200`、`422` |
| `POST /api/v1/jobs/{job_id}/match` | Match Job | `200`、`422` |
| `GET /api/v1/jobs/{job_id}/requirement-match` | Requirement Match Job | `200`、`422` |
| `PUT /api/v1/jobs/{job_id}` | Update Job | `200`、`422` |
| `DELETE /api/v1/jobs/{job_id}` | Delete Job | `200`、`422` |
| `POST /api/v1/jobs/{job_id}/submit-review` | Submit Job Review | `200`、`400`、`404`、`409`、`422` |
| `POST /api/v1/jobs/{job_id}/review` | Review Job | `200`、`403`、`404`、`409`、`422` |
| `PUT /api/v1/jobs/{job_id}/unpublish` | Unpublish Job | `200`、`422` |
| `PUT /api/v1/jobs/{job_id}/restore` | Restore Job | `200`、`422` |

**`PUT /api/v1/jobs/{job_id}/publish` 已删除**（`ADR-024` §2.6）：它是绕过审核的直发通道，留着它审核就只是装饰。它对任何岗位都返回 404，所以测试里凡断言「非创建者 publish 得 404」的用例都成了永真式——那类断言已改为打在 `submit-review` 上。

**`400` / `401` / `404` / `429` / `500` / `502` 不在 schema 里**——它们由 `app/main.py` 注册的异常处理器返回裸 `JSONResponse`，FastAPI 不会把处理器写进 `responses`。下面这张表是按处理器实读出来的实际语义，不要读成「schema 已声明这些码」：

| 触发条件 | HTTP | `error.code` | 代码位置 |
|---|---|---|---|
| 请求体或查询参数不合模型 | 422 | `VALIDATION_ERROR` | `app/main.py:203-215` |
| 无 `Authorization` 头 / 令牌无效或过期 | 401 | `CLIENT_ERROR`，message 为 `UNAUTHORIZED_MESSAGE` | 抛出 `app/services/dependencies.py::_context_from_request`、`app/services/dependencies.py::_bearer_token`；映射 `app/main.py:192-194` |
| 岗位不存在 / 不属于当前用户 / 已在回收站 | 404 | `NOT_FOUND`（处理器的固定文案「资源不存在。」） | 抛出 `app/api/v1/jobs.py:116-118`；映射 `app/main.py::http_exception` |
| 有登录但缺 `job:review`（只出现在 `GET /jobs/review-queue` 与 `POST /jobs/{job_id}/review`） | 403 | `CLIENT_ERROR`，`details.reason = "FORBIDDEN"` | `app/services/dependencies.py:83`；映射 `app/main.py:210-233` |
| 提取请求缺 `X-DeepSeek-Key` | 400 | `AI_KEY_MISSING`，`retryable: false` | `app/services/job_service.py:281-282` → `app/main.py:259-264` |
| DeepSeek 拒绝该 Key（鉴权失败） | 401 | `AI_KEY_INVALID` | `app/integrations/skill_extractor.py:294-298` → `app/services/job_service.py::JobService.extract_skills` → `app/main.py:266-275` |
| 上游不可用 / 输出不是可解析 JSON / 内容为空 / 无一可回溯 | 502 | `AI_SERVICE_UNAVAILABLE`，`retryable: true` | `app/main.py:277-290`（异常族 `app/services/job_service.py:281-290`、`:270-281`） |
| 输出被服务端 `max_tokens` 切断（`finish_reason == "length"`） | 502 | `AI_SERVICE_UNAVAILABLE`，`retryable: true`，**并在外层 `meta.finish_reason` 回显 `"length"`** | 就地组装 `app/api/v1/jobs.py:151-160`，文案 `EXTRACT_TRUNCATED_MESSAGE` `app/api/v1/jobs.py:62-65` |
| 提取超过 `EXTRACT_RATE_LIMIT`（`10/minute`，按客户端 IP） | 429 | `RATE_LIMITED`，`retryable: true`，响应头 `Retry-After: 60` | 常量 `app/core/rate_limit.py:39`；装饰器 `app/api/v1/jobs.py:122`；映射 `app/main.py:217-237` |
| 匹配超过 `MATCH_RATE_LIMIT`（`10/minute`，**按用户**而不是按 IP） | 429 | `RATE_LIMITED`，`retryable: true`，响应头 `Retry-After: 60` | 常量 `app/core/rate_limit.py:42`；键函数 `:45`；装饰器 `app/api/v1/jobs.py:197`；映射同一个 `app/main.py:217-237`（§4.10） |
| 未捕获异常 | 500 | `INTERNAL_ERROR` | `app/main.py:173-182` |
| 提交审核时账号还没有组织归属（`ADR-024` §2.3，第 18 轮裁定 ①） | 400 | `CLIENT_ERROR`，`details.reason = "JOB_ORGANIZATION_REQUIRED"`，文案「发布岗位需要先绑定组织，请用邀请码加入学校组织后再提交审核。」 | 抛出 `app/api/v1/jobs.py:91-113`（`_review_translate`）← `app/services/job_service.py:624-639` |
| 审核/提交时岗位状态已经不是你以为的那个 | 409 | `CLIENT_ERROR`，`details.reason = "JOB_STATE_CONFLICT"` | 同上；状态机见 §4.2 |
| 审核/提交时岗位已过截止时间（`ADR-017` §5 的动作层判据） | 409 | `CLIENT_ERROR`，`details.reason = "JOB_EXPIRED"` | `app/services/job_service.py::JobService._ensure_not_expired`（唯一判据 `app/core/deadline.py::is_expired`） |
| 审核自己提交的岗位 | 409 | `CLIENT_ERROR`，`details.reason = "JOB_SELF_REVIEW"` | `app/services/job_service.py:521-527` |

推论两条：① 改路由的 `response_model` 或 `status_code` 动不了 401/404/500/502 的错误契约，要动它们必须改 `app/main.py` 的处理器；② 前端与联调脚本不要拿 `openapi.json` 的 `responses` 键集合去枚举错误分支，那样会漏掉上表全部行。

### 4.1 身份、归属与可见性（权限矩阵）

身份只来自 JWT：`CurrentUserDep = Depends(get_current_user)`（`app/api/v1/jobs.py:52`）。`users` 表**没有 `role` 列**，角色挂在 `organization_members` 上（`ADR-021` §2.2）；审核身份由 `permissions` 里的 `job:review` 决定（`app/core/permissions.py::JOB_REVIEW`，`ADR-024` §2.2），不是「另一个用户表字段」。旧版「未审核岗位仅创建者和审核者可见」这句在 `ADR-024` 之后**部分回来**了：`pending_review` 对本组织（平台管理员跨组织）的审核人可读，其余人仍是 404。`published` 任何登录用户可读；他人的 `draft` 一律 404，不是 403（403 会暴露「该 id 存在」，先例见 `app/api/v1/profiles.py:10`，本文件与 `app/api/v1/jobs.py:11-12` 的说明）。

| # | 操作 | 允许的调用者 | 非创建者的结果 | 实际状态码 | 代码位置 |
|---|---|---|---|---|---|
| 1 | `GET /jobs` | 任何登录用户 | 列表里不会出现他人的 `draft` | 200 | `app/api/v1/jobs.py:73-106`、谓词 `app/repositories/job_repository.py:476-479` |
| 2 | `GET /jobs/{job_id}` | `published`：任何登录用户；`draft`：仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:181-193`、`app/repositories/job_repository.py:198-207`、`:458-463` |
| 3 | `POST /jobs` | 任何登录用户 | — | 201 / 422 / 401 | `app/api/v1/jobs.py:109-118` |
| 4 | `PUT /jobs/{job_id}` | 仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:341-354`、`app/repositories/job_repository.py:209-224` |
| 5 | `DELETE /jobs/{job_id}` | 仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:313-325`、`app/services/job_service.py:368-376` |
| 6 | `POST /jobs/{job_id}/submit-review`、`PUT /jobs/{job_id}/unpublish` | 仅创建者 | 404；提交审核另有 400（无组织）/ 409（状态冲突、已过期） | 200 / 400 / 404 / 409 | `app/api/v1/jobs.py:357-375`、`app/services/job_service.py:448-482`、`app/api/v1/jobs.py:406-418` |
| 7 | `POST /jobs/extract-skills` | 任何登录用户 | 无归属概念（纯计算，不落库） | 200 / 400 / 401 / 422 / 429 / 502 | `app/api/v1/jobs.py:121-161`、`app/services/job_service.py:190-214` |
| 8 | `GET /jobs/trash` | 任何登录用户 | 只返回**自己的**软删岗位（过滤在 SQL 里） | 200 | `app/api/v1/jobs.py:164-178`、`app/repositories/job_repository.py:284-295` |
| 9 | `PUT /jobs/{job_id}/restore` | 仅创建者 | 404；「岗位存在但不在回收站」同样是 404 | 200 / 404 | `app/api/v1/jobs.py:328-346`、`app/services/job_service.py:378-393` |
| 10 | `POST /jobs/{job_id}/match` | `published`：任何登录用户；`draft`：仅创建者 | 404（他人的草稿**包括**回收站里的） | 200 / 401 / 404 / 422 / 429 | `app/api/v1/jobs.py:196-226`、`app/services/match_service.py::match` |
| 11 | `GET /jobs/{job_id}/requirement-match` | `published`：任何登录用户；`draft`：仅创建者 | 同上，404 而不是 403（**与 #10 同一个 `get_readable` 谓词**） | 200 / 401 / 404 / 422 | `app/api/v1/jobs.py:303-338`、`app/services/match_service.py::requirement_match` |
| 12 | `POST /jobs/{job_id}/review` | `job:review`（teacher / school_admin / platform_admin）**且**不是创建者**且**在审核范围内（同组织；`platform_admin` 跨组织） | 范围外与不存在同为 404（教师不该知道别校有一条待审岗位）；自审 409 而不是静默放过 | 200 / 403 / 404 / 409 | `app/api/v1/jobs.py:378-403`、`app/services/job_service.py:485-541`、范围判定 `app/services/job_service.py:611-622` |
| 13 | `GET /jobs/review-queue` | `job:review`（**没有**权限直接 403：这是唯一一处 403 而不是 404 的岗位端点，因为队列本身不是某一条具体资源） | 只出本组织的 `pending_review`；`platform_admin` 跨组织 | 200 / 403 | `app/api/v1/jobs.py:229-251`、`app/services/job_service.py:543-568` |

**审核人能不能看见 `/jobs` 列表里那条 `pending_review`，与队列无关**：`GET /jobs?status=…` 的 `status` 白名单只有 `draft` / `published`（`app/api/v1/jobs.py:80`），传 `pending_review` 直接 422。待审岗位只从 `/jobs/review-queue` 出；但**详情**（#2）对范围内的审核人是放开的，否则审核面板打不开。

三条实现约束（都有代码兜住，不是口头承诺）：

- 越权 / 不存在 / 已软删在 service 层收敛成同一个 `JobNotFoundError`（`app/services/job_service.py:104-113`），api 层只有一句 `raise _not_found()`；`app/services/` 下不出现 `HTTPException`（AGENTS.md §4.1）。
- 已软删的岗位**读写都是 404**：`get_readable` 带 `deleted_at IS NULL`（`app/repositories/job_repository.py:205`），`get_owned` 默认也带（`:222-223`），只有 `restore` 走 `include_deleted=True`（`app/services/job_service.py:391`）。
- 归属校验写在 SQL 谓词里（`created_by_user_id == user_id`），不是「先查出来再在服务层比一次」（`app/repositories/job_repository.py:215-224` 的注释；`update_status` 再兜一层 `:428-431`）。

### 4.2 请求体里有什么、没有什么

`POST /jobs` 与 `PUT /jobs/{job_id}` 共用同一个模型 `JobWriteRequest`（`app/schemas/job.py:139-196`）：

| 字段 | 类型 / 约束 | 必填 | 代码位置 |
|---|---|---|---|
| `company_name` | string，1–200 | ✅ | `app/schemas/job.py:147` |
| `title` | string，1–200 | ✅ | `:169` |
| `description` | string，1–8000（JD 原文；上限即 ADR-011 2.F 的输入上限） | ✅ | `:172`、`MAX_DESCRIPTION_LENGTH` `:63` |
| `location` | string ≤160，选填 | ❌ | `:170` |
| `employment_type` | `internship` / `full_time` / `part_time` / `contract` / `other`，可空 | ❌ | `:52`、`:171` |
| `salary_min` / `salary_max` | `DECIMAL(12,2)`，0–`99999999.99`，可空；**两端都填时**才比较大小 | ❌ | `:71`、`:173-174`、校验器 `:181-194` |
| `experience_required` | string ≤100（自然语言，不筛） | ❌ | `:175` |
| `deadline` | `date`（`YYYY-MM-DD`），允许填**过去**的日期 | ❌ | `:176-178` |
| `skills` | 数组，最多 15 条（`MAX_SKILLS_PER_JOB`） | ❌，默认 `[]` | `:179`、常量 `app/repositories/job_repository.py:75` |

`skills[]` 每条收**四格**（`JobSkillInput`，`app/schemas/job.py:76-136`）：

| 字段 | 类型 / 约束 | 代码位置 |
|---|---|---|
| `name` | string，1–100；两端空白会被裁掉，裁完为空则 422 | `:113`、`:120-131` |
| `type` | `hard` / `soft` / `domain`，可空 | `:55`、`:114` |
| `evidence_text` | string ≤60（`MAX_EVIDENCE_CHARS`）；纯空白会被折成 `null` | `:116`、`:133-145`、常量 `app/integrations/skill_extractor.py:71` |
| `confidence` | `0–1`（`DECIMAL(5,4)`），可空；越界是 422 而不是静默丢弃 | `:118` |

**请求体里没有、也提交不进来的字段**（`app/schemas/job.py:8-16` 模块说明，行为是 Pydantic 默认 `extra="ignore"` 整键丢掉）：`status`、`created_by_user_id`、`origin`、`extract_model`、`extract_prompt_version`、`extracted_at`，以及旧版的 `requirements`（字符串数组，从未实现）。

四条服务端派生规则（这是「`origin` 不在请求体里」的完整含义）：

1. `POST /jobs` 恒落 `status='draft'`（`app/services/job_service.py:329-421`）；`PUT` 不改 `deleted_at`，`status` 原则上原样保留（`app/repositories/job_repository.py:364-373`），**唯一例外**是编辑 `pending_review` 的岗位会退回 `draft`（`ADR-024` §2.5，`app/services/job_service.py:439`）——审核人批的必须是最终内容，否则审的是旧文本、发的是新文本。提交审核 / 审核 / 下架是三个独立动作（§4.7）。
2. 每条技能的 `origin` 由服务层现场派生：该条 `evidence_text` 与岗位 `description` 各过四步归一化（NFKC → casefold → 去空白 → 去标点）后，前者是后者的子串 → `jd_verifiable`，否则 → `unverified`（`app/services/job_service.py:766-790`，归一化与子串判定在 `app/integrations/skill_extractor.py:189-225`；值域常量 `app/repositories/job_repository.py:69-70`）。`evidence_text` 为空即 `unverified`——空串是任何文本的子串，必须先挡掉（`app/integrations/skill_extractor.py:216-225`）。
3. 三列提取溯源戳由服务端在本次保存里盖：本次提交的技能里**只要有任何一条**被判为 `jd_verifiable`，就写入 `extract_model`（固定 `"deepseek-flash"`，`app/integrations/copy_writer.py:40`）、`extract_prompt_version`（`"v1"`，`app/integrations/skill_extractor.py:56`）、`extracted_at`（应用时钟 UTC naive，`app/services/job_service.py::JobService._to_draft`）；一条都没有则三列保持 `null`。`PUT` 会**整批重算**这三列，不是「只在有值时覆盖」（`app/repositories/job_repository.py:371-373`）。
4. 一次提交里的技能名重复先报 422「`skill names must be unique`」（`app/schemas/job.py:175-194`），判定键是 `skill_name_key()` = NFKC + casefold，**只有两步**，与上面第 2 条那个四步归一化函数是两回事（`app/core/skill_names.py`；为什么必须分开见 ADR-011 §2.D.2）。数据库侧的 `UNIQUE (job_id, name)` 名为 `uq_job_skills_job_name`（`alembic/versions/20261105_0012_job_skills.py:96`）。

过期是**读侧判定，不落状态、不写库**：`deadline is not None and deadline < date.today()`（判据 `app/core/deadline.py:15-21`，`JobService.is_expired` 只是委托它，`app/services/job_service.py:483-490`；取「今天」的调用点 `:153`）。「今天」取**应用进程时钟**，SQL 里不出现数据库侧的当日函数（`app/repositories/job_repository.py:243`、`:490-492` 的谓词只吃绑定参数）。`deadline` 为 `null` 视为长期有效。`jobs.status` 的值域是**四态** `draft` / `pending_review` / `published` / `rejected`（`app/repositories/job_repository.py::STATUS_*`，CHECK `ck_jobs_status` 见 `alembic/versions/20261113_0022_job_review_workflow.py`，`ADR-024` §2.1）——**没有 `approved`**（通过即上架，不留中间态）、**没有 `expired`，也没有 `closed`**：过期仍只由读侧判定，旧版那句「过期岗位自动转为 `expired`」作废。状态迁移只有四条：`draft|rejected --submit-review--> pending_review`、`pending_review --approve--> published`、`pending_review --reject--> rejected`、`published --unpublish--> draft`；此外「编辑 `pending_review`」与「软删后恢复」不改变这条主线。

### 4.3 GET `/jobs` — 广场与我的草稿

查询参数一共**五个**（`app/api/v1/jobs.py:74-83` 的函数签名，实测无其它参数；旧版的 `location`、`skill_id` 已删：前者本轮「存而不筛」，后者依赖并不存在的技能字典表）：

| 参数 | 类型 / 默认 | 语义 |
|---|---|---|
| `status` | `draft` \| `published`，默认 `published` | 白名单外的值由 FastAPI 直接 422（`Literal` 而非自由字符串，`app/api/v1/jobs.py:80`）。`status=draft` 只返回**本人**的草稿。**`pending_review` 不在这个白名单里**：待审队列走 `/jobs/review-queue`（§4.7），它按组织过滤、按先来先审排序、另要 `job:review`，混进广场列表会同时破坏三者 |
| `keyword` | string ≤200，可空 | 子串匹配 `company_name` **或** `title`，`autoescape=True`（`app/repositories/job_repository.py:482-489`） |
| `include_expired` | bool，默认 `false` | `true` 时把 `deadline < 今天` 的岗位一起返回（`:490-492`） |
| `page` | int ≥1，默认 1 | |
| `page_size` | int ≥1，≤100（`MAX_PAGE_SIZE`），默认 20 | `app/schemas/job.py:68-69` |

排序固定 `created_at DESC, id DESC`（`app/repositories/job_repository.py:245`、`:257`），不提供排序参数。软删的行永远不在列表里（`:253-254`）。

返回 `200`，`data.jobs` 是 `JobSummary` 数组（字段见 4.9），`meta` 带 `page` / `page_size` / `total` / `request_id`；`total` 与列表共用同一套谓词，所以筛选后它会跟着变（`app/repositories/job_repository.py:259-282`）。

### 4.4 POST `/jobs` — 录入

返回 **`201`** + 完整岗位对象（`data.job`，`JobRead`）。状态码来源 `app/api/v1/jobs.py:109`（`status.HTTP_201_CREATED`），与 OpenAPI 实测一致。**不返回 `202`，不创建 `ai_job_id`，不在本请求里发生任何岗位分析或 AI 调用**——技能条目就是请求体里 `skills[]` 那几格，由用户在草稿态逐条确认后再提交（ADR-011 2.A）。

请求示例（注意技能走结构体，`origin` 不在里面）：

```json
{
  "company_name": "示例科技",
  "title": "AI应用工程师",
  "location": "深圳",
  "employment_type": "internship",
  "description": "……JD 原文，最长 8000 字……",
  "salary_min": "8000.00",
  "salary_max": "12000.00",
  "experience_required": "3 年以上后端经验",
  "deadline": "2026-12-01",
  "skills": [
    {"name": "Python", "type": "hard", "evidence_text": "熟练掌握 Python", "confidence": "0.9"}
  ]
}
```

`skills[].evidence_text` 若确实能在 `description` 里逐字定位，落库后该条 `origin` 为 `jd_verifiable`，并连带把三列溯源戳盖上；填得对不上就是 `unverified`，三列为 `null`。客户端直接伪造 `origin` 没有任何入口（4.2）。岗位与技能子表在**一个事务**里写完（`app/repositories/job_repository.py:311-320`）。

### 4.5 GET `/jobs/{job_id}` — 详情

`published` 任何登录用户可读，他人 `draft` 与回收站里的行都是 404（`app/api/v1/jobs.py:181-193`）。返回 `200` + `data.job`（`JobRead`，含 `description` 全文与**全部**技能条目）。

### 4.6 PUT `/jobs/{job_id}` — 整体覆盖

仅创建者，请求体与 `POST /jobs` 完全同形（同一个 `JobWriteRequest`）。语义是**字段覆盖 + 技能子表先删后插**，一次提交（`app/repositories/job_repository.py:356-386`）：数组里没出现的技能会被删掉，所以前端必须回传完整列表。`status` / `deleted_at` 不变，三列溯源戳按本次提交重算（4.2 第 3 条）。非创建者或已软删 → 404。返回 `200` + `data.job`。

### 4.7 审核、状态与回收站端点

| 端点 | 方法 | 效果 | 返回 | 代码位置 |
|---|---|---|---|---|
| `/jobs/{job_id}/submit-review` | **POST** | `draft` / `rejected` → `pending_review`；把提交者的组织定格进 `jobs.organization_id` | `200` + `data.job`；无组织 `400`、状态冲突/已过期 `409` | `app/api/v1/jobs.py:357-375`、`app/services/job_service.py:448-482` |
| `/jobs/{job_id}/review` | **POST** | `pending_review` → `published`（`decision=approve`）或 `rejected`（`decision=reject`，`note` ≤200 存进 `review_note`） | `200` + `data.job`；无权限 `403`、范围外 `404`、自审/状态冲突/已过期 `409` | `app/api/v1/jobs.py:378-403`、`app/services/job_service.py:485-541` |
| `/jobs/review-queue` | GET | 本组织（`platform_admin` 跨组织）的 `pending_review`，先提交先审 | `200` + `data.jobs` + 分页 `meta`；无 `job:review` 则 `403` | `app/api/v1/jobs.py:229-251`、`app/services/job_service.py:543-568` |
| `/jobs/{job_id}/unpublish` | **PUT** | `published` → `draft`，广场立刻读不到 | `200` + `data.job` | `app/api/v1/jobs.py:406-418` |
| `/jobs/{job_id}` | DELETE | 软删除：只打 `deleted_at`，`job_skills` 一行不动（恢复后内容必须还在） | `200` + `{"data": {"job_id": "…", "deleted": true}}` | `app/api/v1/jobs.py:421-433`、`app/repositories/job_repository.py:433-443` |
| `/jobs/trash` | GET | **自己的**软删岗位，按 `deleted_at DESC, id DESC` | `200` + `data.jobs`（`JobSummary` 数组），`meta` 只有 `request_id`（**无分页参数、无 `total`**） | `app/api/v1/jobs.py:212-226`、`app/repositories/job_repository.py:284-295` |
| `/jobs/{job_id}/restore` | PUT | `deleted_at` 清回 `null`，`status` 原样保留（删掉已发布岗位再恢复，它仍是已发布） | `200` + `data.job` | `app/api/v1/jobs.py:436-452`、`app/services/job_service.py:378-393` |

**`PUT /jobs/{job_id}/publish` 已删除**（`ADR-024` §2.6）：创建者不能自己把未审内容推上广场，而广场是匹配池的唯一来源。现在「上架」至少是两个人的两件事：创建者 `submit-review`，范围内的审核人 `review`。

三条口径写在这里，因为它们都出现过「界面上什么错都不报」的形态：

- **提交前必须绑定组织**（第 18 轮裁定 ①，批次 B6 收口后仍成立）。没有组织归属的账号拿到 `400 JOB_ORGANIZATION_REQUIRED`，文案是「发布岗位需要先绑定组织，请用邀请码加入学校组织后再提交审核。」——拦在**动作层**（`app/core/action_gate.py::require_organization`），不是注册层：注册只建账号，组织靠邀请码加入（§8）。组织归属在这一刻被写进 `jobs.organization_id`，审核范围从此不靠猜。
  拦的是「提交审核」这一个动作，**不是**「建岗位草稿」：没有组织的账号照样能建、能改、能删自己的草稿，只有要把内容推给审核人时才被要求先绑定。
- **动作层过期拦截**（第 18 轮裁定 ③）：`submit-review` 与 `review` 都先过 `app/core/deadline.py::is_expired`，命中给 `409 JOB_EXPIRED`。所以**一条已经过期的岗位再也进不了广场**——测试里要造「已上架且过期」的行，只能先按未过期发布、再用 SQL 把 `deadline` 改到昨天。
- **不许自审**：`review` 遇到 `created_by_user_id == 当前用户` 给 `409 JOB_SELF_REVIEW`。教师身份可以靠邀请码拿到（`ADR-023`），所以「有权限」不等于「该审这一条」。前端在 `canReview` 里也把创建者排除掉，但那只是省一次点击，边界在后端。

**没有 `/jobs/{job_id}/close`**，也没有 `/archive`、`/expire` 之类的端点。发布与下架是创建者的两个动作，审核是第三个动作，三种身份的边界在 §4.1 #6 / #12 / #13。

路由声明顺序是契约的一部分：`/jobs/trash`、`/jobs/extract-skills` 与 `/jobs/review-queue` 必须排在 `/jobs/{job_id}` **之前**，否则静态段会被当成岗位 id 塞进路径参数，表现为「回收站打不开，404」（`app/api/v1/jobs.py:3-9`）。

岗位没有永久删除端点，也没有定时物理清理（`dev` 库里回收站的行会一直留着），裁定记录见 ADR-011 1.D 的缺口 ②。

### 4.8 POST `/jobs/extract-skills` — AI 技能提取（纯计算）

**200 同步返回**，需要两个请求头：`Authorization: Bearer <access_token>`（`app/api/v1/jobs.py:128`）与 `X-DeepSeek-Key`（BYOK，常量 `DEEPSEEK_KEY_HEADER` `app/services/dependencies.py:38`；只从请求头读，不落库、不写日志、不进异常消息）。Key 缺失或为空串时服务层抛 `ResumeApiKeyMissingError`（`app/services/job_service.py:281-282`）。

本端点**既不读库也不写库**（`app/services/job_service.py:190-203`；ADR-011 附录指定用挂在 **Engine** 上的 `before_cursor_execute` 把这条钉成断言）。提取结果先回显给用户，用户逐条删改后走 `POST/PUT /jobs` 才落库——AI 无法只凭一次点击改动数据库。

调用参数（`app/integrations/skill_extractor.py:280-293`）：`model = "deepseek-flash"`、`stream=False`、`max_tokens=1500`（`MAX_OUTPUT_TOKENS` `:61`）、`extra_body={"thinking": {"type": "disabled"}}`、`max_retries=0`、超时 30 秒（`app/integrations/copy_writer.py:43`）；不启用 `response_format`（理由见 ADR-011「替代方案」）。

请求体只有两格（`ExtractSkillsRequest`，`app/schemas/job.py:298-310`）：

```json
{"description": "……JD 原文，必填，1–8000 字……", "title": "可选岗位名，≤200 字"}
```

限流 `EXTRACT_RATE_LIMIT = "10/minute"`（`app/core/rate_limit.py:39`，按客户端 IP；已知局限是 slowapi 进程内计数、多 worker 各算一份），超限 429 `RATE_LIMITED`。装饰器顺序是 `@router.post` 在上、`@limiter.limit` 紧贴函数定义（`app/api/v1/jobs.py:121-122`）。

返回 `200`。注意**这里有两个 `meta`**：外层信封的 `meta` 只有 `request_id`（`app/core/responses.py:10-11`），七个字段在 **`data.meta`** 里：

```json
{
  "data": {
    "skills": [
      {"name": "Python", "type": "hard", "evidence": "熟练掌握 Python", "confidence": 0.9}
    ],
    "meta": {
      "model": "deepseek-flash",
      "prompt_version": "v1",
      "received_count": 20,
      "dropped_count": 5,
      "deduped_count": 0,
      "truncated": true,
      "finish_reason": "stop"
    }
  },
  "meta": {"request_id": "req_123"}
}
```

`data.skills[]` 四格（`ExtractedSkillRead`，`app/schemas/job.py:318-332`）：`name`、`type`（`hard`/`soft`/`domain` 或 `null`）、`evidence`、`confidence`（`float | null`）。字段名是 **`evidence`**，不是 `evidence_text`：这里是「模型给的原话」，还没经过落库判定；用户确认后保存时同一串文字走 `skills[].evidence_text` 进列（`app/schemas/job.py:322-323`）。数组**至少一条**（`min_length=1`，`app/schemas/job.py:374`）——一条都不剩时不是空列表而是 502。

`data.meta` 七个字段逐个的语义（模型 `app/schemas/job.py:330-359`，赋值点 `app/services/match_service.py::MatchService._to_result`）：

| 字段 | 类型 | 含义 | 值的来源 |
|---|---|---|---|
| `model` | string | 这批结果是哪个模型产出的 | 常量 `DEEPSEEK_MODEL` = `"deepseek-flash"`（`app/integrations/copy_writer.py:40`） |
| `prompt_version` | string | 这批结果是哪一版 Prompt 产出的 | 常量 `EXTRACT_PROMPT_VERSION` = `"v1"`（`app/integrations/skill_extractor.py:56`）；Prompt 每改一次 +1 |
| `received_count` | int | 模型**一共给了**几条（含后来被剔除、被合并、被切断的） | `len(extraction.skills)`（`app/services/job_service.py:326`） |
| `dropped_count` | int | 其中几条因**无法回溯 JD 原文**（或 `name`/`evidence` 不合规）被剔除 | `:251`、`:255-257`；判定 `_verify_item` `:305-326` |
| `deduped_count` | int | 其中几条与前面的条目**重名**被合并掉（保留第一次出现的） | `:252`、`:258-263`；判定键 `skill_name_key`（`app/core/skill_names.py`）。这是 ADR-011 §2.D.2 的第 7 个字段 |
| `truncated` | bool | 15 条封顶**是否真的切掉了东西** | `len(kept) > MAX_EXTRACTED_SKILLS`（`app/services/job_service.py:268`；常量 `app/integrations/skill_extractor.py:65`） |
| `finish_reason` | string \| null | 上游给的原因；用来把「被我们的 token 上限切断」与「模型不守格式」分开 | `completion.choices[0].finish_reason`（`app/integrations/skill_extractor.py:305-306`） |

三个计数之间的算术关系（`docs/adr/ADR-011-job-board-and-skill-extraction.md` §2.D.2）：`received_count = dropped_count + deduped_count + 返回条数 + 被 15 条封顶切掉的条数`，最后那格**没有对应字段**（已定不加第 8 个计数），所以 `truncated` 为真时前三项之和必然小于 `received_count`。`dropped_count` 与 `deduped_count` 各说各的、绝不互相串味：核验失败的条目不进重名集合（`app/services/job_service.py:260-263`）。核验、合并都跑在截断**之前**（`:240-246` 的注释），否则这两句前端文案都会变成假话。`truncated` 只在确实切的时候为真。

服务端强制的七条规则（不指望 Prompt，全部在 `app/services/job_service.py:216-325`）：`evidence` 归一化后必须是 `description` 归一化文本的子串，否则丢弃；`name` 为空或 >40 字（`MAX_SKILL_NAME_CHARS` `app/integrations/skill_extractor.py:75`）丢弃；`evidence` 为空或 >60 字丢弃；`type` 不在 `hard/soft/domain` 内则**保留该条**、`type` 置 `null`（`EXTRACTED_SKILL_TYPES` `:90`）；重名合并保留第一条；>15 条截断并置 `truncated=true`；一条都不剩 → 502，**绝不返回空列表**。

七种失败情形的实际落点（对应 ADR-011 附录的验收清单）：

| # | 情形 | HTTP + `error.code` | 代码位置 |
|---|---|---|---|
| 1 | 请求头没有 Key | 400 `AI_KEY_MISSING` | `app/services/job_service.py:281-282` → `app/main.py:259-264` |
| 2 | DeepSeek 拒绝该 Key | 401 `AI_KEY_INVALID` | `app/integrations/skill_extractor.py:294-298` → `app/services/job_service.py::JobService.extract_skills` → `app/main.py:266-275` |
| 3 | 网络超时 / 上游 4xx·5xx | 502 `AI_SERVICE_UNAVAILABLE` | `app/integrations/skill_extractor.py:299-303` → `app/services/job_service.py:212-213` |
| 4 | 返回内容不是可解析的 JSON 对象 | 502 `AI_SERVICE_UNAVAILABLE` | `app/integrations/skill_extractor.py:327-343` |
| 5 | 所有条目的 `evidence` 都回溯不到原文 | 502 `AI_SERVICE_UNAVAILABLE` | `app/services/job_service.py:270-281` |
| 6 | `choices[0].message.content` 为空或全空白 | 502 `AI_SERVICE_UNAVAILABLE` | `app/integrations/skill_extractor.py:320-323` |
| 7 | `finish_reason == "length"`（被我们自己的 `max_tokens` 切断） | 502 `AI_SERVICE_UNAVAILABLE` + 外层 `meta.finish_reason = "length"` | `app/integrations/skill_extractor.py:309-319` → `app/api/v1/jobs.py:151-160` |

第 7 情形单独命名、不与第 4 情形合并，是因为两者的**用户动作不同**：第 4 该重试同一请求，第 7 该「只粘 JD 的核心段落，或减少期望条数」（`EXTRACT_TRUNCATED_MESSAGE` `app/api/v1/jobs.py:62-65`）。它在 api 层就地组装，所以是「走全局处理器」的唯一例外；错误码仍是 `AI_SERVICE_UNAVAILABLE` 一个都没新增。

401 有两个互不相干的来源，前端靠 `error.code` 区分而不是靠状态码或 URL：令牌失效是 401 + `CLIENT_ERROR`（会触发刷新），`AI_KEY_INVALID` 是 401 + `AI_KEY_INVALID`（**不触发令牌刷新、不踢下线**），后者由 `frontend/src/api/client.ts::refreshPromise` 分流、并由 `frontend/src/api/__tests__/byok.spec.ts:181` 钉住。

三个 `AI_*` 码的处理器文案必须作用域中性（`app/main.py` 里三个 `resume_*` 处理器上方的公共注释，与 ADR-011 2.F）：**不得出现「润色」或「简历」**，因为提取侧复用润色的异常族、两个端点进同一组处理器。

日志里只出现计数、`finish_reason`、`request_id`，不出现 Key、JD 正文、`evidence` 内容、技能名（`app/services/job_service.py:351-369`）。

### 4.9 响应对象的字段

`JobSummary`（列表项与回收站项；**不含 `description`**，JD 最长 8000 字；技能只给**前 6 条**，`SUMMARY_SKILL_LIMIT` `app/services/job_service.py:83`、切片 `:537`）：`id`、`company_name`、`title`、`location`、`employment_type`、`salary_min`、`salary_max`、`deadline`、`status`、`created_by_user_id`、`created_at`、`updated_at`、`deleted_at`、`skills[]`、`is_expired`（`app/schemas/job.py:214-245`）。

`JobRead`（详情与所有写操作的返回；含 `description` 全文与**全部**技能）：上面的字段之外还有 `experience_required`、`review_note`（驳回理由，`ADR-024` §2.6：只有 `rejected` 时有值，重新提交即清空）、`extract_model`、`extract_prompt_version`、`extracted_at`（`app/schemas/job.py:139-196`）。`organization_id` **不出现在任何读模型里**：它是审核范围的服务端依据，前端猜不出来也不需要猜（`canReview` 只看 `job:review` 与状态）。

`skills[]` 每条（`JobSkillRead`，`app/schemas/job.py:199-211`）：`id`、`name`、`type`、`origin`（`jd_verifiable` 或 `unverified`，**只出现在响应里**）、`confidence`、`evidence_text`。

两个序列化口径（前端与联调都会撞上）：

- `Decimal` 在 `model_dump(mode="json")` 下输出为**字符串**（`"8000.00"`、`"0.9000"`），不是数字，为避免二进制浮点尾数噪声（`app/schemas/job.py:26-30`）。方向要说清：**响应侧一律是字符串**；**请求侧数字与数字串都收**（Pydantic 宽松模式转 `Decimal`），本文件 §4.4 的请求示例用字符串只是其中一种写法。`/jobs/extract-skills` 的 `data.skills[].confidence` 例外——它是 `float | null`（`app/schemas/job.py:327`，模型自报的原始数字，未经列宽约束），响应里就是 JSON 数字。
- `created_at` / `updated_at` / `extracted_at` / `deleted_at` 输出为**带 `+08:00` 偏移**的 ISO 串，例如 `2026-11-02T21:25:00+08:00`（`app/core/datetime_display.py:33-44`，调用点 `app/schemas/job.py:237-243`、`:298-308`）。这与本文件 §1「时间：ISO 8601 UTC」那句不一致——§1 属本轮范围外、尚未按实现修订。
- `is_expired` 是服务端算好的布尔值，前端不需要再实现一遍「`deadline < 今天`」（`app/schemas/job.py:233-235`）。判据对列表与详情是同一个，且不随 `include_expired` 或查看者身份变化（`app/core/deadline.py:15-21`，调用点 `app/services/job_service.py:515`、`:538`）。**比较本身自 ADR-013 §7.5 起住在 `app/core/deadline.py::is_expired`**，`JobService.is_expired` 只是委托——因为匹配侧的「剩余投递时间」必须与列表页的「已过期」用同一个函数，两处各写一份迟早漂移。
- **过期的跨端点分工**（`ADR-017`，2026-11-09）：列表**过滤**、详情与回收站**标注**、匹配只经 `terms[remaining_time] = {0.0, computed true, note "expired"}` 表达、角标 `coverage` **与时效无关**。这四个动作是四个问题，不是四份判据；「详情也过滤掉过期岗位」被明确否决（写完回读走同一个 `get_readable`，会让刚存成功的过期岗位立刻 404）。这条分工由 `tests/test_job_match.py::test_expired_job_reads_agree_across_every_endpoint` 一个测试钉住，该测试自带「截止日期挪到今天必须翻面」的反面哨兵。
- `meta.request_id` 取自请求头 `X-Request-ID`，没带时固定回 `req-local`（`app/core/responses.py:6-7`）。

### 4.10 POST `/jobs/{job_id}/match` — 人岗匹配（同步、纯计算、不落库）

`app/api/v1/jobs.py` 的 `match_job` → `app/services/match_service.py` → 纯函数层 `app/services/match_scoring.py`。契约与全部取舍见 `docs/adr/ADR-013-job-matching-and-evidence-scoring.md` §3/§6/§7/§8。

**权限与状态码**（这条端点是 §4.0 那张表里唯一「AI 失败不报错」的一行）：

| 情形 | 状态 | 说明 |
|---|---|---|
| 岗位可读（`published`，或自己的 `draft`） | `200` | 分数与三档清单 |
| 岗位不存在 / 他人草稿 / 回收站里 | `404` | **不是 403**：403 会确认「该 id 存在」（§4.1） |
| 未登录 | `401` | 身份只从 JWT 取 |
| 超过 `10/minute`（**按用户**计数） | `429` | 见下面「限流键」那条 |
| AI 失败（无 Key / Key 无效 / 超时 / 上游 5xx / 输出不可用） | **`200`** | 退化成关键词模式，原因写在 `data.meta.degrade_reason` |

最后一条是与 `/resumes/polish`、`/jobs/extract-skills` 的唯一语义差别：那两个端点失败时报错，
本端点失败时**照样给分**，所以 `app/main.py` 的三个 `AI_*` 处理器在这里永不命中——
三个 AI 码今天仍然只在**两个**端点上可达（§1.1 那句据此更新）。

**请求**：路径里一个 `job_id`，请求体为空。用的是**当前用户的默认档案**（`student_profiles.is_default`，
没有默认时退回最新一份，`app/repositories/student_profile_repository.py:223`）；
用的是哪一份由响应回显，不需要也不允许由请求指定（ADR-013 §3 决策 2）。

**响应 `data`**（`app/schemas/match.py`）：

```json
{
  "profile_id": "uuid", "profile_title": "默认简历", "job_id": "uuid",
  "score": 0.612, "weights_version": "v2",
  "terms": [{"name": "coverage", "weight": 0.45, "value": 0.75, "computed": true, "note": null}],
  "covered": [{"skill": "Python", "evidence": [{"experience_id": "uuid", "field": "description", "snippet": "用 Python 重写订单服务", "strength": 0.667}]}],
  "partial": [{"skill": "Docker", "reason": "技能名命中，但经历里找不到出处"}],
  "gaps": [{"skill": "MySQL", "action": "补一条真的用 MySQL 做过的项目经历：做了什么 → 怎么做的 → 结果如何。", "verify": "按你写的过程细节追问，能答出为什么这样选，就算验证通过。"}],
  "meta": {"evidence_mode": "keyword_fallback", "degrade_reason": "key_missing", "weight_sum_used": 0.85, "...": "其余计数见下"}
}
```

八条读这个响应时必须知道的口径：

1. **`terms[].value` 可以是 `null`**，含义是「这一项没算出来」，与「算出来是 0」是两件事。
   `computed=false` 的项**退出分母**（`meta.weight_sum_used` 如实记录这次用了多少权重）。
   今天恒不参与的是 `relevance`（`note="not_measurable_v1"`，理由与被测出的 0/40 见 ADR-013 §7.3.1）；
   `preference` 有四个原因码、三种分数（老板 2026-11-08 定分数与免加权，2026-11-09 拆原因码，ADR-013 §7.4）：
   没填目标岗位 ⇒ 免加权 `no_target_role`；词面判得出方向一致 ⇒ `contains` 1.0 / `token_overlap` 0.6；
   **词面重合不够 ⇒ `preference_below_threshold` 记 0 分并计入分母**（`0 < ratio < 0.5`）；
   **词面零重合 ⇒ `preference_conflict` 记 0 分并计入分母**。后两个**分数、加权、对结果的影响完全相同**，
   分开只是为了不说谎：一个是「信息不够」，一个是「方向相反」（旧值 `unrelated` 0.2 已删，它不参与）。
   **但 `weight_sum_used` 只是「这一项算出来了」的账，不是「一定会给你一个分数」**——见第 7 条。
   条件性不参与的是 `preference`（没填目标岗位）与 `remaining_time`（岗位没有截止时间）。
   所以「两个都是 0.61 的人」不必同义——这是把 `weight_sum_used` 放进 API 的原因，
   尽管按老板 2026-11-07 的裁定它**不进用户界面**。
2. **`covered[].evidence[].snippet` 是经历原文的连续子串**，由构造保证（服务端从原文切窗口，
   模型只对我们已经切好的片段打分，从不复述文本）。这是 ADR-013 §3 的反幻觉闸门。
3. **`meta` 是给人看的对账数字**：`candidate_pairs`（交给模型的片段数）、`verified_pairs`、
   三个 `dropped_*`、`dropped_missing_output`、`clamped_strength`、`fragments_truncated`。
   两个等式恒成立，单位不同，不要把它们相加（ADR-013 §8 更正版）：
   `candidate_pairs = verified_pairs + dropped_missing_output`；
   `len(model_items) = verified_pairs + dropped_unreferenced + dropped_unknown_skill + dropped_unusable_strength`。
4. **`gaps[].action` / `verify` 是模板文案**，按岗位技能的 `type`（`hard`/`soft`/`domain`/`null`）选，
   不经模型生成。理由写在 ADR-013 §7.2：建议给用户看，模型写得好看不等于写得对，
   而这类建议错了用户没法自检。
5. **「同一个技能」的判据不是字符串全等**，是 `app/core/skill_names.py::skill_forms_match`
   （ADR-013 §1.3 的 2026-11-08 更正）：`Python3` 顶得住 `Python`、`SPSS 26` 顶得住 `SPSS`，
   而 `C++` 顶不住 `C`、`JavaScript` 顶不住 `Java`、`MySQL` 顶不住 `SQL`。
   四条规则里**没有任何领域词表**，所以非计算机专业同样适用；缩写别名（`K8s`↔`Kubernetes`）
   不在其中——那需要一张 L1 词表，2026-11-08 裁定暂缓。
   判据同时作用于**覆盖**与**证据定位**两处：只改一处就会出现「已具备但零片段」，
   而那一格走的是下面第 6 条而不是报错。
6. **`evidence_mode="keyword_fallback"` 有两种，`degrade_reason` 是它们的分流键**：
   `null` = **零候选，不是退化**（一个片段都没切出来，根本没向模型提问，配了 Key 也一样，
   此时 `terms[evidence].note="matched_without_evidence"` 且该项记 0 分并参与加权）；
   五种非 null 值 = AI 想参与但失败。界面必须分句，因为用户动作完全不同：前者是「把经历写出来」，
   后两种是「去 AI 设置补 / 修 Key」，后三种是「稍后再试」。

7. **`score` 可以是 `null`，且现在 `score is null ⇔ meta.no_required_skills`**（老板 2026-11-08 两条裁定合成）。
   机制是**核心 term 不在 ⇒ 整个不评估**：这份分数测的是「技能要求对上了几条」，岗位一条要求技能
   都没填时没有任何东西可比，此时哪怕 `preference` 或 `remaining_time` 算得出来，也**不给分数**
   （旧行为会把 0.10 的分母重归一成整份分数，实测在真实账号上显示「100% 匹配度」，取证见 ADR-013 §5.1 末）。
   `meta.note` 在这个角落写 `not_evaluated`；`nothing_computable`（分母为 0）保留为 F1:A 的名字，
   本层已发不出来，仍由 `normalize_score` 的单测守着。
   **`terms[]` 与 `weight_sum_used` 不跟着抹平**——那几项确实算出来了，只是不构成匹配分数。
   `0.0` 仍然只有一种含义：「算出来了，零覆盖」，即岗位**有**要求技能而一条都没对上。
   两种情况不许压成一个数（证据见 `tests/test_job_match.py::test_skillless_job_is_not_evaluated_end_to_end`
   与 `::test_zero_coverage_still_returns_a_number`，一对儿）。
   界面侧：`null` 显示「—」，那一屏由「这个岗位还没有填写技能要求」那行说明负责，
   **不给用户派活**（他修不动岗位）。为什么当初要改：F1:A 之前这一格返回 `0.0`，
   而 §5.1 否决「缺项记 0 分」引用的理由恰恰是「那会让刚注册、什么都没填的用户稳定拿到 0 分」。
8. **`meta.matcher_version` 是写法判据的版本号**，与 §4.10.1 那个端点同源（同一个常量
   `app/core/skill_names.py::SKILL_MATCHER_VERSION`）。它不是为了展示，是为了让
   「角标与匹配卡各说一套」变成一次可断言的失败（判据 (c)）。什么时候必须 +1 写在那个常量的注释里。
   **2026-11-10（批次 B1）起它是 `"v2"`**：判据构成里多了「查人工别名词表兜底」这一步。
9. **`meta.alias_version` 是词表**内容**的版本号**（起始 `"v1"`，`ADR-016` §2.5 第三条轴），与
   `matcher_version` 分开的理由只有一句：规则是代码、词表是数据，「分数变了」必须能说出是哪一轴造成的。
   **空表也发这一格**。它对应的表是 `skill_aliases`（`canonical_key`/`alias_key`/`scope`/`source`，
   唯一键含 `scope`）：判定**双向**、存**单向**，且只在 `app/core/skill_names.py::skill_forms_match`
   的四条词面规则全部判否之后才查——**判重、存储、回显一律不受影响**（§2.1 选 (c)）。
   `terms[preference]` 因此多出第四来路 `note = "alias_match"`，值 **0.6**（不是 1.0，理由见 §2.4），
   档位顺序写死：`contains` 1.0 → `token_overlap` 0.6 → `alias_match` 0.6 → 两格 0.0。
   词表今天**是空的**（第一批候选还没经老板逐条批准，`ADR-016` §7），所以这一格的值在任何真实响应里
   都是 `v1`，且没有任何配对走过 `alias_match`——别把「有了这一层」读成「匹配变准了」。
10. **`meta.generated_at` 与 `meta.data_updated_at` 是两格，不是一格**（批次 B3 / FR-05「更新时间和数据新鲜度」，决策见 `ADR-020`）：
   前者是**本次读数的组装时刻**（应用时钟，东八区 ISO），后者是**这次读数背后的岗位行与档案行最后一次被改动的时刻**
   （`max(jobs.updated_at, student_profiles.updated_at)`，落库值）。只给前者，「刚刚生成」会被读成「岗位是新的」。
   两个端点上 `data_updated_at` **必须逐字相同**（`tests/test_job_match.py::test_both_endpoints_report_the_same_data_updated_at`），
   `generated_at` 允许不同（两次请求的两个瞬间）。没有档案时 `data_updated_at` 退化成岗位行自己那一格，
   不炸也不谎报（`::test_data_updated_at_falls_back_to_the_job_row_without_a_profile`）。
   **刻意没有的**：子行（`job_skills` / `skills` / `experiences`）的时间戳不进来——两条写路径都会同时 bump 主体行，
   扫子行只是多几次读、不多一个真相；若将来出现「只动子行」的写路径，`_freshness()` 就是它的断点。

**限流键**：本端点是全仓库第一条**按用户**而不是按 IP 的限流规则
（`app/core/rate_limit.py::get_user_or_remote_address`，装饰器级 `key_func` 覆盖，
`AUTH`/`POLISH`/`EXTRACT` 三条仍按 IP 不动）。原因是本端点花的是调用者自己的 Key，
按 IP 会让同一出口下前两个人点光整组额度，而后面的人既没做错也没花钱。

**前端调用**：`frontend/src/api/jobs.ts::matchJob(jobId)`，调用方是岗位详情页的匹配卡
（`frontend/src/views/JobDetailView.vue`，状态与文案在 `frontend/src/stores/job.ts::runMatch` /
`matchDegradeLines` / `matchFailureText`）。`X-DeepSeek-Key` 由
`client.ts` 白名单注入——这是那条白名单里**唯一一条模式判据**（`POST ^/jobs/[^/]+/match$`，
绑方法），为什么它不构成扩张、以及它的可辩护版本是路由表论证而不是「命中即转发」，
见 `client.ts` 的注释与 ADR-013 §6.1。**必须由用户显式点击**，不得在详情页自动调用
（`JobDetailView.spec.ts` 里有一条「mount 后匹配请求数为 0」的断言；
卡片为什么排在 JD 前面见那个文件里的注释与 ADR-013 §10 第 8 步）。

### 4.10.1 GET `/jobs/{job_id}/requirement-match` — 逐条要求状态（确定性、不调 AI、不花钱）

ADR-013 §10 第 3c 步。链路：`app/api/v1/jobs.py::requirement_match_job` →
`app/services/match_service.py::requirement_match` 的 `requirement_match()`。

**它为什么存在**：「你已具备」角标原先在浏览器里判——`trim + NFKC + toLowerCase` 之后做集合成员检查。
那套键够不着版本号写法，于是同一屏上角标说「不具备」、匹配卡说「已具备」（§4.10 第 5 条那个判据
只住在后端）。第 3c 步把判定收回后端，前端只消费。

**请求**：路径里一个 `job_id`，无请求体、无查询参数。用的是**当前用户的默认档案**，
与 `POST /jobs/{job_id}/match` 同一个 `get_readable` 可见性谓词、同一个 `_gather`
（`app/services/match_service.py::_gather`：一个 session、一份数据快照）。
两条链路如果读的不是同一时刻，「同屏一致」就只能靠运气。

**响应 `data`**（模型 `app/schemas/match.py::RequirementMatchResponse`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` / `profile_id` / `profile_title` | `str` / `str\|null` / `str\|null` | 与匹配端点同样的回显：角标是按哪份简历标的 |
| `coverage[]` | 数组，**顺序与条数都等于** `GET /jobs/{job_id}` 的 `skills` | 老板判据 (a) 的基准域 U；少一行就等于那一行永远长不出角标 |
| `coverage[].name` | `str` | 岗位侧原写法，逐字回显 |
| `coverage[].status` | `"covered" \| "partial" \| "missing"` | 前两档都算「写法命中」；`covered` / `partial` 的分界来自**关键词模式**，与匹配卡上可能被 AI 挪动的那条线无关——角标只回答命中没有。**挪档挪不出并集**：装配三档时「命中却拿不到强度」一律落 `partial`（`app/services/match_scoring.py::score_match`），所以配了 Key 的那些次点击里，`status ∈ {covered, partial}` 的集合仍与匹配卡 `covered ∪ partial` 逐字相同，这一点由 `test_badge_union_still_equals_match_union_in_ai_mode` 在 AI 模式下单独测过 |
| `coverage[].matched_form` | `str\|null` | 学生技能表里真正命中的那个写法（岗位 `Python` ↔ 简历 `Python3`）；`missing` 时恒为 `null`，不许拿岗位那侧的字面填进去 |
| `meta.matcher_version` | `str` | 与 §4.10 的 `data.meta.matcher_version` 同一个常量（B1 起 `"v2"`） |
| `meta.alias_version` | `str` | 与 §4.10 同源：本次判定用的是哪一版**别名词表内容**（起始 `"v1"`，空表也发） |
| `meta.generated_at` | `str`（东八区 ISO） | 与 §4.10 同一语义；两次请求之间允许不同 |
| `meta.data_updated_at` | `str`（东八区 ISO） | 与 §4.10 的 `data.meta.data_updated_at` **必须逐字相同**：同一份岗位 × 同一份档案只有一个新鲜度 |

**刻意没有的四样东西**（每一个都是决定，不是遗漏）：

1. **没有 `score` / `terms` / 任何计数**：这个端点算不出、也不该算出分数；要分数走 §4.10。
2. **没有 `evidence_mode` / `degrade_reason`**：它按定义不调 AI。写 `keyword_fallback` 会在 §6.5 的
   语义里撒谎（那三个字的意思是「AI 该参与而没参与成」），写 `ai_scored` 更是。
3. **不限流**：纯读两张表、一次详情页一次请求。给它加限额会造出「角标时有时无」这种新故障，
   比它要防的脚本刷更难排查（老板 2026-11-08 认了这个取舍）。
4. **不进 BYOK 白名单**：路由表论证——handler 不 import `app/integrations`、不产生任何出网请求，
   `X-DeepSeek-Key` 在这条 URL 上**没有转发对象**。反向断言在
   `frontend/src/api/__tests__/byok.spec.ts`（含「同形不同尾」那条），后端侧的闭合断言在
   `tests/test_job_match.py::test_only_the_match_route_is_byok_eligible_among_all_methods`
   ——它遍历**全部方法**，因为原来那条闭合用例只遍历 POST，看不见新增 GET 会不会撞上白名单。

**状态码**：`200` / `401` / `404`（他人草稿与回收站同样 404，不是 403）/ `422`（FastAPI 路径参数层）。
没有 `429`，三个 `AI_*` 码也不可达（§1.1）。

**前端调用**：`frontend/src/api/jobs.ts::getRequirementMatch(jobId)` →
`frontend/src/stores/job.ts::loadRequirementMatch` → 角标判据 `frontend/src/stores/job.ts::isRequirementHit` 的
`isRequirementHit(name)`。为什么要多这一个布尔函数而不是让模板写 `v-if="skillStatus(name)"`：
`missing` 也是非空字符串、在 Vue 里是**真值**，第一版就是这么渲染出「每条技能都长角标」的，
用例当场红（那条断言留在 `JobDetailView.spec.ts` 里防复发）。
拉取失败时角标整体不显示、详情页照常（`JobDetailView.vue::loadCoverage` 的 catch），
判定永远不在前端补做一次。

### 4.11 本章未接通 / 不存在的接口

以下都是**设计意图或已否决项**，本轮不实现、不排期；标注 `# PHASE-2: not wired`（沿用 ADR-011 修订清单第 7 条的口径）。

- `POST /jobs/{job_id}/analyze`（本文件旧版有）：**端点不存在**，OpenAPI 实测的 14 个岗位操作里没有它。异步任务 + `ai_job_id` 形态被 ADR-011 2.A 否决。# PHASE-2: not wired
- `/jobs/{job_id}/close`、「过期自动转 `expired`」：**都不存在**（4.7）。# PHASE-2: not wired
- 按 `location` / `salary` / `experience_required` 筛选：**存而不筛**，谓词函数里没有这三列（`app/repositories/job_repository.py:614-642`、ADR-011 1.G）。
- 岗位收藏、投递记录、批量导出：无端点（ADR-011 议题 3.C）。
- `/teacher/jobs/{job_id}/candidates` 与 `/export`（见本文件 §8）：候选人查询与导出**继续冻结**，本轮无实现。# PHASE-2: not wired
- 岗位与学生技能字典的合并或外键：**不建**（ADR-011 1.E）。「你已具备」原先是**前端只读比对**、无后端端点（ADR-011 2.E）。**这半句已按 ADR-013 §10 第 3c 步作废**（2026-11-08）：判定收回后端 `GET /jobs/{job_id}/requirement-match`（§4.10.1），它同样不写任何表、不产匹配分数、不调 AI；本地那套 `normalizeSkillName` 只剩「提交前重名自检」一个用途。**「不建字典表/外键」那半句仍成立**（2026-11-07）：人岗匹配按 ADR-013 解冻并落地为 §4.10 的 `POST /jobs/{job_id}/match`，它是同步纯计算、不落库；而「你已具备」那条前端只读比对**原样保留**，两者不是同一件事，也没有合并。批量匹配（`/match-runs`）与教师端候选人仍在冻结清单。
- `work_mode`（远程/驻场）列：**有意留下的缺口**，本轮只有 `employment_type`（ADR-011 1.G）；远程/驻场信息留在 `description` 文本里。


## 5. 测评、匹配与成长

### GET `/assessments`

按 `skill_id`、`level`、`version` 查询可用题目集合，不返回答案。

### POST `/assessments/{assessment_id}/attempts`

创建测评尝试，返回 `attempt_id`。答案提交到 `POST /assessment-attempts/{attempt_id}/answers`，完成使用 `POST /assessment-attempts/{attempt_id}/submit`。

### POST `/match-runs`

请求：`{"job_id":"uuid","student_ids":["uuid"],"weight_version":"v1"}`。返回异步 `match_run_id`。

> **这一节与下面那条 GET 描述的形态今天没有实现，也不会按这个形态实现**（2026-11-07，ADR-013）。
> 真实落地的人岗匹配是 **§4.10 `POST /jobs/{job_id}/match`**：同步、单用户 × 单岗位、纯计算不落库。
> 本节的「批量 + 异步 run + `data_as_of`」需要新表与后台任务，而 Redis/ARQ/Celery 仍在 AGENTS.md §3.2
> 冻结清单内（`ADR-013` §12 的第二条替代方案就是它，明确不采用）。留着这两段的理由不是还没做就删掉，
> 而是它们是 PRD 里**批量匹配**这一独立需求的落点，与 §4.10 覆盖的「学生自查一条岗位」不是同一件事。

### GET `/match-runs/{match_run_id}` / GET `/match-runs/{match_run_id}/results`

结果包含 `score`、`matched_requirements`、`gaps`、`evidence`、`explanation`、`data_as_of`，不返回不可解释的单一黑盒结论。

### POST `/growth-plans`

请求：`{"job_id":"uuid","duration_days":30,"focus_gap_ids":["uuid"]}`。返回异步 AI 任务；任务完成后生成可编辑计划。

## 6. 简历

> **本节的状态口径（2026-11-13 批次 B8 剩余之后）**：第 6 章长期混着「已实现」与「产品设计意图」两种东西，
> 读起来分不出哪一条能调。今天的实际路由是**七个**，都在 `app/api/v1/resumes.py`：
>
> | 端点 | 语义 | 来源 |
> |---|---|---|
> | `POST /resumes/generate` | 纯模板生成，落一行新版本并把它设为链头 | ADR-010 / ADR-015 |
> | `GET /resumes/current` | 只读版本指针，不按 `created_at` 猜 | ADR-015 §2.3 |
> | `GET /resumes/versions` | 该档案的全部版本（不含正文） | ADR-026 §2.6 |
> | `GET /resumes/versions/diff` | 两版的结构级差异（纯读） | ADR-026 §2.4 |
> | `POST /resumes/customize` | 按岗位**只重排**，落一行新版本但**不移动链头** | ADR-026 §2.5 |
> | `POST /resumes/polish` | AI 润色，纯计算、不落库 | ADR-018 |
> | `POST /resumes/privacy-check` | 导出前隐私检查，纯计算、不落库 | ADR-011 / B2 |
>
> 其余小节（`GET /resumes`、`GET /resumes/{id}`、`PATCH …/versions/{id}`、`POST …/export`、`POST …/share`）
> **都没有实现**，标题已标「未实现 · 设计意图」，不要照着它们写客户端。
> 特别注意：`PATCH …/versions/{id}` 与已实现的 `GET /resumes/versions` **不是同一件事**——
> 前者是「编辑某一版」，后者是「读版本清单」，前者至今没有路由。

### POST `/resumes/generate`

请求：`{"title": "投后端", "profile_id": "uuid 可省略"}`（`app/schemas/resume.py::ResumeGenerateRequest`）。

> **旧口径已作废**：本节早前写的是 `{student_id, job_id, template, max_pages}`，那个请求体
> **从来没有实现过**，只是设计草案。`profile_id` 省略时取默认简历，与 `GET /resumes/current`、
> `POST /resumes/privacy-check` 共用同一个 `_resolve_profile`。

这是**纯模板**生成（ADR-010）：不调 AI、不需要 `X-DeepSeek-Key`、同步返回 `200` 与生成结果，
**不返回 `202`、不创建 `ai_job_id`**。返回体是 `ResumeRead`（`resume_id` / `title` /
`content_html` / `created_at` / `version_no`）。

失败三档，都走统一错误模型：

| 情况 | 状态码 / code | 说明 |
|---|---|---|
| `profile_id` 不存在 / 别人的 / 在回收站 | `404` `NOT_FOUND` | 三种原因**不区分**，避免暴露别人档案是否存在 |
| 档案不完整（缺基本信息 / 技能 / 经历） | `400` `CLIENT_ERROR` | 文案要说清缺什么（守则 C3） |
| 排版版本是旧的（`layout_config.version != LAYOUT_VERSION`） | `422` **`CLIENT_ERROR`** | 注意 code 不是 `VALIDATION_ERROR`：它不是字段校验失败。文案必须**修得动**（「在排版面板重新保存一次再生成」），**绝不静默回退默认顺序**（ADR-015 §2.2） |

### GET `/resumes/current` — 当前简历（批次 B8，已实现）

请求：`GET /resumes/current?profile_id=uuid`（query，可省略；省略时取默认简历）。

响应 `200`：`data = {resume_id, title, content_html, created_at, version_no}`（`app/schemas/resume.py::ResumeRead`）；
`created_at` 与档案接口同口径，是东八区 ISO 串；`version_no` 是这份简历在**该档案内**的版本号，从 1 递增。

**它只读版本指针，不按 `created_at` 挑「最新一份」**（ADR-015 §2.3 明令禁止那种隐性契约）。
四种失败分开报：

| 情况 | 状态码 | 文案要点 |
|---|---|---|
| 档案不存在 / 别人的 / 在回收站 | `404` `NOT_FOUND` | 「简历不存在。」 |
| 这个用户一份档案都没有 | `400` `CLIENT_ERROR` | 「还没有可用的档案，请先完善并保存个人档案。」 |
| **从未生成过简历**（一行记录都没有） | `404` `NOT_FOUND` | 「还没有生成过简历，请先生成一份。」 |
| **有简历但链头指针落空**（链头行被物理删除） | `400` `CLIENT_ERROR` | 「当前简历的版本指针已失效，请重新生成一份。」——**不回落、不猜** |

「没生成过」与「指针坏了」必须分得开：两者的用户动作完全不同（一个是「去生成」，一个是「重新生成」），
而库里都能读成「没有当前简历」。区分靠的是「该档案简历行数 > 0」（`ResumeRepository.count_by_profile`）。

### GET `/resumes/versions` — 版本清单（批次 B8 剩余，`ADR-026` §2.6）

请求：`GET /resumes/versions?profile_id=uuid`（query，可省略；省略时取默认简历）。

响应 `200`：

```json
{"data":{"profile_id":"…","current_version_id":"…",
         "versions":[{"resume_id":"…","version_no":3,"title":"投后端 · 面向「后端开发实习生」",
                      "origin":"customized","is_head":false,"target_job_id":"…","created_at":"…"}, …]}}
```

| 字段 | 含义 |
|---|---|
| `versions[]` | 按 `version_no` **倒序**（最新在前），**不含 `content_html`** |
| `origin` | `generated`（模板生成）/ `customized`（按岗位定制）。字面量即 `ck_resumes_origin` 的取值 |
| `is_head` | 是不是当前链头。**定制版恒为 `false`**（`ADR-026` §2.2） |
| `target_job_id` | 定制版面向的岗位 id；岗位被删后由 `ON DELETE SET NULL` 置空。**岗位标题不在这里**——它已写进 `title`（快照语义） |
| `current_version_id` | 链头 id，与 `is_head` **同源**；前端画「当前简历」徽标用这一格，`null` 表示链头指针落空 |

三条口径：

1. **列表不背正文**：一份简历的 HTML 是整页纸（数十 KB），20 个版本就是几百 KB 的响应，
   而列表只需要「几号、叫什么、什么时候、是不是链头」。要看正文走 `GET /resumes/current`（链头）或 `diff`（只回差异，不回全文）。
2. **一份都没生成过时返回空列表，不是 404**：本端点回答的是「历史上有哪些版本」，空历史是合法答案；
   「还没有简历」那件事由 `GET /resumes/current` 报（`404 RESUME_NOT_GENERATED`）。两个问题不合并。
3. 失败只有两档：档案不存在/别人的/在回收站 → `404 PROFILE_NOT_FOUND`；一份档案都没有 → `400 PROFILE_REQUIRED`。

### GET `/resumes/versions/diff` — 两版的结构级差异（批次 B8 剩余，`ADR-026` §2.4）

请求：`GET /resumes/versions/diff?from_version_id=uuid&to_version_id=uuid`（**两个都必填**，写在 query 里）。

参数在 URL 而不是请求体，因为它是**纯读**：可以被浏览器刷新、前进后退、分享链接安全重放。
两版必须属于**同一份档案且都属于当前用户**，否则统一 `404 RESUME_VERSION_NOT_FOUND`——
区分「不存在」与「不是你的」等于送出一枚版本 id 探测器。

响应 `200`：

```json
{"data":{"from_version":{"resume_id":"…","version_no":1,"…":""},
         "to_version":{"resume_id":"…","version_no":2,"…":""},
         "comparable":true,
         "note":null,
         "summary":{"added":0,"removed":0,"changed":1,"sections_changed":1,
                    "section_order_changed":false,"item_order_changed":false},
         "sections":[{"key":"experiences","label":"项目经历","change":"changed",
                      "title_before":"项目经历","title_after":"项目经历",
                      "order_changed":false,
                      "items":[{"kind":"changed","label":"简历生成服务","before":"…","after":"…",
                                "fields":[{"field":"描述","before":"…","after":"…"}]}]}]}}
```

差异是**结构级**的，不是文本 diff（`ADR-026` §2.4）：把生成物 HTML 按区块 → 条目 → 字段三层解析
（`app/services/resume_diff.py`），区块按 item 的 **class 名**识别而不是按标题文字识别——
所以「把『项目经历』改名成『实践经历』」会被报成 `title_before != title_after` 的**改名**，
而不是「删掉一整块 + 新增一整块」。这是本节最容易被做错的一格。

四条规则：

1. **`comparable=false` 时 `sections` 为空且 `note` 必给**。`note` 说的是「这两版的排版结构无法识别，只能看正文」——
   **绝不能**用一份空差异表示「两版一样」（`ADR-026` §1.1 末）。前端遇到 `comparable=false` 只显示 `note`，
   不许渲染「没有差异」的结论。
2. **`change=="unchanged"` 的区块照样返回**：用户需要看到「这一块没动」，而不是在列表里找不到它、却不知道是没动还是没解析出来。
   `summary.sections_changed` 只数 `change != "unchanged"` 的区块。
3. `ResumeDiffField.before/after` **允许空串**：空串表示「这一版里这一格不存在」，与「这一格的值是空的」不是一回事
   ——后者在纸面上会显示「待补充：X」。
4. **顺序变化与内容变化分开报**（`order_changed` / `item_order_changed` / `section_order_changed`）。
   按岗位定制产出的两版**只应**出现顺序变化：`added/removed/changed` 全为 0，`item_order_changed=true`。

### POST `/resumes/customize` — 按岗位定制（批次 B8 剩余，`ADR-026` §2.5）

请求（`app/schemas/resume.py::ResumeCustomizeRequest`，`extra="forbid"`）：

```json
{"job_id":"uuid","profile_id":"uuid 可省略"}
```

响应 `200`：

```json
{"data":{"resume":{"resume_id":"…","title":"投后端 · 面向「后端开发实习生」","content_html":"…","created_at":"…","version_no":2},
         "based_on_version_no":1,
         "focus":{"job_id":"…","job_title":"后端开发实习生",
                  "matched":["Python","MySQL"],"missing":["Docker"]}}}
```

**标题是快照**：`resumes.title` 存成「简历名 · 面向「岗位名」」（`_customized_title`，200 字上限按码点截断）。
不新开一列存岗位标题，是因为岗位后来改名时，手上这份简历的名字不该跟着变。

**「定制」= 只重排，一个字都不改**（`ADR-026` §1.2 把它与「改写」明确切开）：

- 技能区块：JD 命中的技能**稳定**排到前面（命中之间的相对顺序不变）；
- 经历区块：按关键词命中条数**降序稳定**排序（命中数相同保持原顺序）。
- **不改任何文字、不删任何条目、不增任何条目**。缺什么由 `focus.missing` 如实告知——
  它是**提示不是结论**：「JD 里点了这几样、你的档案里没有」，不评价重不重要，也**不写进纸面**。

**定制版不移动链头**（`ADR-026` §2.2，本节最重要的不变量）：它会落一行新版本（`origin="customized"`，
`target_job_id` 指向这条岗位），但 `student_profiles.current_version_id` 仍然指着原来的那一版。
所以 `based_on_version_no` 同时是「定制时链头是第几版」与「现在点『当前简历』还会拿到哪一版」的答案。
理由：定制版是「为这一条岗位准备的另一份」，不是「我现在的简历」。若顺手改了链头，用户回到首页会发现自己「换了简历」
却什么都没点——而且多条岗位各定制一次，链头会被最后那次悄悄改写。

五道闸门在服务层，路由只把每种失败翻成一句能动手的话：

| 情形 | 状态码（`error.code` + `details.reason`） | 文案 |
|---|---|---|
| 档案不存在 / 别人的 / 在回收站 | `404` `NOT_FOUND`，`PROFILE_NOT_FOUND` | 「简历不存在或无法访问，请返回简历列表重新选择。」 |
| 一份档案都没有 / 档案不完整（缺基本信息、技能、经历） | `400` `CLIENT_ERROR`，`PROFILE_INCOMPLETE` | 「请先完善个人档案（基本信息、技能、经历）后再生成简历。」 |
| 排版版本是旧的 | `422` `CLIENT_ERROR`，`LAYOUT_VERSION` | 与 `/generate` 同一条：**修得动**，不静默回退默认顺序 |
| 还没生成过简历（没有可定制的底版） | `404` `NOT_FOUND`，`RESUME_NOT_GENERATED` | 「还没有生成过简历，请先生成一份。」 |
| 链头指针落空 | `400` `CLIENT_ERROR`，`RESUME_POINTER_BROKEN` | 「当前简历暂时无法读取，请重新生成一份简历。」 |
| 岗位不存在 / 已删 / 还没发布 | `404` `NOT_FOUND`，`JOB_NOT_FOUND` | 与岗位接口同一句，不区分原因 |
| **岗位已过截止时间** | `409` `CLIENT_ERROR`，`JOB_EXPIRED` | 「岗位已过截止时间，请先更新截止日期，再提交或审核。」 |
| 多塞任何字段（如 `title`、`skills`） | `422` `VALIDATION_ERROR` | 定制会落一行新版本，静默忽略会让调用方以为输入生效了 |

`409` 而不是 `400`：它不是「你填错了」，而是「这条岗位的状态变了，请换一条」——
与岗位审核流对过期动作的口径一致（`ADR-024` §2.4，同一个 `app/core/deadline.py::is_expired`）。

### GET `/resumes` / GET `/resumes/{resume_id}`（未实现 · 设计意图）

返回版本、来源、确认状态和授权状态。

**注意与已实现的 `GET /resumes/versions` 区分**：后者是「一份档案的版本清单」，已上线；
这里的 `GET /resumes` 是「这个用户的简历列表（跨档案）」，`GET /resumes/{resume_id}` 是「按 id 读一份（含正文）」，
两者都**没有路由**。`PATCH …/versions/{id}`（编辑某一版）同样未实现——不要因为 `/versions` 存在就以为它存在。

### PATCH `/resumes/{resume_id}/versions/{version_id}`（未实现 · 设计意图）

更新编辑后的区块。保存新版本，禁止覆盖审计历史。

### POST `/resumes/{resume_id}/export`（未实现 · 设计意图）

请求 `{"version_id":"uuid","format":"pdf"}`；返回异步任务。完成后返回短时下载 URL。

### POST `/resumes/polish` — 润色（同步、纯计算、BYOK）

请求体（`app/schemas/resume.py::ResumePolishRequest`）：

| 字段 | 约束 | 备注 |
|---|---|---|
| `profile` | `{full_name?, contact?, email?}`，全部可选、**宽松校验** | 只用来给模型一点上下文，不参与任何权限或落库判断 |
| `skills` | `list[{name}]`，**1–50** | 上限既是 Prompt 体积兜底，也是「别把这个端点当开放 AI 代理」的第一道闸（第二道是限流） |
| `experiences` | `list[{title?, description, responsibility}]`，**1–20** | 每段 `description`/`responsibility` ≤2000；`title` 只给模型看上下文，**不校验日期**（草稿里没填时间的经历同样值得润色） |
| `mode` | `detail` / `medium` / `heavy` / `expand` | |
| `max_words` | `40–800`，**只对 `expand` 有意义** | 缺省补 200；其它档位在校验层被置空（ADR-018） |

响应 `200`：`data = {skills: [str], experiences: [PolishedExperience]}`（`ResumeCopy`）。
`PolishedExperience` 有五个字段——前两个是老契约，后三个是 ADR-018 加的（**加字段，不改语义**）：

| 字段 | 含义 |
|---|---|
| `index` | 输入经历列表里的**下标**（不是数据库 id）：回贴时不错位 |
| `description` / `responsibility` | 改写后的文字，**≤1200 / ≤600** |
| `target_words` | 本次生效的目标字数，**只有 `expand` 有**，其它档 `null` |
| `over_limit_by` | 返回文字合计**超出目标多少字**，不超为 `0`。它必须是**数**（`len(返回文字) - target_words`），用户据此自己减字 |
| `truncated` | 是否撞到**响应契约**（1200/600）被段级截断 |

**三道边的职责必须分开读**（ADR-018 §6）：

1. **目标**（`max_words`）：进提示词，**不砍尾**——超了只报 `over_limit_by`，减字动作交回用户；
2. **契约**（1200/600）：模型写飞越这道边时**段级**截断并标 `truncated=true`，其余段照常返回，
   **不再整单 502**；也不报「截掉了多少字」（`truncated_from_words` 一类字段被明确否掉）；
3. **失败**：`400 AI_KEY_MISSING`（没配 Key 或 Key 格式不对）、`401 AI_KEY_INVALID`（Key 无效/欠费）、
   `502 AI_SERVICE_UNAVAILABLE`（超时或上游异常，`retryable=true`）、`429 RATE_LIMITED`。

**纯计算、不落库**：不写任何表，结果回显给用户逐条确认，用户点保存才由 `PUT /profiles/me` 落库
（ADR-010 §5）。Key 走请求头 `X-DeepSeek-Key`，不落库、不进日志（ADR-009）。

### POST `/resumes/privacy-check` — 导出前隐私检查（批次 B2，已实现）

请求：`{"profile_id":"uuid 可省略"}`。省略时按登录用户取默认简历，与 `/resumes/generate` 用**同一个** `_resolve_profile`。
响应：`data = {"profile_id": ..., "categories": [四类，顺序固定为 联系方式 / 作品链接 / 个人信息 / 授权范围]}`；每类是
`{key, label, status, note, items[]}`，每格 item 是 `{key, label, present, included, note}`。
批次 B4 起「作品链接」不再是一句 `not_available`：`items[]` 里逐条列出 `{name, url}`，`note` 直接写「会被带出去：<url>」；「个人信息」里也多了 `证书（N 条）` 一格。

三条口径必须连着读：

1. **纯计算、不落库**：不写任何表、不加列、不建表，与 `/resumes/polish`、`/jobs/extract-skills` 同一条路子（`tests/test_resume_privacy_check.py::test_privacy_check_is_pure_computation` 钉的就是「调用一次 `resumes` 行数不变」）。
2. **`present` 与 `included` 是两个问题**，不许合并：`目标岗位偏好` 是 `present=true, included=false`（确实存在，但不写进简历）；反过来 `技能 / 项目经历 / 教育经历 / 证书` 是 `present === included`——它们真的会印在纸上。**期望城市刻意不在这个清单里**：它不是简历区块（`ADR-015` §5 ③），列进来会让用户以为 HR 会看到。反过来，没填的邮箱两边都是 `false`，而那一句「HR 无法用邮箱联系你」只能在这里说——纸面上**故意不给可选项打「待补充」标记**（见第 3 条与 `app/integrations/resume_generator.py` 的调用处注释）。
3. **核不了就说核不了，能核了就必须真核**：今天只剩 **`授权范围`** 一类是 `not_available`（没有「授权给谁看」这一层，属 B5），它的 `note` 必给。`作品链接` 自批次 B4 起**已转正**——`portfolio_links` 是真实存在的表，生成物里每条都是可点的 `<a href>`，所以它现在按 `present` / `missing` 如实报，不再是「本产品还没有这一栏」。把能核的说成「核不了」与把核不了的说成「用户没填」，是同一种假话。

错误口径与 `/generate` 逐字对齐：`profile_id` 是别人的 / 不存在的 / 在回收站的 → `404 NOT_FOUND`；这个用户一份档案都没有 → `400 CLIENT_ERROR`；`profile_id` 超长 → `422 VALIDATION_ERROR`。

**与上面那张「设计意图」表的关系**：`POST /resumes/{resume_id}/export` 至今**未实现**（`app/` 里 `export`/`pdf` 零命中，也没有任何 PDF 依赖），所以「导出前显示」这一格今天没有挂载点。B2 因此把检查做成一个**读同一份档案的纯计算端点**，等导出真落地时它才有地方挂——这不是把两件事做完了，是把其中可核的两格做完、把不可核的两格如实标出来。

### POST `/resumes/{resume_id}/share`（未实现 · 设计意图）

请求 `{"version_id":"uuid","target_type":"enterprise","target_id":"uuid","fields":["contact","skills","projects"],"expires_at":"..."}`。

## 7. 模拟面试

> **本节 2026-11-12 由批次 B11a（FR-07）按实际代码重写。** 旧版写的是「`resume_version_id` + `mode` + `round` + `question_count` + `/turns` + `/finish` + `/report` + `/media-room`」的全形态设计意图（音视频 + 评估报告 + WebRTC），**那一整套本期都没有实现**，且其中 `resume_version_id` 依赖的列本轮 `resumes` 表根本不存在（B8 只落地了版本指针，无 `resume_version_id`），`mode` / `round` / `question_count` 也从未进库。
> 本期 B11a 只落**纯文本**单项：`interview_sessions` + `interview_turns` 两张关系表、五个端点、AI 可见降级（ADR-022）。音视频、评估报告、媒体房间在 `docs/ROADMAP.md` 的 B11b 才排期。
> 读到本节的姿势：下面五条端点是今天能调的契约；凡是「finish / report / media-room / 单独的 turns 路径 / resume_version_id / mode / round / question_count」都是未实现的旧设计意图，**不要照着写客户端**（前端 `interviews.ts` 也未声明这些字段）。

链路：`app/api/v1/interviews.py` → `app/services/interview_service.py` → `app/repositories/interview_repository.py`；AI 适配器复用 `app/integrations/interview_coach.py`（走现有 DeepSeek 客户端 + BYOK，`X-DeepSeek-Key` 由前端 `client.ts` 白名单对「发消息」这一条 URL 注入，见 §7.4）；题源优先级与上下文截断见 ADR-022 §3/§4；表结构 `alembic/versions/20261112_0019_interview_sessions.py`（与 B5 的 `0018` 并行，合入 master 时由 `20261112_0020` merge revision 收敛成单一 head）。

### 7.1 身份、归属与可见性（权限矩阵）

身份只来自 JWT（`CurrentUserDep`）。`users` 表无 `role` 列（ADR-011 1.A）——批次 B5 起角色挂在 `organization_members`（ADR-021 §2.1），面试端点本期不按角色分级，只按会话归属判，与岗位同口径：

| # | 操作 | 允许的调用者 | 非所有者结果 | 状态码 | 代码位置 |
|---|---|---|---|---|---|
| 1 | `GET /interviews` | 当前登录用户 | 只返回**自己的**会话（SQL 按 `user_id` 过滤） | 200 | `app/api/v1/interviews.py::list_interviews`、`interview_repository.list_owned` / `count_owned` |
| 2 | `POST /interviews` | 当前登录用户 | — | 201 / 401 / 422 | `app/api/v1/interviews.py::create_interview` |
| 3 | `GET /interviews/{id}` | 仅所有者 | 404（他人会话与「不存在」同义，不暴露 id 是否存在） | 200 / 401 / 404 | `app/api/v1/interviews.py::read_interview` |
| 4 | `POST /interviews/{id}/messages` | 仅所有者 | 404；已结束 → 400 | 200 / 401 / 404 / 400 / 422 | `app/api/v1/interviews.py::send_interview_message` |
| 5 | `POST /interviews/{id}/end` | 仅所有者 | 404 | 200 / 401 / 404 | `app/api/v1/interviews.py::end_interview` |

两条实现约束（都有代码兜住）：
- 越权 / 不存在在 service 层收敛成同一个 `InterviewNotFoundError`，api 层只有一句 `raise _not_found()`（`app/api/v1/interviews.py::_not_found`）；`app/services/` 下不出现 `HTTPException`。
- 归属校验写在 SQL 谓词里（`get_owned` 按 `user_id` 过滤），不是「先查出来再在服务层比一次」。

### 7.2 请求体与字段

`POST /interviews` 请求体（`InterviewSessionCreate`，`app/schemas/interview.py::InterviewSessionCreate`）只有两格，且都可选：

| 字段 | 类型 / 约束 | 必填 | 代码位置 |
|---|---|---|---|
| `job_id` | string（UUID），可空 | ❌ | `app/schemas/interview.py::InterviewSessionCreate` |
| `target_role` | string ≤200，可空 | ❌ | `app/schemas/interview.py::InterviewSessionCreate`、`app/schemas/interview.py::TARGET_ROLE_MAX_LEN` |

`job_id` 若提供但不可见（不存在 / 未发布 / 回收站）→ 404（`app/services/interview_service.py::InterviewService.create_session`）。`target_role` 与 `resume_version_id` / `mode` / `round` / `question_count` **没有关系**——后四者本期不收、不存、不入路由。
`POST /interviews/{id}/messages` 请求体（`InterviewMessageCreate`，`app/schemas/interview.py::InterviewMessageCreate`）只有一格：`content`（`str`，**1–5000**，应用层第一道闸，超长 422 不进库，上限常量 `ANSWER_MAX_LEN`）。

### 7.3 响应对象

- `InterviewSessionRead`（`app/schemas/interview.py::InterviewSessionRead`）：`id` / `user_id` / `job_id`(可空) / `status`(`active`|`ended`) / `target_role`(可空) / `created_at` / `updated_at` / `ended_at`(可空)。
- `InterviewTurnRead`（`app/schemas/interview.py::InterviewTurnRead`，按 `sequence` 回放）：`id` / `session_id` / `sequence` / `speaker`(`interviewer`|`candidate`) / `content` / `ai_degrade_reason`(可空) / `created_at`。**`ai_degrade_reason` 非空即代表该轮内容由本地离线模板产出、不是真 AI**——这是「可见降级、绝不静默」的数据层证据（ADR-022 (b)）。
- `InterviewMessageResult`（`app/schemas/interview.py::InterviewMessageResult`）：`candidate_turn` + `interviewer_turn` + `ai_degrade_reason`（本轮降级键，见 7.5）。
- `InterviewListResult`（`app/schemas/interview.py::InterviewListResult`）：`sessions[]` + `page` + `page_size` + `total`。

### 7.4 五个端点

| 端点 | 方法 | 返回 | 说明 |
|---|---|---|---|
| `/interviews` | POST | **201** + `data.{session, opening_turn, ai_degrade_reason}` | 创建会话并立刻拿开场问题（sequence=1）；开场 AI 失败也不中断，改走离线模板并带 `ai_degrade_reason` |
| `/interviews` | GET | 200 + `data.{sessions[], page, page_size, total}` | 自己的会话列表，按 `updated_at DESC` 倒序，分页参数 `page`(≥1) / `page_size`(1–50，默认 20) |
| `/interviews/{id}` | GET | 200 + `data.{session, turns[]}` | 详情：元信息 + 全部轮次（按 sequence 顺序回放） |
| `/interviews/{id}/messages` | POST | 200 + `data.{candidate_turn, interviewer_turn, ai_degrade_reason}` | 发一条候选人作答 → 拿面试官下一轮（真 AI 或降级）；已结束 → 400；这条 URL 进 BYOK 白名单、转发 `X-DeepSeek-Key` |
| `/interviews/{id}/end` | POST | 200 + `data.{session}` | 结束会话，`status`→`ended`、`ended_at`→now |

**没有**独立的「提交单轮答案」`/turns` 端点（旧版设计意图），也没有 `/finish` / `/report` / `/media-room`——发送与接收都走 `/messages` 一个端点（候选人的轮次与面试官的轮次在同一个响应里成对返回），评估报告与媒体房间在 B11b。

### 7.5 AI 可见降级（本期核心红线）

面试**绝不**因 AI 失败返回 4xx/5xx：`InterviewService._generate` 把 AI 异常就地翻译成 `ai_degrade_reason` 并继续用本地题库（`_OFFLINE_QUESTIONS`）产出面试官发言（`app/services/interview_service.py::InterviewService._generate`），端点保持 200。降级键与界面话术的映射（`app/services/interview_service.py::_DEGRADE_TEXT`，前端 `frontend/src/api/interviews.ts::interviewDegradeHint`）：

| `ai_degrade_reason` | 含义 | 用户看到的文案（作用域中性，不出现「DeepSeek」） |
|---|---|---|
| `key_missing` | 没传 AI Key（BYOK 未填凭据） | 未配置 AI 密钥，已切换到离线模拟。 |
| `key_invalid` | Key 被上游拒绝 | AI 密钥无效，已切换到离线模拟。 |
| `timeout` | 上游超时 | AI 服务超时，已切换到离线模拟。 |
| `upstream` | 上游不可用 | AI 服务暂时不可用，已切换到离线模拟。 |
| `bad_output` | 输出不可解析 / 截断 / 空 | AI 返回异常，已切换到离线模拟。 |
| `null` | 真 AI 参与 | （无提示，正常） |

异常→键的判定次序有讲究：`EvidenceScoreTimeoutError` 与 `…EmptyContent` / `…BadOutput` 是 `…UnavailableError` 的子类，必须先判子类再判父类，否则超时会被 `upstream` 吃掉、写错原因（`app/services/interview_service.py::InterviewService._generate`）。这条由 `tests/test_interviews.py` 钉住（「AI 失败时用户看到什么」用例）。三个 `AI_*` 错误码（`app/main.py` 的 `resume_*` 处理器）在面试端点**永不命中**——本端点失败也返回 200（与 §4.10 的匹配端点同理，但匹配端点不进 BYOK、面试的发消息端点进 BYOK）。

### 7.6 本章未接通 / 不存在的接口

以下都是**旧版设计意图或 B11b 排期项**，本期不实现、不排期，标注 `# PHASE-2: not wired`（沿用 §4.11 口径）：

- `POST /interviews/{id}/turns`（旧版「单独提交一轮答案」）：**不存在**，收发合并进 `/messages`。
- `POST /interviews/{id}/finish` + `GET /interviews/{id}/report`：**不存在**，评估报告（含回答证据、建议、待练习技能）在 B11b / `interview_reports` 表，本期不建。
- `POST /interviews/{id}/media-room`：**不存在**，WebRTC 房间与 `media_rooms` 表在 B11b，音视频模式本期不做。
- 请求体里的 `resume_version_id` / `mode` / `round` / `question_count`：**都不收、不存、不入路由**；`resume_version_id` 依赖的列本轮 `resumes` 表根本没有。
- 教师端查看他人报告：`/teacher/...` 全局未接通（§8），面试报告授权同理冻结。

## 8. 教师与学校

### 组织与成员（批次 B5 已实现，前缀 `/organizations`）

四个端点，全部需要登录；权限分工见 `docs/adr/ADR-021` §2.3
（**路由层判「你有没有这个权限」，服务层判「这条记录是不是你这个组织的」**，两者缺一都是漏洞）。

| 端点 | 需要的权限 | 说明 |
|---|---|---|
| `GET /organizations/me` | 只需登录 | 当前账号的组织概览：`{organization:{id,code,name,kind}, role, member_count}`。没有组织时 **404**（不是空对象——空对象会让前端画出「组织：（空）」） |
| `GET /organizations/{id}/members` | `org:member:read` | 成员列表：`{items:[{user_id,username,email,role,joined_at}]}`，按用户名排序。目标组织不是自己的 → 403 |
| `PATCH /organizations/{id}/members/{user_id}` | `org:member:manage` | 改角色，body `{"role":"student"\|"teacher"\|"school_admin"}`。改自己 → 400（防自锁：校长把自己降级后没人能改回来）；`platform_admin` → 422 |
| `GET /organizations/{id}/audit-logs` | `audit:read` | `{items:[{id,action,actor_user_id,target_type,target_id,result,detail,ip,created_at}], total}`，`limit` 1–200 默认 50、`offset` ≥0。跨组织 → 403 |

审计的 `action` 是白名单枚举（`user.register` / `user.login` / `organization.create` / `member.join` / `member.role_change`），
`result` 为 `ok` / `denied`——**被拒绝的越权尝试也记**，那是「谁在什么时候试图做什么」的唯一证据。

**组织的建立不走 HTTP**：本批不开放自助建校端点（能自己造组织的人可以立刻给自己
`school_admin`，组织码还是他自己起的，等于没有角色边界）。建组织用
`scripts/seed_organization.py`，见 `docs/adr/ADR-021` §3.3。

### GET `/teacher/classes/{class_id}/dashboard`

参数 `from`、`to`、`job_id`；返回聚合人数、技能缺口、目标岗位分布、任务完成率。默认隐藏小样本可识别数据。

### GET `/teacher/jobs/{job_id}/candidates`

`# PHASE-2: not wired` — 未接通：`app/` 下全量 grep `teacher` **零命中**（含路由、服务、仓储），`app/api/router.py` 只挂了 v1 聚合路由与 `/health`。

只返回学生已授权摘要；支持 `min_score`、`class_id`、`skill_id`、分页。

### POST `/teacher/jobs/{job_id}/candidates/export`

`# PHASE-2: not wired` — 未接通，同上；批量下载与候选人导出仍在 AGENTS.md §3.2 冻结清单内（ADR-011 议题 3.C）。

请求导出字段白名单；返回异步导出任务。每次导出记录审计。

### GET `/school/analytics/skills`

学校管理员可查看脱敏的技能趋势、岗位-课程覆盖和专业缺口。

### 组织邀请（批次 B6 已实现，`ADR-023`）

> 与上面「组织与成员」那张表连着读：`organizations.code` 是长期、不限次、不可撤销的
> 组织标识；这一节管的是**带过期时间、可用次数、可撤销**的邀请码，建在
> `alembic/versions/20261112_0021_organization_invitations.py`。两者不是同一个东西，
> 本批也不删 B5 注册时那个组织码入口——兼容性代价写在 `ADR-023` §3。

链路：`app/api/v1/invitations.py` → `app/services/invitation_service.py::InvitationService`
→ `app/repositories/invitation_repository.py::InvitationRepository`；
审计动作白名单见 `app/repositories/audit_log_repository.py::AuditAction`。

| 端点 | 需要的权限 | 说明 |
|---|---|---|
| `POST /organizations/{id}/invitations` | `org:member:manage` + 同组织 | 生成邀请码，201 返回 `data.invitation`。body `{"role":"student"\|"teacher","max_uses":1..100,"ttl_days":1..30}`，`extra="forbid"`；**不含 `school_admin`**——一张可转发的码不该能造出第二个校长 |
| `GET /organizations/{id}/invitations` | `org:member:manage` + 同组织 | `data.items[]`，按创建时间倒序；撤销过的仍在列表里（`revoked_at` 非空）。每项带 `redeemed_by: string[]`——兑换过这张码的**用户名**（第 18 轮裁定 ④），来源是 `invitation.redeem` 审计行 join `users`，不是邀请表上的列；一张码可被多人兑换（`max_uses > 1`），所以是数组，没被兑换过是 `[]` 而不是 `null` |
| `DELETE /organizations/{id}/invitations/{invitation_id}` | `org:member:manage` + 同组织 | 撤销（打标，不物理删）。重复撤销是幂等成功，不写第二条审计 |
| `POST /invitations/redeem` | 只需登录 | 凭码加入组织，body `{"code":"..."}`，返回 `data.{organization, role}` |

三条实现约束：

1. **两级判断**：路由层判权限，服务层判「这条邀请是不是你这个组织的」；被拒绝的跨组织尝试留 `result=denied` 审计，且绝不落到别人组织的审计列表里。兑换端点**不要求组织权限**——还没加入组织的人按定义不可能有。
2. **兑换是一次事务**：`SELECT ... FOR UPDATE` 锁邀请行 → 校验 → 加成员 → 占次数 → 写审计 → 一次提交。`organization_members.user_id` 唯一，一个账号只能属于一个组织。
3. **四种不可用对外同一句**：不存在 / 已撤销 / 已过期 / 已用尽都返回同一条 400 文案，不给「猜邀请码是否存在」的探针。已有组织的账号重复兑换另给一句明确文案。

**FR-01 仍未完全达成**：本批把「绑定」变成可审计、可撤销、有时效的管理员动作，但**没有**强制「所有账号必须属于某个组织」（存量账号全无组织，迁移方案需产品裁定）。`ADR-021` §2.4 那句「跨组织不可见只在有组织的账号之间成立」继续有效，不许在 ROADMAP 记成已达成。

## 9. AI 任务与实时状态

`# PHASE-2: not wired` — 本章全部端点未接通：`app/api/v1/` 下没有任何 `/ai-jobs` 路由，`app/` 全量 grep `ai-jobs` 与 `ai_job` **零命中**（`app/api/router.py` 只挂了 v1 聚合路由与 `/health`）；队列依赖（Redis / ARQ / Celery）仍在 AGENTS.md §3.2 冻结清单内。**岗位 JD 技能提取不走本章的异步模型**——它是 `POST /jobs/extract-skills` 的 200 同步返回（见 §4.8，异步 + `ai_job_id` 的岗位分析形态被 ADR-011 2.A 否决）。

### GET `/ai-jobs/{ai_job_id}`

返回：

```json
{"data":{"id":"uuid","type":"job_analysis","status":"running","progress":40,"retry_count":0,"error_code":null}}
```

### GET `/ai-jobs/{ai_job_id}/events`

SSE，事件 `progress`、`succeeded`、`failed`；客户端断线可用 `Last-Event-ID` 恢复。不得通过 SSE 推送 API Key 或未授权正文。

## 10. 幂等、并发和权限

- 创建 AI 任务、导出和分享接口支持 `Idempotency-Key`，服务端保存 24 小时结果。
- 资源读取按组织、角色、资源归属和 consent 联合判断，不能只依赖前端隐藏按钮。
- 并发编辑使用 `If-Match`/版本号；冲突返回 `CONFLICT` 和最新版本摘要。
- 管理员批量操作限制数量并要求二次确认。

## 11. OpenAPI 与联调

FastAPI 自动生成 `/openapi.json` 和 `/docs`；生产环境只在内网或通过管理员权限开放。前端根据稳定的 OpenAPI schema 生成 TypeScript 类型，禁止手写与接口不一致的重复类型。接口变更需增加版本或兼容字段，并更新本文件和契约测试。

## 12. 安全与 AI 输出约束

- 所有 `{id}` 路径参数先做组织级对象授权，再读取字段；不能仅依赖角色判断。
- `source_ids`、`evidence_ids` 和 `consent_id` 必须属于当前组织且处于有效授权状态。
- 已实现的 AI 端点（`/resumes/polish`、`/jobs/extract-skills`）都是**纯计算、同步返回、不落库**，没有 `ai_job_id` 可查；结果先回显给用户逐条确认，用户点保存才由各自的写端点落库（ADR-010 §5 / ADR-011 2.A）。未实现的异步任务形态见 §9（`# PHASE-2: not wired`）。不把未核验的生成文本伪装成最终事实——这条在岗位侧的具体实现是：`/jobs/extract-skills` 的每条 `evidence` 都必须能回溯 JD 原文，核验不过的条目直接丢弃并把剔除数量回显在 `data.meta.dropped_count`（§4.8）。
- AI 输出可包含 `support_status`：`supported`、`unsupported`、`needs_review`；`unsupported` 的关键事实必须阻断发布或导出。
- 错误码与日志事件一一对应，错误消息不泄露资源是否存在（例如未授权对象统一使用 `RESOURCE_NOT_FOUND` 或 `AUTH_FORBIDDEN`，按安全策略选择）。
