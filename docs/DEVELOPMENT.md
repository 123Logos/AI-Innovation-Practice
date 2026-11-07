# 开发与架构文档

## 1. 技术基线

| 层 | 选型 |
|---|---|
| 前端 | Vue 3、TypeScript、Vite、Element Plus、Pinia、Vue Router |
| 后端 | Python 3.13、uv、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic |
| 数据库 | MySQL 8.0+，字符集 `utf8mb4` |
| 异步任务 | Redis + ARQ/Celery（二选一，MVP 推荐 ARQ） |
| AI | 后端 DeepSeek API 适配器，结构化输出与提示词版本化 |
| 文件 | S3 兼容对象存储；数据库只保存元数据 |
| 部署 | Docker Compose + Nginx/Caddy + HTTPS |

## 2. 总体架构

浏览器 → 反向代理 → Vue 静态资源 / FastAPI API → 领域服务 → MySQL、Redis、对象存储、DeepSeek、媒体适配器。

前后端通过 JSON REST API 通信；长任务使用轮询或 SSE 推送状态；面试音视频使用 WebRTC，媒体流不经过业务 API，业务服务只保存房间、授权、转写和反馈元数据。

## 3. 推荐目录

```text
backend/
  app/
    api/              # 路由、依赖、鉴权
    core/             # 配置、日志、安全、异常
    db/               # session、迁移、模型基类
    models/           # SQLAlchemy 模型
    schemas/          # Pydantic 请求/响应
    repositories/     # 数据访问
    services/         # 业务用例
    integrations/     # DeepSeek、对象存储、邮件、媒体
    workers/          # 异步任务
  tests/
frontend/
  src/
    api/              # 按模块封装 API
    components/       # 跨页面组件
    layouts/
    views/
    stores/
    router/
    styles/           # token、主题、响应式
docs/
```

## 4. 后端分层规则

1. `api` 只负责协议、鉴权、参数校验和状态码。
2. `services` 编排业务，不直接读取 HTTP 对象。
3. `repositories` 封装查询和事务边界。
4. `integrations` 统一供应商调用，禁止在业务代码中散落 API Key 或 HTTP 请求。
5. AI 和异步任务必须有任务 ID、状态、错误码、重试次数和幂等键。
6. 所有外部输入通过 Pydantic 校验；数据库查询使用 ORM 参数绑定。

## 5. 核心数据模型

- `users`、`organizations`、`memberships`、`roles`
- `student_profiles`、`skills`、`student_skills`、`evidence_items`
- `companies`、`job_posts`、`job_skill_requirements`
- `assessments`、`assessment_questions`、`assessment_attempts`、`assessment_answers`
- `match_runs`、`match_results`、`growth_plans`、`growth_tasks`
- `resumes`、`resume_versions`、`resume_shares`
- `interview_sessions`、`interview_turns`、`media_rooms`、`interview_reports`
- `ai_jobs`、`ai_prompt_versions`、`consents`、`audit_logs`

所有业务表包含 `id`、`created_at`、`updated_at`；软删除对象增加 `deleted_at`。敏感文本和媒体只保存必要版本，文件使用对象存储私有桶和短时签名 URL。

## 6. AI 集成方案

### 6.1 调用原则

- DeepSeek Key 只存在后端环境变量或密钥管理服务。
- 设置连接超时、读取超时、最大 token、并发上限和重试退避；不对 4xx 无限重试。
- 对岗位解析、简历生成、面试反馈使用不同提示词版本和 Pydantic 输出模型。
- 输入前做长度限制、敏感信息处理和提示注入隔离；模型输出后做 JSON 校验、事实引用检查和内容安全检查。
- 记录 `provider`、模型名、prompt 版本、耗时、token 用量和结果状态，不记录完整密钥。

### 6.2 AI 任务状态

`queued → running → succeeded | failed | cancelled`。任务应以业务对象和操作类型生成幂等键，例如 `job:{job_id}:analyze:v1`。

### 6.3 降级策略

- AI 不可用：保留岗位和档案基础 CRUD，提示稍后重试。
- AI 输出无法解析：保存原始响应的脱敏诊断摘要，进入重试/人工处理，不返回伪造成功。
- 超过额度：返回明确的限流错误和预计重试时间。

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

- 统一使用 Element Plus，禁止同一业务功能引入第二套组件库。
- 白色背景、浅灰分区、深色正文、单一主色；成功/警告/危险颜色全局 token 化。
- 表格、筛选器、空状态、加载态、错误态、确认弹窗抽为共享组件。
- 所有页面响应式：桌面侧栏布局，移动端底部导航/抽屉；操作顺序和文案一致。
- AI 生成内容必须标注“AI 建议”，并提供编辑、接受、拒绝入口。
- 颜色之外同时使用文字、图标或形状表达状态。

## 8. 配置与密钥

复制 `.env.example` 为本地 `.env`，生产环境由部署平台注入，不提交 `.env`。应用启动时校验 `ENVIRONMENT=production` 的强密钥、数据库连接和 `DEEPSEEK_API_KEY`；缺失或弱密钥直接失败，不使用静默默认值。

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
- 上面两条现在是**机器强制**的，不是约定：master 由 ruleset `26754995`（`enforcement: active`，
  作用于 `~DEFAULT_BRANCH`）保护，规则为 `pull_request` + `update` + `deletion` + `required_status_checks`，
  逐字段读回值：`required_approving_review_count: 0`、`allowed_merge_methods: ["merge"]`（只允许 merge commit，
  保留「ADR 提交在前、实现在后」的顺序）、状态检查 context `test`、`strict_required_status_checks_policy: true`
  （合入前必须先 rebase/merge 到最新 master，防「两个各自绿的 PR 合出红的 master」）、
  分支不可删、不可 force push。
  **一个必须知道的口子**：`bypass_actors` 里有一条 `actor_id: 5 / actor_type: RepositoryRole / bypass_mode: always`，
  即仓库管理员可以无条件绕过全部规则，`current_user_can_bypass: "always"`。所以闸门对非管理员是硬的，
  对管理员只是默认路径——管理员直推 `master` 仍然物理可行，禁止它靠本节，而不是靠 GitHub。
  没把它关掉的原因：需要保留一次紧急回滚通道。若要改成「管理员也要走 PR」，把该 actor 的 `bypass_mode`
  改为 `never` 或直接删掉 `bypass_actors`，改前先想清楚应急路径。
- **不做**长期 `develop` 分支，**不做** `release`/版本号分支：服务尚未公网部署，
  现在造版本号是给不存在的发布流程做兼容。真到部署那步，按 §4.6 另出 ADR。

下面 10.1 里「E2E：注册→画像→岗位→匹配→简历→面试」那条提到的**匹配**，
在它的 ADR 落地之前仍是 AGENTS.md §3.2 的冻结项（见 `docs/MVP_SCOPE.md`）。

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

生产环境：

1. 构建前端静态资源和后端镜像；
2. 由密钥管理系统注入环境变量；
3. 启动 MySQL、Redis、对象存储、API、Worker、反向代理；
4. 执行 Alembic 迁移和健康检查；
5. 配置 HTTPS、备份、监控和告警；
6. 先灰度，再开放企业和学生流量。

## 12. 可观测性

结构化日志包含 request ID、用户/组织哈希、路由、状态码、耗时和错误码；指标包含 API 延迟、AI 成功率、任务积压、数据库连接、导出量和授权拒绝量。告警不得把敏感请求体直接发送到第三方。

参考：[OWASP API Security Top 10 2023](https://api-security.owasp.org/editions/2023/en/0x11-t10/)、[W3C WebRTC Recommendation](https://www.w3.org/TR/webrtc/)、[ISO/IEC 25010:2023](https://www.iso.org/standard/78176.html)。
所有文本文件统一 UTF-8 无 BOM；Windows 下用 Get-Content -Encoding utf8 查看，避免乱码。
