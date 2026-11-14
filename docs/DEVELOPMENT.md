# 开发与架构文档

> **读法**：本文写的是**架构意图**。今天真的跑起来的是什么，看下面两张「现状」列与
> `docs/PROJECT_STATUS.md`；两者不一致时，**以代码 + `docs/adr/` 为准**，本文的右列就是用来防止有人把设计目标读成已交付（`AGENTS.md` §1、§7.9 同一条理由）。

## 1. 技术基线

| 层 | 选型（设计） | 现状（代码里可验证，2026-11-15） |
|---|---|---|
| 前端 | Vue 3、TypeScript、Vite、Element Plus、Pinia、Vue Router | ✅ 全部在用；单文件组件在 `frontend/src/`，**没有 `layouts/` 与 `styles/` 目录**（外框是 `components/AppLayout.vue`，令牌在 `src/styles.css`） |
| 后端 | Python 3.13、uv、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic | ✅ 全部在用；包根是仓库根的 `app/`，**不是 `backend/app/`** |
| 数据库 | MySQL 8.0+，字符集 `utf8mb4` | ✅ `resume_mvp_dev` / `resume_mvp_test`，23 张表 / 28 个 Alembic 修订 |
| 异步任务 | Redis + ARQ/Celery（二选一，MVP 推荐 ARQ） | ❌ **一行都没写**：`pyproject.toml` 里没有 redis/arq/celery/boto3，所有链路同步 + 单库 + 应用时钟。引入任何一件要先过 `AGENTS.md` §4.6 的 ADR |
| AI | 后端 DeepSeek API 适配器，结构化输出与提示词版本化 | ⚠️ 形态改了：**BYOK**（`ADR-009`）——Key 由用户逐请求携带（`X-DeepSeek-Key`），非流式，模型 `deepseek-flash`；服务端配置里那个 Key 不再被读取 |
| 文件 | S3 兼容对象存储；数据库只保存元数据 | ❌ 未接线。批量导出是同步生成的 ZIP 直接回给浏览器（`ADR-029`），PDF 由宿主机浏览器渲染 |
| 部署 | Docker Compose + Nginx/Caddy + HTTPS | ⚠️ Docker 件已入库但**从未构建过**（`ADR-008`，每个文件头都有 `NOT VERIFIED`）；日常用 `start.bat` |

## 2. 总体架构

**设计目标**：浏览器 → 反向代理 → Vue 静态资源 / FastAPI API → 领域服务 → MySQL、Redis、对象存储、DeepSeek、媒体适配器。

**今天实际的那条线**：浏览器 → Vite dev（生产是静态资源 + 反代）→ FastAPI → services → repositories → **MySQL 一个库**；AI 那一支由浏览器把用户的 Key 交给后端（或语音直连 `api.deepseek.com`），**没有队列、没有对象存储、没有媒体服务**。

前后端通过 JSON REST API 通信；**长任务用轮询还是 SSE 这条至今没有兑现**——全仓没有一处 `text/event-stream` 或 `EventSource`，所有 AI 调用都是同步非流式（`ADR-009` 的裁定理由：同步链路少一个失败面，且用户能立刻看到失败）。面试语音也不是 WebRTC：`ADR-030` 定的是**回合制**——浏览器 `MediaRecorder` 录音 → 直连转写 → 落一条 `interview_turn`，音频不上传、转写完毕即删。

## 3. 推荐目录

**下面是设计推荐的结构**，与实际落地的差异列在每行右侧；实际树以仓库为准。

```text
backend/                     ← 实际是仓库根：app/ 与 tests/ 平级，没有 backend/ 这一层
  app/
    api/              ✅ 存在（v1 路由与依赖）
    core/             ✅ 存在（`config` 配置、`security`/`permissions`/`rate_limit`、`errors`/`error_messages`
                          = G3 那张错误文案表、`responses` 统一信封、`action_gate` 组织与权限前置、
                          `document_style` 主题与排版、`deadline`、`skill_names`、`datetime_display`、
                          `assessment_rules`、`interview_modes`、`teacher_tools_rules`、`pdf_errors`）
                          ——**没有独立的 logging 模块**，日志配置在 `app/main.py`
    db/               ❌ 不存在——设计里给的那一层没有落地：`session_scope` 在
                          `app/repositories/user_repository.py`，SQLAlchemy 模型与各仓储同文件
                          （`app/repositories/*.py` 里 `__tablename__` 共 23 处），没有 `models/` 目录
    schemas/          ✅ 存在（Pydantic 请求/响应）
    repositories/     ✅ 存在
    services/         ✅ 存在
    integrations/     ✅ 存在，但只有 7 个模块：`copy_writer`（润色 BYOK 出口）、`skill_extractor`、
                          `evidence_scorer`、`interview_coach`、`profile_summarizer`、
                          `resume_generator`（模板）、`pdf_renderer`（调宿主机浏览器，不是对象存储）。
                          **没有对象存储适配器、没有邮件、没有媒体适配器**
    workers/          ❌ 不存在（异步任务未接线，见 §1）
  tests/              ✅ 753 条 collected（含 scripts/tests 的闸门自测）
frontend/
  src/
    api/              ✅ 按模块封装 + client.ts（BYOK 两张白名单在这里）
    components/       ✅ 跨页面组件，**外框 `AppLayout.vue` 也在这一层**（不是 layouts/）
    layouts/          ❌ 不存在，守则 L1 用路由嵌套把这件事收进 `AppLayout.vue` 一处
    views/            ✅ 14 个页面
    stores/           ✅ Pinia
    router/           ✅ 嵌套路由 + 401 出口唯一那处（守则 S4）
    composables/      ✅ `useViewport.ts` 是全站唯一发宽度 `matchMedia` 的地方（守则 L2）
    config/           ✅ `modules.ts` 是模块入口唯一那张表（守则 L4）
    styles.css        ← 设计里写的 styles/ 目录没有，令牌与全局样式集中在这一个文件
docs/               ✅ 另有 adr/、design/，以及本文 + ROADMAP/FRONTEND_DESIGN/MVP_SCOPE 等
```

## 4. 后端分层规则

1. `api` 只负责协议、鉴权、参数校验和状态码。
2. `services` 编排业务，不直接读取 HTTP 对象。
3. `repositories` 封装查询和事务边界。
4. `integrations` 统一供应商调用，禁止在业务代码中散落 API Key 或 HTTP 请求。
5. AI 和异步任务必须有任务 ID、状态、错误码、重试次数和幂等键。
6. 所有外部输入通过 Pydantic 校验；数据库查询使用 ORM 参数绑定。

## 5. 核心数据模型

**库里现在真的有的 23 张表**（`grep __tablename__ app/repositories/*.py`，2026-11-15 实测；真相以 `alembic/versions/` 的 28 个修订为准，`docs/design/schema-draft.sql` 是设计草案、**不是迁移源**——`AGENTS.md` §1.2）：

| 组 | 表 |
|---|---|
| 账号与会话 | `users`、`refresh_tokens` |
| 档案与内容 | `student_profiles`、`skills`、`experiences`、`educations`、`profile_certificates`、`portfolio_links`、`profile_city_preferences` |
| 简历与版本 | `resumes`（版本用同表自引用表达：`version_no` + `current_version_id` + `is_head`，另带 `origin` 与 `target_job_id`，`ADR-015`/`ADR-026`）、`profile_tags` |
| 岗位库 | `jobs`、`job_skills`、`skill_aliases` |
| 组织与授权 | `organizations`、`organization_members`、`organization_invitations`、`audit_logs` |
| 测评与画像 | `assessment_questions`（参考数据，由迁移 `0026` 种子建出）、`assessment_sessions`、`assessment_answers` |
| 面试 | `interview_sessions`、`interview_turns` |

**设计里写了、今天没有建的**：`memberships`/`roles`（角色落在 `organization_members.role` 与 `users` 的权限推导上）、`companies`/`job_posts`/`job_skill_requirements`（被 `jobs`/`job_skills` 取代）、`evidence_items`（**没有这张表**：可回溯证据是 `job_skills.evidence_text` 这一列，配合 `origin` 由服务层按 `ADR-011` 1.D 派生）、`assessments`/`assessment_attempts`（被 `assessment_sessions`/`assessment_answers` 取代）、`match_runs`/`match_results`、`growth_plans`/`growth_tasks`、`resume_versions`/`resume_shares`（版本用 `resumes` 的自引用 + `deleted_at` 表达）、`media_rooms`/`interview_reports`（`ADR-030` 明确**不建**，语音落 `interview_turns`）、`ai_jobs`/`ai_prompt_versions`/`consents`/`files`。

所有业务表包含 `id`、`created_at`、`updated_at`；软删除对象增加 `deleted_at`。**「敏感文本与文件走对象存储私有桶 + 短时签名 URL」这一条还没落地**——今天没有对象存储适配器，简历导出是同步 ZIP/PDF 直接回浏览器（`ADR-029`）。

## 6. AI 集成方案

### 6.1 调用原则

- ~~DeepSeek Key 只存在后端环境变量或密钥管理服务。~~ → **已由 `ADR-009` 改为 BYOK（2026-11-04）**：Key 由用户自己在「账户 → AI 设置」里填入，只活在浏览器 `sessionStorage`（关标签页即清）与请求头里；服务端**不落库、不写日志、不进异常消息**，`.env` 的 `DEEPSEEK_API_KEY` 不再被任何代码读取。能带 Key 的端点由 `frontend/src/api/client.ts` 的两张 fail-closed 白名单决定（`BYOK_KEY_PATHS` 2 条 + `BYOK_KEY_PATTERNS` 3 条走我方域名；`DEEPSEEK_DIRECT_PATHS` 2 条语音直连 `api.deepseek.com`）。**新增一条 URL 需要老板逐条书面同意**（`AGENTS.md` §3.2 末行）。
- 设置连接超时、读取超时、最大 token、并发上限和重试退避；不对 4xx 无限重试。⚠️ **现状**：只有超时与最大 token 落地（同步非流式，30 秒预算）；**没有并发上限与重试退避**——那两件属于 §1 里没接线的队列那一栏。
- 对岗位解析、简历生成、面试反馈使用不同提示词版本和 Pydantic 输出模型。✅ 已兑现：`skill_extractor` / `copy_writer` / `interview_coach` / `profile_summarizer` / `evidence_scorer` 各自一个模块，输出走 Pydantic 校验；**提示词版本化没有独立表**（`ai_prompt_versions` 未建），版本写在代码常量里。
- 输入前做长度限制、敏感信息处理和提示注入隔离；模型输出后做 JSON 校验、事实引用检查和内容安全检查。✅ 长度限制与注入隔离在 `ADR-011`/`ADR-019` 里落过；事实引用检查落成了「可回溯原文」标记（`job_skills.origin` + `evidence_text`），不是另建一张证据表。
- 记录 `provider`、模型名、prompt 版本、耗时、token 用量和结果状态，不记录完整密钥。⚠️ **现状只到「不记密钥」这一半**：没有用量/耗时台账表，排障看的是应用日志与 `request_id`。

### 6.2 AI 任务状态

`queued → running → succeeded | failed | cancelled`——**这是设计目标形态，今天没有这套状态机**。没有队列、没有 worker、没有任务表，所有 AI 调用都在**同一个 HTTP 请求内同步完成**并直接返回结果，所以界面上不存在「排队中」这种状态。幂等键那一条同样未落地。把它当现状读会写出等不到回调的前端代码，这是 `AGENTS.md` §7.9 禁的那类描述。

### 6.3 降级策略

- AI 不可用：保留岗位和档案基础 CRUD，提示稍后重试。✅ 已兑现，且遵守「绝不静默降级」（`ADR-018`/守则 C4）：润色失败只提示重试、不动表单；概述没填凭据时回落模板文字**并在界面上说明是哪一种**。
- AI 输出无法解析：保存原始响应的脱敏诊断摘要，进入重试/人工处理，不返回伪造成功。⚠️ 现状是**当场报错 + 原始串留在 `details.reason`**（批次 F1c 之后用户读的那句来自 G3 文案表），没有「人工处理」队列。
- 超过额度：返回明确的限流错误和预计重试时间。⚠️ **一半落地**：限流在 `app/core/rate_limit.py`，429 带 `Retry-After: 60` 响应头（`app/main.py:270`，因为规则是「每分钟 N 次」），用户读的那句是 G3 表里的 `RATE_LIMITED`——**具体秒数只在响应头里，没上屏**。

### 6.4 证据检索与事实核验

对于岗位分析、简历生成、面试反馈，采用受权限控制的轻量 RAG 流程：

1. 按 `organization_id`、对象授权和数据新鲜度过滤可检索记录；
2. 对岗位要求、学生证据和已确认简历区块建立可追踪片段；
3. 生成时要求每个关键结论引用 `source_ids`；
4. 将输出拆成原子事实，执行支持/不支持/待复核判定；
5. 未达到支持率阈值或出现关键未支持事实时转人工复核。

MVP 不引入未经验证的全网爬虫；岗位变化雷达只使用合法来源和已审核数据。检索结果必须带组织和授权过滤，防止跨租户数据泄露。

### 6.5 公平性与模型评估

- 建立冻结的离线评估集，覆盖不同年级、专业方向和样本来源；敏感属性只在经过授权的公平性评估环境中使用，生产排序不使用。
- 每次模型、提示词、权重或检索器变更都运行回归集，并比较事实支持率、拒答率、人工采纳率、推荐稳定性和群体差异。
- 公平性指标样本不足时不得输出精确结论；由治理负责人记录“不评估”的原因。
- AI 结果包含模型卡字段：用途、禁用用途、输入、输出、已知限制、评估日期、责任人和回滚版本。

## 7. 前端统一设计规范

> **这一节已被 [docs/FRONTEND_DESIGN.md](docs/FRONTEND_DESIGN.md) 取代**——那份是带判据与闸门的守则（C/V/M/L/F/S/T/R 八族 + G1…G6），这一节只留大原则。两者冲突以守则为准。

- 统一使用 Element Plus，禁止同一业务功能引入第二套组件库。✅（Element 只在润色对照、对话框这几处；自绘基座 `OverlayPanel`/`DrawerPanel` 是浮层那一支的唯一实现，守则 L3）
- 白色背景、浅灰分区、深色正文、单一主色；成功/警告/危险颜色全局 token 化。✅（`--ui-*` 与 `--theme-*` 两套分工见守则 T1；**存量裸字号/裸色值的批量替换仍未做完**，G4 已进 CI 但只拦新增）
- 表格、筛选器、空状态、加载态、错误态、确认弹窗抽为共享组件。✅（`SkeletonList`、`ConfirmDialog` + `stores/dialog.ts`、`AutoTextarea` 是全仓唯一 `<textarea>`）
- ~~所有页面响应式：桌面侧栏布局，移动端底部导航/抽屉~~ → **现状不是这样**：375px 到 1440px 同一套响应式 Web（`ADR-001`），**没有底部导航**。顶部是一整块 sticky 外框（品牌顶栏 + 二级导航，二级导航可手动收起、不随滚动消失）；≥1440 才有左侧简历栏且**默认收起**，1100–1439 与窄屏的列表走右侧抽屉。操作顺序与文案一致由 `AppLayout.vue` 一处保证（守则 L1）。
- AI 生成内容必须标注「AI 建议」，并提供编辑、接受、拒绝入口。✅（润色走逐条对照弹窗，接受/拒绝都在；概述生成会说明是 AI 写的还是模板整理的）
- 颜色之外同时使用文字、图标或形状表达状态。✅（SVG 图标 + 文字，禁 emoji/伪图标是守则 C1，G1 扫描）

## 8. 配置与密钥

复制 `.env.example` 为本地 `.env`，生产环境由部署平台注入，不提交 `.env`。

**启动时真的会拦的只有两项**（`app/core/config.py`，2026-11-15 核对）：`ENVIRONMENT=production` 时缺 `DATABASE_URL` 直接 `ValueError`；缺 `JWT_SECRET_KEY` 或短于 32 位同样直接失败，开发期才回落那个显式命名的占位密钥。**~~和 `DEEPSEEK_API_KEY`~~ 这条已经不成立**——BYOK 之后没有任何代码读它（`app/integrations/copy_writer.py:143` 就写着），把它列进必换清单会让人以为换掉它就换了 AI 凭据。另有一条独立检查：`app/main.py` 的 lifespan 比对数据库 Alembic 版本与代码 head，不一致时开发/测试打印告警、**生产直接拒绝启动**（`DatabaseSchemaOutOfDateError`）。

## 9. 安全设计

- 密码使用 Argon2id；JWT access token 短时有效，refresh token 轮换并可撤销。
- CORS 只允许配置的前端域名；生产关闭调试文档或限制到内网。
- 所有租户查询带组织条件；管理员操作二次确认。
- 上传限制扩展名、MIME、大小和病毒扫描；下载使用短时签名 URL。
- 日志脱敏：禁止记录密码、token、API Key、完整简历和面试音视频。
- 速率限制登录、AI、上传、导出和候选人批量接口。
- 依赖锁定、定期更新、数据库备份加密和恢复演练。
- 对象级授权测试覆盖所有带 ID 的端点；远程 URL 抓取必须使用 allowlist、禁止内网地址和重定向绕过，防止 SSRF。
- API 按用户、组织、端点和成本设置限流；AI、导出、媒体房间创建设置更严格的配额。
- 建立 API inventory，标明版本、环境、认证要求、数据级别和下线日期，避免旧接口暴露。

## 10. 测试与质量门禁

> 本节多数条目是**验收基线**，不是现状。已落地的检查**以 `.github/workflows/ci.yml` 里 `test` job 的
> 步骤列表为准**（只有一个 job，后端与前端步骤同属它），本文不抄清单——抄一次就烂一次。
> 未落地的（E2E、依赖漏洞扫描、AI 质量指标、WebRTC 用例）在这里只表示「要到哪里去」，
> 不得在交付说明里被当成已通过。

### 10.0 分支与合入约定（2026-11-07 起生效）

- `master` 是 trunk，**只接受 CI 绿的合入**，不再直接提交。
- 一条切片一条分支，命名 `<type>/<slice>`：`feat/`（业务切片）、`fix/`（缺陷）、`chore/`（工具链与闸门）、
  `docs/`（纯文档）。分支只活到它描述的那件事做完为止，不长期养分支。
- 合入路径是 PR：CI 在 PR 上跑绿才合。此前所有历史都是 `master` 直推，PR 上从未跑过 CI，
  所以需求池出口标准「CI 在 PR 上真实绿过」当时是不可满足的；本约定是让它第一次可满足。
- ADR 与实现的先后：ADR 先合，或作为同一 PR 的第一个提交；实现提交不得跑在决策提交之前。
- **合并后立即删分支**：PR 合完就删远端分支（合并按钮勾选删除，或 `gh pr close --delete-branch`），
  本地分支在切回 `master` 后删。理由不是整洁：一条已经合入的分支留着，下一次开工时很容易在它上面
  继续提交，于是「一条切片一条分支」变成「一条切片两条历史」。
  实测：2026-11-07 清理后远端只剩 `master` 一条分支（`gh api repos/…/branches` → `["master"]`）。
  **这一条不是机器强制的**：仓库设置 `delete_branch_on_merge` 实测为 `false`，删分支仍是一次人动作。
  要变成自动的，把它设成 `true` 即可（一条 PATCH，改不改都不影响下面的闸门）。
- 「只接受 CI 绿的 PR 合入」这半条现在是**机器强制**的：master 由 ruleset `26754995`（`enforcement: active`，
  作用于 `~DEFAULT_BRANCH`）保护，规则为 `pull_request` + `deletion` + `required_status_checks`，
  逐字段读回值：`required_approving_review_count: 0`、`allowed_merge_methods: ["merge"]`（只允许 merge commit，
  保留「ADR 提交在前、实现在后」的顺序）、状态检查 context `test`、`strict_required_status_checks_policy: true`
  （合入前必须先 rebase/merge 到最新 master，防「两个各自绿的 PR 合出红的 master」）、分支不可删。
  仓库级 `allow_squash_merge` / `allow_rebase_merge` 实测仍为 `true`，但对 master 无效——ruleset 的
  `allowed_merge_methods` 覆盖面更窄的那一条说了算；其它分支不受约束，所以「在 feature 分支上随便 squash」是允许的。
  **一个必须知道的口子**：`bypass_actors` 里有一条 `actor_id: 5 / actor_type: RepositoryRole / bypass_mode: always`，
  即仓库管理员可以无条件绕过全部规则，`current_user_can_bypass: "always"`。所以闸门对非管理员是硬的，
  对管理员只是默认路径——管理员直推 `master` 仍然物理可行，禁止它靠本节，而不是靠 GitHub。
  没把它关掉的原因：需要保留一次紧急回滚通道。若要改成「管理员也要走 PR」，把该 actor 的 `bypass_mode`
  改为 `never` 或直接删掉 `bypass_actors`，改前先想清楚应急路径。
- **闸门曾经把正常合入也挡死了，原因已定位（2026-11-07）**。现象：两条 `test` 检查都 `pass`、
  `required_approving_review_count: 0`，但 PR 页面显示
  「Merging is blocked — **Cannot update this protected ref.**」，`mergeable_state` 恒为 `blocked`，
  而管理员侧的按钮变成「Bypass rules and merge」。
  凶手是我一开始就加进去的那条 **`update` 规则**（UI 里的「Restrict updates」）。
  它挡的不是「不满足条件的合入」，而是**对 master 这个 ref 的一切更新**——PR 合并本身就是一次 ref 更新，
  于是它把自己的闸门也一并挡了，只剩管理员绕过这一条路可走。
  取证过程（两次 PATCH + 一次读回，都是同一条 ruleset）：
  1. `update` 带 `allow_force_pushes` / `allow_deletions` → `422 Unexpected parameter`：这两个参数不属于 `update`，
     说明这条规则在本 API 版本里只管「更新」这一件事；
  2. `update` 带 `{"update_allows_fetch_and_merge": true}` → 保存成功，但 `mergeable_state` 仍 `blocked`：不是这个参数；
  3. 删掉 `update` 规则 → 读回 `rules: ["pull_request","deletion","required_status_checks"]`，
     `mergeable_state` 立刻变 **`clean`**。
  所以最终配置**不含 `update` 规则**。少掉的保护由谁补：
  「不许直推 master」这件事由 `pull_request` 规则负责（它的语义就是「所有进入该分支的提交必须来自 PR」，
  直推与 force push 都属于被它挡住的那类更新）；「不许删分支」由 `deletion` 规则负责。
  我没有用一次真实的直推去证明这一点（那需要往 master 上推一个测试提交，成功即违规），
  这一句是按 GitHub 该规则的语义写的，与上面三条实测区分开。
- **由此得到的纪律**：闸门配完必须**实测一次「正常合入能不能走通」**，只读回字段不算验证。
  上面那段就是教训：我读回了 `enforcement: active` 与四个规则名，便写下「现在是机器强制的」，
  而它强制的结果是「谁也合不进去，除了绕过的人」。
  判据：`gh api repos/…/pulls/{n} --jq '.mergeable_state'` 必须是 `clean`，不是 `blocked`。
- **这条纪律已经在 PR #11 上执行过一次（2026-11-07）**：两条 `test` 检查 `pass`（4m0s / 3m47s）之后读回
  `{"mergeable": true, "mergeable_state": "clean"}`，也就是闸门不再挡正常合入路径。
  还差的一件事不是忘了，是做不了：**由非管理员真的点一次合入**。这个 PR 的作者就是我，
  GitHub 的 self-approval 策略会挡住我自己合，而绕过按钮在管理员侧总是可用——
  所以「clean」是目前能拿到的最强证据，最后那一次点击仍然属于人类负责人：
  如果 PR 页面上没有出现「Bypass rules and merge」那个红按钮，就说明闸门对正常路径是通的。
- **不做**长期 `develop` 分支，**不做** `release`/版本号分支：服务尚未公网部署，
  现在造版本号是给不存在的发布流程做兼容。真到部署那步，按 §4.6 另出 ADR。

下面 10.1 里「E2E：注册→画像→岗位→匹配→简历→面试」那条提到的**匹配**，
后端与前端卡都已按 `docs/adr/ADR-013-job-matching-and-evidence-scoring.md` 落地（同步、单岗位、纯计算不落库）；
其中**批量匹配与教师端候选人视图仍是 AGENTS.md §3.2 的冻结项**（见 `docs/MVP_SCOPE.md`）。

### 10.2 375px / 1280px 的实测方法（AGENTS.md §3.1 要求的那一步）

本机既没有 Docker 也没有 Playwright/Puppeteer，而 browser-use 那套 MCP **没有 resize 工具**
（`window.resizeTo()` 对受控页面无效：`innerWidth` 不变、`outerWidth` 报 0），
所以视口测量走「headless Edge + CDP」这条零安装的路子：

1. 起两个进程：`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` 与 `frontend` 下 `npm run dev`；
2. `msedge --headless=new --remote-debugging-port=<端口> --user-data-dir=<临时目录>`；
3. Node（≥21 有原生 `WebSocket`，不必装任何驱动库）连浏览器级 WS，
   `Target.createTarget` + `Target.attachToTarget{flatten:true}` 拿到 `sessionId`；
4. 视口用 `Emulation.setDeviceMetricsOverride{width,height,mobile}` 切，
   整页图用 `Page.captureScreenshot{captureBeyondViewport:true}`；
5. 交互一律用 `Runtime.evaluate` 里的**真实 DOM 操作**：
   `input.value = …` 之后必须 `dispatchEvent(new Event('input',{bubbles:true}))` 才进得了 `v-model`，
   点按钮用 `el.click()`——MCP 那种 `uid` 式点击不触发 Vue 的事件处理器；
6. 「有没有自动发请求」这类断言用 `Network.requestWillBeSent` 事件数，而不是读界面。
   **每一轮测量前要把事件缓冲清空**：它是跨视口累计的，不清就会把上一轮那一次请求
   读成本轮「点按钮之前就发过一次」，看起来像自动调用。

判据（本轮 2026-11-07 对 `POST /jobs/{id}/match` 那张卡量的）：
`document.documentElement.scrollWidth <= innerWidth`（无横向滚动条）、
匹配卡内没有任何元素的 `right > innerWidth + 1` 或 `left < -1`、
关键元素 `getBoundingClientRect().top` 落在首屏内、且点击前匹配请求数为 0。
实测结果：1280×900 与 375×780 两个视口下 `scrollWidth` 分别 1265 / 375（都不溢出），
按钮距顶 497px / 694px（都在首屏内），点击前 0 次、点击后 1 次匹配请求。

### 10.1 质量模型条目（验收基线，非现状）

- 单元测试：权限、匹配公式、简历事实约束、AI 输出解析、状态机。
- 集成测试：MySQL、鉴权、组织隔离、文件签名、异步任务。
- API 契约测试：OpenAPI 响应结构、错误码和分页。
- 前端测试：共享组件、表单校验、移动端关键流程。
- E2E：注册→画像→岗位→匹配→简历→面试的主路径。
- CI：格式化、lint、类型检查、测试、迁移检查和依赖漏洞扫描。
- AI 质量：证据支持率、关键事实错误率、拒答正确率、提示注入成功率、敏感信息泄露率、输出延迟和成本。
- WebRTC：权限拒绝、ICE gathering/connection 状态、断网恢复、设备切换、媒体授权撤回和房间过期。
- 质量模型按 ISO/IEC 25010:2023 的产品质量模型组织：功能适合性、性能效率、兼容性、可用性、可靠性、安全性、可维护性、灵活性和安全/隐私相关质量目标，最终指标以项目验收基线为准。

## 11. 本地运行与部署

### 11.1 数据库迁移（每次拉取代码后必做）

**⚠️ 拉取最新代码后，启动后端前必须执行 `uv run alembic upgrade head` 同步数据库。**

表结构只由 `alembic/versions/` 的迁移脚本定义，应用启动时不会自动建表或改表。漏跑迁移的典型症状是「服务能起来，但某个接口一调就 500」——例如 Phase 2 · D2.1 新增 `refresh_tokens` 后，开发库没迁移时登录会报 `Table 'resume_mvp_dev.refresh_tokens' doesn't exist`。

为避免把这类问题拖到运行期，`app/main.py` 的 `lifespan` 在启动时会做一次版本比对：

- 读取数据库 `alembic_version` 表中已执行的版本；
- 读取代码里 `ScriptDirectory.get_heads()` 的最新版本；
- 两者不一致时：`ENVIRONMENT=development` / `test` 打印醒目黄色告警后继续启动；`ENVIRONMENT=production` **抛出 `DatabaseSchemaOutOfDateError` 直接阻止启动**（Fail-fast，避免带病上线）；
- 库中还没有 `alembic_version` 表（全新库）同样视为不一致。

`alembic/env.py` 只读环境变量、不读 `.env`，跑迁移前先把 `DATABASE_URL` 导出到当前 shell：

```bash
export DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)"
uv run alembic upgrade head
uv run alembic current   # 应输出 <最新版本号> (head)
```

```powershell
$env:DATABASE_URL = (Get-Content .env | Where-Object { $_ -match '^DATABASE_URL=' }) -replace '^DATABASE_URL=', ''
uv run alembic upgrade head
uv run alembic current
```

首次拉取仓库时，完整流程为：

```powershell
uv venv --python 3.13
uv sync
Copy-Item .env.example .env
# 导出 DATABASE_URL 后执行迁移，再启动
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

生产环境（**下面是目标形态，今天还没有一次真实部署过**：开发主机没装 Docker，`ADR-008` 那批件全是 `NOT VERIFIED`，`AGENTS.md` §1.5 的 Redis / 对象存储 / Worker 也一件都没接线）：

1. 构建前端静态资源和后端镜像；
2. 由密钥管理系统注入环境变量——**必注入的只有 `DATABASE_URL` 与 `JWT_SECRET_KEY`**（§8 那两个启动即拦的检查）；`DEEPSEEK_API_KEY` 不需要，AI 凭据是用户自己带的（BYOK）；
3. 启动 MySQL、API、反向代理。~~Redis、对象存储、Worker~~ 这三件**当前没有对应代码**，把它们放进 runbook 只会让人去查一个不存在的进程；真要引入时按 §4.6 先写 ADR，再回来改这一节；
4. 执行 Alembic 迁移和健康检查（`/api/v1/health`；迁移漏跑时开发/测试打印告警、**生产直接拒绝启动**）；
5. 配置 HTTPS、备份、监控和告警；
6. 先灰度，再开放企业和学生流量。

## 12. 可观测性

**设计**：结构化日志包含 request ID、用户/组织哈希、路由、状态码、耗时和错误码；指标包含 API 延迟、AI 成功率、任务积压、数据库连接、导出量和授权拒绝量。告警不得把敏感请求体直接发送到第三方。

**现状（2026-11-15 核对）**：统一响应信封里确实带 `request_id`，错误也按枚举码归一（G3 那张 `app/core/error_messages.py`），日志配置在 `app/main.py`；**但没有任何指标系统**——没有 Prometheus/OTel/APM 依赖，「API 延迟 / AI 成功率 / 任务积压 / 导出量 / 授权拒绝量」这五个数今天只能从日志和库里手工统计，其中「任务积压」本来就不存在（没有队列）。这一节整段仍是验收基线，不是已交付能力。

参考：[OWASP API Security Top 10 2023](https://api-security.owasp.org/editions/2023/en/0x11-t10/)、[W3C WebRTC Recommendation](https://www.w3.org/TR/webrtc/)、[ISO/IEC 25010:2023](https://www.iso.org/standard/78176.html)。
所有文本文件统一 UTF-8 无 BOM；Windows 下用 Get-Content -Encoding utf8 查看，避免乱码。
