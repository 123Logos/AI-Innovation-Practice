# AI赋能职业发展和面试指导平台

这是一个面向高校人工智能及相关专业的前后端分离职业成长平台设计项目，覆盖岗位洞察、能力证据、成长任务、人岗匹配、AI 简历、模拟面试和教学分析。

## 文档入口

- [产品需求文档](docs/PRD.md)：用户、范围、功能、创新点、指标和验收标准
- [开发与架构文档](docs/DEVELOPMENT.md)：技术栈、目录、AI、安全、测试和部署
- [接口文档](docs/API.md)：REST/SSE/WebRTC 适配层、数据格式、错误码和权限
- [数据库设计](docs/DATABASE.md)：MySQL 逻辑模型、约束、索引原则和第一版 DDL
- [测试文档](docs/TESTING.md)：测试策略、错误编号、用例、预期输出和发布门槛
- [项目当前进度](docs/PROJECT_STATUS.md)：已实现范围、验证结果、待办事项和 AI 更新协议
- [.env.example](.env.example)：不含真实密钥的配置模板
- [产品设想文档](产品设想文档.md)：原始需求与扩展建议

## 技术方案

- 后端：`uv` + Python 3.13 + FastAPI + MySQL 8
- 前端：Vue 3 + TypeScript + Vite + Element Plus
- AI：后端服务端调用 DeepSeek API
- 异步任务：Redis + Worker
- 文件：S3 兼容私有对象存储
- 部署：本地开发用 `start-local.ps1` 一键启动；`docker-compose.yml` 已入库但**未经验证**（见 [ADR-008](docs/adr/ADR-008-docker-configs-unverified.md)）

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

开发环境可以使用本地配置；生产环境必须由部署平台注入环境变量，并至少替换：

- `JWT_SECRET_KEY`：至少 32 位强随机字符串
- `MYSQL_PASSWORD`、`DATABASE_URL`
- `DEEPSEEK_API_KEY`
- `S3_ACCESS_KEY_ID`、`S3_SECRET_ACCESS_KEY`
- `FRONTEND_URL`、`ALLOWED_ORIGINS`

真实 `.env`、数据库备份、对象存储密钥和 JWT 密钥不得提交到代码仓库。DeepSeek Key 只允许后端使用，不能写入 Vue 环境变量或浏览器代码。

## 设计原则

1. 前后端职责分离，所有敏感业务校验在后端执行。
2. AI 只提供可追溯、可编辑的建议，不直接替代教师或企业作出录用决定。
3. 匹配结果必须可解释，学生数据按组织、角色和授权隔离。
4. 组件、颜色、表单、空状态和错误状态统一，白色为主并支持移动端。
5. 外部 AI/媒体服务不可用时，核心档案和岗位数据仍可访问，任务可重试。

## 研究与治理基线

项目设计参考 NIST AI RMF、OWASP API Security Top 10、W3C WebRTC、ISO/IEC 25010:2023，以及 RAG 和 FActScore 相关论文。具体落地要求与引用见 [产品需求文档](docs/PRD.md)、[开发与架构文档](docs/DEVELOPMENT.md) 和 [测试文档](docs/TESTING.md)。

## 🚀 本地一键启动 (Windows)

这是当前**唯一承诺可用**的启动方式，不需要 Docker。

前置条件（只需装一次）：

- [Node.js](https://nodejs.org/zh-cn) 22 或更高（自带 npm）
- [uv](https://docs.astral.sh/uv/)（Python 包管理器）
- 本机 MySQL 8 正在运行（默认端口 3306）
- 项目根目录有 `.env`，其中 `DATABASE_URL` 指向你的 MySQL

准备完成后：

> **在项目根目录右键 `start-local.ps1` → 选择「使用 PowerShell 运行」。**

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

- **右键没有「使用 PowerShell 运行」，或提示执行策略**：在项目目录打开 PowerShell 执行
  `powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1`。
- **提示 MySQL 没有在 127.0.0.1:3306 上运行**：按 `Win+R` 输入 `services.msc`，找到 MySQL 服务右键「启动」；
  或在管理员 PowerShell 执行 `net start MySQL80`。
- **后端窗口显示「数据库迁移失败」**：九成是 `.env` 里的 `DATABASE_URL` 账号、密码或库名不对，改完重跑脚本。
- **页面打得开但接口报错**：先看后端窗口的报错，不要改前端代码。
- 脚本里为什么要「导出 `DATABASE_URL`」：`alembic/env.py` 只读进程环境变量、不读 `.env`，
  不注入就会在第一步抛 `RuntimeError`（见下方「本地开发约定」）。

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
