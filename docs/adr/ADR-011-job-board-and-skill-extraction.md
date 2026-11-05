# ADR-011：岗位库与 AI 技能提取（数据模型、权限矩阵、提取链路、前端入口、模拟面试前置评估）

- 状态：**v4 · 设计选项已由人类负责人于 2026-11-05 裁定收口；`MVP_SCOPE.md` §5 的解冻待授权，本文不构成解冻**。本轮（Phase 4.0）交付物只有文档：零代码、零迁移、零依赖，未 commit。v1 的悬空项已在「决策」正文全部落成确定选择（见文末「已定事项与生效前提」）；v3 把提取参数改成彼此自洽（2.B、2.D）并把上一轮误写进约束文档的解冻撤下；v4 用实算复核 §2.D.1 的用例表（第 3 行例证修正）、附录的事件挂载目标（`Engine` 而非 `Session`），并把失败语义补到第 7 条（`finish_reason == "length"`）。
- 日期：2026-11-05（v2）
- 决策人：架构选项由人类负责人裁定；本文记录裁定结果与其技术依据。
- 相关：ADR-002（AI 先 stub）、ADR-006（多份简历）、ADR-007（DIY 排版与主题）、ADR-009（DeepSeek BYOK 非流式）、ADR-010（润色与生成解耦、草稿态确认制）
- 冻结关系：**`docs/MVP_SCOPE.md` §5 的「AI 爬虫采集岗位、企业需求自动解析」保持冻结原句**，与 AGENTS.md §3.2 L68 语义一致——岗位 JD 技能提取**尚未解冻**。解冻需人类负责人书面授权，届时一次性修订三处：AGENTS.md §3.2、MVP_SCOPE.md §5、MVP_SCOPE.md 变更记录表。本文只定稿设计选项，不改动任何冻结状态。
- 约束文档本轮唯一落地的修订是表数量口径：AGENTS.md §4.2 改为「Phase 1 建 5 张表；Phase 2 起新增表必须先有 ADR（见 `docs/MVP_SCOPE.md` §6）」，并在 AGENTS.md §1 事实清单追加「现库 7 张表」，按 git 实测把各表归因到 `d02b176` / `6e2f2c7` / `76e23ce` / `fe21c8b` / `a7a1fe2`，**不写批准人**（表结构入库与 ADR-009 的 DeepSeek 授权是两件事，上一轮把它们串成了一句归因）。
- 取代关系：批准后需修订 `docs/API.md` §4 与 §7。**修订清单见下文专章，本轮不改 API.md**（清单里逐条写了「现状 → 新口径」）。

## 背景

### 需求侧

原始 Word 需求的「基本功能 2 与 3」= 录入企业需求 + AI 分析该需求需要的技能点。它在仓库里有三处对应：

- `docs/PRD.md:49`（MVP 第 3 项）：「企业岗位录入、审核、技能拆解和岗位状态管理」；
- `docs/PRD.md:102-107`（FR-03）：AI 分析输出**必须使用结构化 JSON，并保存模型、提示词版本、时间和置信度**；未经审核的岗位不可进入推荐池；
- `docs/PRD.md:80`（6.2 企业岗位闭环）：提交岗位 → 字段校验与内容审核 → AI 提取技能/经验/通用能力 → 教师或管理员确认 → 发布。

它是后续「人岗匹配」和「模拟面试」的地基：没有岗位侧的技能条目，匹配就没有左操作数；没有岗位 JD，面试出题就没有依据。

### 实现侧现状（全部为只读取证）

- `users` 表列：`id / username / email / password_hash / created_at / updated_at`（`alembic/versions/20261023_0001_initial_phase1.py:30-37`）。在 `alembic/versions/` 全量 grep `role|permission|admin|teacher|enterprise` → **0 命中**：没有任何角色/权限痕迹。
- 已实现 7 张表：`users`、`student_profiles`、`skills`、`experiences`、`resumes`、`educations`（0002）、`refresh_tokens`（0006），另有 0007 的多档案/软删与 0009/0010 的主题、排版列。
- 已实现的 `skills` 是**学生私有自评表**：`student_profile_id / name VARCHAR(100) / self_rating TINYINT UNSIGNED (CHECK 1–5)` + `UNIQUE(student_profile_id, name)`。
- 状态字段的既有做法：`educations.degree` 用 `VARCHAR(50)` + CHECK，注释写明「字符串列 + CHECK，避免 MySQL ENUM 难以演进」。本文沿用。
- 分层落地的文件形状：`app/repositories/` 下五个仓储清一色 `*_repository.py`（`user_repository.py`、`refresh_token_repository.py`、`student_profile_repository.py`、`resume_repository.py`、`fake_resume_repository.py`），**本仓没有 `models/` 目录**，ORM 声明与仓储实现同文件。
- 越权语义的既有先例（本文权限矩阵的依据）：`app/api/v1/profiles.py:10` ——「越权处理：不属于当前用户的 id 一律 404（不是 403）——403 会暴露「该 id 存在」。」
- BYOK 请求头已在 CORS 白名单里：`app/main.py:46` `CORS_ALLOW_HEADERS = ["Authorization", "Content-Type", "X-Request-ID", "X-DeepSeek-Key"]`（注释还写明：少列一个，浏览器预检会在跨源部署时直接拒掉这个头，而本地 Vite 代理同源、测不出来）。
- AI 通道现状（`app/integrations/copy_writer.py`）：`DEEPSEEK_MODEL = "deepseek-flash"`、`REQUEST_TIMEOUT_SECONDS = 30.0`、`stream=False`、`extra_body={"thinking": {"type": "disabled"}}`、`max_retries=0`；Key 只来自请求头 `X-DeepSeek-Key`（`app/services/dependencies.py`），不落库、不写日志、不进异常消息；测试缝是 `httpx2.Client` + `MockTransport`。
- 端点现状（`app/api/v1/resumes.py`）：`POST /resumes/generate` 纯模板；`POST /resumes/polish` 纯计算不落库 + `@limiter.limit(POLISH_RATE_LIMIT)`。
- `docs/design/schema-draft.sql` 里有岗位设计（`companies` L175-187、`job_posts` L189-217、`job_skill_requirements` L219-237、技能字典 `skills` L117-126），但该文件标注**非迁移源**，且外键指向实现中根本不存在的 `organizations`、`memberships`（L28-48）和字典式 `skills`。照搬会一次性拖进 5 张以上的表。
- 前端现状：`frontend/src/router/index.ts` 只有 4 条路由（`/`、`/trash`、`/login`、`/register` + 兜底重定向）；`views/DashboardView.vue` 有「岗位洞察 / 模拟面试」的 coming-soon 占位，但**它没有被路由挂载**。

## 决策

### 议题 1：数据模型与权限

#### 1.A `users` 不加 `role` —— 已定

选项是「加 role 枚举」与「不区分角色、大家都能发」。选择后者：`jobs.created_by_user_id` 只记录创建者，任何登录用户都可录入。

理由：

- 需求要的是「老师/在校生/校友都能录」，用「都能录」实现即可，结论与「不区分角色」一致，而鉴权面一分没扩。
- `MVP_SCOPE.md:88`「不做管理后台，数据修正由开发者直接操作数据库」——`role` 一进表，第一件事就变成「谁能改 role」，而改 role 需要后台界面；没有后台的 `role` 只会退化成一个没人管的字符串，却已经真实扩大了授权面。
- 一旦有 `role`，每个岗位端点都得回答「这一步是不是 teacher 才能做」，包括审核与候选人导出（`docs/API.md:110`、L186-190 预先写了这些）。而现在既没有审核人也没有后台，**那些端点无论怎么设计都是假权限**。不引入 `role`，就把它们整批推迟到有真实审核角色为止。
- 完整 RBAC（`schema-draft.sql` L28-48 的 `roles`/`memberships`）对应 PRD §3 的五角色 + 默认拒绝模型，本文明确不采用。

#### 1.B `jobs` 表字段 —— 已定

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | `String(36)` PK | 沿用 0006 的结论：`VARCHAR(36)` 而非 `CHAR(36)` |
| `created_by_user_id` | `String(36)` NOT NULL，FK → `users`，`ondelete="RESTRICT"` | 服务端由 `get_current_user` 写入，**绝不采信请求体** |
| `company_name` | `String(200)` NOT NULL | **不建 `companies` 表**（见下） |
| `title` | `String(200)` NOT NULL | 岗位名 |
| `location` | `String(160)` NULL | 存而不筛（见 1.G） |
| `employment_type` | `String(20)` NULL + CHECK（`internship` / `full_time` / `part_time` / `contract` / `other`） | 沿用 `educations.degree` 的「VARCHAR + CHECK，不用 ENUM」 |
| `description` | `TEXT` NOT NULL | JD 原文；应用层上限 8000 字（2.F） |
| `salary_min` / `salary_max` | `DECIMAL(12,2)` NULL + CHECK（`salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max`） | **本轮新增**。先例：`schema-draft.sql:199-200` 的列与 `:216` 的同名 CHECK。表单选填，本轮不做薪资筛选 |
| `experience_required` | `String(100)` NULL | **本轮新增**，对应 PRD FR-03 的「经验」。放字符串而不是枚举：JD 里写的是「3 年以上后端经验」这类自然语言，硬塞 CHECK 会造出一个没人填得对的枚举。表单选填，本轮不筛 |
| `deadline` | `Date` NULL | 过期判定见 2.G |
| `status` | `String(16)` NOT NULL DEFAULT `'draft'` + CHECK（`draft` / `published`） | 7 态砍成 2 态，见下 |
| `extract_model` | `String(60)` NULL | PRD FR-03 L105 要求保存模型 |
| `extract_prompt_version` | `String(20)` NULL | 同上，提示词版本 |
| `extracted_at` | `DATETIME(6)` NULL | 同上，时间 |
| `deleted_at` | `DATETIME(6)` NULL | 软删（与 0007 的回收站语义一致） |
| `created_at` / `updated_at` | `DATETIME(6)` NOT NULL | |

索引：`ix_jobs_status_created (status, created_at)`、`ix_jobs_creator (created_by_user_id, deleted_at)`。实现迁移时记住 0007 的教训：**先建替换索引，再 drop 旧索引**。

为什么不建 `companies` 表：需求只要「企业名称」这一个字符串。建表 + 外键就得回答「字节跳动」和「字节跳动科技有限公司」是否同一家、谁有权合并企业记录——那是企业数据治理，需要人工审核能力，而审核角色不存在。

为什么 7 态砍成 2 态（`schema-draft.sql:203` 是 `draft/pending_review/approved/published/closed/expired/rejected`）：

- `pending_review` / `approved` / `rejected` 需要审核人 → 没有后台（`MVP_SCOPE.md:88`）；
- `closed` / `expired` 需要状态流转任务 → Redis/ARQ/Celery 仍冻结；过期改为读侧判定（2.G），不落状态、不动数据；
- `draft`（自己录的还没确认）+ `published`（广场可见）已能表达真实产品行为，`deleted_at` 承接「下架」。

#### 1.C 不记录录入者身份 —— 已定

「老师/在校生/已毕业校友均可录入」这句需求的意图是「别把入口锁死给某一类人」，不是「要按身份统计」。因此**不建** `submitter_kind` 列，`jobs` 表保持干净。将来真需要统计（例如「老师贡献了多少岗位」），用一个迁移补一列的成本远低于现在埋下一个「名字像权限、其实是自述」的字段并被误用。

#### 1.D AI 解析的技能列表：子表 `job_skills` —— 已定

三个候选：`jobs.ai_skills` JSON 列、子表、技能字典表 + 外键。选择**子表**。

- JSON 列：最省事（0010 已趟过 MySQL JSON 列的坑：不能带 `server_default`），但「按技能找岗位」退化成 `LIKE`；`UNIQUE(job, skill)` 无法由数据库保证；用户想删掉其中一条 AI 错认的技能，得整块 JSON 重写。
- 技能字典表（`schema-draft.sql:117-126` 的 `code`/`category`/`parent_id`）：长期正确，但字典需要有人维护归一化（"PyTorch" / "pytorch" / "PyTorch 框架" 是否同一条），Phase 4 既无后台也无审核角色。**不采用**；若将来匹配真的做不动，为归一化单起一份 ADR。
- 子表：可 JOIN、可单条编辑、可由数据库去重——这三条正是匹配与人机共编的前提。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | `String(36)` PK | |
| `job_id` | `String(36)` NOT NULL，FK → `jobs`，`ondelete="CASCADE"` | 技能条目是岗位的组成部分，不独立存在 |
| `name` | `String(100)` NOT NULL | 技能名，来自 JD 或由 JD 归纳（2.D） |
| `type` | `String(10)` NULL + CHECK（`hard` / `soft` / `domain`） | **可空**：AI 的分级不可信，宁可为空让用户填 |
| `origin` | `String(16)` NOT NULL + CHECK（`ai_extracted` / `manual`） | 由**服务端派生**，见 1.F |
| `confidence` | `DECIMAL(5,4)` NULL + CHECK（0–1） | PRD FR-03 L105 |
| `evidence_text` | `String(60)` NULL | JD 原文片段，2.D 的核验锚点；上限与理由见 2.B |
| `created_at` / `updated_at` | `DATETIME(6)` NOT NULL | |

约束：`UNIQUE (job_id, name)`。

**去重语义必须由测试证明，不得靠推断**（AGENTS.md §4.4）。当前库使用 MySQL 8 的 `utf8mb4_0900_ai_ci`，按其定义大小写与重音不敏感，全角/半角按 MySQL 文档亦不敏感——这意味着 `Python`、`python`、`Ｐython`（首字母全角）**理论上**会撞同一条唯一键。但这是排序规则的实现细节，不是我们的契约。验收要求：集成测试同时提交这三种写法，断言只有 1 行落库；测试红了就说明假设错了，届时改用应用层归一化后的 `name_norm` 列做唯一键，而不是删掉断言。

`origin` 只有两个值，且**禁止出现在请求体**：

- 服务端派生规则：该条的 `evidence_text` 经 §2.D.1 的归一化函数处理（NFKC → casefold → 去空白 → 去标点）后**是** `jobs.description` 同法归一化文本的子串 → `ai_extracted`；否则 → `manual`。
- v1 曾列的第三个值 `user_edited` **已从 CHECK 枚举中删除**。理由：`origin` 的用途是「这条技能能不能回溯到原文」，而客户端可以任意伪造提交的 `origin` 值——一个客户端可伪造的溯源标记等于没有标记，把它当审计字段反而会骗到后来读代码的人。PRD `docs/PRD.md:99`「AI 推断必须标记为建议，学生可修订或拒绝」是由**草稿态交互**满足的（结果先回显、用户逐条删改、点保存才落库，见 2.A/议题 3），不是由这一列满足的。用户改过的条目落在 `manual` 里，语义仍然正确：它不再能证明来自原文。

#### 1.E 与现有 `skills`（学生自评）表：不合并、不加外键

两者语义相反：`skills` 是「我会什么 + 自评熟练度」，`job_skills` 是「这个岗位要求什么」；所有权也不同（学生私有 vs 岗位公开）。合并或让 `job_skills` 外键指向 `skills`，等于把公开数据的写入权限接到私有数据上。关联只发生在展示层（2.E）。

#### 1.F 权限矩阵 —— 已定

身份来源只有一个：`get_current_user`（JWT）。**任何身份与归属字段都不从请求体读**——这条是 T7.2 定下的边界，现有测试 `test_generate_ignores_profile_fields_sent_in_request_body` 是它的先例。

| # | 操作 | 允许的调用者 | 非创建者的结果 | 状态码 |
|---|---|---|---|---|
| 1 | `GET /jobs`（列表） | 任何登录用户 | 只能看到 `status=published` 的岗位；自己的 `draft` 需 `?status=draft` 才出现 | 200 |
| 2 | `GET /jobs/{id}` | published：任何登录用户；draft：仅创建者 | **404**（不区分「不存在」与「无权」） | 200 / 404 |
| 3 | `POST /jobs` | 任何登录用户 | — | 201 |
| 4 | `PUT /jobs/{id}`（改字段与技能） | 仅创建者 | **404** | 200 / 404 |
| 5 | `DELETE /jobs/{id}`（软删） | 仅创建者 | **404** | 200 / 404 |
| 6 | `PUT /jobs/{id}/publish`、`/unpublish` | 仅创建者 | **404** | 200 / 404 |
| 7 | `POST /jobs/extract-skills` | 任何登录用户 | 无归属概念（不落库，2.A） | 200 / 4xx |
| 8 | `GET /jobs/trash` | 任何登录用户 | 只返回**自己的**软删岗位（service 层过滤，不是查出全部再前端过滤） | 200 |

三条必须照抄的实现约束：

- **404 而不是 403**：先例与理由见 `app/api/v1/profiles.py:10`——403 会暴露「该 id 存在」。岗位 id 是 `VARCHAR(36)` 的 UUID，看似猜不到，但列表接口泄露的 id 会被二次枚举，「存在但无权」本身就是一条信息通道。
- **service 层抛领域异常，api 层映射状态码**：`JobService` 抛 `JobNotFoundError`（与 `ProfileNotFoundError` 同族，见 `app/services/profile_service.py`），`app/api/v1/jobs.py` 用 `except JobNotFoundError: raise HTTPException(404)` 收口。**service 层禁止 `raise HTTPException`**——AGENTS.md §4.1 明令 `services/` 不得 import FastAPI 的 `Request`/`Response`，`HTTPException` 是同一个理由的延伸：HTTP 语义只属于 api 层。
- 越权与「不存在」在 service 层收敛成同一个异常，不给 api 层区分两者的机会。

写侧的隐含规则：`POST /jobs` 一律落 `status='draft'`，客户端提交的 `status` / `created_by_user_id` 直接忽略（同样地，`origin` 不采信客户端，见 1.D）。发布是显式动作（#6），不是创建或编辑的副作用。

#### 1.G FR-03 字段覆盖对照 —— 需求缺口显式化

`docs/PRD.md:104` 逐字：**「岗位包含企业、地点、薪资区间、工作方式、职责、硬技能、软技能、经验和截止时间。」**

| FR-03 要求的要素 | 本轮字段 | 覆盖 |
|---|---|---|
| 企业 | `company_name` | ✅（无 `companies` 表，不做企业实体化） |
| 地点 | `location` | ✅ 存，❌ 不筛 |
| 薪资区间 | `salary_min` / `salary_max` | ✅ 存（选填），❌ 不筛 |
| 工作方式 | `employment_type` | ⚠️ **部分**：`schema-draft.sql:196-197` 把这层拆成 `employment_type`（实习/全职）与 `work_mode`（驻场/远程）两列，本轮只留前者，远程/驻场留在 JD 文本里。**这是本轮有意留下的缺口**，不是遗漏；若真实用户开始按「远程」找岗位，补 `work_mode` 一列即可 |
| 职责 | `description`（JD 原文） | ✅ |
| 硬技能 / 软技能 | `job_skills.type` = `hard` / `soft` | ✅（额外还有 `domain`） |
| 经验 | `experience_required` | ✅ 存（自然语言），❌ 不筛 |
| 截止时间 | `deadline` | ✅ 含过期判定（2.G） |

`PRD.md:105`（保存模型 / 提示词版本 / 时间 / 置信度）：✅ `jobs.extract_model`、`extract_prompt_version`、`extracted_at`、`job_skills.confidence`。
`PRD.md:106`（未经审核的岗位不可进入推荐池）：❌ **未覆盖**——审核角色与推荐池都不存在，相关能力仍在冻结（L42）。最接近的现有语义是 `status='draft'` 的岗位不进广场，任何登录用户只能读到 `published`（1.F #1/#2）。
`PRD.md:107`（来源：人工录入 / 学校导入 / 合法授权接口）：只覆盖人工录入；「学校导入」与任何自动采集**保持冻结**。

筛选与统计本轮一律不做，只保留 `keyword` + `status`（议题 3.C）。

### 议题 2：AI 技能提取链路

#### 2.A 端点形态：纯计算、不落库 —— 已定

```
POST /api/v1/jobs/extract-skills     请求头 X-DeepSeek-Key
  body: { "description": "…JD 原文…", "title": "可选岗位名" }
  resp: { "skills": [ { "name", "type", "evidence", "confidence" } ],
          "meta": { "model", "prompt_version", "received_count", "dropped_count", "truncated", "finish_reason" } }
```

为什么提取必须发生在「岗位还没落库」时：用户在录入表单里粘贴 JD → 点「AI 提取技能」→ 结果先回显、用户逐条删改 → 点「保存岗位」才写 `jobs` + `job_skills`。这是 ADR-010 §5 草稿态确认制搬到岗位侧，AI 永远无法只凭一次点击改动数据库。

被否决的两个形态：

- **`POST /jobs` 时顺带提取**（一次调用出成品）：AI 失败时岗位到底落成 draft 还是不落？静默降级被明令禁止，而报错回滚会把用户粘进去的 8000 字 JD 一起丢掉。拆成两个动作，失败只损失提取结果。
- **异步任务 + `ai_job_id`**（`docs/API.md:100`、L106-108 现在的写法）：需要队列（Redis/ARQ/Celery 仍冻结）。**不采用**，据此修订 API.md（见专章）。

配套端点草图（命名对齐 `app/api/v1/profiles.py`）：

```
GET    /api/v1/jobs              ?status=&keyword=&include_expired=&page=&page_size=
POST   /api/v1/jobs              201
GET    /api/v1/jobs/{job_id}
PUT    /api/v1/jobs/{job_id}
DELETE /api/v1/jobs/{job_id}     软删
PUT    /api/v1/jobs/{job_id}/publish   /   /unpublish
GET    /api/v1/jobs/trash
```

注意 `/jobs/trash` 必须声明在 `/{job_id}` 之前——`profiles.py` 的文件头注释记录过踩坑：静态段排在动态段之后会被当成 id 塞进路径参数，表现为「回收站打不开，404」。

分层不变：`api/ → services/ → repositories/ → integrations/`，`api/` 不得 import `integrations/`；新适配器 `app/integrations/skill_extractor.py` 由 import-linter 兜住。

#### 2.B 调用方式：逐条继承 ADR-009/010，并加服务端输出封顶 —— 已定

继承（不重新发明）：BYOK（Key 只来自 `X-DeepSeek-Key`，前端 `sessionStorage` 的 `deepseek-api-key`）、`deepseek-flash`、`stream=False`、`extra_body={"thinking":{"type":"disabled"}}`、`max_retries=0`、`REQUEST_TIMEOUT_SECONDS = 30.0`、`httpx2.Client` 可注入以便测试换 `MockTransport`；Key 不落库、不写日志、不进异常消息，禁止在任何仓储或数据模型里加字段存 Key；JSON 解析复用 `copy_writer.py` 的剥围栏 + `_first_object` 括号平衡（模型爱把 JSON 包在 ``` 里）；本端点不向 SDK 传结构化输出参数，理由见「替代方案」。

本端点新增的三封顶（这是岗位提取与润色的真实差异）：

| 封顶 | 值 | 为什么 |
|---|---|---|
| `max_tokens` | **1500** | 润色的长度由 Prompt 与「经历条数 ≤ 20」共同约束；提取的长度完全由模型自定，一个 8000 字的 JD 可以让模型生成几十条技能，服务端只能等它写完 |
| 技能条数上限 | **15**（v1 的 25 下调） | 一页 JD 的真实技能密度撑不起 25 条；15 条已足够覆盖硬技能 + 软技能，超出部分通常是模型在拆同义词 |
| `evidence` 单条上限 | **60 字**（v1 的 500 → v2 的 120 → v3 收到 60） | **主因是误杀率，不是 token**：evidence 越长，要求模型**逐字复现的连续原文跨度越大**，而一个标点或全半角差异就会让整条核验作废。60 字足以在 JD 里唯一定位到某一句话，所以收到 60 的主要收益是降低误杀，省 token 只是附带 |

**三封顶的自洽算式（导出值，不是实测）**：

```
15 条 × (60 字 evidence + 约 30 token 结构开销) = 15 × 90 = 1350 ≤ max_tokens = 1500
```

中文按约 1 字 ≈ 1 token 估（DeepSeek 中文分词的近似值）；「结构开销」指每条里 `name` + `type` + `confidence` + JSON 的引号/逗号/括号，按 `name` 常态长度（≤ 10 字）折进去。这三个数是**互相推出来的**：动了任何一个，算式和下面那段延迟推导都要重算。

> **边界提醒（保持自洽所必需）**：若模型把 `name` 也写到 40 字上限，峰值变成 `15 × (40 + 60 + 30) = 1950 > 1500`（`name` 只用 20 字时 `15 × (20 + 60 + 30) = 1650` 仍 > 1500），`max_tokens` 会在第 14/15 条处切断，末条 JSON 残缺 → 命中验收清单**第 7 情形**（`finish_reason == "length"`），返回 502 并在 `meta.finish_reason` 回显；不与第 4 情形（模型自身输出不合法）混记，理由见附录第 7 行。也就是说这组封顶的作用是**把最坏延迟钉住**，而不是保证一次成功；实现时应把 `finish_reason` 记进日志，用来区分「模型答得太多被我们切断」与「模型没答」。人类负责人已明确否决「为提高成功率而调高 `max_tokens`」，因为那会推翻下面那段关于 30 秒上界的推导。

**并显式更正 v1 的一处推理**：v1 在 2.A 写过「同步非流式 + 30 秒预算已在 polish 链路上被验证够用」，并拿它否决异步方案。这个结论**不可直接迁移到本端点**：`copy_writer.py:181-192` 的 `chat.completions.create(...)` 调用里根本没有 token 上限，润色的输出长度是靠 Prompt 里的字数约束封顶的（`copy_writer.py:85` 的「每段 description 不超过 120 个汉字、responsibility 不超过 60 个汉字」、`:90` 的 `max_words`）。也就是说 polish 的「30 秒够用」是**被 Prompt 间接约束出来的**，而本端点若不加服务端封顶，同一条推理并不成立。补上 `max_tokens=1500` + 15 条 + 60 字之后，「30 秒够用」在本端点上是**推导出的上界**（输出被封顶 → 生成长度有界 → 延迟有界），不是实测结论——真实延迟仍需在有凭据时复测。

`extract_prompt_version` 的来源：`skill_extractor.py` 里显式写一个常量（如 `"v1"`），Prompt 每改一次 +1，并落进 `jobs.extract_prompt_version`。这是 FR-03 L105「保存提示词版本」唯一的实现路径，否则三个月后无人可考。

#### 2.C 输出契约：带 `evidence`，不用纯字符串数组 —— 已定

```json
{"skills": [{"name": "Python", "type": "hard", "evidence": "熟练掌握 Python", "confidence": 0.9}]}
```

v1 议题里「`{"skills":["Python","Docker"]}` 还是带来源的对象」这个选择已定：取**带 `evidence` 的对象数组**。理由是可验证性——纯字符串数组无法在本地核验，只能整批相信模型；带 `evidence` 才能做子串核验（2.D）。前端仍然只展示技能名 + 类型徽标，`evidence` 收在展开/hover 里，不增加用户负担。代价（须承认）：Prompt 更复杂、模型更容易输出不合规结构，因此 2.D 的丢弃与 502 路径会比字符串数组方案更常被走到。

#### 2.D 反幻觉边界：每条技能必须能回溯到 JD 原文，且**核验结果对用户可见**

ADR-010 给润色的第一硬约束是「不得新增任何输入里不存在的事实」。技能提取天然违反它——把「熟悉主流大模型 API」归纳成 `LLM API` 就是新增表述。所以约束换成一条更强、也更可核验的：

> **每条技能都必须能回溯到 JD 原文。**

服务端强制（不指望 Prompt）：

1. Prompt 给硬约束：只输出一个 JSON 对象；技能 ≤ 15 条；`evidence` 必须是 JD 原文的**最短连续片段**（≤ 60 字，够定位即可，不要抄整段），不得改写；不得输出 JD 里读不到的技术栈。
2. 返回后逐条核验：`evidence` 与 `description` 各过一个归一化函数，要求前者是后者的**子串**；不是子串 → 丢弃该条。归一化的可测定义与「标点是否参与比较」的取舍见下面的 §2.D.1，它不是实现细节，而是本端点误杀率的直接决定项。
3. 丢弃后为空 → `AI_SERVICE_UNAVAILABLE`（502，`retryable`）。**不返回空列表糊弄用户，也不静默降级。**
4. 条数 > 15 → 截断到 15，`meta.truncated = true`，前端提示「模型给出的条目超过 15 条，只保留了前 15 条」。截断可见，与「悄悄替换结果」不同类。
5. `type` 不在枚举内 → 保留该条、`type` 置 NULL（所以 1.D 的 `type` 可空）。
6. `name` 长度 ≤ 40；`evidence` 为空或 > 60 字 → 丢弃该条（没有锚点的条目无法核验，落库只能标 `manual`，与「AI 提取」的语义矛盾）。
7. 落库时按 1.D 的派生规则重算 `origin`，不采信模型自报、也不采信客户端提交的任何标记。

#### 2.D.1 归一化的可测定义，以及「标点是否参与比较」

核验不做字符串直等。四个步骤写成函数，实现时照抄、单测按用例表逐条落：

```python
def _normalize_for_trace(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)   # 1) 全角→半角、兼容字符归一
    text = text.casefold()                        # 2) 大小写折叠
    text = "".join(c for c in text if not c.isspace())                    # 3) 删除空白与换行
    text = "".join(c for c in text if not unicodedata.category(c).startswith("P"))  # 4) 删除标点
    return text

# 判定：_normalize_for_trace(evidence) in _normalize_for_trace(description) → 可回溯
```

第 4 步就是「标点是否参与比较」的答案：**标点不参与**。选它覆盖 Unicode 通用类别 `Pc/Pd/Ps/Pe/Po/Pi/Pf`，即 ASCII 标点、中文全角标点、顿号、书名号、引号、省略号一并删除。符号类 `S` **不删**：`+` 的类别是 `Sm`，所以「C++」里的加号留在串里——它是技能名的一部分，不是分隔符。已知副作用要写明：`Pc` 含下划线，于是 `user_id` 与 `userid` 会被判为相同；这只影响定位的严格度，不影响「是否出自原文」，可接受。

| # | JD 原文 | 模型给的 evidence | 标点**参与**比较 | 标点**不参与**（本 ADR 选它） | 判读 |
|---|---|---|---|---|---|
| 1 | 熟练掌握 Python，熟悉 FastAPI | 熟练掌握 Python, 熟悉 FastAPI | 通过 | 通过 | 你举的这个反例（全角「，」对半角「,」）两条路都过，因为 NFKC 已把全角标点折成半角；真正的分歧在第 2 行 |
| 2 | 熟悉 Python、FastAPI、Docker | 熟悉 Python, FastAPI, Docker | **丢弃** | 通过 | 「、」NFKC 后仍是「、」，不会变成「,」。选「不参与」的主因就是这里：中文标点怎么选与原文事实无关，却会整条误杀，而用户重试并不能改善 |
| 3 | 负责数据采集。负责数据清洗 | 负责数据采集负责数据清洗 | **丢弃** | **通过** | **这是有意放过的代价**：跨句去标点拼接（该 evidence 是 JD 去标点后的逐字片段，无省略）。可接受的理由——两段短语都逐字出自 JD，「能回溯」这条判据并没有被削弱；靠删标点拼出来的 evidence 藏不住一个 JD 里根本不存在的技能名 |
| 4 | （JD 无 Kubernetes 字样）负责订单服务 | 负责 Kubernetes 集群运维 | 丢弃 | 丢弃 | 真幻觉与标点无关，两种口径都拦得住。这就是「不参与」不损失反幻觉强度的根据 |
| 5 | 熟悉\nPython（原文换行） | 熟悉 Python | 通过 | 通过 | 第 3 步去空白覆盖 |

> **例子的判据（照此构造测试）**：表中凡是期望「标点不参与」列为通过的 evidence，必须是 **JD 删除标点后的逐字片段**，不许夹带省略或改写。v3 的第 3 行原本写成 `负责数据采集负责清洗`——**例证举错了**：它自己漏掉第二个「数据」，于是两种口径都丢弃（实测 DROP / DROP），根本不构成"放过"。修正为 `负责数据采集负责数据清洗` 之后才是 参与=丢弃 / 不参与=通过。
>
> **第 2、3 行在四步函数下的实算中间值**（`.venv/Scripts/python.exe` 跑出的原文，可直接抄成单测断言）：
>
> ```
> #2  参与:   JD='熟悉python、fastapi、docker'   EV='熟悉python,fastapi,docker'    → DROP
>     不参与: JD='熟悉pythonfastapidocker'       EV='熟悉pythonfastapidocker'      → PASS
> #3  参与:   JD='负责数据采集。负责数据清洗'     EV='负责数据采集负责数据清洗'      → DROP
>     不参与: JD='负责数据采集负责数据清洗'       EV='负责数据采集负责数据清洗'      → PASS
> ```
>
> 为什么会分岔：NFKC 只统一**有兼容映射**的字符。字符类别与 NFKC 实测——`，`(U+FF0C) 是 `Po` 且 NFKC → `,`；`、`(U+3001) 与 `。`(U+3002) 同为 `Po` 但**没有** ASCII 兼容映射，NFKC 后原样保留。所以第 1 行两条路都过，第 2、3 行才真正分岔。另：`+` 是 `Sm`（保留，故「C++」不受影响）、全角 `＿` 经 NFKC 得 `_`（类别 `Pc`，被第 4 步删除）。

一句话取舍：**核验要证明的是「这句话真在 JD 里出现过」，不是「连标点都抄对了」**。要求后者会把误杀集中到信息量最低的那类差异上；#3 那种放过则不会给用户带来任何虚构内容，因为技能名与 `type` 仍然要用户在草稿态逐条过目（2.A、议题 3.B）。

**核验计数必须可见**（v2 新增，这是本端点最重要的一条产品承诺）：

- 响应 `meta` 带 `received_count`（模型给了几条）与 `dropped_count`（因无法回溯原文被剔除几条）；
- 前端在结果区直接显示：「AI 给出 20 条，其中 14 条无法在 JD 原文中找到依据，已剔除」；
- 服务端日志同时记这两个计数与 `finish_reason`（**不含 Key、不含 JD 正文、不含 evidence 内容**，只有计数、`finish_reason` 与 `request_id`）；
- 「已定事项」里那条「规则可能偏严，需要真实样本才能调」正是靠这两个计数落地：它是本端点唯一能在无凭据环境下预先设计好、上线后可直接读数的观测点。没有这两个数，「子串核验误伤合理归纳」将无法被证实或被否认。

加强项（已定：本轮不做）：`name` 里若出现 JD 中完全不存在的英文 token（正则 `[A-Za-z][A-Za-z0-9+#.]{1,}`）即丢弃该条。它能挡住「模型顺手加了 Kubernetes」这类最典型的编造，代价是误伤合理的中文归纳，且规则复杂度高。决定先上线子串规则，用 `dropped_count` 观察真实失败样本，再判断要不要加这层。

#### 2.E 与学生技能的关联：只做只读展示 —— 已定

岗位详情页把 `job_skills.name` 与当前登录用户 `skills.name` 做归一化等值比较，命中处标「你已具备」。

- **不写任何表**，不产生匹配分数，不做排序——人岗匹配仍冻结在 `MVP_SCOPE.md:42`，PRD FR-05 的 `0.45/0.20/0.15/0.10/0.10` 权重（`PRD.md:119`）属于另一份 ADR。
- 价值：零成本让「岗位库」与「我的档案」发生关系，用户不会觉得岗位库是别人的公告板；同时把「归一化到底痛不痛」变成可观察的事实，为将来是否建字典表提供证据。

#### 2.F 限流、输入上限与错误语义 —— 已定

- 新常量 `EXTRACT_RATE_LIMIT = "10/minute"`（与 `POLISH_RATE_LIMIT` 同族，`app/core/rate_limit.py`）。必须限流：BYOK 下烧的是用户 Token，但**请求是我们的服务器转发给第三方的**，不限流等于把服务器变成开放代理（ADR-010 §2 的原始理由）。已知局限同样是 slowapi 进程内计数、多 worker 各自独立。
- 输入上限：`description` ≤ 8000 字（Pydantic `le=8000`），`title` ≤ 200，超限 422。
- 错误码沿用 ADR-010 §6 三码，且只可能来自 `/jobs/extract-skills`：`AI_KEY_MISSING`(400) / `AI_KEY_INVALID`(401) / `AI_SERVICE_UNAVAILABLE`(502, `retryable`)。前端 401 不触发令牌刷新、不踢下线的分流（判据是 code 不是 URL）原样复用；Key 只随该端点发送。

#### 2.G 过期判定与时钟口径 —— 已定

- **日期比较只发生在 service 层，用 `datetime.date.today()`；SQL 里不使用数据库侧的当日函数取日期。** 理由：全仓时间列清一色 `mysql.DATETIME(fsp=6)`，写入用的是**应用进程的时钟**（见各迁移与仓储），而数据库侧的当日函数取的是 **DB 服务器时区**的日期。一旦两者混用，「今天之前算过期」这条规则在跨进程/跨时区部署下会给出不同答案，且这种不一致在本地永远测不出来。统一以应用时钟为准，与既有时间写入口径一致。
- `GET /jobs` **默认隐藏过期岗位**：`deadline IS NOT NULL AND deadline < date.today()` 的行不出现在默认结果里；需要连过期一起看到，必须显式传 `include_expired=true`。过期**不写库、不改状态**（不引入 `expired` 状态，见 1.B）。
- `deadline IS NULL`（没写截止时间）的岗位视为长期有效，永远出现在默认列表里。
- 验收测试两条：① 造一条 `deadline = 今天 - 1` 的 published 岗位，`GET /jobs` 不返回它、`GET /jobs?include_expired=true` 返回它；② 造一条 `deadline = 今天` 的岗位，默认列表**应当**返回它（边界：「今天截止」还没过期）。

### 议题 3：前端

#### 3.A 入口：顶栏文字链接，不加侧边栏 —— 已定

`AppTopBar` 增加「岗位库」文字入口，与现有「回收站」同级，沿用 Phase 3.2 定下的 32px 控件规格与 `--ui-*` 令牌。

被否决的两个入口：挂到 `DashboardView`（该视图有 coming-soon 占位但**未挂路由**，走这条路等于先上线一个多页 Dashboard 外壳）；新增常驻侧边栏（为一个页面引入一套导航结构属过度设计，且 375px 顶栏刚压到 58.7px，侧栏要重做响应式）。

#### 3.B 路由与页面形状

```
/jobs        列表页（requiresAuth: true）
/jobs/new    录入页（粘贴 JD → AI 提取 → 逐条编辑 → 保存）
/jobs/:id    详情页
```

- **录入用整页，不用弹窗**：8000 字 JD + 提取结果需要并排对照；Phase 3.2 定的弹窗宽度在 ≤768px 是 `calc(100% - 24px)`，长表单塞进去只会得到一条很高的内部滚动条，比整页更难填。
- 列表页：卡片式（企业名 + 岗位名 + 类型标签 + 截止时间 + 技能前 6 条）。375px 单列；≥1100px 两列卡片 + 独立详情页（比右侧抽屉实现更简单，且抽屉要再处理一轮滚动与焦点）。
- 详情页：JD 原文（长文本折叠 + 「展开全文」）、技能条目列表（`origin` 徽标只有两态：`ai_extracted` = 「AI 提取·可回溯原文」/ `manual` = 「手动·未标注出处」）、`evidence` 展开可见、「你已具备」只读比对（2.E）；凡 `deadline < date.today()` 的岗位，详情页一律显示「已过期」，**不绑定列表参数、也不区分查看者身份**（`include_expired` 只作用于列表，见 2.G）。
- 提取交互完全复用润色那套已验证的模式：点「✨ AI 提取技能」→ Loading（前端超时 45 秒，对齐 `MVP_SCOPE.md:64` 的 NFR 口径）→ 结果显示 2.D 的核验计数 → 用户逐条删改 → 点「保存岗位」才落库；「放弃」什么都不改。
- 样式走既有标准：`--ui-*` 令牌、`.ui-dialog`、`--ui-radius: 2px`、控件 32px、遮罩用 `flex + margin:auto`（不要 `grid + place-items:center`，它会把高内容裁成不可滚动）。

#### 3.C 本轮明确不做

搜索只做 `keyword`（企业名/岗位名）+ `status`；不做地点/薪资/经验筛选（字段存了但不筛，见 1.G）、不做收藏与投递记录、不做批量导出（`API.md:190` 的候选人导出继续冻结）。分页参数后端一次做对（`page`/`page_size`），前端首屏拉 50 条。

### 议题 4：模拟面试（仅评估，零实现）

#### 4.0 口径已裁定 —— 两份文档各司其职

冲突是真实的：Word 需求说「不能是纯文字，必须支持电话/视频」，而 `docs/PRD.md:53`（MVP 第 7 项）写「文本模拟面试；预留语音/视频实时会话适配层」，`docs/PRD.md:59` 把 WebRTC 放在第二阶段；`docs/MVP_SCOPE.md:41` 又把「模拟面试（文字/电话/视频/WebRTC）」整条冻结。

裁定结果（照此执行，不再是开放项）：

- **最终验收按 Word**：交付出去给用户看的界面**不得是纯文字**——至少 `speechSynthesis` 朗读 + `MediaRecorder` 录音回放。
- **工程排期按 PRD**：先做文本面试并预留语音/视频适配层（`PRD.md:53`），WebRTC 属第二阶段（`PRD.md:59`）。
- 两者不矛盾：Word 约束的是"验收形态的地板"，PRD 约束的是"施工顺序"。落地形态就是 4.B 的第 2 步。
- **`MVP_SCOPE.md:41` 整条继续冻结**：本文只交付评估结论，本轮不含任何语音/视频实现，也不采纳任何"顺手接上"的过渡方案；开工前需另行书面解冻。

#### 4.A 三个方案的事实盘点

**方案 A：纯文字对话框**

- 成本：0 新依赖，完全复用 BYOK + DeepSeek chat 链路（出题、追问、复盘都只是 Prompt 与状态管理）。
- 能验证的：面试**内容**是否有价值——问题是否贴 JD、追问是否有力、复盘能否改进。这是模拟面试唯一不可替代的部分。
- 不能验证的：「打电话的感觉」。按 4.0 的口径，A 单独存在时**达不到 Word 的验收地板**，但符合 PRD 的 MVP 描述，因此它是施工的第一步而不是终点。

**方案 B：浏览器原生 Web Speech API**（「前端录音转文字发给大模型，回复用 TTS 播放，像打电话一样」）

必须拆成两半评估——它们是两个互不相干、风险也不相干的 API：

- `SpeechRecognition`（STT）：Chromium/Edge 的默认实现**把音频送到 Google 云端识别**，不是本地跑。在中国大陆的网络条件下，主力用户（高校学生、以手机为主）很可能一句都识别不出来。iOS Safari 的本地识别覆盖面窄、需额外手势与权限。**这条方案的命脉握在浏览器厂商手里，而且恰好握在最不可用的那通道上。**
- `speechSynthesis`（TTS）：走系统语音，零依赖、零网络往返。中文可用性因设备而异（Windows/Edge 效果不错；无 Google TTS 的国产 Android 可能枚举不到一个 zh 声音）。**这是 B 里唯一便宜且能真落地的部分**，也正是 4.0 验收地板所需要的部分。
- 交互结构：识别与播放抢麦克风、半双工、无 barge-in（用户打断）、无服务端 VAD → 「像打电话」兑现不了；且任何一环不通都必须**显式失败**（禁止静默降级），要写清「此浏览器不支持」的兜底 UI。
- 最关键的架构事实：**DeepSeek 只有文本进文本出的 chat 接口，没有 ASR、没有实时语音通道**。所以「录音转文字发给大模型」这一步仍必须引入第二家外部服务（讯飞/腾讯云/Whisper 等），BYOK 的整套密钥生命周期、错误码分流、限流、日志脱敏要为第二家重做一份。**B 不是"零依赖方案"，它只是"把依赖藏在浏览器里"。**
- 隐私面：音频离开浏览器进第三方云，需重新对照 PRD §3 的显式授权要求与 `PRD.md:69`（不保存高敏感资料）。

**方案 C：第三方 RTC（声网 Agora / 腾讯云 TRTC）**

唯一能真正兑现「电话感」的方案：低延迟双向音频、可打断、可选云端 ASR/TTS 管道、可选云端录制做复盘。它的障碍**不是价格**（价格会变，且本文未核实当期报价，不作为依据），而是两条不随价格变化的结构性理由：

1. **凭据归属变了。** 需要 AppID + 服务端签发的 RoomToken，这是**我方付费、由我方持有**的凭据（要进服务端配置），与 BYOK「用户自带 Key、服务端不落库不写日志、用完即焚」不是同一类东西。它需要单独设计：脱敏规则、不进前端包、不进日志、轮换方式，以及一旦泄露的爆炸半径评估。这套设计的工作量与风险，与「换一个价格」无关。
2. **CI 无法覆盖。** 需要真实设备、真实麦克风/摄像头权限、真实第三方账号，只能人工验收。这会让本项目第一次出现「无法附测试输出」的交付项，直接撞上 AGENTS.md §7.6（没有测试输出不得声称测试通过）。**必须先与该约束的维护者单独约定验收方式**，否则实现得再干净也无法宣布完成。

其余工程代价（仍要付，但不构成决定性理由）：信令、ICE/TURN、设备权限、弱网降级、断线重连，每一条都是独立可失败点且都要有用户可见错误；前端 SDK 体积约 300KB 量级，是本项目首次引入重型前端依赖；音视频属个人敏感数据，云端录制需要单独同意与保留策略（PRD §3）；用量计费的价格与免费额度需在真正立项时以官网当期报价为准。

#### 4.B 三步施工顺序 —— 已定（每步以真实用户验证为闸门）

前提：三步都在 `MVP_SCOPE.md:41` 冻结清单之内，任何一步开工前都需要单独书面解冻。

1. **A：文字面试**。回答最贵的那个问题——我们的 AI 面试内容对真实学生有没有用。内容不成立时，「打电话的感觉」救不了它。
2. **A + `speechSynthesis` 朗读 + `MediaRecorder` 录音回放**：零新依赖、零网络往返就让界面「不是纯文字」——模型的问题用系统语音读出来（电话里的声音），学生按住说话录一段，先只做回放/留存，**不假装能实时转写**。这一步同时满足 PRD 的「文本 + 预留适配层」和 Word 的验收地板。
   - **口径写死**：Word 的「不是纯文字」验收地板由 **`speechSynthesis` 朗读**满足；**作答仍以文字为主**，`MediaRecorder` 录的音只作自评/回放，**不转写、不送 AI**（DeepSeek 无 ASR，见 4.A）。这条必须写明，否则第 2 步声称要验证的「内容价值」实际验证不了——学生的回答进不了模型，追问与复盘就无从谈起，而界面听起来已经"像面试"了，很容易被误当成验证通过。
   - 真要接 ASR，才引出第二家供应商的 ADR。
3. **仅当 1+2 被真实用户验收、且「打电话感」被证明是必要而非好看时**，单起一份 RTC ADR（方案 C）。该 ADR 里必须先记下：若做，**优先评估「托管式 ASR + TTS + 打断」管道**（一家供应商把识别、合成、打断都包了），不自研全栈语音——自研要同时持有 ASR/TTS/VAD 三家凭据并自己拼装打断时序，是本项目规模下最贵的路。

不推荐直接上 B：产品成败押在浏览器厂商云端识别的国内可用性上，且仍要额外接一家 ASR。不推荐直接上 C：在内容价值未经一次真实验证之前，先引入重型依赖、改变凭据归属、并把验收方式推向 CI 之外。

## `docs/API.md` 修订清单（批准后执行，本轮不改）

| # | 位置 | 现状 | 新口径 |
|---|---|---|---|
| 1 | §4 L100（`POST /jobs`） | 「返回 `202` 并创建 `ai_job_id`，用于异步岗位分析」 | 返回 `201` + 岗位对象；岗位分析不在创建请求里发生 |
| 2 | §4 L97 示例 body | `"requirements": ["Python","FastAPI"]`（字符串数组，客户端直传技能） | 请求体收 `salary_min`/`salary_max`/`experience_required`；技能条目走 `job_skills` 结构（`name`/`type`/`confidence`/`evidence_text`），`origin` **不在请求体里**（服务端派生，1.D） |
| 3 | 新增 | 无提取端点 | `POST /jobs/extract-skills`：200 同步返回 `{skills, meta}`；需 `X-DeepSeek-Key`；限流 `EXTRACT_RATE_LIMIT = "10/minute"`；错误码 `AI_KEY_MISSING`/`AI_KEY_INVALID`/`AI_SERVICE_UNAVAILABLE`；**`meta` 结构必须逐字段写进 API.md**：`meta = {model, prompt_version, received_count, dropped_count, truncated, finish_reason}`（缺一份字段契约，将来改 API.md 时就只剩端点名，6 个字段的语义会丢） |
| 4 | §4 L104 | 「未审核岗位仅创建者和**审核者**可见」 | 无审核角色。新口径：`published` 任何登录用户可读；他人 `draft` **404**（1.F） |
| 5 | §4 L104 | 查询参数 `keyword`、`status`、`location`、**`skill_id`**、`page`、`page_size` | 只保留 `keyword`、`status`、`include_expired`、`page`、`page_size`；删 `location` 与 `skill_id`（本轮不筛，`skill_id` 依赖并不存在的技能字典表） |
| 6 | §4 L110-112 | 「`/publish`、`/close` 需要**审核权限**；过期岗位**自动转为 `expired`**」 | `/publish`、`/unpublish` 仅创建者（非创建者 404）；无 `/close`；过期不落状态，读侧按 `date.today()` 判定且默认隐藏（2.G） |
| 7 | §4 标题层级 | 「岗位」章节含教师端候选人接口 | `API.md:186-190` 的 `/teacher/jobs/...` 候选人查询与导出**继续冻结**（L42），本轮保留为设计意图占位并标注 `# PHASE-2: not wired` |
| 8 | §7 错误码表 | 已列 `AI_JOB_FAILED`、`AI_OUTPUT_UNSUPPORTED`、`MEDIA_UNAVAILABLE` | `AI_JOB_FAILED` 依附于异步任务模型，随 #1 一并删除；`AI_OUTPUT_UNSUPPORTED` 合并进 `AI_SERVICE_UNAVAILABLE`（与 ADR-010 §6 的三码一致）；`MEDIA_UNAVAILABLE` 属模拟面试，保持冻结、不启用 |

## 后果（批准之后会发生什么）

- 表数量从 7 张增加到 9 张（`jobs`、`job_skills`）。`MVP_SCOPE.md` §6 的标题与 AGENTS.md §4.2 已在本轮同步改为「后续阶段新增表必须走 ADR」，并在 AGENTS.md §1 事实清单写明「现库 7 张表」，因此本 ADR 满足的是「先有 ADR 再建表」这条程序要求。
- 解冻**没有**落到约束文档里：`MVP_SCOPE.md` §5 的「AI 爬虫采集岗位、企业需求自动解析」本轮已从上一版的「Phase 4 部分解除」**回滚为冻结原句**，与 AGENTS.md §3.2 L68 一致。因此岗位 JD 技能提取目前**不具备开工资格**；解冻需要人类负责人书面授权，届时一次性修订 AGENTS.md §3.2 + `MVP_SCOPE.md` §5 + `MVP_SCOPE.md` 变更记录表三处，本文不预判该授权。
- 后端新增文件清单（**本仓没有 `models/` 目录**，ORM 声明与仓储实现同文件，与 `user_repository.py` / `student_profile_repository.py` / `resume_repository.py` 的形状一致）：
  - `app/integrations/skill_extractor.py` —— 新外部出口，沿用 `ResumeCopyWriter` 那种 ABC + 具体实现的形状；
  - `app/repositories/job_repository.py` —— `jobs` + `job_skills` 两个 ORM 模型与仓储同文件；
  - `app/schemas/job.py` —— 保留（与 `schemas/auth.py` / `profile.py` / `resume.py` 的命名一致）；
  - `app/services/job_service.py`；
  - `app/api/v1/jobs.py`；
  - `app/api/v1/__init__.py` —— **补 `include_router(jobs.router)`**（不改这一行，端点根本不存在）；
  - `app/core/rate_limit.py` —— 增 `EXTRACT_RATE_LIMIT`；
  - `app/services/dependencies.py` —— 增装配（Key 取法与 `get_resume_service` 同构）。
  - 无需改 `app/main.py`：`CORS_ALLOW_HEADERS`（`app/main.py:46`）已含 `X-DeepSeek-Key`，本端点复用同一个头，加它反而会让人误以为引入了第二套凭据通道。
- 迁移：`0011_jobs.py` + `0012_job_skills.py`，各自必须可 `downgrade`；沿用 0007（先建替换索引）与 0010（JSON 列不能带 `server_default`——本文已决定不用 JSON，该坑自然绕开）的既有教训。
- 前端：新增 3 条路由 + 3 个视图 + 一个 `jobs` store，顶栏加一个入口。
- 反幻觉核验（2.D）使服务端第一次对模型输出做**语义级**校验（子串匹配 + 归一化）。它必然把一部分合理归纳误判成幻觉而丢弃——这是有意的偏保守：宁可用户多点一次重试，也不要岗位库里出现 JD 没写的技术栈。误伤率不是猜测，由 `dropped_count` / `received_count` 直接读数；据此调整规则时，调整本身要有测试记录。
- 与润色同源的不可验证项：真实 Key 下 `deepseek-flash` 的提取质量、`confidence` 是否是随手填的数、15 条与 `max_tokens=1500` 是否合适，**本轮无法验证**（不持有真实凭据）；Mock 测试只能证明「指令与封顶参数确实发出去了」和「不合规则的结果会被拦下」。

## 替代方案

- **照搬 `schema-draft.sql` 的岗位三表**（`companies` + `job_posts` + `job_skill_requirements` + 技能字典）：看着"架构完备"，但外键指向实现中不存在的 `organizations`/`memberships`，落地要先建 5 张组织表；7 态状态机需要的审核角色与定时任务都不存在。正是 AGENTS.md §2 批评的「文档深度与代码深度倒挂」。不采用。
- **岗位 + 一次 AI 调用直接落库**（不做草稿态）：与 ADR-010 确立的产品诚信线冲突——用户第一次读到 AI 文案时它已是既有事实。不采用。
- **异步任务队列做岗位分析**：违反 Redis/ARQ/Celery 冻结。注意本文**不再**用「30 秒预算已在润色上验证够用」为它辩护——那个结论对未设服务端输出封顶的端点并不成立，理由见 2.B 的更正段；否决异步的依据只有「队列仍冻结 + 本端点已有服务端封顶」。不采用。
- **`origin` 由客户端提交**（含一个「用户编辑过」标记）：客户端可伪造的溯源标记等于没有标记。改为服务端派生（1.D），`user_edited` 值已从 CHECK 枚举中删除。不采用。
- **启用结构化输出参数**（`response_format={"type":"json_object"}`）：本轮**不采用**，三条理由——① `deepseek-flash` 对 `json_object` 的支持与 `thinking: {"type":"disabled"}` 组合无法在本轮验证（不持有真实凭据，而 `MockTransport` 只是在本地编造响应，测不出上游是否真接受这组参数）；② 官方文档称：启用该模式后若模型产出无法被识别为合法 JSON，接口会把内容替换为空白字符串——那会把「模型其实答对了但格式没对上」变成一次无信息量的失败，而现有的剥围栏 + 括号平衡解析（`copy_writer.py` 的 `_FENCE` / `_first_object`）至少能观测到原始输出；③ 我们的核验靠的是 `evidence` 是否落在 JD 原文里，这与输出是否"合法 JSON"无关，结构化输出参数解决的是最不成分的那一半问题。若将来有凭据实测确认该组合可用，可作为独立优化再加回来。
- **不做服务端核验，纯靠 Prompt 约束模型别编**：模型不可信而用户要的是一条确定性承诺（ADR-009/010 同源理由）。不采用。
- **模拟面试与岗位库绑成一份 ADR**：面试形态取决于口径与内容价值验证，与岗位库的表设计无依赖关系；绑在一起只会让两边都拍不下去。已按人类裁定拆开（议题 4 只出结论，不出实现）。

## 已定事项与生效前提

v1 的 13 个悬空问题在 v2 里全部落成确定选择，不再有「需你裁定」项：

| v1 | v2 结论 |
|---|---|
| Q0 阶段口径 / 表数量限制 | 已改文档：AGENTS.md §4.2 改写 + §1 追加「现库 7 张表」；`MVP_SCOPE.md` §6 标题改写。新增表按「先 ADR 再建表」程序推进 |
| Q1 是否解冻 L43 的解析部分 | **未解除**：待人类负责人书面授权后，一次性修订 AGENTS.md §3.2 + MVP_SCOPE §5 + 变更记录表。本文不构成解冻 |
| Q2 `users.role` | 不加（1.A） |
| Q3 `submitter_kind` | 不建（1.C） |
| Q4 薪资 / 经验字段 | **加** `salary_min`/`salary_max` + CHECK、`experience_required`；表单选填、本轮不筛（1.B、1.G） |
| Q5 JSON / 子表 / 字典 | 子表 `job_skills`（1.D） |
| Q6 端点同步、不落库 | 采纳，并据此修订 API.md（2.A、修订清单 #1/#3） |
| Q7 契约带 `evidence` | 带（2.C） |
| Q8 `name` 英文 token 加强项 | 本轮不做，靠 2.D 的计数观察真实样本（2.D） |
| Q9 与学生技能只读比对 | 采纳，L42 保持冻结（2.E） |
| Q10 入口位置 | 顶栏（3.A） |
| Q11 Word / PRD 口径冲突 | **验收按 Word（不得纯文字）、排期按 PRD（文本 + 适配层、WebRTC 二阶段）**；`MVP_SCOPE.md:41` 整条继续冻结（4.0） |
| Q12 RTC 障碍 | 改记为两条不随价格变化的依据：凭据归属变更、CI 无法覆盖（4.A）。价格因素留待真正立项时以官网当期报价核实 |

**生效前提**：本文所列实现工作（迁移、后端、前端、`API.md` 修订、测试）仍需人类负责人把本 ADR 转为正式任务指令后才开工；开工那轮按 AGENTS.md §5.1 先复述范围与文件清单。

**解冻是另一道独立的门**：即便本 ADR 被批准为实现任务，`/jobs/extract-skills` 仍受 `MVP_SCOPE.md` §5 那条冻结的约束。顺序只能是——若解冻授权尚未下达，先做岗位库的建表、仓储与 CRUD（不含任何外部调用，不碰 AI），**AI 技能提取一行都不写**；授权下达时，与提取实现同轮把 AGENTS.md §3.2、MVP_SCOPE §5、MVP_SCOPE 变更记录表三处一起改掉，使约束文档与代码同步。不允许「先写代码、文档以后再补」，也不允许用「顾问已口头同意」替代老板本人的书面授权。

## 附：实现阶段验收清单（下一轮照抄）

失败语义（7 条，`/jobs/extract-skills`，逐条都要有测试）：

| # | 情形 | 期望 |
|---|---|---|
| 1 | 请求头没有 Key | `AI_KEY_MISSING`（400） |
| 2 | DeepSeek 拒绝该 Key（401/欠费） | `AI_KEY_INVALID`（401），**不触发前端令牌刷新、不踢下线** |
| 3 | 网络超时 / 上游限流 / 5xx | `AI_SERVICE_UNAVAILABLE`（502，`retryable`） |
| 4 | 返回内容不是可解析的 JSON 对象 | `AI_SERVICE_UNAVAILABLE`（502） |
| 5 | 所有条目的 `evidence` 都通过不了子串核验 | `AI_SERVICE_UNAVAILABLE`（502），**不得返回空列表** |
| 6 | **`choices[0].message.content` 为空或全空白** | `AI_SERVICE_UNAVAILABLE`（502，`retryable`）——v1 漏了这一条：现有 `copy_writer.py` 用 `(completion.choices[0].message.content or "").strip()` 兜住 `None`，但兜住之后仍会走解析路径；本端点必须在「空内容」这一格显式判失败，否则会退化成一条意义不明的 4xx |
| 7 | **`completion.choices[0].finish_reason == "length"`**（输出被我们自己的 `max_tokens` 切断） | `AI_SERVICE_UNAVAILABLE`（502，`retryable`），并把 `finish_reason` 回进 `meta.finish_reason` 与日志；**不得**与第 4 情形合并成"JSON 解析失败" |

为什么必须有第 7 条（不是把数字缩小就能省掉）：2.B 的算式按 `name` 常态长度折算是 1350 ≤ 1500，但最坏角落实测更大——`15 × (40 + 60 + 30) = 1950`、`15 × (20 + 60 + 30) = 1650`，**两者都 > 1500**。也就是说只要模型把 `name` 写长一点，第 15 条就会在半截 JSON 处被切断；把它归进第 4 情形（"模型返回的内容不是合法 JSON"）会说出一个**错误的诊断**：不是模型不守格式，是我们的 token 上限把它切了。用户的下一步动作完全不同（第 4 情形该重试同一请求；第 7 情形该「请重试并只粘贴 JD 的核心段落，或减少期望条数」——注意缩短 JD 只是**间接**起效：`max_tokens` 切断与输入长度无关，真正决定它的是**输出**规模，短 JD 只是间接降低了条目密度）。所以必须单独命名。三个封顶数（1500 / 15 / 60）保持不变，人类负责人已定。

其他验收项：

- 迁移 `upgrade head` 与 `downgrade base` 均可跑通，且有测试；
- 提取端点**不写任何表**：用 SQLAlchemy 事件在 **Engine**（或 `Connection`）上捕获——`event.listen(engine, "before_cursor_execute", ...)` 收集本次请求执行过的全部语句，断言其中**没有 INSERT / UPDATE / DELETE**。
  - **挂 `Session` 不成立**（v3 把目标写成「Engine 或 Session 皆可」是错的，已废）：SQLAlchemy 2.1.6 实测 `event.listen(engine, "before_cursor_execute", rec)` 正常，而 `event.listen(session, "before_cursor_execute", rec)` 抛 `InvalidRequestError: No such event 'before_cursor_execute' for target '<sqlalchemy.orm.session.Session object at 0x…>'`——`before_cursor_execute` 是 Connection/Engine 层事件，不在 Session 的事件表里。写成挂 Session 的话，测试会在收集阶段就报错或被 except 掉，等于没有断言。
  - **零写入断言可实现**（实测）：只读请求经 `Session.execute(text("SELECT 1"))` 时，事件收集到的语句列表就是 `['SELECT 1']`，写语句筛出来为空 → 断言成立且不空过。
  - 上一轮写的「或事务回滚后行数不变」这种备选**删掉**：如果代码压根没 flush，回滚式断言会空过，测不出任何东西，属于典型的"绿色但无意义"的测试。
- 输出封顶参数（`max_tokens=1500`、`thinking.disabled`、`stream=False`、`max_retries=0`）在 `MockTransport` 捕获的请求体里逐条可验；
- **切断情形单独命名（第 7 情形）**：用 `MockTransport` 构造一份 `finish_reason="length"` 且 `content` 只有半截 JSON 的响应（例如 `{"skills": [{"name": "Python", "evidence": "熟练`），断言①返回 `AI_SERVICE_UNAVAILABLE`（502，`retryable`）、②`meta.finish_reason == "length"`、③**没有**走到第 4 情形的"不是合法 JSON"文案。再构造一份 `finish_reason="stop"` 但内容同样不合法的响应，断言它命中第 4 情形——两条测试互为反向，防止后来人把两者合并。
- 条数 > 15 时 `meta.truncated = true` 且只落 15 条；`received_count` / `dropped_count` 与前端文案一致；
- `origin` 派生：`evidence` 归一化后是 `description` 子串 → `ai_extracted`，否则 → `manual`；客户端提交的 `origin` 被忽略；
- `UNIQUE (job_id, name)` 拦得住 `Python` / `python` / `Ｐython`（首字母全角）三种写法（只落 1 行）；
- `_normalize_for_trace` 按 2.D.1 的用例表逐条落成单元测试，**其中「仅标点/全半角不同」这条必须可复现**：JD 原文「熟悉 Python、FastAPI、Docker」配 evidence「熟悉 Python, FastAPI, Docker」→ 按本 ADR 选择的「标点不参与比较」口径**必须通过**并落库为 `ai_extracted`；同一对字符串在去掉归一化第 4 步的实现里**必须被丢弃**（用一条反向断言钉住口径本身，防止后来人"顺手改成严格比较"而不改测试）；
- 真幻觉对照：JD 里没有 `Kubernetes`，evidence 写「负责 Kubernetes 集群运维」→ **必须丢弃**且 `dropped_count` +1；evidence 长度 > 60 字 → **必须丢弃**；全部条目被丢弃 → 502，不返回空列表；
- 越权语义：他人 `draft` 的 `GET` / `PUT` / `DELETE` / `/publish` 全部 404，且 `JobService` 只抛 `JobNotFoundError`、`services/` 下不出现 `HTTPException`；
- 过期判定：`deadline < 今天` 默认不出现、`include_expired=true` 出现、`deadline = 今天` 仍出现；SQL 里不出现数据库侧当日函数；
- 日志与异常消息中不出现 Key，也不出现 JD 正文（只有两个计数、`finish_reason` 与 `request_id`）；
- 375px 与 1280px 下「粘 JD → 提取 → 删改 → 保存 → 详情」主路径完整可走通；
- import-linter 契约全绿，type check / lint / 双端 CI 全绿。
