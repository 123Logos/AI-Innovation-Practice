# AI赋能职业发展和面试指导平台

这是一个面向高校人工智能及相关专业的前后端分离职业成长平台，覆盖岗位洞察、能力证据、成长任务、人岗匹配、AI 简历、模拟面试和教学分析。

**仓库现在跑的是可使用的 MVP，不只是一份设计。** 注册/登录后有 7 个模块（工作台、简历工作台、岗位库、组织管理、模拟面试、能力测评、学情看板），简历主线「填写 → 生成 A4 → 预览编辑 → 保存」已闭环。上面那句是产品愿景的覆盖面；每一项的当前状态（已交付 / 已实现未接线 / 仅设计）写在 [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)，口径决策在 `docs/adr/`，开工顺序在 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 文档入口

按「谁说了算」排，不是按字母：

| 文档 | 管什么 |
|---|---|
| [AGENTS.md](AGENTS.md) | 工作协议与硬约束，优先级高于仓库里任何其他文档 |
| [docs/MVP_SCOPE.md](docs/MVP_SCOPE.md) | Phase 1 范围基线（Phase 2 起被 ADR 与 ROADMAP 批次覆盖，文件里已注明） |
| [docs/adr/](docs/adr/) | 30 张 ADR（ADR-001…ADR-030）：**口径与决策的唯一来源** |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 批次编号（前端轨 F1a–F7、后端轨 B1–B11）与开工顺序的唯一来源 |
| [docs/FRONTEND_DESIGN.md](docs/FRONTEND_DESIGN.md) | 用户可见物的判据：文案 C / 视觉 V / 动效 M / 布局 L / 表单 F / 状态 S / 令牌 T / 可测性 R + 六道闸门 G |
| [产品需求文档](docs/PRD.md) | 用户、范围、功能、创新点、指标和验收标准 |
| [开发与架构文档](docs/DEVELOPMENT.md) | 技术栈、目录、AI、安全、测试和部署 |
| [接口文档](docs/API.md) | REST 接口、数据格式、错误码和权限。**SSE 与 WebRTC 是设计目标，代码里一条都没有** |
| [数据库设计](docs/DATABASE.md) | MySQL 逻辑模型与约束原则；**表结构真相在 `alembic/versions/`**，`docs/design/schema-draft.sql` 不是迁移源 |
| [测试文档](docs/TESTING.md) | 测试策略、错误编号、用例、预期输出和发布门槛 |
| [项目当前进度](docs/PROJECT_STATUS.md) | 已实现范围、验证结果、待办事项和 AI 更新协议 |
| [Phase 2 需求池](docs/PHASE2_BACKLOG.md) | Phase 2 逐条复核记录；后续交付已由 ROADMAP 批次承接 |
| [约定摘要](docs/AGENT_CONVENTIONS.md) / [交接快照](docs/AGENT_HANDOFF.md) | 后者是 2026-10-23 的**历史快照**，只作时点记录，不代表现状 |
| [.env.example](.env.example) | 不含真实密钥的配置模板 |
| [产品设想文档](产品设想文档.md) | 原始需求与扩展建议 |

## 技术方案

下表刻意分成两列：**今天真的在跑的**与**设计里写着、代码里还没有的**。把右列当成现状读，是这个项目过去最容易误导人的地方（`AGENTS.md` §1 就是为这件事写的）。

| 方面 | 现状（代码里可验证） | 设计目标（尚未接线，一行实现都没有） |
|---|---|---|
| 后端 | `uv` + Python 3.13 + FastAPI + SQLAlchemy + MySQL 8，同步请求 | Redis、ARQ/Celery、消息队列、任何后台任务框架 |
| 前端 | Vue 3 + TypeScript + Vite + Element Plus，响应式 Web（`ADR-001`） | 小程序 / 原生 App |
| AI | **BYOK**：用户在界面里填自己的 DeepSeek Key，后端携带它调用 DeepSeek（`ADR-009` 润色 / `ADR-011` JD 技能提取 / `ADR-013` 匹配 / `ADR-028` 画像概述），**非流式** | 服务端自持 Key 的调用形态（已废弃，`.env` 的 `DEEPSEEK_API_KEY` 不再被任何代码读取） |
| 语音 | 浏览器 `MediaRecorder` 录音 + **直连** DeepSeek 语音工具做转写（`ADR-030`，回合制面试） | WebRTC 音视频房间（`media_rooms` 等表已裁定不建） |
| 文件 | 不落对象存储：批量下载是同步生成的 ZIP（`ADR-029`），PDF 用宿主机浏览器渲染 | S3 兼容私有对象存储 |
| 部署 | 本地 `start.bat` / `start-local.ps1` 一键启动；CI 在 GitHub Actions 的 **PR** 上跑 | `docker-compose.yml` 等 Docker 件已入库但**从未构建过**（见 [ADR-008](docs/adr/ADR-008-docker-configs-unverified.md)） |

数据库里现在是 **23 张表 / 28 个 Alembic 修订**（Phase 1 基线那 5 张早已扩出，新增每张表都要先有 ADR——`AGENTS.md` §4.2）。

当前项目本地与演示环境使用 MySQL 数据库 `resume_mvp_dev`，集成测试使用 `resume_mvp_test`
（与 CI 的 MySQL service 同名）。本地配置请以 `.env.example` 为模板，并把 `DATABASE_URL`
指向同一个库。两个库只需建一次：

```sql
CREATE DATABASE IF NOT EXISTS resume_mvp_dev  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS resume_mvp_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

> `MYSQL_DATABASE` 只是写给人看的记录字段，没有任何代码读取它；真正决定连哪个库的是
> `DATABASE_URL`（`app/core/config.py:22`、`alembic/env.py:12`）。两者保持一致是为了不让人误判。

## 配置安全

```powershell
Copy-Item .env.example .env
```

开发环境可以使用本地配置；生产环境**启动时真的会校验**的只有两项（`app/core/config.py::validate_production_database_url` 与 `::validate_jwt_secret_key`，缺一个就拒绝启动），清单如下：

- `JWT_SECRET_KEY`：至少 32 位强随机字符串（生产环境缺失或短于 32 位 = 启动即失败）
- `DATABASE_URL`（含 `MYSQL_PASSWORD`）：生产环境缺失 = 启动即失败
- `FRONTEND_URL`、`ALLOWED_ORIGINS`：跨域与回调地址，按部署域改

下面这几项**不再是生产必换项**，别再照旧清单去轮换它们：

- `DEEPSEEK_API_KEY`：BYOK 之后**没有任何代码读它**（`app/integrations/copy_writer.py:143` 就写着这件事）。留着它只会让人以为换掉它就换了 AI 的凭据。
- `S3_ACCESS_KEY_ID`、`S3_SECRET_ACCESS_KEY`：对象存储一行代码都没接（`ADR-008` 之外，`AGENTS.md` §1.5），等真接入时再要求。

真实 `.env`、数据库备份和 JWT 密钥不得提交到代码仓库。

**AI 的 Key 走 BYOK（ADR-009 + ADR-010 + ADR-011 + ADR-013 + ADR-028 + ADR-030）**：用户在**顶栏右侧「账户」抽屉**里的「AI 设置」填入自己的 DeepSeek Key，
它只存在于浏览器 `sessionStorage`（关标签页即清除，不写 `localStorage`），服务端只在本次请求内存中持有，
**不落库、不写日志**，因此 `.env` 里的 `DEEPSEEK_API_KEY` 已不再被任何代码读取。「生成简历」是纯模板路径，不需要也不发送这个 Key。

Key 能出现在哪，是 `frontend/src/api/client.ts` 里**两张 fail-closed 白名单**决定的，不在表里的请求一个字节都不发：

| 表 | 端点 | Key 在哪个头 | 发往 |
|---|---|---|---|
| `BYOK_KEY_PATHS` | `POST /resumes/polish`、`POST /jobs/extract-skills` | `X-DeepSeek-Key` | 我方域名 |
| `BYOK_KEY_PATTERNS` | `POST /jobs/{uuid}/match`、`POST /interviews/{uuid}/messages`、`POST /assessments/sessions/{uuid}/summary` | `X-DeepSeek-Key` | 我方域名 |
| `DEEPSEEK_DIRECT_PATHS` | `/v1/tools/asr`、`/v1/tools/tts`（浏览器直连，音频不上传、转写即删，`ADR-030`） | `Authorization` | `api.deepseek.com` |

**新增一条 URL 就是新增一个注入面，需要老板逐条书面同意**（`AGENTS.md` §3.2 末行，这条不在 2026-11-08 全面解冻的范围内）。

## 设计原则

1. 前后端职责分离，所有敏感业务校验在后端执行。
2. AI 只提供可追溯、可编辑的建议，不直接替代教师或企业作出录用决定。
3. 匹配结果必须可解释，学生数据按组织、角色和授权隔离。
4. 组件、颜色、表单、空状态和错误状态统一，白色为主并支持移动端。
5. 外部 AI/媒体服务不可用时，核心档案和岗位数据仍可访问，任务可重试。

## 研究与治理基线

项目设计参考 NIST AI RMF、OWASP API Security Top 10、W3C WebRTC、ISO/IEC 25010:2023，以及 RAG 和 FActScore 相关论文。具体落地要求与引用见 [产品需求文档](docs/PRD.md)、[开发与架构文档](docs/DEVELOPMENT.md) 和 [测试文档](docs/TESTING.md)。

## 🚀 本地一键启动 (Windows)

这是当前**唯一承诺可用**的启动方式，不需要 Docker，也**不需要管理员权限**。

前置条件（只需装一次）：

- [Node.js](https://nodejs.org/zh-cn) 22 或更高（自带 npm）
- [uv](https://docs.astral.sh/uv/)（Python 包管理器）
- 本机 MySQL 8 正在运行（默认端口 3306）
- 项目根目录有 `.env`，其中 `DATABASE_URL` 指向你的 MySQL

准备完成后：

> **在项目根目录双击 `start.bat`（Windows 一键启动）。**

`start.bat` 是 `start-local.ps1` 的安全唤起壳，只多做一件事——用
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-local.ps1"` 启动脚本。

- `Bypass` **仅对这一个 PowerShell 进程生效**（Process 作用域）：不写注册表，不改机器级
  `LocalMachine` 或用户级 `CurrentUser` 策略，窗口一关就消失，也无法覆盖组策略（GPO）下发的限制。
- 全程运行在**普通权限**下：脚本只做 TCP 端口探测和开新窗口，没有任何需要管理员的命令，
  也不读写系统盘。
- 出错时 `pause` 会把黑窗口停住，方便截图。

脚本会依次做四件事，全程有彩色提示：

1. 检查 `uv` / `npm` 是否安装、`.env` 是否存在、MySQL 端口是否可连；
2. 新开一个窗口跑后端：先 `uv run alembic upgrade head` 同步表结构，再 `uv run uvicorn app.main:app --reload`；
3. 新开一个窗口跑前端：进入 `frontend`，首次运行会先 `npm install`，然后 `npm run dev`；
4. 轮询 `http://127.0.0.1:8000/api/v1/health` 和 `http://localhost:5173`，等两个服务真的能响应后自动用默认浏览器打开页面。

启动完成后：

| 入口 | 地址 |
|---|---|
| 网页 | http://localhost:5173 |
| 后端健康检查 | http://127.0.0.1:8000/api/v1/health |
| 接口文档 | http://127.0.0.1:8000/docs |

停止服务：直接关掉「后端」和「前端」两个窗口。

常见问题：

- **在 PowerShell 里手动运行 `start-local.ps1` 被执行策略拦截**：改用 `start.bat` 入口就行；
  或者只对当前这一次、当前进程绕过：
  `powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1`。
  两者都不需要管理员权限，也不需要把全局 `ExecutionPolicy` 改成别的值。
- **提示 MySQL 没有在 127.0.0.1:3306 上运行**：按 `Win+R` 输入 `services.msc`，找到 MySQL 服务右键「启动」；
  或在管理员 PowerShell 执行 `net start MySQL80`。
- **后端窗口显示「数据库迁移失败」**：九成是 `.env` 里的 `DATABASE_URL` 账号、密码或库名不对，改完重跑脚本。
- **页面打得开但接口报错**：先看后端窗口的报错，不要改前端代码。
- 脚本里为什么要「导出 `DATABASE_URL`」：`alembic/env.py` 只读进程环境变量、不读 `.env`，
  不注入就会在第一步抛 `RuntimeError`（见下方「本地开发约定」）。

## 界面结构（前端轨现状）

判据本身归 [docs/FRONTEND_DESIGN.md](docs/FRONTEND_DESIGN.md)，这一节只说「打开页面看到的是什么」：

- **外框只有一处定义**（守则 L1）：`frontend/src/components/AppLayout.vue`。视图里出现第二处手工摆的 `<AppTopBar />` 会被测试判红。
- **顶部是一整块 sticky 外框** `.app-chrome`：品牌顶栏 + 二级导航 `ModuleNav.vue`。二级导航**不随滚动消失**，右侧有一颗「收起导航 / 展开导航」由人控制；收起后仍说出当前在哪个模块。
- **模块入口只有一张表**（守则 L4）：`frontend/src/config/modules.ts` 的 `APP_MODULES`，7 个模块。顶栏里一个模块名都没有——入口重复是这个项目改过两遍的毛病。
- **简历列表住哪按宽度换**：≥1440 是左侧常驻栏（240px，**默认收起**，左边缘那颗按钮拉出与收回都用它）；1100–1439 与窄屏走右侧抽屉。同一份内容组件 `ResumeList.vue`，两个外壳共用。
- **账户的事在右边**：顶栏右侧「账户」按钮开 `UserDrawer.vue`，里面是「AI 设置」与「退出登录」。
- **字号层级**：品牌名 20px > 用户名 17px > 正文与辅助文字。页面标题 22px 恒高于品牌名。
- **每个页面同一套壳**：`min(900px, 100%)`（岗位库 1180px）+ 居中 + `32px 48px 72px` 内缩 + `.page-heading`（`.eyebrow` / `h1` / `.intro` 三行）。
- **表单同一套排布**：输入框/选择框 `flex: 1 1 180~220px` 吃掉整行剩余宽度，按钮右对齐且与控件等高 34px。
- **说明性小字不上屏**（守则 C5，2026-11-14 第 22 轮扩的）：解释「这东西是什么/怎么用」的淡灰小字已删掉 32 处；保留的四类豁免是校验与错误提示、数据与状态、花用户凭据与用量的告知、空态与不可逆后果。

## 验证与闸门

改完跑这些，全部在 CI 的 PR 检查里（`.github/workflows/ci.yml`；push 触发已删，所以**只有开 PR 才会跑**）：

```bash
# 后端（753 条 collected）
uv run ruff format --check app tests alembic scripts
uv run ruff check app tests scripts
uv run mypy app scripts
uv run lint-imports          # 分层闸：api → services → repositories → integrations
uv run pytest -q             # 含「空库 upgrade head → downgrade base」那条迁移检查

# 前端（450 条 / 39 个 spec）
cd frontend
npm run lint
npm run typecheck            # vue-tsc --noEmit
npm run test                 # vitest run
npm run build

# 文档与文案闸（五道，全在 scripts/）
uv run python scripts/check_ui_copy.py            # G1 文案：emoji / 黑话 / 豁免标记
uv run python scripts/check_error_copy.py         # G3 错误文案表
uv run python scripts/check_design_tokens.py      # G4 令牌：裸 hex / 裸字号
uv run python scripts/check_route_reachability.py # G5 路由可达性（孤儿视图）
uv run python scripts/check_docs_citations.py     # 文档里的 file:line / path::symbol 指针
```

布局与视觉类改动**必须真浏览器实测 375px 与 1280px 两档**（守则 R2）：本机没有 Docker 也没有 Playwright，配方是 headless Edge + CDP，写在 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) §10.2。jsdom 不跑媒体查询，CSS 断点在单测里绿了不算验过。

## 本地开发约定

> **⚠️ 拉取最新代码后，启动后端前必须执行 `uv run alembic upgrade head` 同步数据库。**
>
> 代码里的表结构由 `alembic/versions/` 下的迁移脚本定义，**不会在启动时自动建表**。
> 忘记执行的表现是：服务能正常启动，但一旦走到新表（例如 `refresh_tokens`）就报
> `Table 'xxx' doesn't exist` / 接口 500。为提前暴露这类问题，后端启动时会比对
> 数据库的 Alembic 版本与代码的最新版本：开发/测试环境打印醒目告警，
> **生产环境直接拒绝启动**。

`alembic/env.py` 只读环境变量、不读 `.env`，所以跑迁移前要先把 `DATABASE_URL` 导出到当前 shell：

```bash
# Git Bash / macOS / Linux
export DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)"
uv run alembic upgrade head
uv run alembic current   # 确认已到 head
```

```powershell
# PowerShell
$env:DATABASE_URL = (Get-Content .env | Where-Object { $_ -match '^DATABASE_URL=' }) -replace '^DATABASE_URL=', ''
uv run alembic upgrade head
uv run alembic current
```

后端代码落地后：

```powershell
uv venv --python 3.13
uv sync
Copy-Item .env.example .env
uv run alembic upgrade head   # 同步数据库结构（拉取新代码后必跑）
uv run uvicorn app.main:app --reload
```

前端代码落地后：

```powershell
cd frontend
npm install
npm run dev
```

部署、迁移、测试、监控和接口联调要求以 [开发与架构文档](docs/DEVELOPMENT.md) 和 [接口文档](docs/API.md) 为准。

## 🐳 Docker 启动 (可选)

> ⚠️ 注意：此 Docker 配置在当前开发环境中尚未经过实际运行验证，仅作工程化结构参考。
> 开发主机未安装 Docker，`Dockerfile.backend` / `Dockerfile.frontend` / `docker-compose.yml` /
> `docker/nginx.conf` 一次都没有构建或启动过，每个文件头部都写有 `NOT VERIFIED` 声明。
> 决策背景与后果见 [ADR-008](docs/adr/ADR-008-docker-configs-unverified.md)。
> 日常开发请使用上一节的 `start-local.ps1`。

前置条件：已安装 Docker Desktop，且项目根目录存在 `.env`。`docker compose` 会自动读取根目录
`.env` 里的 `MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`、`JWT_SECRET_KEY`；缺失时
compose 会带着 `:?` 提示直接失败，不会拉起半配置的服务。

```powershell
# 只做解析和变量插值检查，不构建镜像
docker compose config

# 构建并启动 db + backend + frontend
docker compose up --build
```

启动后访问 **http://localhost:8080**（前端由 nginx 托管静态文件，并把 `/api/` 反代到
`backend:8000`，与浏览器同源，因此不需要额外配置 CORS）。后端容器启动命令是
`alembic upgrade head && uvicorn ...`，空数据库卷会自动建表。

```powershell
docker compose down      # 停止服务，保留 MySQL 数据卷
docker compose down -v   # 连数据卷一起删除，数据会丢失
```

第一次在真实 Docker 宿主上完整跑通 `build` + `up` 之后，应回来把 ADR-008 的状态改为「已验证」。
