# 项目状态（真实版）

> 本文件描述的是“已实现并验证”的状态，不是“已设计”的状态。
> 任何未附测试输出或手动复现步骤的条目，不得写入“已验证”。
> 最后更新：2026-11-04（Phase 3.1 · AI 润色与生成解耦，ADR-010）

## 1. 一句话状态

**T7.3 已实现前后端全链路联调（JWT 鉴权、Pinia 状态同步、A4 实时预览、真实数据库落库）**：
注册/登录 → 保存学生档案（`student_profiles` + `skills` + `experiences` + `educations`）
→ 生成简历写入 `resumes`，前端「实时预览」与「已生成简历」共用同一套版式与类名。

简历正文的**文案**可走真实 DeepSeek 润色，**排版与生成仍是模板**（ADR-010 把两者拆成两条互不调用的路）：
「生成简历」= `POST /resumes/generate`，纯模板、不需要 Key、必然成功；
「AI 润色」= `POST /resumes/polish`，四档（细节/中幅/大幅/扩写）只改写经历文案与技能排序，
**服务端不落库**，用户在对照弹窗点「接受」才写进 Pinia 表单，之后仍需自己点「保存修改」。
AI 全程不产出 HTML，注入面止于文本插值。真实 Key 下的端到端效果**尚未验证**（本轮不持有任何真实凭据）。
已有一轮以上小范围人工测试反馈（时区、版式对齐、手机号校验、结束时间未来限制等）
并已修复，但尚无规模化真实用户数据。

> 历史说明：本节曾写“前端仍未接 API”，该描述截至 T7.3 已过期，现行事实以本节与 §3 为准。

Phase 2 已启动，按 `docs/PHASE2_BACKLOG.md` 优先级推进：

- **D1（已完成）**：用户名在应用层显式归一小写，存量数据由迁移回填；
- **D3（已完成）**：保存入口合并为一个「保存修改」按钮；
- **D2（进行中）**：D2.1 后端已支持双 Token（access 15 分钟 + refresh 7 天、落库可撤销、
  每次刷新轮换），**D2.2 前端拦截器尚未接入**——当前前端仍只拿 access token，
  到期即掉线，需重新登录。

## 2. 资产分级

### 2.1 保留复用（已实现，可继续用）

| 资产 | 状态 | 说明 |
|---|---|---|
| 分层目录 `api/services/repositories/integrations` | 已存在 | 依赖方向由 import-linter 5 个 contract 强制 |
| 健康检查接口 | 已实现 | 有测试 |
| 配置管理骨架 | 已实现 | production fail-fast（DB / JWT 密钥）已有测试，`.env.example` 已提供 |

### 2.2 降级为设计稿（已设计，未实现，禁止当真相源）

| 资产 | 新位置 | 说明 |
|---|---|---|
| 领域模型 SQL | `docs/design/schema-draft.sql` | **非迁移源**；Phase 1 只建 5 张表 |
| Redis / ARQ / Celery 规划 | 文档 | Phase 1 不接入 |
| S3 / 对象存储规划 | 文档 | Phase 1 不接入 |
| WebRTC / 模拟面试规划 | 文档 | 冻结项 |
| 教师端 / 学校端规划 | 文档 | 冻结项 |

### 2.3 待补承重（Phase 1 必须完成）

| 项 | 对应任务 | 状态 |
|---|---|---|
| Alembic 基线 + 5 张表迁移 | T4 | 已验证（T7.5 追加第 6 张 `educations`、D2.1 追加第 7 张 `refresh_tokens`，见下） |
| fake 垂直切片 + 集成测试 | T5 | 已验证；T7.2 起被真实仓储取代，`fake_resume_repository.py` 仅留存不再被路由引用 |
| import-linter 架构约束 | T6 | 已验证（5 个 contract 全部通过） |
| 配置 fail-fast + `.env.example` | T6 | 已验证 |
| 统一错误模型 + 测试 | T6 | 已验证（含 404/422/405/500） |
| CI 四件套 | T6 | 已配置，未在 GitHub 运行 |
| T7.4 前端组件与本地状态流转 | T7 | 已验证（typecheck/build 通过） |
| 注册/登录/JWT 鉴权基建 | T7.1 | 已验证（`tests/test_auth.py` 12 passed，连 `resume_mvp_test`） |
| 真实档案仓储 + 档案接口 | T7.2 | 已验证（`tests/test_profiles.py` 14 passed，写满四张表） |
| 简历生成落真实库 | T7.2 | 已验证（`tests/test_resumes_generate.py` 8 passed，写入 `resumes`） |
| 多段教育经历（`educations` 子表） | T7.5 | 已验证：迁移 `20261102_0002`（upgrade/downgrade 可逆）；`tests/test_profiles.py` 覆盖「两段 → 一段」全量替换；生成器按入学时间倒序渲染教育区块 |
| 前后端全链路联调 | T7.3 | **已验证**：JWT 鉴权、Pinia 状态同步、A4 实时预览、真实数据库落库（登录 → 保存档案 → 生成简历） |
| 技术债清理与安全加固 | T7.7 | 已验证：废弃路由 `/me`、`/jobs` 下线；`utc_now()` 收敛到 `app/core/datetime_display.py`；CORS 白名单化；登录/注册 5 次/分钟限流并并入统一错误模型 |
| 用户名大小写归一（D1） | Phase 2 | 已验证：模型层 + 业务层双重 `strip().lower()`；迁移 `20261103_0005` 用 `BINARY` 比较回填存量；`test_login_is_case_insensitive`、`test_register_lowercases_username` 守护 |
| 显式保存按钮（D3） | Phase 2 | 已验证：保存语义合并为预览区工具栏唯一入口（三态：保存修改 / 保存中… / 已保存禁用），左侧「保存档案」按钮与相关样式删除；`typecheck` / `build` 通过 |
| 双 Token + 刷新接口（D2.1 后端） | Phase 2 | 已验证：`refresh_tokens` 表（迁移 `20261103_0006`，`upgrade`/`downgrade` 可逆）；登录返回双 Token、库里只存 SHA-256；`POST /auth/refresh` 轮换、复用旧令牌 401；`POST /auth/logout` 撤销；`tests/test_auth.py` 9 项新用例 |

## 3. 已验证 / 未验证

| 项 | 状态 | 证据 |
|---|---|---|
| 进程可启动 | 未验证 | 本次未运行启动或健康检查命令 |
| 数据库迁移可跑通 | **已验证** | `alembic/versions/20261023_0001_initial_phase1.py`、`tests/test_migrations.py`、`uv run pytest` 1 passed、MySQL 8.0.43、Ruff 0.17.1 全绿 |
| 注册/登录写入真实 MySQL | **已验证** | `uv run pytest tests/test_auth.py -q` 23 passed（201 注册 + 库内记录断言、重复用户名/邮箱 400、登录 200 + Bearer、大小写不敏感登录、错密码/未知用户 401、`/auth/me` 200、六种无效令牌 401；D2.1 追加双 Token / 轮换 / 复用 401 / 登出 / 级联删除） |
| 双 Token 与刷新 | **已验证** | `POST /auth/login` 返回 `access_token` + `refresh_token`（`expires_in` 仍为 access 的 900 秒）；库内 `refresh_tokens.token_hash` = `sha256(明文)`（64 位，明文不落库）；`POST /auth/refresh` 换发新双 Token 并把旧记录置 `revoked_at`；同一 refresh token 二次使用 401；`POST /auth/logout` 返回 204 且撤销后不可再刷新 |
| JWT 生产 fail-fast | **已验证** | `Settings(environment="production")` 缺 `jwt_secret_key` 或不足 32 位均抛 `ValueError` |
| 档案读写写入真实 MySQL | **已验证** | `uv run pytest tests/test_profiles.py -v` 14 passed：PUT 后 `student_profiles` 1 行 / `skills` 3 行 / `experiences` 1 行 / `educations` 1 行；教育经历 2 段再改 1 段时旧行被物理删除；二次 PUT 技能/经历全量替换且 profile id 不变；两用户互不可见 |
| 简历生成写入真实 MySQL | **已验证** | `uv run pytest tests/test_resumes_generate.py -v` 8 passed（**历史记录：当时 8 例，现为 22 例** —— D4.1/D5.2/D5.3 陆续补了多简历、配色与排版用例；ADR-010 起这 22 例就是**纯模板路径**，`use_fallback` 开关已随解耦删除，润色另有 `tests/test_resume_ai_copywriting.py`）：注册→PUT 档案→生成，断言 `resumes` 行的 id / student_profile_id / title / content_html 与响应一致；教育区块含学校/专业/学历/起止时间且按入学时间倒序，无教育经历时整块不渲染；未登录 401；无档案 400 |
| 业务链路可跑通 | **已验证（前端 + 后端）** | T7.3 起前后端全链路联调：登录态（JWT）→ 档案保存 → A4 实时预览 → 生成简历落真实 `resumes` 表；`uv run pytest -q` 69 passed 覆盖后端链路，前端 `typecheck` / `build` 通过 |
| 手机/电脑可用 | **部分验证** | 已在小范围人工测试中跑通主流程（并据此修复 10 项体验问题）；无规模化真实用户数据 |
| 生成简历（纯模板） | **已验证** | ADR-010：`POST /resumes/generate` 不再调用 AI，`ResumeGenerateRequest` 只剩 `title` / `profile_id`（`use_fallback` 已删除，OpenAPI 有用例钉住）。带 Key 生成也是零外呼——`tests/test_resume_ai_copywriting.py::test_generate_never_calls_deepseek_even_with_a_key` |
| AI 润色（独立端点） | **已接入，真实调用未验证** | `POST /resumes/polish`：四档 Prompt、`expand` 的服务端字数硬上限、`index` 一一对应、技能不新增不丢、**不写任何表**、400/401/502/429 分流、Key 不进日志。`uv run pytest -q` 213 passed，其中 `tests/test_resume_ai_copywriting.py` 44 例用 `httpx2.MockTransport`（autouse，结构上不可能真出网）断言线级请求参数与档位指令；前端 `resume-polish.spec.ts` 11 例钉「确认前不改表单、接受才置 dirty、且不发保存」，`byok.spec.ts` 7 例盯 sessionStorage 与 Key 只挂润色。**用手测的缺口**：真实 Key 下的延迟、模型 JSON 输出的遵循度、429 的真实形态、以及两个弹窗在浏览器里的实际交互尚未验证（本轮不持有任何真实凭据） |

## 4. 当前阻塞项

1. `resumes` 表只有写入路径，缺列表 / 详情 / 编辑接口——「我的简历」还没有档案库视图；
2. 纸面（`contenteditable`）上的就地编辑只写进 Pinia store，不写回已生成的 `content_html`；
3. CI 四件套（ruff / mypy / pytest / lint-imports）仅在本地验证过，未在 GitHub Actions 实际运行；
4. 登录/注册限流是**进程内内存计数**，多 worker 部署时每个进程各算一份，需换 Redis 存储才准确；
5. **D2.2 未做**：后端已能签发/轮换 refresh token，但前端 `client.ts` 仍只持有 access token，
   15 分钟后 401 直接清 Token 跳登录——双 Token 的价值要等前端拦截器接上才成立；
6. `refresh_tokens` 没有清理任务：过期记录只会累积（每条登录会话一行，7 天后失效），
   需要定时删除或「按用户保留最近 N 条」的策略（Phase 2 后续）。

## 5. 下一步

按 `AGENTS.md` 第 8 节 T1 → T7 顺序执行，一次一个任务，完成一个更新本文件一次。

## 6. 本轮检查记录

- 已完成：修正 `docs/AGENT_HANDOFF.md` 第 5 节，使代码块仅包含 git 状态输出；修正第 6 节，使优先级链条使用 `text` 代码块。
- 已执行：未运行代码、测试、lint、类型检查或迁移命令；本轮仅涉及文档格式。
- 待办：T2.5 仍未执行，需按 `docs/AGENT_HANDOFF.md` 第 7 节的既定范围处理。
- T6 补正：统一错误模型已覆盖 404/422/405/500；import-linter layers 与 API forbidden
  contracts 均通过；已提供仅含占位符的 `.env.example`。
- T7.4：新增三栏简历编辑器、A4 预览、样式辅助面板与 Pinia 本地状态同步；未接入后端 API。
- 前端 TS 配置修复：新增 `@types/node`，移除已弃用 `baseUrl`；`npm run typecheck` 与 `npm run build` 通过。
- T7.1：新增 ADR-004（JWT 决策，含 bcrypt 版本锁定补记）；补充 `jwt_secret_key` /
  `jwt_algorithm` / `jwt_expire_minutes` 配置与 production fail-fast；新增
  `app/core/security.py`、`app/schemas/auth.py`、`app/repositories/user_repository.py`、
  `app/services/auth_service.py`、`app/api/v1/auth.py`，在 `dependencies.py` 增加
  `get_auth_service` / `get_current_user`；新增 `tests/test_auth.py`（12 项，连真实 MySQL
  `resume_mvp_test`，测试前后自动建表/删表）。全量 `uv run pytest -q` 31 passed，
  `ruff` / `mypy` / `lint-imports`（5/5 contract）全绿。未改前端、未改简历业务逻辑、
  未新增表、未 commit。
- T7.1 已知遗留：401 响应未透传 `WWW-Authenticate` 头（需改 `main.py`，不在本轮授权）；
  `docs/API.md` 尚未登记 `/api/v1/auth/*` 三个端点；前端未接入登录态。
- T7.2：新增 `app/repositories/student_profile_repository.py`（profiles/skills/experiences
  映射 + 父表 upsert、子表全量替换）与 `app/repositories/resume_repository.py`（`resumes`
  只写）；新增 `app/services/profile_service.py`、`app/api/v1/profiles.py`
  （`GET` / `PUT /api/v1/profiles/me`）；改造 `resume_service` 与 `resumes.py`：
  请求体只剩 `title`，档案一律按登录用户从库中读，未登录 401、无档案 400。
  `dependencies.py` 的 `get_resume_service` 改装配真实仓储，`FakeResumeRepository`
  不再被正式路由引用（文件保留）。新增 `tests/test_profiles.py`（10 项）与重写
  `tests/test_resumes_generate.py`（7 项），建表走 Alembic `upgrade head` / `downgrade base`。
  全量 `uv run pytest -q` 40 passed；`ruff` / `mypy` / `lint-imports`（5/5）全绿。
- T7.2 越界声明：`tests/test_errors.py` 改了 14 行（超出授权清单）。原因：`/resumes/generate`
  加鉴权后，FastAPI 先解依赖再做请求体校验，未带 Token 的 `json={}` 会返回 401 而不是 422，
  原「422 统一错误模型」用例必然变红；处理方式是为该用例补一个 `get_current_user` 覆盖，
  没有放宽任何断言。500 用例靠「service 依赖先于鉴权解析」保持原样通过。
- T7.2 已知遗留：`GET /api/v1/profiles/me` 未填档案时返回 `200 + {"profile": null}`
  （任务允许 404 或空结构，这里选空结构以免前端把「还没填」当错误）；
  `docs/API.md` 仍未登记 auth / profiles 端点；`app/api/router.py` 里旧的
  `GET /api/v1/me`（返回 `authenticated: false`）占位未清理，不在授权范围。
- T7.5：新增迁移 `20261102_0002_educations.py`（`educations` 表：id / student_profile_id
  FK RESTRICT / school / major / degree（CHECK 专科·本科·硕士·博士）/ edu_start / edu_end
  NULL / created_at·updated_at DATETIME(6)，含 `ix_educations_student_profile_id` 与
  `ck_educations_date_range`，downgrade 为 `drop_table`）。
  仓储、`ProfileUpsertRequest` / `ProfileRead`、`ProfileService` 全部加上 educations
  （先删后插，与 skills/experiences 同策略）；生成器在「基本信息」之后插入「教育经历」
  区块，按 `edu_start` 倒序渲染「学校 / 专业 ｜ 学历 ｜ 2021.09 — 2025.06（或至今）」，
  无数据时整块不渲染。前端移除「年级」输入与预览中的年级段，新增教育经历动态列表
  （默认 1 段、可增删、至少保留 1 段、必填项带星号），store 的 `ProfileDraft` 增加
  `educations`、payload 里 `YYYY-MM` 补成 `YYYY-MM-01`，旧草稿缺该字段时补空数组。
  验证：`uv run pytest -q` 45 passed（连 `resume_mvp_test`）；`ruff` / `mypy` /
  `lint-imports`（5/5）全绿；前端 `npm run typecheck` / `npm run build` 通过；
  迁移在测试库上跑通 `downgrade base → upgrade head` 两轮。
- T7.5 越界声明：除授权清单外还改了 4 个文件，均为「不改就跑不通」的连带改动 ——
  `app/schemas/resume.py`（`ProfileInput` 去掉 `grade`：库里该列已恒为空串，
  留着会触发 `min_length=1` 校验直接炸掉生成接口）、`app/services/resume_service.py`
  （把 educations 传给生成器，约 10 行）、`frontend/src/api/types.ts`（补 EducationInput /
  EducationRead，否则 typecheck 必红）、`tests/test_migrations.py`（表集合与 FK 集合是
  精确断言，新增表必然变红，且「downgrade 后 educations 消失」的用例要求写在这里）。
  另：`grade` 列保留在库中但前后端都不再写业务值（恒写空串），未删列、未改表结构。
- T7.6：迁移 `20261102_0003_drop_profile_major` 删除主表 `student_profiles.major`
  （专业只由每段教育经历携带，`educations.major` 保留）；前后端同步移除顶层 `major`，
  旧客户端残留的 `major` 字段被 Pydantic `extra="ignore"` 丢弃。
- 真实用户反馈轮（T7 阶段内，未单独编号）：①时间统一按东八区返回（新增
  `app/core/datetime_display.py`），前端用 `Intl` + Asia/Shanghai 展示；②生成器版式与
  `ResumePreview.vue` 对齐（去「我的简历」标题与「基本信息」区块）；③教育经历支持纸面就地编辑；
  ④保存不拦必填、严格校验挪到生成前；⑤档案新增 `email` 列（迁移 `20261102_0004`）；
  ⑥有未保存修改时生成前提示；⑦默认入学时间取「当前年 - 4 年」的 9 月。
- 真实用户反馈第二轮：①手机号收紧为 `^1[3-9]\d{9}$`（前端同源正则 + 后端 field_validator，
  空串放行以支持存草稿）；②**开始**时间禁未来（前端 `:max` + 生成前校验 + 后端 422）；
  ③项目经历纸面时间拆成「开始 / 分隔符 / 结束」三个独立可编辑节点。
- 体验修复轮：结束时间（`edu_end` / `end_date`）放开未来限制（应届生要填「预计毕业时间」），
  只保留「不早于开始时间」；项目经历日期由 `YYYY.MM` 改为 `YYYY.MM.DD`（教育经历仍按月份）。
  验证 `uv run pytest -q` 55 passed，前端 `typecheck` / `build` 通过。
- T7.7（本轮）：①删除 `app/api/router.py` 里 T7.1 遗留的占位路由 `GET /me`、`GET /jobs`
  （已被 `/api/v1/auth/me` 等真实接口取代）；②删除仓库根目录 101 字节的孤儿 `package-lock.json`，
  `.gitignore` 清掉 `.uv/`、`ENV/`、`env/`、`yarn/pnpm` 日志、`frontend/.cache/`、重复的
  `frontend/dist/`（`dist/` 已覆盖）；③`utc_now()` 从两个仓储复制粘贴改为
  `app/core/datetime_display.py` 单点定义（`resume_repository` 里同一份内联实现一并收敛）；
  ④CORS 去掉通配符：方法/头改显式白名单；⑤新增 `slowapi` 限流：`/auth/register`、
  `/auth/login` 各 5 次/分钟，超限由 `RateLimitExceeded` 处理器转成 429 +
  `{"error": {"code": "RATE_LIMITED", "retryable": true}}`（沿用 T6 统一错误模型），
  并补 `tests/test_rate_limit.py`（登录 5×401 → 第 6 次 429、注册 5×201 → 第 6 次 429）。
  验证：`uv run pytest -q` 58 passed（连真实 `resume_mvp_test`）；`ruff` / `mypy` /
  `lint-imports`（5/5）全绿；未新增表、未新增迁移、未 commit。
- Phase 2 · D1（用户名归一与回填）：`RegisterRequest.validate_username` 与
  `AuthService.register/login` 双重 `strip().lower()`（模型层决定落库值、服务层幂等兜底），
  并在归一后重判长度（`'İ'.lower()` 会变 2 字符，只信 `Field(max_length)` 会炸成 500）。
  迁移 `20261103_0005` 回填存量——**指令给的原句在 `utf8mb4_0900_ai_ci` 下一条都不会更新**
  （`'ZhangSan' <> LOWER('ZhangSan')` 求值为 0，实测命中 0 行），改用
  `WHERE BINARY username <> BINARY LOWER(username)` 后命中 1 行并在测试库真跑通。
  注册页加「用户名不区分大小写」提示。验证：`pytest` 60 passed。
- Phase 2 · D3（显式保存按钮）：保存语义合并为预览区工具栏唯一入口，左侧「保存档案」
  按钮连同 `handleSave` 与 `.save-*` 样式一并删除；三态（`dirty` → 青绿高亮「保存修改」+
  琥珀圆点、`!dirty` → 灰底禁用「已保存」、`saving` → 「保存中…」且保持高亮）；
  失败走内联 `role="alert"` 提示；`dirty` 时生成前确认弹窗与「先保存再生成」保持不变。
  `StylePanel` 的档案状态改为 `dirty` 时显示「待保存」并新增「最近保存」行。
  未加 `watch` 自动保存。验证：`typecheck` / `build` 通过 + 22 条 SSR 行为断言全过。
- Phase 2 · D2.1（后端双 Token）：新增 `refresh_tokens` 表与迁移 `20261103_0006`
  （`token_hash` 唯一、`user_id` FK `ON DELETE CASCADE`、`downgrade` 为 `drop_table`）；
  `security.py` 新增 `create_refresh_token` / `token_hash(SHA-256)` /
  `refresh_token_expires_at`，`decode_token` 增加 `expected_type` 防错配；
  新增 `app/repositories/refresh_token_repository.py`（`issue` / `get_by_hash` /
  `rotate` 单事务 / `revoke`）；`AuthService.login` 发双 Token 并落哈希，
  `refresh` 走四道闸门后轮换，`logout` 幂等撤销；新增 `POST /auth/refresh`、
  `POST /auth/logout`（204），错误统一落 T6 模型。
  **实现中踩到一个真 bug**：JWT 的 `iat`/`exp` 精度只到秒，同一秒内为同一用户签发两次
  会得到逐字节相同的令牌，`token_hash` 撞唯一索引 → 登录 500。已在载荷里加随机 `jti`
  修复，并补回归用例 `test_login_twice_issues_distinct_refresh_tokens`。
  验证：`uv run pytest -q` 69 passed；`ruff` / `mypy`(32 文件) / `lint-imports`(5/5) 全绿；
  迁移在测试库跑通 `upgrade head` / `downgrade base`；手工冒烟（登录→刷新→复用→登出）见
  本轮记录；未改前端、未 commit。
- Phase 3 ·  DeepSeek BYOK（ADR-009）：新增 `app/integrations/copy_writer.py`
  （`deepseek-flash`、非流式、`thinking=disabled`、`max_retries=0`、30 秒硬超时），
  `openai` SDK 进依赖；Key 走 `X-DeepSeek-Key` 请求头，服务端不落库、不写日志
  （`main.py` 按库压级 + 三个专用异常处理器给出 `AI_KEY_MISSING` / `AI_KEY_INVALID` /
  `AI_SERVICE_UNAVAILABLE`）；前端顶栏「AI 设置」把 Key 存进 `sessionStorage`。
  验证：`tests/test_resume_ai_copywriting.py` 16 例（`httpx2.MockTransport`，
  因 pytest-httpx 拦不住 openai 3.x 的 `httpx2`，实测会真出网）+ 前端 `byok.spec.ts` 7 例。
- Phase 3.1（AI 润色与生成解耦，ADR-010）：`POST /resumes/generate` 退回**纯模板**
  （`ResumeGenerateRequest` 删掉 `use_fallback`，服务层不再调 writer），新增
  `POST /resumes/polish`——收前端草稿态、四档 System Prompt（`build_system_prompt(mode, max_words)`）、
  **不读库也不写库**、必须登录、`POLISH_RATE_LIMIT = "10/minute"`；
  `_apply_copy` 改名 `_sanitize_copy` 并新增第 5 条规则：`expand` 的字数上限由服务端截断
  （句末优先），不再「相信模型」。前端拆成两个按钮 + Element Plus 两个弹窗
  （`PolishSettingsDialog.vue` 选档位与字数、`PolishReviewDialog.vue` 逐段对照，
  只有「接受并同步到表单」会写 Pinia `draft` 并置 `dirty`，落库仍要用户手动点保存）。
  验证：`uv run pytest -q` 213 passed（润色 44 例 + 生成 22 例 + 限流 6 例）；
  前端 `npm run test` 26 passed（新增 `resume-polish.spec.ts` 11 例）、`typecheck` / `build` 通过；
  `ruff` / `mypy`(34 文件) / `lint-imports`(5/5) 全绿。未新增表、未新增依赖、未 commit。

## 7. 本轮未做（显式声明）

- T7.7 未新增或修改任何数据库表、未新增迁移（本轮只删路由、清文件、去重、改配置、加限流）。
- T7.7 未改动任何业务规则：档案/简历的字段校验、生成器版式、前端组件行为均保持原样。
- 未新增简历列表 / 详情 / 编辑接口，`resumes` 表仍只有写入路径。
- 未修改 `app/integrations/` 的既有模板逻辑（体验修复轮只改了日期格式与区块顺序）。
- 档案写入的边界（技能 0–8、经历 0–3、教育 0–5，保存不拦必填）沿用 T5/T7 契约，未再调整。
- D2.1 未改前端任何文件（`frontend/` 下一行未动），前端拦截器属 D2.2；
  未实现「同一 refresh token 复用 → 撤销该用户全部令牌」的家族撤销策略（当前只拒绝该次请求）；
  未做 `refresh_tokens` 的过期清理任务；未给 `/auth/refresh`、`/auth/logout` 挂限流（理由见 `app/api/v1/auth.py` 模块注释）。
- D2.1 的 `id` / `user_id` 用 `VARCHAR(36)` 而非设计稿的 `CHAR(36)`：与本库所有主键同型，
  避免外键两侧类型不一致导致的隐式转换与 `CHAR` 补空格问题（见迁移文件说明）。
- Phase 3.1 未新增数据库表、未新增迁移：`/resumes/polish` 是纯计算端点，`app/repositories/`
  与模型层一行未动，DeepSeek Key 依然没有任何持久化落点。
- Phase 3.1 未新增依赖（`openai` 已由 ADR-009 批准），前端只用到仓库里已有的 Element Plus，
  没有引入第二个组件库。
- Phase 3.1 **没有在浏览器里跑过两个新弹窗**：润色需要真实 DeepSeek Key，本轮不持有任何凭据，
  因此弹窗的交互与移动端布局只经过类型检查与构建验证，未做人工点击验收。
- Phase 3.1 未做「润色结果落库」的任何自动路径：接受只写 Pinia 草稿，保存仍由用户点。

## 8. 下一阶段

Phase 1（MVP）核心开发已结束，已创建 `docs/PHASE2_BACKLOG.md` 规划下一阶段迭代。

Phase 2 进度：D1（用户名归一）完成 → D3（显式保存按钮）完成 →
**D2.1（后端双 Token）完成** → 下一步 D2.2（前端 401 拦截器 + 静默续期）。

Phase 3 进度：DeepSeek BYOK 接入（ADR-009）→ **Phase 3.1 生成与润色解耦 + 四档润色（ADR-010）完成**。
下一步是给润色接一个**真实 Key** 做人工验收（延迟、JSON 遵循度、429 形态、两个弹窗在手机上的排版），
再决定要不要把档位做成可配置项。
