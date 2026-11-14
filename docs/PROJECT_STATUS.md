# 项目状态（真实版）

> 本文件描述的是“已实现并验证”的状态，不是“已设计”的状态。
> 任何未附测试输出或手动复现步骤的条目，不得写入“已验证”。
> 最后更新：2026-11-15（Phase 4 已交付至 Beta 就绪；PR #43/#44/#45 = UI 收尾第 20/21/22 轮已合入 master `73b6f06`）
>
> **本文件的分工**：§1–§5 与 §8 是**现状**；§6 与 §7 是**截至 2026-11-04 的逐轮检查日志**，
> 按时点真相保留、不回头改注（那些数字如 `213 passed` 是当时的实测值，不是今天的）。
> 批次编号与开工顺序看 [ROADMAP.md](ROADMAP.md)，口径决策看 `docs/adr/`，界面判据看 [FRONTEND_DESIGN.md](FRONTEND_DESIGN.md)。

## 1. 一句话状态

**产品已经从「一条简历垂直切片」扩成 7 个模块的可用 MVP，且每一条主链路都有集成测试与 CI 守着**：
注册/登录（双 Token + single-flight 静默续期）→ 档案（多值：技能 / 经历 / 教育 / 证书 / 作品链接 / 期望城市）
→ 生成 A4 简历（纯模板、必然成功）→ 纸面就地编辑 + 显式保存 → 版本历史与差异对比 → 按岗位定制；
外围三条支线：岗位库（录入 / 审核 / 回收站 / JD 技能提取 / 人岗匹配 + 逐条要求状态）、
组织与角色（邀请码加入、成员角色调整、审计流水、教师端学情看板 + 批量 ZIP 下载 + PDF 导出）、
能力测评（50 题标准题库 + 画像标签 + 概述生成）与模拟面试（文本回合制 + 录音转写回合制）。

**规模（2026-11-15 实测）**：23 张表 / 28 个 Alembic 修订；后端 `uv run pytest --collect-only -q` = **753 tests collected**；
前端 `npx vitest run` = **450 passed / 39 个 spec 文件**；30 张 ADR；7 个模块、14 个页面视图。
CI 只有一个 `test` job，**在 PR 上跑**（push 触发已删），含 ruff format/check、mypy、lint-imports、pytest、
五道文档与文案闸（G1/G3/G4/G5 + 引用闸）与前端 lint/typecheck/test/build。

**AI 全部走 BYOK、同步、非流式**（`ADR-009` 起）：Key 由用户在「账户 → AI 设置」填入，只活在该标签页的
`sessionStorage` 与请求头里，服务端不落库、不写日志；能带 Key 的端点是 `frontend/src/api/client.ts`
里两张 fail-closed 白名单（同源 5 条 + 语音直连 2 条）。**真实凭据下的端到端效果只有老板本人能验**——
自动化的会话永远走关键词回落分支。

**真实用户门槛已过**：2026-11-07 完成 5 人测试，2026-11-08 老板裁定「10 名用户目标已达标」并
**全面解除 `AGENTS.md` §3.2 的冻结清单**（`ADR-014`）。解除 ≠ 已做：Redis / ARQ / Celery / S3 /
消息队列 / WebRTC / 指标系统 / E2E 至今**一件都没接线**，引入任何一件要先过 §4.6 的 ADR。

## 2. 资产分级

### 2.1 保留复用（已实现，可继续用）

| 资产 | 状态 | 说明 |
|---|---|---|
| 分层目录 `api/services/repositories/integrations` | 已实现 | 依赖方向由 import-linter 5 个 contract 强制，CI 跑 `lint-imports` |
| 外框与导航（`AppLayout.vue` + `ModuleNav.vue` + `AppTopBar.vue`） | 已实现 | 守则 L1「一个布局一处定义」由 `layoutGuards.spec.ts` 钉住；模块入口只有 `config/modules.ts` 一张表（L4） |
| 浮层基座 `OverlayPanel` / `DrawerPanel` | 已实现 | 点外面 / Esc / 焦点归还 / 按形态开关的焦点陷阱都在基座；三个消费者（账户抽屉、简历抽屉、二级导航无遮罩）共用 |
| 健康检查接口 | 已实现 | 有测试 |
| 配置管理骨架 | 已实现 | production fail-fast（`DATABASE_URL` / `JWT_SECRET_KEY`）+ Alembic 版本比对（生产不一致直接拒绝启动）|
| 统一错误模型 + G3 文案表 | 已实现 | `app/core/error_messages.py` 是唯一文案出处，`scripts/check_error_copy.py` 在 CI 里守 |
| 双 Token 会话（后端 + 前端） | 已实现 | D2.2 已落地：`client.ts` 提前 10 秒判过期 + single-flight 刷新 + 重放原请求，会话真相在 `localStorage` |

### 2.2 降级为设计稿（已设计，未实现，禁止当真相源）

| 资产 | 新位置 | 说明 |
|---|---|---|
| 领域模型 SQL | `docs/design/schema-draft.sql` | **非迁移源**（`AGENTS.md` §1.2）；表结构真相在 `alembic/versions/` 的 28 个修订，现库 23 张表 |
| Redis / ARQ / Celery / 消息队列规划 | 文档 | **一行都没写**；`ADR-014` 只是解除了禁令，没有引入任何东西 |
| S3 / 对象存储规划 | 文档 | 未接线；批量导出是同步 ZIP，PDF 由宿主机浏览器渲染（`ADR-029`） |
| WebRTC 音视频房间规划 | 文档 | 未实现。语音面试落的是 `ADR-030` 的**回合制录音 + 直连转写**，`media_rooms` / `interview_reports` 两张表已裁定**不建** |
| SSE 长任务进度推送 | 文档 | 全仓没有一处 `text/event-stream` / `EventSource`，所有 AI 调用同步返回 |
| 指标与可观测性系统 | 文档 | 有 `request_id` 与结构化错误码，**没有 Prometheus/OTel/APM**，「任务积压」这类指标不存在 |
| E2E（Playwright） | 文档 | 无依赖、无脚本；主路径靠集成测试 + headless Edge + CDP 手工实测（守则 R2） |

### 2.3 待补承重（Phase 1 那批 T1–T7 已全部交付，此表转为历史基线记录）

| 项 | 对应任务 | 状态 |
|---|---|---|
| Alembic 基线 + 5 张表迁移 | T4 | 已验证；此后一路扩到 **23 张表 / 28 个修订**，每张新表都有 ADR（`AGENTS.md` §4.2 的口径没变：Phase 1 那 5 张是基线，不是上限） |
| fake 垂直切片 + 集成测试 | T5 | 已验证；T7.2 起被真实仓储取代，`fake_resume_repository.py` 仅留存不再被路由引用 |
| import-linter 架构约束 | T6 | 已验证（5 个 contract 全部通过，在 CI） |
| 配置 fail-fast + `.env.example` | T6 | 已验证 |
| 统一错误模型 + 测试 | T6 | 已验证（含 404/422/405/500），并已扩成 G3 文案表 + 扫描闸 |
| CI 四件套 | T6 | **已在 GitHub Actions 真实运行**（单个 `test` job，跑在 PR 上；见 §3 与 `docs/DEVELOPMENT.md` §10.0） |
| T7.4 前端组件与本地状态流转 | T7 | 已验证（typecheck/build 通过） |
| 注册/登录/JWT 鉴权基建 | T7.1 | 已验证（`tests/test_auth.py`，连 `resume_mvp_test`） |
| 真实档案仓储 + 档案接口 | T7.2 | 已验证（`tests/test_profiles.py`） |
| 简历生成落真实库 | T7.2 | 已验证（`tests/test_resumes_generate.py`） |
| 多段教育经历（`educations` 子表） | T7.5 | 已验证：迁移 `20261102_0002`（upgrade/downgrade 可逆）；生成器按入学时间倒序渲染教育区块 |
| 前后端全链路联调 | T7.3 | **已验证**：登录态（JWT）→ 档案保存 → A4 实时预览 → 生成简历落真实 `resumes` 表 |
| 技术债清理与安全加固 | T7.7 | 已验证：废弃路由 `/me`、`/jobs` 下线（`/jobs` 后被 `ADR-011` 作为真实岗位库重新实现）；`utc_now()` 单点收敛；CORS 白名单化；登录/注册限流并入统一错误模型 |
| 用户名大小写归一（D1） | Phase 2 | 已验证：模型层 + 业务层双重 `strip().lower()`；迁移 `20261103_0005` 用 `BINARY` 比较回填存量 |
| 显式保存按钮（D3） | Phase 2 | 已验证：保存语义合并为预览区工具栏唯一入口（三态） |
| 双 Token + 刷新接口（D2.1 后端 / D2.2 前端） | Phase 2 | 已验证：`refresh_tokens` 表 + 轮换 + 复用拒绝 + 登出撤销；前端拦截器已接（single-flight + 提前续期 + 重放） |

> 下面这几条 T7.x 的逐轮数值（`12 passed` / `14 passed` / `迁移 20261102_0002` 等）是**那一轮的实测**，保留作证据链；
> 全量口径看 §1（后端 753 collected / 前端 450 passed）。

## 3. 已验证 / 未验证

| 项 | 状态 | 证据（2026-11-15） |
|---|---|---|
| 全量测试 | **已验证** | 后端 `uv run pytest --collect-only -q` = 753 tests collected；前端 `npx vitest run` = Test Files 39 passed / Tests 450 passed |
| CI 真实运行 | **已验证** | `.github/workflows/ci.yml` 单个 `test` job 在 PR 上跑；最近一次 PR #45 → `test pass 8m10s`（run 43612941070）。**push 触发已删，所以「没开 PR 就没跑过 CI」这句话现在仍然成立** |
| 数据库迁移可跑通 | **已验证** | `tests/test_migrations.py` 覆盖「空库 → `upgrade head` → `downgrade base`」；28 个修订，多线并行时另有 `alembic merge`  revision |
| 进程可启动 | **已验证** | PR #44 / #45 验收那两轮（2026-11-14/15）实测：`uv run uvicorn app.main:app` + `npm run dev` 起得来，`/api/v1/health` 返回 200，headless Edge + CDP 连上 5173 做完 375 / 1280 / 1440 三档量测。**本轮（11-15 文档刷新）按指令未起服务、未跑测试** |
| 简历主线（注册→档案→生成→预览编辑→保存） | **已验证** | `tests/test_auth.py` / `test_profiles.py` / `test_resumes_generate.py` / `test_resume_versions*.py` 等 + 前端 store/spec 链路；纸面就地编辑 + 显式保存 + 版本差异 + 按岗位定制（`ADR-015`/`ADR-026`） |
| 岗位库 + 审核 + 回收站 + JD 技能提取 + 人岗匹配 | **已验证** | `ADR-011`/`ADR-013`/`ADR-017`/`ADR-024`；`tests/test_jobs.py`、匹配与逐条要求状态端点各有契约测试；`matcher_version` 写法判据钉住 |
| 组织 / 角色 / 邀请 / 审计 / 教师端 | **已验证** | `ADR-021`/`ADR-023`/`ADR-029`；`app/api/v1/teacher_tools.py` 四个端点（看板 / 简历清单 / 批量下载 / 技能确认）+ 跨组织 404 + 审计留痕 |
| 能力测评 + 画像 | **已验证** | `ADR-028`；题库 v2 = 50 题 / 5 技能（v1 的 12 题原样保留）；刷新后恢复进行中作答 |
| 模拟面试（文本 + 语音回合制） | **已验证（自动化层面）** | `ADR-022`/`ADR-030`；`interview_sessions` + `interview_turns` 落库，语音走浏览器录音 → 直连转写 → 立即丢弃音频。**未验证**：真机麦克风权限矩阵（Safari mp4 转换、iOS 行为）与真实凭据下的转写质量 |
| BYOK 凭据链路 | **已验证（结构）/ 未验证（真实往返）** | `byok.spec.ts` 27 例盯 sessionStorage、两张白名单 fail-closed、Key 不进日志；真实 Key 的延迟 / JSON 遵循度 / 429 形态**只有老板本人在浏览器里粘 Key 才能验**，自动化会话永远走关键词回落 |
| 界面与布局（375 / 1280 / 1440） | **已验证（本轮 CDP 实测）** | 第 22 轮 24 项 + 第 21 轮 30 项全过：外框高度实测（`--chrome-h` = 114px @1440）、侧栏展开时「新建简历」「回收站」在视口内（855..890）、导航手动收起 41→25、排序行单行、组织页两表单照面试页规范。**jsdom 不跑媒体查询，这类判据只能真浏览器量**（守则 R2） |
| 规模化真实用户 | **已过门槛** | 2026-11-07 完成 5 人测试（数据在另一库归档），2026-11-08 老板裁定 10 人达标 → `ADR-014` 全面解冻冻结清单 |
| E2E / 指标 / 对象存储 / 队列 / WebRTC | **未实现** | 见 §2.2，别在任何交付说明里写成已通过 |

## 4. 当前阻塞项与已知局限

按「会不会咬人」排序，不是按发现顺序：

1. **限流是进程内内存计数**（`app/core/rate_limit.py:11-12` 自己写明）：多 worker 部署时每个 worker 各算一份，
   5 次/分钟会变成 5×N 次。换 Redis 存储要等真上多进程，且那是 `AGENTS.md` §4.6 的 ADR 事件。
2. **`refresh_tokens` 没有清理任务**：过期记录只累积（每条登录会话一行，7 天后失效），
   需要「定时删」或「按用户保留最近 N 条」的策略——没有队列，所以这件事现在只能靠人跑 SQL。
3. **PDF 的真实几何验收依赖宿主机浏览器**：CI 用假 renderer 测契约，线上/本机必须真有 Chromium 或 Edge
   （老板 2026-11-14 已裁定接受这条限制，见 ROADMAP §7 第 19 轮）。
4. **Docker 那套件从未构建过**（`ADR-008`，每个文件头都有 `NOT VERIFIED`）：它不是可用部署路径，
   日常只有 `start.bat`；第一次真跑通 `build` + `up` 之后要回来把 ADR-008 改成「已验证」。
5. **T1 存量裸字号 / 裸色值的批量替换没做完**（守则 T1 的违例面）：G4 已进 CI，但它只拦**新增**，
   存量替换需要一次专门批次 + 视觉回归，否则「零像素变化」的承诺守不住。
6. **无 E2E**：主路径靠集成测试 + 手工 CDP 实测。这条在 `TESTING.md` 里仍是验收基线。

> 历史阻塞项里这几条**已经不再是阻塞**，别照旧清单再列一遍：`resumes` 只有写入路径（现在有
> `/current`、`/versions`、`/versions/diff`、`/customize` 与档案列表 + 回收站）；纸面就地编辑不写回
> （已走「保存修改」闭环）；CI 未在 GitHub 运行（已在 PR 上跑绿）；D2.2 前端拦截器未接（已接）。

## 5. 下一步

**不再按 `AGENTS.md` §8 的 T1→T7 顺序执行了**——那七条已全部交付并验证。当前下一步与批次顺序的唯一来源是
[docs/ROADMAP.md](docs/ROADMAP.md)（§1 批次表、§4 建议顺序、§7 已入账裁定）。

## 6. 本轮检查记录

> **这一节与 §7 是历史日志，时点截至 2026-11-04（Phase 3.1）。** 里面的用例数（`31 passed` / `69 passed` /
> `213 passed` / 前端 `26 passed`）与「未 commit」都是**那一轮**的实测与状态，按 `AGENTS.md` 的口径
> 不回头补注、不就地更新——今天的口径看 §1 与 §3。往后的逐轮记录写在 [ROADMAP.md](ROADMAP.md) §7。

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

**Phase 2 → Phase 4 的账已经各自结在 ADR 与 ROADMAP 里，这一节只说「接下来干什么」。**

已完成（按批次，编号权威在 `docs/ROADMAP.md` §1）：Phase 2 的 D1/D2.1/D2.2/D3 → Phase 3 的 BYOK 润色与生成解耦（`ADR-009`/`ADR-010`）→ Phase 4 的 B1–B11（岗位库、岗位审核、组织与角色、邀请码、多值档案与版本指针、技能别名层、过期岗位可见性、简历版本对比与按岗位定制、技能元数据、标准题库测评与画像、教师端看板与批量导出、文本面试、回合制语音面试）+ 前端轨 F1a–F7 与 UI 收尾第 20/21/22 轮（PR #43/#44/#45）。「Beta 就绪」这句话已经写在 ROADMAP 里，它的含义是**可以给真人用**，不是「上线就不用管」。

接下来三件事，按依赖顺序：

1. **把 Beta 交给真人用一轮**：`start.bat` 起得来就能投。先收「能不能走完一条主路径」的反馈，而不是继续加功能。UI 收尾那三批就是被真实用户实测打回来才修准的——侧栏那 47px 溢出、展开后收不回去、组织页排布不像其他页面，都不是我们自己想出来的。
2. **部署与运维文档补真**：Docker 那套要么真跑一次、把 `ADR-008` 改成「已验证」，要么在部署说明里明确「不要用」；生产 runbook 里 Redis / 对象存储 / Worker 这三件不存在的东西已经从步骤里删掉（`docs/DEVELOPMENT.md` §11）。**多 worker 一上，§4 第 1 条限流那条会立刻咬人**。
3. **还挂着的工程欠账**：T1 存量裸字号 / 裸色值的批量替换（G4 只拦新增）、`refresh_tokens` 的清理策略、E2E 主路径、PDF 真实几何验收依赖宿主机浏览器、Safari / iOS 录音兼容矩阵。

**明确不在射程内**（除非老板重新拍）：小程序 / 原生 App（`ADR-001` 定响应式 Web）、WebRTC 音视频房间（`ADR-030` 已裁定不建 `media_rooms`）、任何后台任务框架（当前所有链路同步够用，引入要先过 `AGENTS.md` §4.6）。
