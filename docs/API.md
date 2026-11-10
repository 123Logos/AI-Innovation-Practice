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

实际由代码发出的错误码（以 `app/` 全量 grep 这些字面量为准，不含测试）：`VALIDATION_ERROR`（`app/main.py:211`）、`RATE_LIMITED`（`app/main.py:231`）、`NOT_FOUND`（`app/main.py:190`）、`CLIENT_ERROR`（`app/main.py:193`）、`INTERNAL_ERROR`（`app/main.py:179`、`:196`）、`AI_KEY_MISSING`（`app/main.py:263`）、`AI_KEY_INVALID`（`app/main.py:272`）、`AI_SERVICE_UNAVAILABLE`（`app/main.py:283`、`app/api/v1/jobs.py:154`）。三个 `AI_*` 码只在 `/resumes/polish` 与 `/jobs/extract-skills` 上可达（`app/main.py:239-240` 的注释写明这一沿革；`POST /jobs/{job_id}/match` **不在这个集合里**——它 AI 失败也返回 `200`，见 §4.10；`GET /jobs/{job_id}/requirement-match` 同样不在——它按定义不调 AI，见 §4.10.1），且处理器文案要求作用域中性——不得出现「润色」或「简历」（`docs/adr/ADR-011-job-board-and-skill-extraction.md` 2.F）。

以下码目前**只是本文件的设计意图**，`app/` 下没有任何一处产生它们：`AUTH_REQUIRED`、`AUTH_INVALID_CREDENTIALS`、`AUTH_FORBIDDEN`、`RESOURCE_NOT_FOUND`、`CONSENT_REQUIRED`、`CONFLICT`。未登录与令牌失效实际走 `HTTPException(401)`（`app/services/dependencies.py:52`、`:132`），被通用 4xx 处理器压成 `CLIENT_ERROR`（`app/main.py:192-194`），message 取 `UNAUTHORIZED_MESSAGE`（`app/services/dependencies.py:35`）。

`MEDIA_UNAVAILABLE` 属模拟面试，仍在冻结清单（AGENTS.md §3.2），未启用、无代码位置。

已删除的两个码（依 ADR-011 修订清单第 8 条）：`AI_JOB_FAILED` 依附于「异步 AI 任务」模型，该模型被 ADR-011 2.A 否决且从未实现；`AI_OUTPUT_UNSUPPORTED` 并入 `AI_SERVICE_UNAVAILABLE`——ADR-011 2.F 规定提取端点只复用润色那三码，一个都不新增。

错误响应必须包含稳定的 `code`、面向用户的 `message`、可供日志关联的 `request_id`；不得返回堆栈、SQL、密钥、供应商原始错误或其他租户数据。测试编号、预期 HTTP 状态、输出和处理规则见 [测试文档](TESTING.md)。

## 2. 认证与用户

> **本节是目标契约，不是今天的读数**（老板 2026-11-09 定性：「register 收 email+role+organization_code、`GET /me` 返回「组织、角色和可用权限」是没问题的，后续的角色、组织、权限部分必须用」）。
> 今天实际存在的是：`POST /auth/register` 收 `username`（必填，3–100）+ `password` + 可选 `email`（`app/schemas/auth.py:15-17`），**没有 `role`、没有 `organization_code`**；
> `GET /me` 只回 `{user: {id, username, email, created_at}}`（`app/api/v1/auth.py:87-88`），组织 / 角色 / 权限三格在 `app/` 下零命中；
> 登录标识今天的唯一入口是 `username`，且已做大小写归一（`app/services/auth_service.py:72` 的 `strip().lower()`）。
> 差距由批次 **B5**（组织 + 角色 + 审计日志）与 **B6**（审核流 + 邮箱/手机号登录 + 改密）补齐——**那两批要实现本节，不是反过来把本节改成现状**。
> 读这段的姿势：`role` / `organization_code` 现在**不是** 422，而是被 Pydantic 静默丢掉——实测（`RegisterRequest(username=..., password=..., role="student", organization_code="AI2026")`）`model_dump()` 只剩 `['email','password','username']`，因为 `app/schemas/auth.py:14` 没有 `extra="forbid"`。
> **所以别把「客户端能提交」读成「服务端认了」**：今天任何人都注册成同一种身份，而声称自己是教师的那个请求会被无声降级。B5/B6 落 `role`/`organization` 时必须同时决定这一格走 `forbid` 还是显式校验，不能继续静默。


### POST `/auth/register`

请求：

```json
{"email":"student@example.com","password":"Strong-password-1","role":"student","organization_code":"AI2026"}
```

返回 `201`：`{"data":{"user_id":"uuid","access_token":"...","refresh_token":"...","expires_in":900}}`

### POST `/auth/login`

请求 `{"email":"student@example.com","password":"..."}`；返回同注册。登录失败统一返回 `AUTH_INVALID_CREDENTIALS`，不暴露账号是否存在。

### POST `/auth/refresh` / POST `/auth/logout`

刷新令牌轮换；注销使当前 refresh token 失效，返回 `204`。

### GET `/me`

返回当前用户、组织、角色和可用权限。

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

### 3.2 `PUT /profiles/me` 请求体（`ProfileUpsertRequest`，`app/schemas/profile.py:220` 起）

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

### 3.3 `skills[]` 每条的名字规矩（2026-11-07 起）

`SkillInput`（`app/schemas/resume.py:32`）= 只有 `name`（1–100）一格。

- **`name` 两端空白由服务端裁掉**，纯空白名 → 422（以前 `"   "` 能过 `min_length=1`，会往库里存一条空白技能，在简历里渲染成空条目）。这与岗位侧 `JobSkillInput.normalize_name` 同规矩，目的是让两张技能表只有一种规范化程度。
- **重名判定用 `skill_name_key` = NFKC + casefold**（`app/core/skill_names.py`，与提取端点、岗位写侧同一个函数），不是裸相等。改之前 `Python` 与 `python` 能过校验、走到 INSERT，再由 `uq_skills_profile_name`（建在大小写不敏感的 `utf8mb4_0900_ai_ci` 上）抛 `IntegrityError 1062`——用户看到的是内部错误，正确答案是「技能名称重复」的 422。全角 `Ｐython` 同理。
- 判定键**不删标点、不去内部空白**，所以 `C#` 与 `C`、`Web␣␣开发` 与 `Web␣开发` 仍是两个技能；理由与实测对照表见 ADR-011 §2.D.2。
- `self_rating` **已于 2026-11-07 删除**（迁移 `20261107_0013`；它此前在 UI 已不采集、渲染不使用、库里实测恒为 `{2:1, 3:23, 4:1}`，是一个零信息常量，理由见 ADR-013 §4）。旧客户端多发的那个键不会报错也不会入库（Pydantic 丢弃未知键），但**不要继续写它**。


## 4. 岗位

> 本章是 Phase 4（B1 / B1 补正 / B2 / B3 / C1）**已实现**的后端契约，取代本文件旧版的异步岗位分析口径。
> 代码位置：端点 `app/api/v1/jobs.py`，请求/响应模型 `app/schemas/job.py`，业务规则 `app/services/job_service.py`，ORM 与仓储 `app/repositories/job_repository.py`，DeepSeek 适配器 `app/integrations/skill_extractor.py`；表结构 `alembic/versions/20261105_0011_jobs.py`（`jobs`）与 `20261105_0012_job_skills.py`（`job_skills`）。设计依据 `docs/adr/ADR-011-job-board-and-skill-extraction.md`。
> 旧版的 `POST /jobs` 返回 `202` + `ai_job_id`、以及 `POST /jobs/{job_id}/analyze` 异步任务形态**从未实现**，并被 ADR-011 2.A 明确否决（队列依赖 Redis/ARQ/Celery，仍在 AGENTS.md §3.2 冻结清单内）。

### 4.0 状态码从哪里来（改错误契约前必读）

运行时 `/openapi.json` 里，本章 **12 个操作的 `responses` 只声明成功码与 `422`**（实测导出，只读、不连库）：

| 操作 | OpenAPI summary | schema 里声明的 responses |
|---|---|---|
| `GET /api/v1/jobs` | List Jobs | `200`、`422` |
| `POST /api/v1/jobs` | Create Job | `201`、`422` |
| `POST /api/v1/jobs/extract-skills` | Extract Job Skills | `200`、`422` |
| `GET /api/v1/jobs/trash` | List Job Trash | `200` |
| `GET /api/v1/jobs/{job_id}` | Read Job | `200`、`422` |
| `POST /api/v1/jobs/{job_id}/match` | Match Job | `200`、`422` |
| `GET /api/v1/jobs/{job_id}/requirement-match` | Requirement Match Job | `200`、`422` |
| `PUT /api/v1/jobs/{job_id}` | Update Job | `200`、`422` |
| `DELETE /api/v1/jobs/{job_id}` | Delete Job | `200`、`422` |
| `PUT /api/v1/jobs/{job_id}/publish` | Publish Job | `200`、`422` |
| `PUT /api/v1/jobs/{job_id}/unpublish` | Unpublish Job | `200`、`422` |
| `PUT /api/v1/jobs/{job_id}/restore` | Restore Job | `200`、`422` |

**`400` / `401` / `404` / `429` / `500` / `502` 不在 schema 里**——它们由 `app/main.py` 注册的异常处理器返回裸 `JSONResponse`，FastAPI 不会把处理器写进 `responses`。下面这张表是按处理器实读出来的实际语义，不要读成「schema 已声明这些码」：

| 触发条件 | HTTP | `error.code` | 代码位置 |
|---|---|---|---|
| 请求体或查询参数不合模型 | 422 | `VALIDATION_ERROR` | `app/main.py:203-215` |
| 无 `Authorization` 头 / 令牌无效或过期 | 401 | `CLIENT_ERROR`，message 为 `UNAUTHORIZED_MESSAGE` | 抛出 `app/services/dependencies.py:52`、`:132`；映射 `app/main.py:192-194` |
| 岗位不存在 / 不属于当前用户 / 已在回收站 | 404 | `NOT_FOUND`（处理器的固定文案「资源不存在。」） | 抛出 `app/api/v1/jobs.py:68-70`；映射 `app/main.py:189-191` |
| 提取请求缺 `X-DeepSeek-Key` | 400 | `AI_KEY_MISSING`，`retryable: false` | `app/services/job_service.py:204-205` → `app/main.py:259-264` |
| DeepSeek 拒绝该 Key（鉴权失败） | 401 | `AI_KEY_INVALID` | `app/integrations/skill_extractor.py:294-298` → `app/services/job_service.py:208-209` → `app/main.py:266-275` |
| 上游不可用 / 输出不是可解析 JSON / 内容为空 / 无一可回溯 | 502 | `AI_SERVICE_UNAVAILABLE`，`retryable: true` | `app/main.py:277-290`（异常族 `app/services/job_service.py:204-213`、`:270-281`） |
| 输出被服务端 `max_tokens` 切断（`finish_reason == "length"`） | 502 | `AI_SERVICE_UNAVAILABLE`，`retryable: true`，**并在外层 `meta.finish_reason` 回显 `"length"`** | 就地组装 `app/api/v1/jobs.py:151-160`，文案 `EXTRACT_TRUNCATED_MESSAGE` `app/api/v1/jobs.py:62-65` |
| 提取超过 `EXTRACT_RATE_LIMIT`（`10/minute`，按客户端 IP） | 429 | `RATE_LIMITED`，`retryable: true`，响应头 `Retry-After: 60` | 常量 `app/core/rate_limit.py:39`；装饰器 `app/api/v1/jobs.py:122`；映射 `app/main.py:217-237` |
| 匹配超过 `MATCH_RATE_LIMIT`（`10/minute`，**按用户**而不是按 IP） | 429 | `RATE_LIMITED`，`retryable: true`，响应头 `Retry-After: 60` | 常量 `app/core/rate_limit.py:42`；键函数 `:45`；装饰器 `app/api/v1/jobs.py:197`；映射同一个 `app/main.py:217-237`（§4.10） |
| 未捕获异常 | 500 | `INTERNAL_ERROR` | `app/main.py:173-182` |

推论两条：① 改路由的 `response_model` 或 `status_code` 动不了 401/404/500/502 的错误契约，要动它们必须改 `app/main.py` 的处理器；② 前端与联调脚本不要拿 `openapi.json` 的 `responses` 键集合去枚举错误分支，那样会漏掉上表全部行。

### 4.1 身份、归属与可见性（权限矩阵）

身份只来自 JWT：`CurrentUserDep = Depends(get_current_user)`（`app/api/v1/jobs.py:52`）。`users` 表**没有 `role` 列**，也没有任何审核角色（ADR-011 1.A），因此旧版「未审核岗位仅创建者和审核者可见」这句作废：**`published` 任何登录用户可读；他人的 `draft` 一律 404，不是 403**（403 会暴露「该 id 存在」，先例见 `app/api/v1/profiles.py:10`，本文件与 `app/api/v1/jobs.py:11-12` 的说明）。

| # | 操作 | 允许的调用者 | 非创建者的结果 | 实际状态码 | 代码位置 |
|---|---|---|---|---|---|
| 1 | `GET /jobs` | 任何登录用户 | 列表里不会出现他人的 `draft` | 200 | `app/api/v1/jobs.py:73-106`、谓词 `app/repositories/job_repository.py:476-479` |
| 2 | `GET /jobs/{job_id}` | `published`：任何登录用户；`draft`：仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:181-193`、`app/repositories/job_repository.py:198-207`、`:458-463` |
| 3 | `POST /jobs` | 任何登录用户 | — | 201 / 422 / 401 | `app/api/v1/jobs.py:109-118` |
| 4 | `PUT /jobs/{job_id}` | 仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:267-280`、`app/repositories/job_repository.py:209-224` |
| 5 | `DELETE /jobs/{job_id}` | 仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:313-325`、`app/services/job_service.py:368-376` |
| 6 | `PUT /jobs/{job_id}/publish`、`/unpublish` | 仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:283-310`、`app/services/job_service.py:360-366` |
| 7 | `POST /jobs/extract-skills` | 任何登录用户 | 无归属概念（纯计算，不落库） | 200 / 400 / 401 / 422 / 429 / 502 | `app/api/v1/jobs.py:121-161`、`app/services/job_service.py:190-214` |
| 8 | `GET /jobs/trash` | 任何登录用户 | 只返回**自己的**软删岗位（过滤在 SQL 里） | 200 | `app/api/v1/jobs.py:164-178`、`app/repositories/job_repository.py:284-295` |
| 9 | `PUT /jobs/{job_id}/restore` | 仅创建者 | 404；「岗位存在但不在回收站」同样是 404 | 200 / 404 | `app/api/v1/jobs.py:328-346`、`app/services/job_service.py:378-393` |
| 10 | `POST /jobs/{job_id}/match` | `published`：任何登录用户；`draft`：仅创建者 | 404（他人的草稿**包括**回收站里的） | 200 / 401 / 404 / 422 / 429 | `app/api/v1/jobs.py:196-226`、`app/services/match_service.py:121-138` |
| 11 | `GET /jobs/{job_id}/requirement-match` | `published`：任何登录用户；`draft`：仅创建者 | 同上，404 而不是 403（**与 #10 同一个 `get_readable` 谓词**） | 200 / 401 / 404 / 422 | `app/api/v1/jobs.py:229-264`、`app/services/match_service.py:150-184` |

三条实现约束（都有代码兜住，不是口头承诺）：

- 越权 / 不存在 / 已软删在 service 层收敛成同一个 `JobNotFoundError`（`app/services/job_service.py:94-103`），api 层只有一句 `raise _not_found()`；`app/services/` 下不出现 `HTTPException`（AGENTS.md §4.1）。
- 已软删的岗位**读写都是 404**：`get_readable` 带 `deleted_at IS NULL`（`app/repositories/job_repository.py:205`），`get_owned` 默认也带（`:222-223`），只有 `restore` 走 `include_deleted=True`（`app/services/job_service.py:391`）。
- 归属校验写在 SQL 谓词里（`created_by_user_id == user_id`），不是「先查出来再在服务层比一次」（`app/repositories/job_repository.py:215-224` 的注释；`update_status` 再兜一层 `:428-431`）。

### 4.2 请求体里有什么、没有什么

`POST /jobs` 与 `PUT /jobs/{job_id}` 共用同一个模型 `JobWriteRequest`（`app/schemas/job.py:137-194`）：

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

`skills[]` 每条收**四格**（`JobSkillInput`，`app/schemas/job.py:74-134`）：

| 字段 | 类型 / 约束 | 代码位置 |
|---|---|---|
| `name` | string，1–100；两端空白会被裁掉，裁完为空则 422 | `:113`、`:120-131` |
| `type` | `hard` / `soft` / `domain`，可空 | `:55`、`:114` |
| `evidence_text` | string ≤60（`MAX_EVIDENCE_CHARS`）；纯空白会被折成 `null` | `:116`、`:133-145`、常量 `app/integrations/skill_extractor.py:71` |
| `confidence` | `0–1`（`DECIMAL(5,4)`），可空；越界是 422 而不是静默丢弃 | `:118` |

**请求体里没有、也提交不进来的字段**（`app/schemas/job.py:8-16` 模块说明，行为是 Pydantic 默认 `extra="ignore"` 整键丢掉）：`status`、`created_by_user_id`、`origin`、`extract_model`、`extract_prompt_version`、`extracted_at`，以及旧版的 `requirements`（字符串数组，从未实现）。

四条服务端派生规则（这是「`origin` 不在请求体里」的完整含义）：

1. `POST /jobs` 恒落 `status='draft'`（`app/services/job_service.py:329-344`）；`PUT` 不改 `status` 也不改 `deleted_at`（`app/repositories/job_repository.py:364-366`）。发布 / 下架是独立动作。
2. 每条技能的 `origin` 由服务层现场派生：该条 `evidence_text` 与岗位 `description` 各过四步归一化（NFKC → casefold → 去空白 → 去标点）后，前者是后者的子串 → `jd_verifiable`，否则 → `unverified`（`app/services/job_service.py:456-480`，归一化与子串判定在 `app/integrations/skill_extractor.py:189-225`；值域常量 `app/repositories/job_repository.py:69-70`）。`evidence_text` 为空即 `unverified`——空串是任何文本的子串，必须先挡掉（`app/integrations/skill_extractor.py:216-225`）。
3. 三列提取溯源戳由服务端在本次保存里盖：本次提交的技能里**只要有任何一条**被判为 `jd_verifiable`，就写入 `extract_model`（固定 `"deepseek-flash"`，`app/integrations/copy_writer.py:40`）、`extract_prompt_version`（`"v1"`，`app/integrations/skill_extractor.py:56`）、`extracted_at`（应用时钟 UTC naive，`app/services/job_service.py:437-453`）；一条都没有则三列保持 `null`。`PUT` 会**整批重算**这三列，不是「只在有值时覆盖」（`app/repositories/job_repository.py:371-373`）。
4. 一次提交里的技能名重复先报 422「`skill names must be unique`」（`app/schemas/job.py:175-194`），判定键是 `skill_name_key()` = NFKC + casefold，**只有两步**，与上面第 2 条那个四步归一化函数是两回事（`app/core/skill_names.py`；为什么必须分开见 ADR-011 §2.D.2）。数据库侧的 `UNIQUE (job_id, name)` 名为 `uq_job_skills_job_name`（`alembic/versions/20261105_0012_job_skills.py:96`）。

过期是**读侧判定，不落状态、不写库**：`deadline is not None and deadline < date.today()`（判据 `app/core/deadline.py:15-21`，`JobService.is_expired` 只是委托它，`app/services/job_service.py:483-490`；取「今天」的调用点 `:153`）。「今天」取**应用进程时钟**，SQL 里不出现数据库侧的当日函数（`app/repositories/job_repository.py:243`、`:490-492` 的谓词只吃绑定参数）。`deadline` 为 `null` 视为长期有效。`jobs.status` 的值域只有 `draft` / `published`（`app/repositories/job_repository.py:56-57`，CHECK `ck_jobs_status` 见 `alembic/versions/20261105_0011_jobs.py:84-88`）——**没有 `expired`，也没有 `closed`**，旧版那句「过期岗位自动转为 `expired`」作废。

### 4.3 GET `/jobs` — 广场与我的草稿

查询参数一共**五个**（`app/api/v1/jobs.py:74-83` 的函数签名，实测无其它参数；旧版的 `location`、`skill_id` 已删：前者本轮「存而不筛」，后者依赖并不存在的技能字典表）：

| 参数 | 类型 / 默认 | 语义 |
|---|---|---|
| `status` | `draft` \| `published`，默认 `published` | 白名单外的值由 FastAPI 直接 422（`Literal` 而非自由字符串，`app/api/v1/jobs.py:57`）。`status=draft` 只返回**本人**的草稿 |
| `keyword` | string ≤200，可空 | 子串匹配 `company_name` **或** `title`，`autoescape=True`（`app/repositories/job_repository.py:482-489`） |
| `include_expired` | bool，默认 `false` | `true` 时把 `deadline < 今天` 的岗位一起返回（`:490-492`） |
| `page` | int ≥1，默认 1 | |
| `page_size` | int ≥1，≤100（`MAX_PAGE_SIZE`），默认 20 | `app/schemas/job.py:66-67` |

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

### 4.7 状态与回收站端点

| 端点 | 方法 | 效果 | 返回 | 代码位置 |
|---|---|---|---|---|
| `/jobs/{job_id}/publish` | **PUT**（不是 POST） | `status` → `published`，进广场 | `200` + `data.job` | `app/api/v1/jobs.py:283-295` |
| `/jobs/{job_id}/unpublish` | **PUT** | `status` → `draft`，广场立刻读不到 | `200` + `data.job` | `app/api/v1/jobs.py:298-310` |
| `/jobs/{job_id}` | DELETE | 软删除：只打 `deleted_at`，`job_skills` 一行不动（恢复后内容必须还在） | `200` + `{"data": {"job_id": "…", "deleted": true}}` | `app/api/v1/jobs.py:313-325`、`app/repositories/job_repository.py:433-443` |
| `/jobs/trash` | GET | **自己的**软删岗位，按 `deleted_at DESC, id DESC` | `200` + `data.jobs`（`JobSummary` 数组），`meta` 只有 `request_id`（**无分页参数、无 `total`**） | `app/api/v1/jobs.py:164-178`、`app/repositories/job_repository.py:284-295` |
| `/jobs/{job_id}/restore` | PUT | `deleted_at` 清回 `null`，`status` 原样保留（删掉已发布岗位再恢复，它仍是已发布） | `200` + `data.job` | `app/api/v1/jobs.py:328-346`、`app/services/job_service.py:378-393` |

**没有 `/jobs/{job_id}/close`**，也没有 `/archive`、`/expire` 之类的端点（OpenAPI 实测 12 个操作里没有）。发布与下架都是 `PUT` 且仅创建者可调，非创建者一律 404（4.1 #6），旧版「`/publish` 需要审核权限」作废。

路由声明顺序是契约的一部分：`/jobs/trash` 与 `/jobs/extract-skills` 必须排在 `/jobs/{job_id}` **之前**，否则静态段会被当成岗位 id 塞进路径参数，表现为「回收站打不开，404」（`app/api/v1/jobs.py:3-9`）。

岗位没有永久删除端点，也没有定时物理清理（`dev` 库里回收站的行会一直留着），裁定记录见 ADR-011 1.D 的缺口 ②。

### 4.8 POST `/jobs/extract-skills` — AI 技能提取（纯计算）

**200 同步返回**，需要两个请求头：`Authorization: Bearer <access_token>`（`app/api/v1/jobs.py:128`）与 `X-DeepSeek-Key`（BYOK，常量 `DEEPSEEK_KEY_HEADER` `app/services/dependencies.py:37`；只从请求头读，不落库、不写日志、不进异常消息）。Key 缺失或为空串时服务层抛 `ResumeApiKeyMissingError`（`app/services/job_service.py:204-205`）。

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

`data.skills[]` 四格（`ExtractedSkillRead`，`app/schemas/job.py:313-327`）：`name`、`type`（`hard`/`soft`/`domain` 或 `null`）、`evidence`、`confidence`（`float | null`）。字段名是 **`evidence`**，不是 `evidence_text`：这里是「模型给的原话」，还没经过落库判定；用户确认后保存时同一串文字走 `skills[].evidence_text` 进列（`app/schemas/job.py:317-318`）。数组**至少一条**（`min_length=1`，`app/schemas/job.py:369`）——一条都不剩时不是空列表而是 502。

`data.meta` 七个字段逐个的语义（模型 `app/schemas/job.py:330-359`，赋值点 `app/services/job_service.py:293-302`）：

| 字段 | 类型 | 含义 | 值的来源 |
|---|---|---|---|
| `model` | string | 这批结果是哪个模型产出的 | 常量 `DEEPSEEK_MODEL` = `"deepseek-flash"`（`app/integrations/copy_writer.py:40`） |
| `prompt_version` | string | 这批结果是哪一版 Prompt 产出的 | 常量 `EXTRACT_PROMPT_VERSION` = `"v1"`（`app/integrations/skill_extractor.py:56`）；Prompt 每改一次 +1 |
| `received_count` | int | 模型**一共给了**几条（含后来被剔除、被合并、被切断的） | `len(extraction.skills)`（`app/services/job_service.py:249`） |
| `dropped_count` | int | 其中几条因**无法回溯 JD 原文**（或 `name`/`evidence` 不合规）被剔除 | `:251`、`:255-257`；判定 `_verify_item` `:305-326` |
| `deduped_count` | int | 其中几条与前面的条目**重名**被合并掉（保留第一次出现的） | `:252`、`:258-263`；判定键 `skill_name_key`（`app/core/skill_names.py`）。这是 ADR-011 §2.D.2 的第 7 个字段 |
| `truncated` | bool | 15 条封顶**是否真的切掉了东西** | `len(kept) > MAX_EXTRACTED_SKILLS`（`app/services/job_service.py:268`；常量 `app/integrations/skill_extractor.py:65`） |
| `finish_reason` | string \| null | 上游给的原因；用来把「被我们的 token 上限切断」与「模型不守格式」分开 | `completion.choices[0].finish_reason`（`app/integrations/skill_extractor.py:305-306`） |

三个计数之间的算术关系（`docs/adr/ADR-011-job-board-and-skill-extraction.md` §2.D.2）：`received_count = dropped_count + deduped_count + 返回条数 + 被 15 条封顶切掉的条数`，最后那格**没有对应字段**（已定不加第 8 个计数），所以 `truncated` 为真时前三项之和必然小于 `received_count`。`dropped_count` 与 `deduped_count` 各说各的、绝不互相串味：核验失败的条目不进重名集合（`app/services/job_service.py:260-263`）。核验、合并都跑在截断**之前**（`:240-246` 的注释），否则这两句前端文案都会变成假话。`truncated` 只在确实切的时候为真。

服务端强制的七条规则（不指望 Prompt，全部在 `app/services/job_service.py:216-325`）：`evidence` 归一化后必须是 `description` 归一化文本的子串，否则丢弃；`name` 为空或 >40 字（`MAX_SKILL_NAME_CHARS` `app/integrations/skill_extractor.py:75`）丢弃；`evidence` 为空或 >60 字丢弃；`type` 不在 `hard/soft/domain` 内则**保留该条**、`type` 置 `null`（`EXTRACTED_SKILL_TYPES` `:90`）；重名合并保留第一条；>15 条截断并置 `truncated=true`；一条都不剩 → 502，**绝不返回空列表**。

七种失败情形的实际落点（对应 ADR-011 附录的验收清单）：

| # | 情形 | HTTP + `error.code` | 代码位置 |
|---|---|---|---|
| 1 | 请求头没有 Key | 400 `AI_KEY_MISSING` | `app/services/job_service.py:204-205` → `app/main.py:259-264` |
| 2 | DeepSeek 拒绝该 Key | 401 `AI_KEY_INVALID` | `app/integrations/skill_extractor.py:294-298` → `app/services/job_service.py:208-209` → `app/main.py:266-275` |
| 3 | 网络超时 / 上游 4xx·5xx | 502 `AI_SERVICE_UNAVAILABLE` | `app/integrations/skill_extractor.py:299-303` → `app/services/job_service.py:212-213` |
| 4 | 返回内容不是可解析的 JSON 对象 | 502 `AI_SERVICE_UNAVAILABLE` | `app/integrations/skill_extractor.py:327-343` |
| 5 | 所有条目的 `evidence` 都回溯不到原文 | 502 `AI_SERVICE_UNAVAILABLE` | `app/services/job_service.py:270-281` |
| 6 | `choices[0].message.content` 为空或全空白 | 502 `AI_SERVICE_UNAVAILABLE` | `app/integrations/skill_extractor.py:320-323` |
| 7 | `finish_reason == "length"`（被我们自己的 `max_tokens` 切断） | 502 `AI_SERVICE_UNAVAILABLE` + 外层 `meta.finish_reason = "length"` | `app/integrations/skill_extractor.py:309-319` → `app/api/v1/jobs.py:151-160` |

第 7 情形单独命名、不与第 4 情形合并，是因为两者的**用户动作不同**：第 4 该重试同一请求，第 7 该「只粘 JD 的核心段落，或减少期望条数」（`EXTRACT_TRUNCATED_MESSAGE` `app/api/v1/jobs.py:62-65`）。它在 api 层就地组装，所以是「走全局处理器」的唯一例外；错误码仍是 `AI_SERVICE_UNAVAILABLE` 一个都没新增。

401 有两个互不相干的来源，前端靠 `error.code` 区分而不是靠状态码或 URL：令牌失效是 401 + `CLIENT_ERROR`（会触发刷新），`AI_KEY_INVALID` 是 401 + `AI_KEY_INVALID`（**不触发令牌刷新、不踢下线**），后者由 `frontend/src/api/client.ts:394` 分流、并由 `frontend/src/api/__tests__/byok.spec.ts:181` 钉住。

三个 `AI_*` 码的处理器文案必须作用域中性（`app/main.py:247-250` 的注释与 ADR-011 2.F）：**不得出现「润色」或「简历」**，因为提取侧复用润色的异常族、两个端点进同一组处理器。

日志里只出现计数、`finish_reason`、`request_id`，不出现 Key、JD 正文、`evidence` 内容、技能名（`app/services/job_service.py:274-292`）。

### 4.9 响应对象的字段

`JobSummary`（列表项与回收站项；**不含 `description`**，JD 最长 8000 字；技能只给**前 6 条**，`SUMMARY_SKILL_LIMIT` `app/services/job_service.py:83`、切片 `:537`）：`id`、`company_name`、`title`、`location`、`employment_type`、`salary_min`、`salary_max`、`deadline`、`status`、`created_by_user_id`、`created_at`、`updated_at`、`deleted_at`、`skills[]`、`is_expired`（`app/schemas/job.py:212-243`）。

`JobRead`（详情与所有写操作的返回；含 `description` 全文与**全部**技能）：上面的字段之外还有 `experience_required`、`extract_model`、`extract_prompt_version`、`extracted_at`（`app/schemas/job.py:246-287`）。

`skills[]` 每条（`JobSkillRead`，`app/schemas/job.py:197-209`）：`id`、`name`、`type`、`origin`（`jd_verifiable` 或 `unverified`，**只出现在响应里**）、`confidence`、`evidence_text`。

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
`app/services/match_service.py:150-184` 的 `requirement_match()`。

**它为什么存在**：「你已具备」角标原先在浏览器里判——`trim + NFKC + toLowerCase` 之后做集合成员检查。
那套键够不着版本号写法，于是同一屏上角标说「不具备」、匹配卡说「已具备」（§4.10 第 5 条那个判据
只住在后端）。第 3c 步把判定收回后端，前端只消费。

**请求**：路径里一个 `job_id`，无请求体、无查询参数。用的是**当前用户的默认档案**，
与 `POST /jobs/{job_id}/match` 同一个 `get_readable` 可见性谓词、同一个 `_gather`
（`app/services/match_service.py:199-244`：一个 session、一份数据快照）。
两条链路如果读的不是同一时刻，「同屏一致」就只能靠运气。

**响应 `data`**（模型 `app/schemas/match.py::RequirementMatchResponse`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` / `profile_id` / `profile_title` | `str` / `str\|null` / `str\|null` | 与匹配端点同样的回显：角标是按哪份简历标的 |
| `coverage[]` | 数组，**顺序与条数都等于** `GET /jobs/{job_id}` 的 `skills` | 老板判据 (a) 的基准域 U；少一行就等于那一行永远长不出角标 |
| `coverage[].name` | `str` | 岗位侧原写法，逐字回显 |
| `coverage[].status` | `"covered" \| "partial" \| "missing"` | 前两档都算「写法命中」；`covered` / `partial` 的分界来自**关键词模式**，与匹配卡上可能被 AI 挪动的那条线无关——角标只回答命中没有。**挪档挪不出并集**：装配三档时「命中却拿不到强度」一律落 `partial`（`app/services/match_scoring.py:752`），所以配了 Key 的那些次点击里，`status ∈ {covered, partial}` 的集合仍与匹配卡 `covered ∪ partial` 逐字相同，这一点由 `test_badge_union_still_equals_match_union_in_ai_mode` 在 AI 模式下单独测过 |
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
`frontend/src/stores/job.ts::loadRequirementMatch` → 角标判据 `frontend/src/stores/job.ts:750-753` 的
`isRequirementHit(name)`。为什么要多这一个布尔函数而不是让模板写 `v-if="skillStatus(name)"`：
`missing` 也是非空字符串、在 Vue 里是**真值**，第一版就是这么渲染出「每条技能都长角标」的，
用例当场红（那条断言留在 `JobDetailView.spec.ts` 里防复发）。
拉取失败时角标整体不显示、详情页照常（`JobDetailView.vue::loadCoverage` 的 catch），
判定永远不在前端补做一次。

### 4.11 本章未接通 / 不存在的接口

以下都是**设计意图或已否决项**，本轮不实现、不排期；标注 `# PHASE-2: not wired`（沿用 ADR-011 修订清单第 7 条的口径）。

- `POST /jobs/{job_id}/analyze`（本文件旧版有）：**端点不存在**，OpenAPI 实测的 12 个操作里没有它。异步任务 + `ai_job_id` 形态被 ADR-011 2.A 否决。# PHASE-2: not wired
- `/jobs/{job_id}/close`、「过期自动转 `expired`」：**都不存在**（4.7）。# PHASE-2: not wired
- 按 `location` / `salary` / `experience_required` 筛选：**存而不筛**，谓词函数里没有这三列（`app/repositories/job_repository.py:463-491`、ADR-011 1.G）。
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

### POST `/resumes/generate`

请求：`{"student_id":"uuid","job_id":"uuid","template":"clean_white","max_pages":1.5}`。只能使用学生已确认资料；**同步返回 `200` 与生成结果，不返回 `202`、不创建 `ai_job_id`**（`app/api/v1/resumes.py:43-63` 直接 `success_response(..., resume.model_dump(...))`；`POST /api/v1/resumes/generate` 的 OpenAPI `responses` 实测为 `200`、`422`）。
<!-- 本行的请求体示例仍是旧口径：实际模型是 `app/schemas/resume.py:79-91` 的 `{title, profile_id}`。该差异属 §6 简历章节，不在本轮「岗位 + 错误码」范围内，已列入交付说明的未解决问题。 -->

### GET `/resumes` / GET `/resumes/{resume_id}`

返回版本、来源、确认状态和授权状态。

### PATCH `/resumes/{resume_id}/versions/{version_id}`

更新编辑后的区块。保存新版本，禁止覆盖审计历史。

### POST `/resumes/{resume_id}/export`

请求 `{"version_id":"uuid","format":"pdf"}`；返回异步任务。完成后返回短时下载 URL。

### POST `/resumes/privacy-check` — 导出前隐私检查（批次 B2，已实现）

请求：`{"profile_id":"uuid 可省略"}`。省略时按登录用户取默认简历，与 `/resumes/generate` 用**同一个** `_resolve_profile`。
响应：`data = {"profile_id": ..., "categories": [四类，顺序固定为 联系方式 / 作品链接 / 个人信息 / 授权范围]}`；每类是
`{key, label, status, note, items[]}`，每格 item 是 `{key, label, present, included, note}`。

三条口径必须连着读：

1. **纯计算、不落库**：不写任何表、不加列、不建表，与 `/resumes/polish`、`/jobs/extract-skills` 同一条路子（`tests/test_resume_privacy_check.py::test_privacy_check_is_pure_computation` 钉的就是「调用一次 `resumes` 行数不变」）。
2. **`present` 与 `included` 是两个问题**，不许合并：`目标岗位偏好` 是 `present=true, included=false`（确实存在，但不写进简历）。反过来，没填的邮箱两边都是 `false`，而那一句「HR 无法用邮箱联系你」只能在这里说——纸面上**故意不给可选项打「待补充」标记**（见第 3 条与 `app/integrations/resume_generator.py` 的调用处注释）。
3. **两类今天核不了就说核不了**：`作品链接` 与 `授权范围` 的 `status` 是 `not_available`（数据模型里没有对应的列或表；`not_available` 时 `note` 必给）。把它们写成「用户没填」是假话，把它们从清单里省掉则是把 FR-06 点名的两格悄悄删了。实现在 B4（作品/证书）与 B5（组织与授权）时才出现。

错误口径与 `/generate` 逐字对齐：`profile_id` 是别人的 / 不存在的 / 在回收站的 → `404 NOT_FOUND`；这个用户一份档案都没有 → `400 CLIENT_ERROR`；`profile_id` 超长 → `422 VALIDATION_ERROR`。

**与上面那张「设计意图」表的关系**：`POST /resumes/{resume_id}/export` 至今**未实现**（`app/` 里 `export`/`pdf` 零命中，也没有任何 PDF 依赖），所以「导出前显示」这一格今天没有挂载点。B2 因此把检查做成一个**读同一份档案的纯计算端点**，等导出真落地时它才有地方挂——这不是把两件事做完了，是把其中可核的两格做完、把不可核的两格如实标出来。

### POST `/resumes/{resume_id}/share`

请求 `{"version_id":"uuid","target_type":"enterprise","target_id":"uuid","fields":["contact","skills","projects"],"expires_at":"..."}`。

## 7. 模拟面试

### POST `/interviews`

请求：`{"job_id":"uuid","resume_version_id":"uuid","mode":"text","round":"technical","question_count":8}`。返回 `interview_id`。

### POST `/interviews/{interview_id}/turns`

文本模式请求 `{"answer":"..."}`；返回下一问题或结束状态。音视频模式仅提交转写片段和时间戳。

### POST `/interviews/{interview_id}/finish`

结束会话并创建反馈 AI 任务；返回 `202`。

### GET `/interviews/{interview_id}/report`

只对本人或获授权教师返回，包含回答证据、建议和待练习技能，不输出录用保证。

### POST `/interviews/{interview_id}/media-room`

返回短时 WebRTC 房间 token、ICE 配置引用和过期时间。业务 API 不接收长期媒体密钥；媒体供应商通过适配器实现。

## 8. 教师与学校

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
