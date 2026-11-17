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

---

## 修订：第 32–33 轮部署加固（2026-11-17）

- 决策人：本项目人类负责人（第 32 轮下达五项指令；第 33 轮追加三条裁定：tzdata 不挪 /
  开发形态不留双标 / README 过期描述立刻改）
- 修订记录：第 32 轮五项 = PR **#52**（合并于 `8334f4f`）；第 33 轮 = 本修订的决策 6 与
  「谁渲染 PDF 谁装浏览器」那条闸，随本 PR 落地。
- 前置：第 30/31 轮把 Redis + ARQ（`ADR-033`）与生产编排 `docker-compose.prod.yml` 落了地，
  项目进入「部署服务器」阶段。本修订只改部署配置与该配置对应的账，不动业务代码。

### 真机部署的证据（2026-11-17，负责人的 Ubuntu 22.04 云主机）

`NOT VERIFIED` 那句话从今天起要打个折：这套编排在云服务器上**第一次真机跑起来了**，
而真机立刻抓出三处配置文件与代码对不上的地方——它们全都不是「Docker 语法错」，
而是「坐标写错」，正是本 ADR 当年担心的那类：

| 真机证据（负责人跑出来的原话） | 结论 |
|---|---|
| `logs web` → `PermissionError: [Errno 13] Permission denied: '/app/temp/imports'`，导入 PDF 报 500 | 卷是加固前的镜像建的，属主仍是 root（决策 5 的「已存在的卷不会重放」这一支被真机证实） |
| `exec worker-pdf ls -l /app/frontend/src/styles.css` → `No such file or directory`；打包任务 0.1 秒落 `error_code=PDF_RENDERER_UNAVAILABLE`、`stage=rendering` | 服务器上跑的镜像早于决策 3 的 COPY 修复——**compose 新、镜像旧不算升级** |
| `exec web ls -la /usr/bin/chromium*` → `No such file`；单份导出 `POST /resumes/{id}/export` 稳定 **503** | 决策 6：同步渲染端点所在的容器必须有浏览器，此前只有 `worker-pdf` 有 |
| `exec web date` → `Tue Nov 17 04:26:20 UTC 2026` | 与上一条同源：那台机器当时的 compose/镜像还没有 `TZ`（决策 2 未生效） |

**仍然没有验证的**：本修订第 33 轮的改动（`web`/`backend` 的浏览器开关、README 的升级节、
新的归属闸）——它要在负责人 `git pull` + `build` + `up -d` 之后才算真机跑过。
开发主机依旧没有 Docker，本文件的每一条坐标仍然是「按代码推出来、被真机部分证伪过一轮」。

### 背景

负责人给出五条加固指令。逐条对着代码核过之后的结论是**两条已经在了、三条是真缺陷**：

| 指令 | 核对结论 |
|---|---|
| Nginx 加 `client_max_body_size 10m` 与 `try_files … /index.html` | 两条本来就在 `docker/nginx.conf` 里。**真问题在别处**：`proxy_pass http://backend:8000` 的主机名只在开发编排里成立（那一份的服务就叫 `backend`），生产的服务叫 `web`，第一次 `up` 之后所有 `/api/` 都会 502，而 `docker compose ps` 全绿、直连 8000 也是 200。ADR-008 §3 当年写「nginx 代理 `/api/` → `backend:8000`」时只有开发形态，这一半从没被补上。 |
| `x-app-env` 加 `TZ: Asia/Shanghai` | 缺。真影响：`shanghai_today()` 之外还有一批 `date.today()` 调用（岗位过期判定、匹配、简历服务），在 UTC 机器上北京时间 00:00–08:00 之间「今天」还是昨天，该下线的岗位多挂 8 小时。 |
| `Dockerfile.backend` 显式 COPY 两份纸面 CSS | 缺。`app/core/teacher_tools_rules.py` 用 `parents[2]` 拼出 `/app/frontend/src/…` 直接读源码里的 CSS（`ADR-029` §2.5 拒绝手抄第二份），镜像里没有这两份文件时导出会以 `PaperStylesUnavailableError` 失败。 |
| worker 关掉 HTTP 健康检查、`stop_grace_period` 缩到 10s | 缺。`Dockerfile.backend` 的全局 `HEALTHCHECK` 打的是 `:8000/api/v1/health`，worker 不监听 HTTP，两条队列容器在 `ps` 里会永远挂着 unhealthy——一个与「有没有在消费任务」毫无关系的状态。 |
| 任务卷的写权限说明 | 缺，而且是真会踩的：`app/core/task_files.py` 只做 `mkdir(parents=True, exist_ok=True)`，不会补属主；镜像里目录不存在时它以 root 建出来，随后 `appuser` 写盘就是 `PermissionError`。 |

### 决策

1. **一份 nginx.conf 覆盖两种拓扑**：`proxy_pass` 不动，改由生产编排给 `web` 挂默认网络
   别名 `backend`。别名与主机名这一组对应关系由 `tests/test_deployment_config.py` 钉住
   （它去两份编排里查这个名字能不能解析到服务：直接的服务名，或某个服务的网络别名）。
   负责人裁定的落地路径：真机第一次 `up` 用 README 验收的第一条
   `curl http://127.0.0.1/api/v1/health` 判别名是否生效（它走的就是 nginx 反代），
   直连 8000 是 200 而它 502 → 立刻按备选方案把 `proxy_pass` 改成 `http://web:8000`
   并把开发形态的 `backend` 改名，**不留两份 nginx.conf**。
2. **`TZ` 只进 `x-app-env`（web + worker 共享），`db` / `redis` 不加**：库里时间统一是
   naive UTC（`utc_now()`），MySQL 留在 UTC 才对得上；给 `db` 也加 TZ 会让「Python 写的
   UTC」与「MySQL `NOW()`」两套基准分叉，而今天所有时间戳都是 Python 写的，没必要引入第二套。
   **配套改动（不在五条指令里）**：`Dockerfile.backend` 无条件 `apt-get install tzdata`。
   `TZ: Asia/Shanghai` 是让 glibc 去 `/usr/share/zoneinfo` 查名字的，所以「这行 TZ 到底
   作不作数」取决于底座带不带那个目录。这台开发主机**核实不了**这件事：没装 Docker，
   Docker Hub 也不可达（`auth.docker.io` 连接超时，取不到镜像层清单）。能核实的是
   bookworm 的 Packages 索引（直接下 `main/binary-amd64/Packages.gz` 解析）：
   `tzdata 2026b-0+deb12u1` 是 **`Priority: required`**，下载 304,148 字节、
   `Installed-Size` 2,573 KB，连同 Depends 共 2 个包（0.4 MB / 3.0 MB）——
   按 Debian 的惯例 required 包在 slim 底座里应该在，**所以这一层大概率是空操作**。
   仍然显式装它，理由不是「底座缺」而是「本机会写出一句无法核实的断言」：
   一行注释说「TZ 生效」不如一条 `apt-get install tzdata` 让 TZ 生效，
   代价是百 KB 级、底座已有时 apt 不重复落文件。判据是
   `tests/test_deployment_config.py::test_the_image_can_actually_resolve_the_timezone_it_is_told`。
   **装的位置也是判据**（`test_the_timezone_data_is_not_baked_only_into_the_pdf_browser_branch`）：
   `INSTALL_PDF_BROWSER=1` 只有 `worker-pdf` 一条服务传，把 tzdata 写进那个 `if` 里，
   web 与 worker-ocr 用的就是不含这层的另一支镜像——三条容器读同一个 `TZ`，
   却只有一条有保险。第 32 轮初稿就把它挪进 `if` 里省那一层，被这条判据拦下，
   故记为判据而不是记为教训。**负责人复核后裁定：不挪**，留在无条件的基础层——
   岗位过期与审计时间要的是「三个容器都有准确的时间基准」，0.4 MB 的下载量买这个确定性值得。
   真要连这百 KB 都省掉，路子是把 `TZ` 换成 `TZ=UTC-8` 那类 POSIX 串（glibc 不需要
   zoneinfo 目录），但它不可读、还要重新核对 `shanghai_today()` 那批 aware 调用，本轮不做。
   **顺带一条纪律**（`test_the_backend_image_never_lets_apt_pull_in_recommends`）：
   每条 `apt-get install` 都带 `--no-install-recommends`。按同一份索引算闭包
   （Depends，或项取第一个候选），`chromium fonts-noto-cjk fontconfig tzdata` 是
   **230 个包 / 下载 256 MB / Installed-Size 820 MB**；放进 recommends 变成
   **466 个包 / 333 MB / 1151 MB**，多出的 331 MB 主要是 Qt5 整套、perl、samba-libs、
   udev、cpp-12、python3.11——与「把页面打成 PDF」无关。
   注意别把这笔账记成「recommends 会拖进 GTK」：`libgtk-*` / `libgbm1` / `libcups2`
   是 chromium 的**硬 Depends**，装浏览器躲不掉；`chromium` 自己的 Recommends
   只有 `chromium-sandbox` 一个。（这几个数字是包索引的算术，不是镜像层实测。）
   不装 `chromium-sandbox` 也不影响导出：渲染器本来就带 `--no-sandbox`
   （`app/integrations/pdf_renderer.py:99`），容器里今天也不打算开内核沙箱。
3. **后端镜像 COPY 那两个具体文件，不搬整个 `frontend/`**：后端运行不需要 SPA 源码与
   `node_modules`，只需要这两份「纸面真相」。断言的判据是**运行时拼出来的路径**，
   所以前端文件改名会让这里红，而不是等到导出失败才发现。
4. **两个 worker `healthcheck: disable: true` + `stop_grace_period: 10s`**；`web` 的健康检查
   保留（`frontend` 用 `depends_on: web: condition: service_healthy` 等它，去掉就起不来界面）。
   10s 是「重启要快」的裁定，代价明写在编排注释与 README：SIGTERM 后仍在跑的 job 被硬杀，
   记录停在 running 而 job 消失，下一次轮询收敛成 `TASK_LOST`（`ADR-033` §2.3 那张判定表），
   界面读到的是 `app/core/error_messages.py` 里那句
   「任务进行到一半中断了，请重新上传（或重新发起导出）。」——不会假装成功，也不会永远转圈。
5. **降权步骤一次做完**：`useradd` 同一条 `RUN` 里 `mkdir -p /app/temp/imports /app/downloads`
   并 `chown -R appuser:appuser` 两个挂载点，然后才 `USER appuser`。README 增「卷权限」小节：
   新建卷不用管（Docker 会把镜像里那个目录的属主带进空卷），**已存在的卷不会重放属主**，
   所以给出一次性的 `run --rm --no-deps --user root web sh -c "chown 10001:10001 …"`，
   并明确不要用 `down -v` 来清这两条卷（那会连 `mysql-data` 一起删）。
   UID 在编排/镜像/README 三处只允许有一份真相：README 里的数字必须等于 `useradd --uid`，
   chown 的目标目录必须等于编排的挂载点，都由同一批断言核对。
6. **谁渲染 PDF，谁的镜像就装 Chromium——第 33 轮补上 `web` 与开发的 `backend`/`worker-pdf`**：
   本修订第 3 条把纸面 CSS 塞进后端镜像时，我只核了**批量**那条链路（渲染在 `worker-pdf`，
   那边一直有 `INSTALL_PDF_BROWSER=1`），没核**单份导出**。它是同步端点：
   `app/api/v1/teacher_tools.py` 的 `export_router.post("/{resume_id}/export")` 是普通 `def`，
   渲染发生在提供 HTTP 的那个进程里，`_render()` 把「读不到纸面 CSS」和「没有可用浏览器」
   一并归成 `PdfRendererUnavailableError` → 对外 503。生产编排当时只给 `worker-pdf` 传开关，
   于是云服务器上的真实表现是：**健康检查全绿、`/api/v1/health` 200、打包能被 worker 接走，
   但单份导出稳定 503、批量任务 0.1 秒落 `PDF_RENDERER_UNAVAILABLE`（`stage=rendering`）**。
   同一条缺陷在开发形态有两处：`backend` 与 `worker-pdf` 都没传开关。三处一起补。
   判据不靠人记：`_services_that_render_pdf()` 从 `command` 里读 `PdfWorkerSettings`
   认出消费 pdf 队列的服务，再加上「提供 HTTP 的那个」，逐个断言
   `build.args.INSTALL_PDF_BROWSER == "1"`；另有一条测试专门守「单份导出仍是同步 `def`」
   这个前提——它红了就说明归属变了（比如挪进队列），要连着改判据与编排，不许注释掉。
7. **小机器的并发档位按「谁真的吃内存」分档，不按机器大小一刀切**（负责人给出云主机规格
   2 vCPU / 4 GiB 之后定的）：
   - `WORKER_PDF_MAX_JOBS` 是内存的主账——每条任务一个 Chromium 子进程，4 GiB 设 `1`；
   - `WORKER_OCR_MAX_JOBS` **不是**模型内存的账：`app/integrations/pdf_ocr.py` 里 `_ENGINE`
     是双检锁单例、`recognize()` 全程持 `_CALL_LOCK`，识别在进程内**串行**，调大只会让任务
     在锁上排队 + 各自握着已解码的页面位图，4 GiB 建议 `2`（DeepSeek 那一段还能重叠）。
     第 33 轮我先把这条写成「四条并发会被内核 OOM 杀」——那是没读代码的推断，已改账；
   - 两个数都**不是量出来的**：`.env.example` 与 README 都改成「先跑 `docker stats
     --no-stream`（空闲 + 正在导出各一次）、`free -m`、`dmesg -T | grep -i 'killed process'`
     再定」，并把这句话放在数字旁边，免得下一个人在 8 GiB 机器上照抄 4 GiB 的档位。

### 后果

- 「未验证」状态**不变**：本机仍没有 Docker，本修订碰的镜像与编排一次都没在这台机器上
  构建或起过。新增的 `tests/test_deployment_config.py` 是配置之间的**耦合**闸，
  不是「容器能起来」的证据。（第 33 轮云服务器是第一次真机部署，它验出来的三件事
  都写进了下面的条目。）
- 镜像**内容**变了（纸面 CSS、任务目录属主、tzdata、`web` 的浏览器），所以对老部署来说
  `restart` 与「只改 compose 文件」都不算升级，必须 `git pull` + `build`。第 33 轮就是这么卡的：
  compose 是新的、镜像是旧的，`worker-pdf` 里没有 `/app/frontend/src/*`。
  README 生产节因此新增「从第 32 轮之前的镜像升级」一节，把四条症状与一条判据列成表。
- 本修订留下两处我的判断失误，记在案：①决策 3 只修了批量那条链路、漏了同步的单份导出
  （见决策 6）；②「卷权限」那节最初给的 chown 没带 `-R`，老卷里 root 建出来的
  `/app/temp/imports` 改不到，现在 README 与实操命令统一成 `-R`。
- 顺带查出一件**配置依据是假的**：`docker/nginx.conf` 里 `client_max_body_size 10m` 的注释
  一直写「Matches `MAX_UPLOAD_MB=10` in `.env.example`」，而 `MAX_UPLOAD_MB` 在 `app/`、
  `scripts/`、两份编排里**零命中**——`Settings` 是 `extra="ignore"`，所以它躺在
  `.env.example` 里谁都不读。真实上限是代码里的 `MAX_PDF_BYTES = 5 MiB`
  （`app/services/resume_import.py`）。nginx 的注释已改成指向它，`.env.example` 那一行
  加了「没有任何代码读它」的说明并暂时保留。
  同一轮扫出来 `.env.example` 里还有约 17 个键没人读（`S3_*`、`DEEPSEEK_API_KEY` /
  `_BASE_URL` / `_MODEL`、`ENABLE_UUID_AUTH`、`ACCESS_TOKEN_COOKIE_SECURE`、
  `JWT_REFRESH_EXPIRE_DAYS` 等；`TEST_DATABASE_URL` 由 `tests/conftest.py` 读，不算死键）。
  **那是一份待裁定的清单，不在本 PR 的改动里**：要么接线、要么删，逐个判要单独一轮账。
- `web` 现在也带浏览器，代价按包索引闭包算是 230 个包 / 下载 256 MB /
  `Installed-Size` 820 MB —— 生产的最小机器规格因此上升，`build` 也更久。这是「同步端点
  在 web 进程里渲染」这个既有形状的账单；真正省掉它的办法见替代方案最后一条。
- 闸不是空转的：31 个变异点逐个把配置改回坏的样子（删别名 / 打开 worker 健康检查 /
  删 TZ / 给 `db` 加 TZ / 删 tzdata / **把 tzdata 挪进 `INSTALL_PDF_BROWSER` 分支** /
  去掉某条 `apt-get install` 的 `--no-install-recommends` / 删或改 COPY 落点 /
  chown 少一个目录 / 改 uid / 上传上限降回 1m / 去掉 try_files / 宽限期改回 60s /
  README 的 chown 少一个路径 / README 标题不再点名卷名 / `.dockerignore` 排掉 `frontend/src` /
  开发形态那三件各改一遍 + 把任务卷挂到别的路径 / **撤掉 prod `web`、dev `backend`、
  dev `worker-pdf` 的浏览器开关 / 把 `export_resume` 改成 `async def` /
  `.env.example` 的 OCR 与超时默认值各漂移一格 / nginx 与 `.env.example` 各丢掉
  `MAX_PDF_BYTES` 这条真实依据**），
  对应用例每一次都红，恢复后 42 条全绿。
  每条红的用例 id 都逐个核对过是**该管它的那一条**，不是靠别家用例凑数。
- worker 没有健康检查之后，`docker compose ps` 对 worker 不提供任何信号；观测手段只剩
  `logs` 与 `redis-cli zcard`（README「日常运维」）。这是接受的状态，不是遗漏。
- `stop_grace_period` 从 60s/120s 收到 10s，发版重启高峰会有一批任务落成 `TASK_LOST`，
  需要用户重新发起。要换回「停机时把在跑的做完」，改那两个数字即可，注释里写着。
- **开发形态（`docker-compose.yml`）本轮跟改了**：负责人原话「立刻顺手修掉，不能留一个
  同样会踩坑的开发版」。三件事同步过去——`TZ: Asia/Shanghai` 进 `backend` 与 `&worker-env`
  （`db` 仍不加）、三条 worker 用一份 `x-worker-runtime` 锚点统一
  `healthcheck: disable: true` + `stop_grace_period: 10s`。`worker-default` 在开发形态照起
  （生产那份仍然不起）。闸因此从「只查生产」改成**两份编排逐个容器过**，
  并加了一条「两边的任务卷挂载点必须一致」——路径一分叉，镜像里那一步 chown 就只认一套。

### 替代方案

- **`proxy_pass` 直接写 `web:8000`，把开发编排的 `backend` 改名**：也能一份配置通吃，
  但要动开发编排的服务名与 `depends_on`，改动面比加一个别名大，而且 `backend` 这个名字
  已经散在 README 与 ADR-008 §3 的表述里。不采用。
- **nginx 官方镜像的 envsubst 模板（`/etc/nginx/templates/*.template`）注入上游主机**：
  最显式，但要改 `Dockerfile.frontend` 的落点、给两份编排各加一个环境变量，还要确认
  `$host` / `$remote_addr` 这些 nginx 变量不被 envsubst 吞掉。为两个主机名的差别引入
  一层模板渲染，不划算。不采用。
- **只在 README 写「生产请把 proxy_pass 改成 web」**：把一个会 502 的缺陷降级成
  「运维手册里的一句话」，正是 AGENTS.md 第 2 节点名的「用文档代替行为」。不采用。
- **worker 用「起一个只回 200 的本地端口」来自证活着**：真要精确观测 worker，
  这是唯一比 `zcard` 更好的信号，但它要求 ARQ 侧装一个 HTTP 探针，属新组件。
  今天不做，留在这里备忘。
- **把单份导出也挪进 `arq:queue:pdf`（202 + 轮询，与批量同一条路）**：这样 web 永远不需要
  浏览器，`Installed-Size` 那 820 MB 的账直接省掉，也让「导出类工作都在 worker 里」变成一条
  能说清的架构口径。不采用（第 33 轮），因为它是**端点契约变更**：响应从 PDF 字节流变成
  task_id，前端 `export` 的调用点、`docs/API.md`、下载审计的落点都要改，属另一轮工作。
  本轮先用一行 `build.args` 把线上打通。若真做，记得连着改
  `test_sync_export_endpoint_still_lives_in_the_api_process` 与两份编排的开关。
- **容器启动时自检任务目录可写、写不进去就拒绝启动**（把 `PermissionError` 的 traceback
  换成日志第一行的指名命令）：与 §4.3「缺配置启动即失败」同一条纪律，不动数据库也不加依赖。
  第 33 轮已向负责人提出，**尚未拍板**，因此本轮没做——老卷那个坑今天仍然靠 README 的
  人工命令兜。
