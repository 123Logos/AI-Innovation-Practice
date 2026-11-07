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

实际由代码发出的错误码（以 `app/` 全量 grep 这些字面量为准，不含测试）：`VALIDATION_ERROR`（`app/main.py:211`）、`RATE_LIMITED`（`app/main.py:231`）、`NOT_FOUND`（`app/main.py:190`）、`CLIENT_ERROR`（`app/main.py:193`）、`INTERNAL_ERROR`（`app/main.py:179`、`:196`）、`AI_KEY_MISSING`（`app/main.py:256`）、`AI_KEY_INVALID`（`app/main.py:265`）、`AI_SERVICE_UNAVAILABLE`（`app/main.py:276`、`app/api/v1/jobs.py:147`）。三个 `AI_*` 码只在 `/resumes/polish` 与 `/jobs/extract-skills` 上可达（`app/main.py:239-240` 的注释写明这一沿革），且处理器文案要求作用域中性——不得出现「润色」或「简历」（`docs/adr/ADR-011-job-board-and-skill-extraction.md` 2.F）。

以下码目前**只是本文件的设计意图**，`app/` 下没有任何一处产生它们：`AUTH_REQUIRED`、`AUTH_INVALID_CREDENTIALS`、`AUTH_FORBIDDEN`、`RESOURCE_NOT_FOUND`、`CONSENT_REQUIRED`、`CONFLICT`。未登录与令牌失效实际走 `HTTPException(401)`（`app/services/dependencies.py:50`、`:106`），被通用 4xx 处理器压成 `CLIENT_ERROR`（`app/main.py:192-194`），message 取 `UNAUTHORIZED_MESSAGE`（`app/services/dependencies.py:32`）。

`MEDIA_UNAVAILABLE` 属模拟面试，仍在冻结清单（AGENTS.md §3.2），未启用、无代码位置。

已删除的两个码（依 ADR-011 修订清单第 8 条）：`AI_JOB_FAILED` 依附于「异步 AI 任务」模型，该模型被 ADR-011 2.A 否决且从未实现；`AI_OUTPUT_UNSUPPORTED` 并入 `AI_SERVICE_UNAVAILABLE`——ADR-011 2.F 规定提取端点只复用润色那三码，一个都不新增。

错误响应必须包含稳定的 `code`、面向用户的 `message`、可供日志关联的 `request_id`；不得返回堆栈、SQL、密钥、供应商原始错误或其他租户数据。测试编号、预期 HTTP 状态、输出和处理规则见 [测试文档](TESTING.md)。

## 2. 认证与用户

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

运行时 `/openapi.json` 里，本章 **10 个操作的 `responses` 只声明成功码与 `422`**（实测导出，只读、不连库）：

| 操作 | OpenAPI summary | schema 里声明的 responses |
|---|---|---|
| `GET /api/v1/jobs` | List Jobs | `200`、`422` |
| `POST /api/v1/jobs` | Create Job | `201`、`422` |
| `POST /api/v1/jobs/extract-skills` | Extract Job Skills | `200`、`422` |
| `GET /api/v1/jobs/trash` | List Job Trash | `200` |
| `GET /api/v1/jobs/{job_id}` | Read Job | `200`、`422` |
| `PUT /api/v1/jobs/{job_id}` | Update Job | `200`、`422` |
| `DELETE /api/v1/jobs/{job_id}` | Delete Job | `200`、`422` |
| `PUT /api/v1/jobs/{job_id}/publish` | Publish Job | `200`、`422` |
| `PUT /api/v1/jobs/{job_id}/unpublish` | Unpublish Job | `200`、`422` |
| `PUT /api/v1/jobs/{job_id}/restore` | Restore Job | `200`、`422` |

**`400` / `401` / `404` / `429` / `500` / `502` 不在 schema 里**——它们由 `app/main.py` 注册的异常处理器返回裸 `JSONResponse`，FastAPI 不会把处理器写进 `responses`。下面这张表是按处理器实读出来的实际语义，不要读成「schema 已声明这些码」：

| 触发条件 | HTTP | `error.code` | 代码位置 |
|---|---|---|---|
| 请求体或查询参数不合模型 | 422 | `VALIDATION_ERROR` | `app/main.py:203-215` |
| 无 `Authorization` 头 / 令牌无效或过期 | 401 | `CLIENT_ERROR`，message 为 `UNAUTHORIZED_MESSAGE` | 抛出 `app/services/dependencies.py:50`、`:106`；映射 `app/main.py:192-194` |
| 岗位不存在 / 不属于当前用户 / 已在回收站 | 404 | `NOT_FOUND`（处理器的固定文案「资源不存在。」） | 抛出 `app/api/v1/jobs.py:61-63`；映射 `app/main.py:189-191` |
| 提取请求缺 `X-DeepSeek-Key` | 400 | `AI_KEY_MISSING`，`retryable: false` | `app/services/job_service.py:203-204` → `app/main.py:252-257` |
| DeepSeek 拒绝该 Key（鉴权失败） | 401 | `AI_KEY_INVALID` | `app/integrations/skill_extractor.py:294-298` → `app/services/job_service.py:207-208` → `app/main.py:259-268` |
| 上游不可用 / 输出不是可解析 JSON / 内容为空 / 无一可回溯 | 502 | `AI_SERVICE_UNAVAILABLE`，`retryable: true` | `app/main.py:270-283`（异常族 `app/services/job_service.py:209-215`、`:269-280`） |
| 输出被服务端 `max_tokens` 切断（`finish_reason == "length"`） | 502 | `AI_SERVICE_UNAVAILABLE`，`retryable: true`，**并在外层 `meta.finish_reason` 回显 `"length"`** | 就地组装 `app/api/v1/jobs.py:144-153`，文案 `EXTRACT_TRUNCATED_MESSAGE` `app/api/v1/jobs.py:55-58` |
| 提取超过 `EXTRACT_RATE_LIMIT`（`10/minute`，按客户端 IP） | 429 | `RATE_LIMITED`，`retryable: true`，响应头 `Retry-After: 60` | 常量 `app/core/rate_limit.py:37`；装饰器 `app/api/v1/jobs.py:115`；映射 `app/main.py:217-237` |
| 未捕获异常 | 500 | `INTERNAL_ERROR` | `app/main.py:173-182` |

推论两条：① 改路由的 `response_model` 或 `status_code` 动不了 401/404/500/502 的错误契约，要动它们必须改 `app/main.py` 的处理器；② 前端与联调脚本不要拿 `openapi.json` 的 `responses` 键集合去枚举错误分支，那样会漏掉上表全部行。

### 4.1 身份、归属与可见性（权限矩阵）

身份只来自 JWT：`CurrentUserDep = Depends(get_current_user)`（`app/api/v1/jobs.py:45`）。`users` 表**没有 `role` 列**，也没有任何审核角色（ADR-011 1.A），因此旧版「未审核岗位仅创建者和审核者可见」这句作废：**`published` 任何登录用户可读；他人的 `draft` 一律 404，不是 403**（403 会暴露「该 id 存在」，先例见 `app/api/v1/profiles.py:10`，本文件与 `app/api/v1/jobs.py:11-12` 的说明）。

| # | 操作 | 允许的调用者 | 非创建者的结果 | 实际状态码 | 代码位置 |
|---|---|---|---|---|---|
| 1 | `GET /jobs` | 任何登录用户 | 列表里不会出现他人的 `draft` | 200 | `app/api/v1/jobs.py:66-99`、谓词 `app/repositories/job_repository.py:476-479` |
| 2 | `GET /jobs/{job_id}` | `published`：任何登录用户；`draft`：仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:174-186`、`app/repositories/job_repository.py:198-207`、`:458-463` |
| 3 | `POST /jobs` | 任何登录用户 | — | 201 / 422 / 401 | `app/api/v1/jobs.py:102-111` |
| 4 | `PUT /jobs/{job_id}` | 仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:189-202`、`app/repositories/job_repository.py:209-224` |
| 5 | `DELETE /jobs/{job_id}` | 仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:235-247`、`app/services/job_service.py:369-377` |
| 6 | `PUT /jobs/{job_id}/publish`、`/unpublish` | 仅创建者 | 404 | 200 / 404 | `app/api/v1/jobs.py:205-232`、`app/services/job_service.py:361-367` |
| 7 | `POST /jobs/extract-skills` | 任何登录用户 | 无归属概念（纯计算，不落库） | 200 / 400 / 401 / 422 / 429 / 502 | `app/api/v1/jobs.py:114-154`、`app/services/job_service.py:191-215` |
| 8 | `GET /jobs/trash` | 任何登录用户 | 只返回**自己的**软删岗位（过滤在 SQL 里） | 200 | `app/api/v1/jobs.py:157-171`、`app/repositories/job_repository.py:286-297` |
| 9 | `PUT /jobs/{job_id}/restore` | 仅创建者 | 404；「岗位存在但不在回收站」同样是 404 | 200 / 404 | `app/api/v1/jobs.py:250-268`、`app/services/job_service.py:379-394` |

三条实现约束（都有代码兜住，不是口头承诺）：

- 越权 / 不存在 / 已软删在 service 层收敛成同一个 `JobNotFoundError`（`app/services/job_service.py:93-102`），api 层只有一句 `raise _not_found()`；`app/services/` 下不出现 `HTTPException`（AGENTS.md §4.1）。
- 已软删的岗位**读写都是 404**：`get_readable` 带 `deleted_at IS NULL`（`app/repositories/job_repository.py:205`），`get_owned` 默认也带（`:222-223`），只有 `restore` 走 `include_deleted=True`（`app/services/job_service.py:390`）。
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

1. `POST /jobs` 恒落 `status='draft'`（`app/services/job_service.py:330-345`）；`PUT` 不改 `status` 也不改 `deleted_at`（`app/repositories/job_repository.py:364-366`）。发布 / 下架是独立动作。
2. 每条技能的 `origin` 由服务层现场派生：该条 `evidence_text` 与岗位 `description` 各过四步归一化（NFKC → casefold → 去空白 → 去标点）后，前者是后者的子串 → `jd_verifiable`，否则 → `unverified`（`app/services/job_service.py:456-481`，归一化与子串判定在 `app/integrations/skill_extractor.py:189-227`；值域常量 `app/repositories/job_repository.py:69-70`）。`evidence_text` 为空即 `unverified`——空串是任何文本的子串，必须先挡掉（`app/integrations/skill_extractor.py:220-226`）。
3. 三列提取溯源戳由服务端在本次保存里盖：本次提交的技能里**只要有任何一条**被判为 `jd_verifiable`，就写入 `extract_model`（固定 `"deepseek-flash"`，`app/integrations/copy_writer.py:40`）、`extract_prompt_version`（`"v1"`，`app/integrations/skill_extractor.py:56`）、`extracted_at`（应用时钟 UTC naive，`app/services/job_service.py:437-454`）；一条都没有则三列保持 `null`。`PUT` 会**整批重算**这三列，不是「只在有值时覆盖」（`app/repositories/job_repository.py:371-373`）。
4. 一次提交里的技能名重复先报 422「`skill names must be unique`」（`app/schemas/job.py:175-194`），判定键是 `skill_name_key()` = NFKC + casefold，**只有两步**，与上面第 2 条那个四步归一化函数是两回事（`app/core/skill_names.py`；为什么必须分开见 ADR-011 §2.D.2）。数据库侧的 `UNIQUE (job_id, name)` 名为 `uq_job_skills_job_name`（`alembic/versions/20261105_0012_job_skills.py:96`）。

过期是**读侧判定，不落状态、不写库**：`deadline is not None and deadline < date.today()`（`app/services/job_service.py:483-489`，调用点 `:152`）。「今天」取**应用进程时钟**，SQL 里不出现数据库侧的当日函数（`app/repositories/job_repository.py:243`、`:490-492` 的谓词只吃绑定参数）。`deadline` 为 `null` 视为长期有效。`jobs.status` 的值域只有 `draft` / `published`（`app/repositories/job_repository.py:56-57`，CHECK `ck_jobs_status` 见 `alembic/versions/20261105_0011_jobs.py:84-88`）——**没有 `expired`，也没有 `closed`**，旧版那句「过期岗位自动转为 `expired`」作废。

### 4.3 GET `/jobs` — 广场与我的草稿

查询参数一共**五个**（`app/api/v1/jobs.py:67-76` 的函数签名，实测无其它参数；旧版的 `location`、`skill_id` 已删：前者本轮「存而不筛」，后者依赖并不存在的技能字典表）：

| 参数 | 类型 / 默认 | 语义 |
|---|---|---|
| `status` | `draft` \| `published`，默认 `published` | 白名单外的值由 FastAPI 直接 422（`Literal` 而非自由字符串，`app/api/v1/jobs.py:50`）。`status=draft` 只返回**本人**的草稿 |
| `keyword` | string ≤200，可空 | 子串匹配 `company_name` **或** `title`，`autoescape=True`（`app/repositories/job_repository.py:482-489`） |
| `include_expired` | bool，默认 `false` | `true` 时把 `deadline < 今天` 的岗位一起返回（`:490-492`） |
| `page` | int ≥1，默认 1 | |
| `page_size` | int ≥1，≤100（`MAX_PAGE_SIZE`），默认 20 | `app/schemas/job.py:66-67` |

排序固定 `created_at DESC, id DESC`（`app/repositories/job_repository.py:245`、`:257`），不提供排序参数。软删的行永远不在列表里（`:253-254`）。

返回 `200`，`data.jobs` 是 `JobSummary` 数组（字段见 4.9），`meta` 带 `page` / `page_size` / `total` / `request_id`；`total` 与列表共用同一套谓词，所以筛选后它会跟着变（`app/repositories/job_repository.py:261-284`）。

### 4.4 POST `/jobs` — 录入

返回 **`201`** + 完整岗位对象（`data.job`，`JobRead`）。状态码来源 `app/api/v1/jobs.py:102`（`status.HTTP_201_CREATED`），与 OpenAPI 实测一致。**不返回 `202`，不创建 `ai_job_id`，不在本请求里发生任何岗位分析或 AI 调用**——技能条目就是请求体里 `skills[]` 那几格，由用户在草稿态逐条确认后再提交（ADR-011 2.A）。

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

`published` 任何登录用户可读，他人 `draft` 与回收站里的行都是 404（`app/api/v1/jobs.py:174-186`）。返回 `200` + `data.job`（`JobRead`，含 `description` 全文与**全部**技能条目）。

### 4.6 PUT `/jobs/{job_id}` — 整体覆盖

仅创建者，请求体与 `POST /jobs` 完全同形（同一个 `JobWriteRequest`）。语义是**字段覆盖 + 技能子表先删后插**，一次提交（`app/repositories/job_repository.py:356-386`）：数组里没出现的技能会被删掉，所以前端必须回传完整列表。`status` / `deleted_at` 不变，三列溯源戳按本次提交重算（4.2 第 3 条）。非创建者或已软删 → 404。返回 `200` + `data.job`。

### 4.7 状态与回收站端点

| 端点 | 方法 | 效果 | 返回 | 代码位置 |
|---|---|---|---|---|
| `/jobs/{job_id}/publish` | **PUT**（不是 POST） | `status` → `published`，进广场 | `200` + `data.job` | `app/api/v1/jobs.py:205-217` |
| `/jobs/{job_id}/unpublish` | **PUT** | `status` → `draft`，广场立刻读不到 | `200` + `data.job` | `app/api/v1/jobs.py:220-232` |
| `/jobs/{job_id}` | DELETE | 软删除：只打 `deleted_at`，`job_skills` 一行不动（恢复后内容必须还在） | `200` + `{"data": {"job_id": "…", "deleted": true}}` | `app/api/v1/jobs.py:235-247`、`app/repositories/job_repository.py:435-445` |
| `/jobs/trash` | GET | **自己的**软删岗位，按 `deleted_at DESC, id DESC` | `200` + `data.jobs`（`JobSummary` 数组），`meta` 只有 `request_id`（**无分页参数、无 `total`**） | `app/api/v1/jobs.py:157-171`、`app/repositories/job_repository.py:286-297` |
| `/jobs/{job_id}/restore` | PUT | `deleted_at` 清回 `null`，`status` 原样保留（删掉已发布岗位再恢复，它仍是已发布） | `200` + `data.job` | `app/api/v1/jobs.py:250-268`、`app/services/job_service.py:379-394` |

**没有 `/jobs/{job_id}/close`**，也没有 `/archive`、`/expire` 之类的端点（OpenAPI 实测 10 个操作里没有）。发布与下架都是 `PUT` 且仅创建者可调，非创建者一律 404（4.1 #6），旧版「`/publish` 需要审核权限」作废。

路由声明顺序是契约的一部分：`/jobs/trash` 与 `/jobs/extract-skills` 必须排在 `/jobs/{job_id}` **之前**，否则静态段会被当成岗位 id 塞进路径参数，表现为「回收站打不开，404」（`app/api/v1/jobs.py:3-9`）。

岗位没有永久删除端点，也没有定时物理清理（`dev` 库里回收站的行会一直留着），裁定记录见 ADR-011 1.D 的缺口 ②。

### 4.8 POST `/jobs/extract-skills` — AI 技能提取（纯计算）

**200 同步返回**，需要两个请求头：`Authorization: Bearer <access_token>`（`app/api/v1/jobs.py:121`）与 `X-DeepSeek-Key`（BYOK，常量 `DEEPSEEK_KEY_HEADER` `app/services/dependencies.py:35`；只从请求头读，不落库、不写日志、不进异常消息）。Key 缺失或为空串时服务层抛 `ResumeApiKeyMissingError`（`app/services/job_service.py:203-204`）。

本端点**既不读库也不写库**（`app/services/job_service.py:191-202`；ADR-011 附录指定用挂在 **Engine** 上的 `before_cursor_execute` 把这条钉成断言）。提取结果先回显给用户，用户逐条删改后走 `POST/PUT /jobs` 才落库——AI 无法只凭一次点击改动数据库。

调用参数（`app/integrations/skill_extractor.py:280-293`）：`model = "deepseek-flash"`、`stream=False`、`max_tokens=1500`（`MAX_OUTPUT_TOKENS` `:61`）、`extra_body={"thinking": {"type": "disabled"}}`、`max_retries=0`、超时 30 秒（`app/integrations/copy_writer.py:43`）；不启用 `response_format`（理由见 ADR-011「替代方案」）。

请求体只有两格（`ExtractSkillsRequest`，`app/schemas/job.py:298-310`）：

```json
{"description": "……JD 原文，必填，1–8000 字……", "title": "可选岗位名，≤200 字"}
```

限流 `EXTRACT_RATE_LIMIT = "10/minute"`（`app/core/rate_limit.py:37`，按客户端 IP；已知局限是 slowapi 进程内计数、多 worker 各算一份），超限 429 `RATE_LIMITED`。装饰器顺序是 `@router.post` 在上、`@limiter.limit` 紧贴函数定义（`app/api/v1/jobs.py:114-115`）。

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

`data.meta` 七个字段逐个的语义（模型 `app/schemas/job.py:330-359`，赋值点 `app/services/job_service.py:292-303`）：

| 字段 | 类型 | 含义 | 值的来源 |
|---|---|---|---|
| `model` | string | 这批结果是哪个模型产出的 | 常量 `DEEPSEEK_MODEL` = `"deepseek-flash"`（`app/integrations/copy_writer.py:40`） |
| `prompt_version` | string | 这批结果是哪一版 Prompt 产出的 | 常量 `EXTRACT_PROMPT_VERSION` = `"v1"`（`app/integrations/skill_extractor.py:56`）；Prompt 每改一次 +1 |
| `received_count` | int | 模型**一共给了**几条（含后来被剔除、被合并、被切断的） | `len(extraction.skills)`（`app/services/job_service.py:248`） |
| `dropped_count` | int | 其中几条因**无法回溯 JD 原文**（或 `name`/`evidence` 不合规）被剔除 | `:251`、`:255-257`；判定 `_verify_item` `:305-326` |
| `deduped_count` | int | 其中几条与前面的条目**重名**被合并掉（保留第一次出现的） | `:252`、`:258-263`；判定键 `skill_name_key`（`app/core/skill_names.py`）。这是 ADR-011 §2.D.2 的第 7 个字段 |
| `truncated` | bool | 15 条封顶**是否真的切掉了东西** | `len(kept) > MAX_EXTRACTED_SKILLS`（`app/services/job_service.py:267`；常量 `app/integrations/skill_extractor.py:65`） |
| `finish_reason` | string \| null | 上游给的原因；用来把「被我们的 token 上限切断」与「模型不守格式」分开 | `completion.choices[0].finish_reason`（`app/integrations/skill_extractor.py:305-306`） |

三个计数之间的算术关系（`docs/adr/ADR-011-job-board-and-skill-extraction.md` §2.D.2）：`received_count = dropped_count + deduped_count + 返回条数 + 被 15 条封顶切掉的条数`，最后那格**没有对应字段**（已定不加第 8 个计数），所以 `truncated` 为真时前三项之和必然小于 `received_count`。`dropped_count` 与 `deduped_count` 各说各的、绝不互相串味：核验失败的条目不进重名集合（`app/services/job_service.py:259-262`）。核验、合并都跑在截断**之前**（`:240-246` 的注释），否则这两句前端文案都会变成假话。`truncated` 只在确实切的时候为真。

服务端强制的七条规则（不指望 Prompt，全部在 `app/services/job_service.py:217-326`）：`evidence` 归一化后必须是 `description` 归一化文本的子串，否则丢弃；`name` 为空或 >40 字（`MAX_SKILL_NAME_CHARS` `app/integrations/skill_extractor.py:75`）丢弃；`evidence` 为空或 >60 字丢弃；`type` 不在 `hard/soft/domain` 内则**保留该条**、`type` 置 `null`（`EXTRACTED_SKILL_TYPES` `:90`）；重名合并保留第一条；>15 条截断并置 `truncated=true`；一条都不剩 → 502，**绝不返回空列表**。

七种失败情形的实际落点（对应 ADR-011 附录的验收清单）：

| # | 情形 | HTTP + `error.code` | 代码位置 |
|---|---|---|---|
| 1 | 请求头没有 Key | 400 `AI_KEY_MISSING` | `app/services/job_service.py:203-204` → `app/main.py:252-257` |
| 2 | DeepSeek 拒绝该 Key | 401 `AI_KEY_INVALID` | `app/integrations/skill_extractor.py:294-298` → `app/services/job_service.py:207-208` → `app/main.py:259-268` |
| 3 | 网络超时 / 上游 4xx·5xx | 502 `AI_SERVICE_UNAVAILABLE` | `app/integrations/skill_extractor.py:299-303` → `app/services/job_service.py:213-214` |
| 4 | 返回内容不是可解析的 JSON 对象 | 502 `AI_SERVICE_UNAVAILABLE` | `app/integrations/skill_extractor.py:327-343` |
| 5 | 所有条目的 `evidence` 都回溯不到原文 | 502 `AI_SERVICE_UNAVAILABLE` | `app/services/job_service.py:269-280` |
| 6 | `choices[0].message.content` 为空或全空白 | 502 `AI_SERVICE_UNAVAILABLE` | `app/integrations/skill_extractor.py:320-323` |
| 7 | `finish_reason == "length"`（被我们自己的 `max_tokens` 切断） | 502 `AI_SERVICE_UNAVAILABLE` + 外层 `meta.finish_reason = "length"` | `app/integrations/skill_extractor.py:309-319` → `app/api/v1/jobs.py:144-153` |

第 7 情形单独命名、不与第 4 情形合并，是因为两者的**用户动作不同**：第 4 该重试同一请求，第 7 该「只粘 JD 的核心段落，或减少期望条数」（`EXTRACT_TRUNCATED_MESSAGE` `app/api/v1/jobs.py:55-58`）。它在 api 层就地组装，所以是「走全局处理器」的唯一例外；错误码仍是 `AI_SERVICE_UNAVAILABLE` 一个都没新增。

401 有两个互不相干的来源，前端靠 `error.code` 区分而不是靠状态码或 URL：令牌失效是 401 + `CLIENT_ERROR`（会触发刷新），`AI_KEY_INVALID` 是 401 + `AI_KEY_INVALID`（**不触发令牌刷新、不踢下线**），后者由 `frontend/src/api/client.ts:394` 分流、并由 `frontend/src/api/__tests__/byok.spec.ts:181` 钉住。

三个 `AI_*` 码的处理器文案必须作用域中性（`app/main.py:247-250` 的注释与 ADR-011 2.F）：**不得出现「润色」或「简历」**，因为提取侧复用润色的异常族、两个端点进同一组处理器。

日志里只出现计数、`finish_reason`、`request_id`，不出现 Key、JD 正文、`evidence` 内容、技能名（`app/services/job_service.py:273-291`）。

### 4.9 响应对象的字段

`JobSummary`（列表项与回收站项；**不含 `description`**，JD 最长 8000 字；技能只给**前 6 条**，`SUMMARY_SKILL_LIMIT` `app/services/job_service.py:82`、切片 `:537`）：`id`、`company_name`、`title`、`location`、`employment_type`、`salary_min`、`salary_max`、`deadline`、`status`、`created_by_user_id`、`created_at`、`updated_at`、`deleted_at`、`skills[]`、`is_expired`（`app/schemas/job.py:212-243`）。

`JobRead`（详情与所有写操作的返回；含 `description` 全文与**全部**技能）：上面的字段之外还有 `experience_required`、`extract_model`、`extract_prompt_version`、`extracted_at`（`app/schemas/job.py:246-287`）。

`skills[]` 每条（`JobSkillRead`，`app/schemas/job.py:197-209`）：`id`、`name`、`type`、`origin`（`jd_verifiable` 或 `unverified`，**只出现在响应里**）、`confidence`、`evidence_text`。

两个序列化口径（前端与联调都会撞上）：

- `Decimal` 在 `model_dump(mode="json")` 下输出为**字符串**（`"8000.00"`、`"0.9000"`），不是数字，为避免二进制浮点尾数噪声（`app/schemas/job.py:26-30`）。方向要说清：**响应侧一律是字符串**；**请求侧数字与数字串都收**（Pydantic 宽松模式转 `Decimal`），本文件 §4.4 的请求示例用字符串只是其中一种写法。`/jobs/extract-skills` 的 `data.skills[].confidence` 例外——它是 `float | null`（`app/schemas/job.py:327`，模型自报的原始数字，未经列宽约束），响应里就是 JSON 数字。
- `created_at` / `updated_at` / `extracted_at` / `deleted_at` 输出为**带 `+08:00` 偏移**的 ISO 串，例如 `2026-11-02T21:25:00+08:00`（`app/core/datetime_display.py:33-44`，调用点 `app/schemas/job.py:237-243`、`:298-308`）。这与本文件 §1「时间：ISO 8601 UTC」那句不一致——§1 属本轮范围外、尚未按实现修订。
- `is_expired` 是服务端算好的布尔值，前端不需要再实现一遍「`deadline < 今天`」（`app/schemas/job.py:233-235`）。判据对列表与详情是同一个，且不随 `include_expired` 或查看者身份变化（`app/services/job_service.py:483-489`、`:514`、`:538`）。
- `meta.request_id` 取自请求头 `X-Request-ID`，没带时固定回 `req-local`（`app/core/responses.py:6-7`）。

### 4.10 本章未接通 / 不存在的接口

以下都是**设计意图或已否决项**，本轮不实现、不排期；标注 `# PHASE-2: not wired`（沿用 ADR-011 修订清单第 7 条的口径）。

- `POST /jobs/{job_id}/analyze`（本文件旧版有）：**端点不存在**，OpenAPI 实测的 10 个操作里没有它。异步任务 + `ai_job_id` 形态被 ADR-011 2.A 否决。# PHASE-2: not wired
- `/jobs/{job_id}/close`、「过期自动转 `expired`」：**都不存在**（4.7）。# PHASE-2: not wired
- 按 `location` / `salary` / `experience_required` 筛选：**存而不筛**，谓词函数里没有这三列（`app/repositories/job_repository.py:465-493`、ADR-011 1.G）。
- 岗位收藏、投递记录、批量导出：无端点（ADR-011 议题 3.C）。
- `/teacher/jobs/{job_id}/candidates` 与 `/export`（见本文件 §8）：候选人查询与导出**继续冻结**，本轮无实现。# PHASE-2: not wired
- 岗位与学生技能字典的合并或外键：**不建**（ADR-011 1.E）。「你已具备」是**前端只读比对**，无后端端点、不写任何表、不产匹配分数（`frontend/src/stores/job.ts:481`，ADR-011 2.E）。人岗匹配整条仍在冻结清单。
- `work_mode`（远程/驻场）列：**有意留下的缺口**，本轮只有 `employment_type`（ADR-011 1.G）；远程/驻场信息留在 `description` 文本里。


## 5. 测评、匹配与成长

### GET `/assessments`

按 `skill_id`、`level`、`version` 查询可用题目集合，不返回答案。

### POST `/assessments/{assessment_id}/attempts`

创建测评尝试，返回 `attempt_id`。答案提交到 `POST /assessment-attempts/{attempt_id}/answers`，完成使用 `POST /assessment-attempts/{attempt_id}/submit`。

### POST `/match-runs`

请求：`{"job_id":"uuid","student_ids":["uuid"],"weight_version":"v1"}`。返回异步 `match_run_id`。

### GET `/match-runs/{match_run_id}` / GET `/match-runs/{match_run_id}/results`

结果包含 `score`、`matched_requirements`、`gaps`、`evidence`、`explanation`、`data_as_of`，不返回不可解释的单一黑盒结论。

### POST `/growth-plans`

请求：`{"job_id":"uuid","duration_days":30,"focus_gap_ids":["uuid"]}`。返回异步 AI 任务；任务完成后生成可编辑计划。

## 6. 简历

### POST `/resumes/generate`

请求：`{"student_id":"uuid","job_id":"uuid","template":"clean_white","max_pages":1.5}`。只能使用学生已确认资料；**同步返回 `200` 与生成结果，不返回 `202`、不创建 `ai_job_id`**（`app/api/v1/resumes.py:43-63` 直接 `success_response(..., resume.model_dump(...))`；`POST /api/v1/resumes/generate` 的 OpenAPI `responses` 实测为 `200`、`422`）。
<!-- 本行的请求体示例仍是旧口径：实际模型是 `app/schemas/resume.py:77-97` 的 `{title, profile_id}`。该差异属 §6 简历章节，不在本轮「岗位 + 错误码」范围内，已列入交付说明的未解决问题。 -->

### GET `/resumes` / GET `/resumes/{resume_id}`

返回版本、来源、确认状态和授权状态。

### PATCH `/resumes/{resume_id}/versions/{version_id}`

更新编辑后的区块。保存新版本，禁止覆盖审计历史。

### POST `/resumes/{resume_id}/export`

请求 `{"version_id":"uuid","format":"pdf"}`；返回异步任务。完成后返回短时下载 URL。

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
