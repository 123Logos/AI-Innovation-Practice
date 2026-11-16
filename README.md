# AI赋能职业发展和面试指导平台

这是一个面向高校人工智能及相关专业的前后端分离职业成长平台，覆盖岗位洞察、能力证据、成长任务、人岗匹配、AI 简历、模拟面试和教学分析。

**仓库现在跑的是可使用的 MVP，不只是一份设计。** 注册/登录后有 7 个模块（工作台、简历工作台、岗位库、组织管理、模拟面试、能力测评、学情看板），简历主线「填写 → 生成 A4 → 预览编辑 → 保存」已闭环。上面那句是产品愿景的覆盖面；每一项的当前状态（已交付 / 已实现未接线 / 仅设计）写在 [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)，口径决策在 `docs/adr/`，开工顺序在 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 文档入口

按「谁说了算」排，不是按字母：

| 文档 | 管什么 |
|---|---|
| [AGENTS.md](AGENTS.md) | 工作协议与硬约束，优先级高于仓库里任何其他文档 |
| [docs/MVP_SCOPE.md](docs/MVP_SCOPE.md) | Phase 1 范围基线（Phase 2 起被 ADR 与 ROADMAP 批次覆盖，文件里已注明） |
| [docs/adr/](docs/adr/) | 32 张 ADR（ADR-001…ADR-032）：**口径与决策的唯一来源** |
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
| 后端 | `uv` + Python 3.13 + FastAPI + SQLAlchemy + MySQL 8；重任务走 Redis + ARQ（本地可内存降级） | Celery、S3、WebRTC |
| 前端 | Vue 3 + TypeScript + Vite + Element Plus，响应式 Web（`ADR-001`） | 小程序 / 原生 App |
| AI | **BYOK**：用户在界面里填自己的 DeepSeek Key，后端携带它调用 DeepSeek（`ADR-009` 润色 / `ADR-011` JD 技能提取 / `ADR-013` 匹配 / `ADR-028` 画像概述 / `ADR-031` 外部 PDF 简历结构化），**非流式** | 服务端自持 Key 的调用形态（已废弃，`.env` 的 `DEEPSEEK_API_KEY` 不再被任何代码读取） |
| 语音 | 浏览器 `MediaRecorder` 录音 + **直连** DeepSeek 语音工具做转写（`ADR-030`，回合制面试） | WebRTC 音视频房间（`media_rooms` 等表已裁定不建） |
| 文件 | 不落对象存储：批量下载是同步生成的 ZIP（`ADR-029`），PDF 用宿主机浏览器渲染；导入外部 PDF 先读文字层（`pypdf`），读不出才本机 OCR（`pypdfium2` 渲染 + `rapidocr`/`onnxruntime`，模型在 wheel 内、不出网，`ADR-031` v2） | S3 兼容私有对象存储、云端 OCR（学生姓名/电话不送给第二个服务商） |
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
| `BYOK_KEY_PATHS` | `POST /resumes/polish`、`POST /jobs/extract-skills`、`POST /resumes/import-pdf`（第 28 轮 `ADR-031` 2.6 扩的最后一条；三条都是**整段相等**，不是前缀） | `X-DeepSeek-Key` | 我方域名 |
| `BYOK_KEY_PATTERNS` | `POST /jobs/{uuid}/match`、`POST /interviews/{uuid}/messages`、`POST /assessments/sessions/{uuid}/summary` | `X-DeepSeek-Key` | 我方域名 |
| `DEEPSEEK_DIRECT_PATHS` | `/v1/tools/asr`、`/v1/tools/tts`（浏览器直连，音频不上传、转写即删，`ADR-030`） | `Authorization` | `api.deepseek.com` |

**新增一条 URL 就是新增一个注入面，需要老板逐条书面同意**（`AGENTS.md` §3.2 末行，这条不在 2026-11-08 全面解冻的范围内）。

### 测试账号：局域网保留，公网上线前必须清理或强密码化

| 账号 | 角色 | 口令 | 来历 |
|---|---|---|---|
| `cjlu_admin` | `school_admin` | `12345678` | `scripts/seed_cjlu.py`（组织「中国计量大学」，码 `CJLU2026`） |
| `cjlu_teacher` | `teacher` | `12345678` | 同上 |
| `role_nav_probe_23` | `student` | `Probe#2026a` | 2026-11-15 角色分流窄屏实测时注册的探针 |

**老板 2026-11-15 裁定：这三个暂时全部保留**，方便局域网测试直接登录对照三种角色。
它们用的是弱口令，因此**公网部署前必须逐个删除或改强密码**——这一步是上线的前置条件，
不是上线后的收尾清理（同一笔账还记在 `docs/API.md` 的组织那一节与
`docs/PROJECT_STATUS.md` §4 那批界面探针账号上）。

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
- **Redis 可以不装**。第 31 轮起（`ADR-034`），`.env` 里不配 `REDIS_URL` 时
  `development` 会走**内存降级**：「导入 PDF 简历」与「批量导出」照常可用，
  任务在 web 进程内执行，前端体验与生产一致（202 + 轮询 + 进度条）。
  **代价写在明处**：只对**单 Web 进程**有效（`uvicorn --workers >1` 会 404），
  且不应用 `WORKER_*_MAX_JOBS` 并发上限。
  → 要测**多进程并发或完整异步链路**（三条队列、真 worker、任务停摆观察），
  请按 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) §11.2 起 Redis 与 ARQ Worker；
  那时 `.env` 里配上 `REDIS_URL` 即可，配了就不降级。

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
   **（异步任务）默认什么都不用起**——`.env` 不配 `REDIS_URL` 时走内存降级。
   只有在 `.env` 里配了 `REDIS_URL` 之后，才需要另开窗口跑 worker
   （`uv run arq app.worker.OcrWorkerSettings` / `PdfWorkerSettings`）；
   **配了地址却不起 worker 的表现是「任务永远停在 pending」**，不是报错，
   三条队列与并发配置见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) §11.2；
4. 轮询 `http://127.0.0.1:8000/api/v1/health` 和 `http://localhost:5173`，等两个服务真的能响应后自动用默认浏览器打开页面。

启动完成后：

| 入口 | 地址 |
|---|---|
| 网页 | http://localhost:5173 |
| 后端健康检查 | http://127.0.0.1:8000/api/v1/health |
| 接口文档 | http://127.0.0.1:8000/docs |

停止服务：直接关掉「后端」「前端」（以及 worker，如果你起了的话）窗口。

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
- **顶部是一整块 sticky 外框** `.app-chrome`：品牌顶栏 + 二级导航 `ModuleNav.vue`。宽屏的二级导航**不随滚动消失**，右侧有一颗「收起导航 / 展开导航」由人控制，收起后仍说出当前在哪个模块；**窄屏（≤760px）换成一行「当前模块 ▾」+ 竖排菜单**，横滚那一排整条退役——七个页签在 375px 上要滚 242px，滚走的那几个既看不见也点不到（2026-11-15 第 24 轮）。
- **模块入口只有一张表**（守则 L4）：`frontend/src/config/modules.ts` 的 `APP_MODULES`，7 个模块，每一项带 `visibleToRoles`——**导航与工作台按角色过滤入口**（学生 5 个，教师侧 7 个），过滤器只有 `modulesVisibleTo(role)` 一处。顶栏里一个模块名都没有——入口重复是这个项目改过两遍的毛病。**这条只减入口，不改边界**：`/teacher/*` 与 `/organization/*` 的权限照旧由后端判（403 / 400）。
- **学生没班级时，工作台顶上有一条「加入班级」**：判据是 `GET /organizations/me` 的 404，点开是一个只有邀请码输入框的面板，走的是现有 `/invitations/redeem`；成功后重读这一页（横幅当场消失、留下「已加入「××」」的回执）。它补的是角色过滤之后断掉的那条路（「凭邀请码加入」那一屏本来在 `/organization`）。
- **简历列表住哪按宽度换**：≥1440 是左侧常驻栏（240px，**默认收起**，左边缘那颗按钮拉出与收回都用它）；1100–1439 与窄屏走右侧抽屉。同一份内容组件 `ResumeList.vue`，两个外壳共用。
- **账户的事在右边**：顶栏右侧「账户」按钮开 `UserDrawer.vue`，里面是「个人资料」「设置」「退出登录」三行（2026-11-15 第 25 轮；原来的「AI 设置」快捷入口已经并进设置弹窗）。「个人资料」是一个只读身份三格 + 四格可编辑表头的弹窗，保存时**把原本有值、这次被清空的字段按旧值发回去**——PUT 是整体覆盖，清空不该等于删除。"设置"弹窗里是「AI 密钥」「其他设置」两个页签。
- **字号层级**：品牌名 20px > 用户名 17px > 正文与辅助文字。页面标题 22px 恒高于品牌名。
- **每个页面同一套壳**：`min(900px, 100%)`（岗位库 1180px）+ 居中 + `32px 48px 72px` 内缩 + `.page-heading`（`.eyebrow` / `h1` / `.intro` 三行）。
- **表单同一套排布**：输入框/选择框 `flex: 1 1 180~220px` 吃掉整行剩余宽度，按钮右对齐且与控件等高 34px。
- **AI 没配好时有一条直达设置的路**（2026-11-15 第 25 轮）：润色、岗位技能提取、人岗匹配、模拟面试四处，凡是失败/降级原因是「凭据没填 / 凭据无效」，提示下面都会多一行「👉 点此配置 AI 密钥」，点了直接从任何页面打开设置弹窗（开合收在 `stores/ui.ts`）。
- **说明性小字不上屏**（守则 C5，2026-11-14 第 22 轮扩的）：解释「这东西是什么/怎么用」的淡灰小字已删掉 32 处；保留的四类豁免是校验与错误提示、数据与状态、花用户凭据与用量的告知、空态与不可逆后果。

## 验证与闸门

改完跑这些，全部在 CI 的 PR 检查里（`.github/workflows/ci.yml`；push 触发已删，所以**只有开 PR 才会跑**）：

```bash
# 后端（824 条 collected）
uv run ruff format --check app tests alembic scripts
uv run ruff check app tests scripts
uv run mypy app scripts
uv run lint-imports          # 分层闸：api → services → repositories → integrations
uv run pytest -q             # 含「空库 upgrade head → downgrade base」那条迁移检查

# 前端（559 条 / 47 个 spec）
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

## 🚢 生产环境一键部署（`docker-compose.prod.yml`）

> ⚠️ 与上一节同一条声明：开发主机没有 Docker，**这份生产编排从未真机跑过**（ADR-008）。
> 它按今天的代码把坐标写准了（服务名、卷、迁移顺序、worker 与浏览器），但第一次 `up`
> 仍可能需要修。生产上线前请先在一台测试机跑通。

一条命令拉起六个容器：`db`（MySQL）、`redis`、`web`（Uvicorn）、`worker-ocr`、
`worker-pdf`、`frontend`（nginx）。前五个是**必起的**；`frontend` 是界面本身，
你自己接反向代理时可以整段删掉。`worker-default` 故意不起——那条队列今天没有任务注册。

### 1. 准备 `.env`（缺一项就当场失败，不会起半配置的容器）

| 变量 | 必填 | 说明 |
|---|---|---|
| `MYSQL_PASSWORD` | ✅ | 数据库密码，**别用示例值** |
| `MYSQL_DATABASE` | ✅ | 库名（空库会自动建表） |
| `MYSQL_USER` |  | 默认 `root` |
| `JWT_SECRET_KEY` | ✅ | **≥ 32 字符**，`production` 下短于此长度启动即失败。生成：`openssl rand -hex 32`（或 `python -c "import secrets;print(secrets.token_hex(32))"`） |
| `PUBLIC_ORIGIN` | ✅ | 对外访问地址，例如 `https://career.example.com`；写进 `FRONTEND_URL` / `ALLOWED_ORIGINS` |
| `WORKER_PDF_MAX_JOBS` |  | 默认 `2`（两个浏览器子进程）。**内存 < 4 GB 的机器调到 1**，否则 OOM 杀的是 worker |
| `ENABLE_API_DOCS` |  | 默认 `false`（生产不暴露 `/docs` 与 `/openapi.json`） |

`production` 是**强制**的：缺 `DATABASE_URL` / `REDIS_URL` / 强 JWT 中任意一项，容器
启动即失败；库结构落后于代码时 `check_migration_state` 直接拒绝启动。**不会**像本地那样
降级成内存队列（`ADR-034` §2.1）——生产的并发上限只存在于真队列里。

### 2. 起服务

```bash
# 构建并后台启动（首次会构建镜像；worker-pdf 因为装了 Chromium 会多花几分钟）
docker compose -f docker-compose.prod.yml up -d --build

# 看状态：db / redis 等到 healthy，web 才会起；frontend 等 web 健康
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f web worker-pdf
```

`web` 的启动命令是 `alembic upgrade head && uvicorn ...`，所以空库第一次起来就会建表。

### 3. 验收（三条都过才算起来了）

```bash
curl -fsS http://127.0.0.1/api/v1/health                 # 200 + {"status":"ok",...}
docker compose -f docker-compose.prod.yml exec redis redis-cli ping   # PONG
docker compose -f docker-compose.prod.yml exec redis redis-cli zcard arq:queue:pdf  # 0
```

再到网页上走一遍两条重链路：**导入 PDF**（看进度条是否推进到「整理成档案」）与
**教师端批量导出**（看进度行 + 自动下载 ZIP）。导出若失败并显示「导出服务暂时不可用」，
说明 `worker-pdf` 里的 Chromium 没装上——看 `logs worker-pdf` 里的
`PDF 渲染调用失败`。

### 4. 日常运维

```bash
docker compose -f docker-compose.prod.yml restart worker-pdf   # 只重启一条队列
docker compose -f docker-compose.prod.yml pull && up -d        # 升级镜像
docker compose -f docker-compose.prod.yml down                 # 停服务，保留数据卷
docker compose -f docker-compose.prod.yml down -v              # 连 MySQL / Redis 数据一起删
```

- **迁移**：单副本时由 `web` 启动时跑。**多副本**要先单独跑一次
  `docker compose -f docker-compose.prod.yml run --rm web alembic upgrade head`，
  否则几个容器会抢同一把 DDL。
- **队列积压**：`redis-cli zcard arq:queue:ocr` / `arq:queue:pdf`（`arq:queue:pdf` 长期不降
  说明浏览器渲染慢或 `worker-pdf` 挂了）。没有指标系统，这是今天唯一的观测手段。
- **Redis 开了 AOF**（`appendonly yes`）：重启不会丢在途任务。任务记录本身有 TTL
  （`WORKER_TASK_TTL_SECONDS`，默认 30 分钟），不会无限涨。
- **换 `JWT_SECRET_KEY` 会让在途任务的 BYOK 密文解不开**（加密密钥由它派生），
  挑没有任务在跑的窗口换。
- **不要多机部署**：任务产物在本地盘（`task-downloads` 卷），
  `ADR-033` §7 第 5 条记着这条限制。
