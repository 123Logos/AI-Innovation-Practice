---
date: 2026-11-04T20:03:51+08:00
session_name: general
researcher: 123Logos
git_commit: 54d8241c8fdd47f3b3e4951247d95e5961020e05
branch: master
repository: ai-innovation-practice
topic: "CI 双端覆盖与本地一键启动 Implementation Strategy"
tags: [ci, docker, powershell, local-dev, adr-008]
status: complete
last_updated: 2026-11-04
last_updated_by: 123Logos
type: implementation_strategy
root_span_id:
turn_span_id:
---

# Handoff: CI 加前端门 + 未验证 Docker 配置 + Windows 一键启动脚本

## Task(s)

人类下达的「最终任务」四条，全部完成，**未 commit**：

1. CI 补前端任务（同 job 内加 Node 22 + `npm ci` / test / typecheck / build）— 完成，四道门本地已实跑绿。
2. Docker 工程配置（仅编写、本机无 Docker、不运行）— 完成，五个文件带 `NOT VERIFIED` 声明。
3. `start-local.ps1` Windows 原生一键启动 — 完成（本轮核心交付），AST 语法通过 + 前半段检查逻辑实跑通过。
4. README 两个新章节 — 完成。

另按 grill 拷问结论新增 `docs/adr/ADR-008-docker-configs-unverified.md`。

## Critical References

- [AGENTS.md](file:///E:/AI%20Innovation%20Practice/AGENTS.md) — 工作合同；7.1/7.9 红线是本轮 Docker 定位的直接原因
- [docs/adr/ADR-008-docker-configs-unverified.md](file:///E:/AI%20Innovation%20Practice/docs/adr/ADR-008-docker-configs-unverified.md) — Docker 为何以「未验证资产」入库
- [.github/workflows/ci.yml](file:///E:/AI%20Innovation%20Practice/.github/workflows/ci.yml) — 唯一的 CI 定义

## Recent changes

- `.github/workflows/ci.yml:49-75` — `test` job 尾部追加 setup-node@v4（`cache-dependency-path: frontend/package-lock.json`）+ 四道前端门
- `start-local.ps1`（新）— 前置检查 → 双窗口启动 → 健康轮询 → 开浏览器
- `Dockerfile.backend` / `Dockerfile.frontend` / `docker-compose.yml` / `docker/nginx.conf` / `.dockerignore`（新，全部 NOT VERIFIED）
- `README.md:23`（部署行降级）、`README.md` 新增「🚀 本地一键启动 (Windows)」「🐳 Docker 启动 (可选)」
- `docs/adr/ADR-008-docker-configs-unverified.md`（新）

## Learnings

- **`alembic/env.py:12-14` 只读进程环境变量**，`app/core/config.py:27-32` 才读 `.env`。任何自动化脚本在 `alembic upgrade head` 前必须导出 `DATABASE_URL`，否则第一步就 `RuntimeError`。`start-local.ps1` 靠「父进程赋值 `$env:DATABASE_URL` + `Start-Process` 继承环境块」解决。
- **含中文的 `.ps1` 必须存成 UTF-8 with BOM**，否则 Windows PowerShell 5.1（本机 5.1.26100）按 GBK 解码 → 乱码或语法错误。已用 `[Console]::OutputEncoding = UTF8` + BOM + CRLF。
- **子窗口用 `-EncodedCommand`（UTF-16LE Base64）启动**，绕开 Windows 命令行的引号/换行/编码问题；项目路径含空格（`AI Innovation Practice`）也不受影响。
- **`actions/setup-node@v4` 的 `cache: npm` 必须配 `cache-dependency-path`**：lockfile 在 `frontend/`，默认在仓库根找不到会直接报错。
- **`frontend` 没有 ESLint 配置文件**，`package.json` 里的 `npm run lint` 现在必然失败 → 故意不加进 CI，加进去流水线就红。
- `.env` 的 `DATABASE_URL` 指向 `resume_mvp_dev`，而 `MYSQL_DATABASE=ai_innovation_practice`、README:25 也写 `ai_innovation_practice` —— 库名三处不一致，本轮未动。

## Post-Mortem

### What Worked
- Approach: 写脚本前先把 `alembic/env.py`、`app/core/config.py`、`vite.config.ts` 读完，发现 DATABASE_URL 陷阱后才动手 → 避免了「交付一个第一句就崩的一键脚本」。
- Pattern: **AST 语法检查 + 探针运行**。用 `[Parser]::ParseFile` 查语法，再把脚本前半段（纯检查逻辑）逐字复制成临时探针脚本实跑，验证中文/URL 解析/端口探测。比「只贴代码不运行」强，且不产生启动服务等副作用。
- Pattern: 先实跑 `npm run test/typecheck/build` 确认为绿，再写进 CI，保证流水线不会因为新步骤变红。

### What Failed
- Tried: 用 node `-e` 内联拼探针脚本 → Failed because: Git Bash 对反引号/`$` 的转义与 PowerShell here-string 冲突，`unexpected EOF while looking for matching '`'`。改成用 Write 工具写一个 `.ps1` 构建器，一步跑通。
- Tried: `git branch --abbrev-ref HEAD` → Failed because: 该 git 构建不支持，用 `git branch --show-current`。
- 自查修复：初版脚本有 3 个真 bug —— 迁移窗口里 `$LASTEXITCODE` 检查的是上一条命令而非 `alembic`；结尾引用未赋值的 `$backendReady`（`Set-StrictMode 2.0` 下直接抛异常）；`$dbUrl.Split('/')[3]` 在无路径的 URL 上越界。

### Key Decisions
- Decision: Docker 配置保留但显式标注未验证 + 补 ADR-008
  - Alternatives considered: 删掉任务 2；不加声明照写
  - Reason: 满足人类要「工程备份件」的意图，同时不违反 AGENTS.md 7.1/7.9 的完成度叙事要求。由人类负责人在 grill 中选择确认。
- Decision: 一键脚本用健康轮询取代「固定 sleep 几秒」
  - Reason: 首次运行 `uv sync` + 迁移可能上百秒，固定延迟必然打开空白页；轮询 `/api/v1/health` 才符合「小白一点就好」。
- Decision: CI 不加 `npm run lint`
  - Reason: `frontend/` 无 ESLint 配置，加了立刻红；修配置属于另一件事。

## Artifacts

- `start-local.ps1`
- `Dockerfile.backend`、`Dockerfile.frontend`、`docker-compose.yml`、`docker/nginx.conf`、`.dockerignore`
- `.github/workflows/ci.yml`
- `README.md`
- `docs/adr/ADR-008-docker-configs-unverified.md`
- 参考读取：`alembic/env.py`、`app/core/config.py`、`app/api/router.py`、`frontend/vite.config.ts`、`frontend/vitest.config.ts`、`frontend/package.json`、`pyproject.toml`

## Action Items & Next Steps

1. 在有 Docker 的宿主上真跑一次 `docker compose config` → `up --build`，重点验证：`useradd` 非 root 用户能否读 `/opt/venv`、`uv sync` 走清华源是否可达、nginx 反代与 SPA 回退。**跑通后把 ADR-008 状态改为「已验证」**。
2. 人类在自己机器上右键跑一次 `start-local.ps1`，确认双窗口与浏览器行为；本轮只验证了语法与检查逻辑，没有真正拉起服务。
3. 想给前端加 lint：先补 `frontend/eslint.config.js`（ESLint 9 扁平配置）+ 决定规则集，再把 `npm run lint` 加进 CI。
4. 统一库名：`.env` 的 `DATABASE_URL`（resume_mvp_dev）/ `MYSQL_DATABASE`（ai_innovation_practice）/ README:25 三处不一致，需要人类拍板以哪个为准。
5. `docs/DEVELOPMENT.md:13` 仍写「部署 Docker Compose + Nginx/Caddy + HTTPS」，本轮超出文件范围未改，下次一并降级为未验证表述。

## Other Notes

- 本项目 `.gitignore` 未忽略 `thoughts/`，本 handoff 会以 untracked 出现；仓库既有约定是 `docs/AGENT_HANDOFF.md`，两者别混用。
- 本机环境实测：PowerShell 5.1.26100 Desktop、Node v22.17.1、npm 10.9.2、MySQL 127.0.0.1:3306 可连、8000/5173 空闲。
- `pyproject.toml:93` 把 uv 索引源设为清华镜像，CI 注释仍写「GitHub Actions 用公共 PyPI」，该注释与实际行为不符（本轮未改，属既有问题）。
