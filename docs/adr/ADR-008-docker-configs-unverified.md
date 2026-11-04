# ADR-008：Docker 配置以「未验证工程资产」形式入库

- 状态：已接受
- 日期：2026-11-04
- 决策人：本项目人类负责人

## 背景

项目已达成交付标准，需要具备工程级的仓库形态：CI 覆盖前后端两端，同时提供本地一键
启动能力。人类负责人要求补一套 Docker 配置作为未来部署的备份件。

约束与冲突：

1. 开发主机**没有安装 Docker**，三份配置文件（`Dockerfile.backend`、
   `Dockerfile.frontend`、`docker-compose.yml`）以及支撑文件 `docker/nginx.conf`、
   `.dockerignore` 在本机一次都无法构建或运行，连 `docker compose config` 这种
   离线校验也执行不了。
2. AGENTS.md 7.1 禁止「用新增文档/配置替代可运行代码」，7.9 禁止用「工程级/生产级」
   描述未验证的东西，第 6 节 DoD 要求「目标行为可被人类按给定步骤复现」。
3. README:23 与 docs/DEVELOPMENT.md:13 此前已把「Docker Compose + 反向代理 + HTTPS」
   写成既定部署方式，属于未验证陈述。
4. 真正面向当下的可复现路径是 Windows 原生：本机已装 MySQL、uv、Node 22，
   `start-local.ps1` 的每一步都能被人类当场跑通。

## 决策

1. **两条路径分主次**：`start-local.ps1` 是本轮唯一承诺可用的启动方式；Docker 配置
   作为「未来部署就绪」的参考件入库，不承诺可运行。
2. **三处显式声明，不做沉默交付**：
   - `Dockerfile.backend`、`Dockerfile.frontend`、`docker-compose.yml`、
     `docker/nginx.conf`、`.dockerignore` 头部统一写
     `# NOT VERIFIED: Docker is not installed on the development host.`
   - README 的 Docker 章节顶部加中文警告，明确「尚未经过实际运行验证，仅作工程化
     结构参考」。
3. **补齐配置文件间的真实依赖**：nginx 代理 `/api/` → `backend:8000`、SPA history
   回退、`client_max_body_size` 对齐 `MAX_UPLOAD_MB`；compose 的 `DATABASE_URL`
   按服务名拼装而不是复用 `.env` 里指向 127.0.0.1 的串。
4. **不引入新的运行组件承诺**：compose 只有 db/backend/frontend 三个服务，
   不接 Redis、消息队列、对象存储、后台任务框架，冻结清单保持不动。
5. **CI 只加已验证的门**：前端 `npm ci` / `npm run test` / `npm run typecheck` /
   `npm run build` 四步在本地已跑绿才写进流水线；`npm run lint` 因为 `frontend/`
   下没有 ESLint 配置文件，本轮**不**加入 CI，避免把流水线做红。

## 后果

- 仓库里存在两份从未运行过的配置，任何人把它们当成可直接部署的产物都会踩坑；
  头部注释与 README 警告是唯一的防线。
- 第一次在有 Docker 的机器上执行 `docker compose up` 时大概率需要修东西
  （镜像拉取、非 root 用户权限、`uv sync` 走的是 `pyproject.toml` 里配置的国内
  镜像源）。届时以真实构建结果为准，本 ADR 状态改为「已验证」。
- Docker 路径与本地路径不再同构：本地前端由 Vite 代理 `/api`，Docker 前端由 nginx
  代理 `/api`。两者行为一致，但实现不同，改动代理规则时要同时检查两处。
- README:23 的部署描述被降级为「未验证」，docs/DEVELOPMENT.md:13 的同款表述本轮
  未改动（超出本任务文件范围），列为待办。

## 替代方案

- **不写 Docker 配置，只交付 `start-local.ps1`**：最贴合 AGENTS.md「跑得通才算完成」，
  且不留下任何无法验证的产物。不采用，因为人类负责人明确要求工程备份件，且未来
  答辩/部署需要现成配置。
- **把 Docker 配置写成不带任何声明的成品**：文件更干净，但等于把「写了」包装成
  「能用」，正是 AGENTS.md 第 2 节点名的「完成度叙事失真」。不采用。
- **在 CI 里加一个跑 `docker compose config` 的校验 job**：能在无人值守时提供最低
  限度的正确性证据，成本很低。不采用（本轮），因为它会把「未验证」变成「部分验证」
  却仍不等于镜像可构建，容易给出虚假安全感；真要做应在有 Docker 的宿主上跑一次
  完整 build 并同步更新本 ADR 状态。
