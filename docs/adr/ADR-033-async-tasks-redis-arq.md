# ADR-033: 异步任务（Redis + ARQ）与按资源类型隔离的并发上限

- 状态：已采纳（Accepted，ADR 先行，代码随本文件同分支落地）
- 日期：2026-11-16（第 30 轮）
- 关联需求：FR-01（档案）、FR-08（教师端导出）；`ADR-031`（PDF 导入的三级链路）、`ADR-029`（批量下载 / 导出）、`ADR-009`（BYOK 的 Key 生命周期）、`ADR-014`（Redis 与任务框架从「禁令」变成「事实与选择」）
- 关联裁定：第 30 轮老板指令「引入 Redis + ARQ 异步任务队列，解决重任务（PDF 解析/OCR、无头浏览器导出）导致的同步阻塞与 OOM 风险，并实现按资源类型隔离的并发控制」「`WORKER_OCR_MAX_JOBS=4` / `WORKER_PDF_MAX_JOBS=2` / `WORKER_DEFAULT_MAX_JOBS=10`」「`POST /resumes/import-pdf` 与 `POST /teacher/resumes/batch-download` 立即返回 202 + `task_id`」「新增 `GET /tasks/{task_id}`」「前端 1.5 秒轮询」「无真实 Redis 时用 fakeredis 的 asyncio 支持 Mock 入队与结果存储」
- 前置：`ADR-031` §6 遗留第 6 条（「真要横向扩扫描件吞吐，答案是任务队列（另一篇 ADR）」）、`ADR-029` §5 遗留第 4 条（「要异步得先解决任务队列与文件存储的归属」）

## 1. 背景：两条已经在跑的链路，各自顶着一面墙

第 28/29 轮的两条链路都是**同步**的，而且两条都写下了同一句遗留：

| 链路 | 今天的形态 | 顶到的墙 |
|---|---|---|
| `POST /resumes/import-pdf` | multipart 进来 → pypdf 抽文字层 →（扫描件）pypdfium2 渲染 2.0× + RapidOCR → DeepSeek → 200 | ① OCR 引擎是**进程内全局单例 + 一把 `threading.Lock`**（`pdf_ocr.py`），两个人同时导扫描件，第二个排队；② 一次请求 6–40 秒，占着一个 worker 线程与一份 31 MB 模型；③ 多进程部署时模型按进程数翻倍（`ADR-031` §6 第 6 条） |
| `POST /teacher/resumes/batch-download` | 50 份简历逐个渲染 PDF → 拼 ZIP → 同步流式返回 | ① 一次最多 50 次 `--print-to-pdf` 子进程，内存与句柄一起涨（OOM 风险正是老板点名的）；② 请求期间连接一直挂着，用户看不到进度；③ 与 OCR 抢同一批线程 |

`ADR-029` §5 第 4 条当时写的是「要异步得先解决**任务队列与文件存储的归属**」，
`AGENTS.md` §3.2 把 Redis / ARQ 从禁令改成了「事实与选择，引入要先过 §4.6 的 ADR」。
本篇就是那次 ADR，同时回答三件事：**队列怎么选**、**并发怎么隔离**、**文件与密钥归谁**。

## 2. 决策

### 2.1 选型：FastAPI + Redis + ARQ，不选 Celery / RQ / Dramatiq

| | ARQ | Celery | RQ | Dramatiq |
|---|---|---|---|---|
| 与现有栈 | **asyncio 原生**，worker 就是一个 `async def` | 同步优先，async 要另配 | 同步 worker | 同步 worker |
| 依赖面 | `arq` + `redis`（+ `hiredis`），**3 个包** | `celery` + 传输层 + 结果后端，通常还要 ` kombu` / `billiard` | `rq`（+ `click`） | `dramatiq` + broker |
| 额外组件 | **无**（Redis 既做 broker 也做结果） | 生产上常配 beat / flower | 无 | 无 |
| 失败语义 | 任务里抛什么就记什么，`max_tries` 显式 | 丰富但重 | 简单 | 中等 |

**选 ARQ 的三条理由**，每条都能对上本仓的既有约束：

1. **我们的业务代码已经是 async**（`/resumes/import-pdf` 是 `async def`，`openai` 客户端是 async）。
   ARQ 的 worker 直接 `await` 我们的服务，不需要「在同步 worker 里 `asyncio.run()`」这种套娃。
2. **只有一个外部组件**（Redis）。本仓到今天仍然只有 MySQL 一个外部服务，加第二个已经是重大决策，
   不该同时再引入 beat / flower / 结果后端三件套。
3. **失败语义要能被我们自己解释**。ARQ 让我们**不**依赖它的结果存储（见 §2.3），
   只借它的队列与在途状态——这一点 Celery 也能做，但代价是几十个配置项。

**Redis 版本**：`redis>=5.0`（`arq 0.28` 要求），解析到 `redis 5.3.1` + `hiredis 3.5.0`。

### 2.2 队列隔离：三条队列、三个 worker 进程、三个 `max_jobs`

老板点名的病根是「OCR 和 PDF 互相阻塞」，所以隔离做在**队列**这一层，而不是在同一个 worker 里
靠优先级或锁去调度：

| 队列名（ARQ） | 装什么 | `max_jobs` | 为什么是这个数 |
|---|---|---|---|
| `arq:queue:ocr` | `import_pdf`（PDF 文本层 / OCR / 结构化） | `WORKER_OCR_MAX_JOBS`，默认 **4** | 吃 CPU，且**共用一个 OCR 引擎**；4 与「一台 8 核机器留一半给 web」相当，再高只是把 31 MB 模型的那份内存压得更紧 |
| `arq:queue:pdf` | `batch_download`（无头浏览器渲染 + 打包） | `WORKER_PDF_MAX_JOBS`，默认 **2** | 每份 PDF 是一次 `msedge --print-to-pdf` 子进程，**极其吃内存**；2 是「两个老师同时导出不排队」与「不 OOM」之间的那个点 |
| `arq:queue:default` | 常规 I/O 任务（今天没有调用方，先占位） | `WORKER_DEFAULT_MAX_JOBS`，默认 **10** | 纯等待型任务，10 与 `AI_MAX_CONCURRENCY` 同一量级 |

隔离的**判据是可观测的**：`app/worker.py` 里三个 `WorkerSettings` 子类各自钉死
`queue_name` 与 `max_jobs`，`tests/test_tasks.py::test_queue_isolation_is_wired` 逐个比对
「settings 的 max_jobs == 配置项」「三条 queue_name 互不相同」「每个队列只注册了自己那类任务」。
**一条队列里混装两类任务 = 这条用例红**，而不是等生产上排队才发现。

**为什么不是「一个 worker 里按优先级调度」**：ARQ 的 worker 一次取 N 个 job 后就用
`asyncio` 并发跑它们，**没有抢占式优先级**。要在同一个进程里做隔离，只能自己实现
「先查 PDF 在跑几个、再决定要不要取 OCR」，那等于在 ARQ 之上再写一个调度器，
而它的正确性没人能验收。三条队列 + 三个进程是**操作系统级的隔离**：
PDF 那两条把内存吃光，OCR 那四条照样转。

**部署形态**（`docker-compose.yml` 与 `docs/DEVELOPMENT.md` 都写）：
web 一个进程 + `worker-ocr` / `worker-pdf` / `worker-default` 三个 worker 进程，
各自 `arq app.worker.<Settings>`。缺 RAM 的机器可以只起 `worker-ocr`（导入仍可用），
`batch-download` 会一直停在 `pending` —— 这是**可见的**，不是静默失败。

### 2.3 任务状态：自己的 Redis 记录为准，ARQ 只回答「还在不在跑」

ARQ 自己会存 job 状态与结果（`arq:job:<id>` + 结果 key），但**不能拿它当唯一真相**：

1. ARQ 的结果里**没有失败原因**（`ResultInfo` 只有 `success` / `result`），
   而我们需要把「没配 Key」「这份 PDF 没识别到文字」原样告诉用户（`ADR-031` §2.5 的五个码）；
2. 结果保留时长（`keep_result`）是给「想知道上次跑出什么」用的，
   而我们要的是「这条任务现在什么状态、成功了给什么、失败了为什么」这一份**结构化的记录**；
3. PDF 产物（ZIP）在磁盘上，ARQ 丢不丢结果都不该影响用户还能不能下载。

所以状态机**双层**：

```
我们自己的记录（Redis hash，`task:<task_id>`）
  owner_id / kind / queue / created_at / ip / stage / state / error_code / error_message / result_json / file_*
ARQ 的 job（同一个 task_id 作为 `_job_id`）
  只用来回答 pending → running 的推进，以及「job 不见了」这一种异常
```

`state` 对外只有四个值（老板点名的那四个）：`pending` / `running` / `complete` / `failed`。
判定顺序写死在一处（`app/services/task_service.py::status`）：

| 记录里的 state | ARQ status | 对外 state |
|---|---|---|
| `complete` / `failed` | 不看 | 原样（终态只写一次，后到的 worker 覆盖不了） |
| `queued` | `deferred` / `queued` | `pending` |
| `queued` | `in_progress` | `running` |
| `queued` | `not_found` | `failed` + `TASK_LOST`（worker 崩了 / 任务过期 / 记录还在但 job 没了） |
| 记录不存在 | — | 404（与「不是你的任务」同一个响应，不做任务 id 探测器） |

**终态只写一次**：`mark_complete` / `mark_failed` 用 Lua-free 的「先读后写 + 只在 state 为
`queued`/`running` 时写」实现（`RedisTaskStore.finish`），这样「超时被 ARQ 杀掉之后
worker 又把成功写回来」这种竞态不会把 `failed` 翻成 `complete`。

### 2.4 BYOK 的 Key 怎么跨进程：加密后短存，取用即删（**对 `ADR-009` 的显式修订**）

这是本篇最需要被记住的一条，因为它是**唯一一处与既有 ADR 有张力**的地方。

`ADR-009` 的原文口径是「Key 只来自请求头、只在本次请求的内存中、不落库、不写日志」。
异步化之后有一条硬事实：**worker 是另一个进程**，用户不会在 worker 里再发一次 Key。
要么放弃异步，要么让 Key 跨进程走一次。

**决定**：让 Key 以**服务端密钥加密的密文**在 Redis 里短暂存在，worker 取用后**第一时间删除**。

| 项 | 落地 |
|---|---|
| 存哪 | `task:key:<task_id>`（与任务记录分开的 key，避免任何一次 `HGETALL` 把密文当业务字段读出来） |
| 存什么 | Fernet 密文。密钥由 `jwt_secret_key` 经 SHA-256 派生（`cryptography` **已是既有依赖**，不新增包） |
| 存多久 | `SETEX`，TTL = `WORKER_JOB_TIMEOUT_SECONDS`（默认 900 秒）；任务读完立刻 `DEL`，**不是**任务结束才删 |
| 不进哪里 | 不进 ARQ job 的 args/kwargs（`Job.info()` 与任何 arq 面板都看不到）、不进任务记录、不进日志、不进响应、不进异常消息 |
| 单次性 | `getdel`（`GETDEL` 命令）语义：读到即删。重试同一任务时读不到 Key → `AI_KEY_MISSING`，而不是静默成功 |

**为什么这不算推翻 `ADR-009`**：被禁的是「Key 落进**可长期读取**的存储」与「Key 出现在日志/响应里」。
这里密文的生命周期是「一个任务跳超时」量级，且**没有服务端密钥就读不出明文**——
拿到 Redis 快照的人拿到的是 Fernet 密文。这是「为了让用户不必为同一件事输两次 Key」
所付出的最小代价，代价本身写在 §3。

**没有第二把 Key 的地方**：`batch_download` 一个字节都不出网（`ADR-029` §2.6），
所以它**不碰**这张表，`task:key:*` 只出现在 `import_pdf` 上。

### 2.5 文件归属：`temp/imports/` 与 `downloads/`，都带 TTL，都不长期留存

| 目录 | 谁写 | 谁删 | 名字怎么定 |
|---|---|---|---|
| `temp/imports/`（`WORKER_IMPORT_DIR`） | 上传端点（`202` 之前落盘） | worker 任务**结束就删**（成功/失败都删，`finally`） | `<task_id>.pdf`（不采信文件名，见下） |
| `downloads/`（`WORKER_DOWNLOAD_DIR`） | worker（`batch_download` 成功时） | ① 下载端点**发一次就删**；② PDF worker 启动时按 mtime 清掉过期文件（`clear_stale_downloads`，**不清空目录**）；③ TTL 到点后 `/tasks` 报 `failed`+`TASK_EXPIRED` | `<task_id>.zip` |

三条刻意的选择：

1. **文件名不用用户的文件名**：`<task_id>.pdf` 让路径里不可能出现 `..`、`/`、控制字符，
   「上传的文件名」这件事在异步链路里根本不需要（我们从不按它做任何判断）。
2. **上传即落盘，而不是把字节塞进 job**：5 MB 的字节进 Redis 会让一次导入在 Redis 里
   留下一份 5 MB 的 value（还可能被 AOF 写两遍）。落盘 + 传路径是唯一不把 Redis 当文件系统用的形态。
3. **下载 URL 是单次性的**（`GET /tasks/{task_id}/download`）：发完就删，
   第二次拿到 410 + 一句「请重新发起导出」。所以「短时下载 URL」不是靠过期时间假装，
   而是**真的只有一个窗口**。

### 2.6 API 契约：两个 202、一个轮询、一个下载

```
POST /api/v1/resumes/import-pdf        → 202 {task_id, kind:"import_pdf", state:"pending"}
POST /api/v1/teacher/resumes/batch-download → 202 {task_id, kind:"batch_download", state:"pending"}
GET  /api/v1/tasks/{task_id}           → 200 见下
GET  /api/v1/tasks/{task_id}/download  → 200 application/zip（一次性），410 已下载/过期
```

轮询响应（`data`）：

```json
{
  "task_id": "…", "kind": "import_pdf", "state": "pending|running|complete|failed",
  "stage": "queued|extracting|structuring|rendering|packaging|null",
  "progress": 5,                       // 0–100，由 stage 推出来（唯一算法在服务层）
  "created_at": "2026-11-16T12:00:00+08:00", "finished_at": null,
  "result": { "profile": {…}, "meta": {…} }              // import_pdf 完成时
           | { "filename": "resumes_….zip", "bytes": 123, "download_url": "/api/v1/tasks/…/download" },
  "error": null | { "code": "PDF_INVALID", "message": "…", "retryable": false }
}
```

- **202 而不 200**：请求确实被接受了、但业务还没做完，这是 202 的字面含义；
  把「排队了」报成 200 会让任何按状态码判断的调用方以为结果已经在手上。
- **`error.code` 复用 `ADR-031` 的五个码**（`PDF_TOO_LARGE` / `PDF_INVALID` /
  `AI_KEY_MISSING` / `AI_SERVICE_UNAVAILABLE` / `AI_OUTPUT_INVALID`），只新增三个
  与任务本身有关的：`TASK_LOST`（job 不见了）、`TASK_EXPIRED`（产物已被清掉）、
  `TASK_INTERNAL`（任务里抛了没分类的异常）。**任务失败不是 HTTP 错误**：
  轮询端点对「任务失败」返回 200 + `state=failed`，因为请求本身成功地被回答了。
- **`message` 一律来自 `ErrorMessage`**（G3 的既有纪律）：worker 里抛出的上游异常文本
  只进日志，不进记录、不进响应。
- **入队失败必须 503，不许假装 202**：Redis 连不上 / 没配 `REDIS_URL` 时，
  两个入队端点返回 **503**。「先回 202 再想办法」会让用户永远等一个不存在的任务。
  消息本身走全站 5xx 的既有口径——**固定一句**「服务暂时不可用，请稍后重试。」，
  `details.reason = "TASK_QUEUE_UNAVAILABLE"` 供排障（与 `PDF_RENDERER_UNAVAILABLE`
  那个 503 完全同一处置）。这里没有为「队列挂了」单独开一句人话，因为 5xx 一律不上屏
  具体文案是 F1c 定的红线，破一次就会有第二个例外。
  → **第 31 轮补口（`ADR-034`）**：`development` / `local` 下**没配** `REDIS_URL` 不再
  返回 503，而是走内存降级（任务在 web 进程内执行，契约仍是 202 + 轮询）。
  这不是把这一条放松：`test` 环境与其它环境名仍然 503，`production` 仍然启动即失败；
  变的只是「本地开发默认值」，而且换的是执行位置，不是响应形状。

### 2.7 两个端点的行为变化（如实记账：这是**破坏性**契约变更）

| 端点 | 之前 | 现在 | 谁受影响 |
|---|---|---|---|
| `POST /resumes/import-pdf` | 200 + `{profile, meta}` | **202** + `{task_id}`，结果从 `/tasks/{id}` 取 | 前端 store；`tests/test_resumes_import_pdf.py` 全部用例的取结果方式 |
| `POST /teacher/resumes/batch-download` | 200 + `application/zip` | **202** + `{task_id}`，ZIP 从 `/tasks/{id}/download` 取 | 前端看板页；`tests/test_teacher_tools.py` 的 6 条批量下载用例 |

**同步的兜底路径不保留**（不加 `?sync=1`）：两条形态同时存在就是两套错误语义、
两套审计时机、两份测试，而它们的行为差异只在「快慢」上——那正是队列存在的理由。

**错误发生的时机变了**：以前「没配 Key」在请求里就 422，现在是任务失败后
`state=failed` + `error.code=AI_KEY_MISSING`。所以上传端点在**入队前**仍做三件零成本的检查
（体积超限 → 422 `PDF_TOO_LARGE`；没带 Key → 422 `AI_KEY_MISSING`；限流），
把「用户能立刻修」的错留在同步路径上；而「文件读不出字」「AI 挂了」这类必须真跑一遍才知道的，
才落到任务终态。这条分界是可测的：`tests/test_tasks.py` 两条用例分别钉住两边。

### 2.8 重试：`max_tries = 1`，不自动重试

ARQ 默认 `max_tries=5`。这里**显式改成 1**，理由是本项目的成本模型：
`import_pdf` 的重试会重跑 OCR（1.8 秒/页）并**再烧用户一次自己的 AI 额度**；
`batch_download` 的重试会重跑最多 50 次浏览器渲染。自动重试在这两条链路上
都是「用用户的钱换一个我们自己不确定会不会好的结果」。失败就是失败，
用户在界面上点一次「重试」比我们在后台偷偷重试 4 次诚实。

### 2.9 配置（三项并发上限 + 六个运维项）

| 配置 | 默认 | 作用 | 为什么是这个默认 |
|---|---|---|---|
| `WORKER_OCR_MAX_JOBS` | 4 | `arq:queue:ocr` 并发 | 老板指定；受 CPU 核数影响（每 job 一次 OCR 推理 + 一次 AI 等待） |
| `WORKER_PDF_MAX_JOBS` | 2 | `arq:queue:pdf` 并发 | 老板指定；每 job 一次浏览器子进程，防 OOM |
| `WORKER_DEFAULT_MAX_JOBS` | 10 | `arq:queue:default` 并发 | 老板指定；纯 I/O |
| `REDIS_URL` | 空 | 队列与任务记录 | 空 = 任务功能不可用（503），**不静默回落内存队列**；`production` 下缺失则启动即失败（§4.3） |
| `WORKER_JOB_TIMEOUT_SECONDS` | 900 | 单任务超时（也是 Key 密文的 TTL） | 导入 6–40 秒、50 份导出约 100–300 秒，900 秒是「真的卡住了」而不是「慢」 |
| `WORKER_TASK_TTL_SECONDS` | 1800 | 任务记录保留 | 用户刷新页面/换设备后仍能取到结果；半小时后重查一律 404 |
| `WORKER_IMPORT_DIR` | `temp/imports` | 上传暂存 | 与 `downloads/` 都在仓库根、都进 `.gitignore` |
| `WORKER_DOWNLOAD_DIR` | `downloads` | ZIP 产物 | 同上 |
| `WORKER_BATCH_MAX_ITEMS` | 50 | 一次打包上限 | 沿用 `ADR-029` §2.3 的 50，不放宽 |

三项 `MAX_JOBS` 都用 `Field(ge=1, le=64)`：写 0 会让 worker 一个 job 都不取（表现为
「任务永远 pending」），写 10000 会让 OOM 变成必然——两者都在启动时 422 掉，
而不是留到运行期。

## 3. 后果

**正面**

1. 两条重链路不再占 HTTP 连接：上传/点击后**立即**拿到 `task_id`，进度可见（`stage` + `progress`）。
2. 队列隔离之后，一个老师导 50 份 PDF 也不会把学生的扫描件导入挤到超时——
   这是本篇最主要的产品价值。
3. OOM 面收窄：浏览器渲染的并发从「web 进程里有多少线程」变成「`WORKER_PDF_MAX_JOBS=2`」。
4. OCR 模型从「每个 web worker 各一份」变成「OCR worker 各一份」，且**进程启动时预热一次**
   （`on_startup` 里 `get_ocr_engine()`），首个扫描件不再付 2.2 秒冷启动。

**代价 / 如实登记**

1. **两个外部组件变成三个**（MySQL + Redis + 无头浏览器）。Redis 一挂，导入与批量导出两条功能
   直接不可用（503），其它功能不受影响。这是唯一新增的单点。
2. **`ADR-009` 的 Key 生命周期被放宽了一格**（§2.4）：密文在 Redis 里活一个任务超时的时间。
   若要回到「Key 完全不落任何存储」，唯一的路是不做异步——那等于放弃本篇的目标。
3. **契约破坏性变更**：两个端点的响应码与体都变了（§2.7），前端与 40+ 条既有用例必须同批改。
4. **失败要等一会儿才知道**：以前「没配 Key」立刻 422，现在（只有在绕过前端本地检查时）
   要等任务跑完。缓解是上传端点仍做零成本预检（§2.7）。
5. **多一个进程要部署**：三个 worker 各自一个进程，部署文档与 compose 文件都要写清
   「不起 worker 就只有队列没有消费者」——任务会停在 `pending` 直到 TTL，**不报错**。
   这条是本篇最容易在部署时踩到的坑，所以 `README`/`DEVELOPMENT.md` 都点名。
6. **本机没有 Redis**（2026-11-16 实测 `127.0.0.1:6379` 拒绝连接），所以
   **真实 ARQ worker 在真 Redis 上的运行本次未验证**，只验证到「fakeredis 上
   enqueue / status / worker 执行」这一层。刻意不写成「已在真实 Redis 上验证」。

## 4. 测试策略

| 层 | 怎么测 | 覆盖什么 |
|---|---|---|
| 契约 | `tests/test_tasks.py` 用 `fakeredis.aioredis.FakeRedis`（`FakeServer` 跨事件循环共享）替换 `TaskRuntime` | 202 两个端点、`GET /tasks` 四态、越权 404、下载一次性 410、入队失败 503、`REDIS_URL` 缺失 503 |
| 隔离 | 源码级：三个 `WorkerSettings` 的 `queue_name` / `max_jobs` / `functions` 逐项比对 | 「一条队列混装两类任务」「max_jobs 没接配置」当场红 |
| BYOK | 任务是 `failed` 之后，断言 Redis 里 `task:key:<id>` **不存在**；入队后 `GETDEL` 前只有密文（不含明文 Key） | 「Key 明文进了 Redis」与「Key 用完没删」两种 |
| 业务 | 既有 `tests/test_resumes_import_pdf.py` / `tests/test_teacher_tools.py` 改为「上传 → 跑任务 → 轮询 → 取结果」的同形断言 | 五个 422 码、三级降级、ZIP 结构、审计行形状**一条都不许丢** |
| 前端 | `usePollTask.spec.ts` + store/视图用例（假时钟推进 1.5 秒） | 轮询到终态、失败上报 `error.code`、组件卸载即停 |

`fakeredis` **只进 dev 依赖**（`dependency-groups.dev`），生产镜像里没有它。

## 5. 验收判据

1. `POST /resumes/import-pdf` 带 Key 上传 → **202**，体里只有 `task_id` / `kind` / `state`，
   **没有** `profile`；不带 Key → 422 `AI_KEY_MISSING`（同步预检，§2.7）。
2. 任务跑完 → `GET /tasks/{id}` 连续可观测到 `pending` → `running`（`stage` 变化）→ `complete`，
   `progress` 单调不减，`result.profile` 与旧契约逐格一致。
3. 任务失败 → `state=failed` + `error.code`（五码之一）+ `error.message`，且 `retryable` 与
   `ADR-031` §2.5 表一致。
4. **别的人** `GET` 同一个 `task_id` → 404（与「不存在」同响应）。
5. `POST /teacher/resumes/batch-download` → 202；跑完后 `download_url` 取到 ZIP（结构同
   `ADR-029` §2.3），**第二次取 410**，磁盘上的文件已删。
6. 入队时 Redis 不可用 → 503 `TASK_QUEUE_UNAVAILABLE`，且**没有** 202。
7. 队列隔离：三个 `WorkerSettings` 的 `max_jobs` 分别等于三个配置项；三条 `queue_name` 互不相同；
   `import_pdf` 只注册在 ocr 队列，`batch_download` 只注册在 pdf 队列。
8. BYOK：任务结束后 `task:key:<id>` 不存在；任务记录与 ARQ job 的 args 里都不含明文 Key。
9. 前端：导入点一次 → 遮罩出现 → 轮询 1.5 秒一次 → 成功后回填表单且 `dirty` 为真；
   失败时展示后端 `error.message` 与（仅 `AI_KEY_MISSING` 时）配置 Key 的链接。
10. 教师端：打包按钮进入「正在准备…」，完成后自动触发下载；失败展示原因。

## 6. 替代方案（都想过，都不选）

| 方案 | 为什么不选 |
|---|---|
| 不引入队列，改成 `BackgroundTasks` / `asyncio.create_task` | 进程重启就丢；多 worker 部署时状态在各自进程里；没有任何并发上限与隔离——老板点名的两个病症一个都没治 |
| Celery + Redis | 依赖面与组件面都大一圈（§2.1），且我们的业务是 async 的 |
| 一个队列 + worker 内部优先级 | ARQ 没有抢占式优先级；自己写调度器等于写一个没人验收的中间件（§2.2） |
| 把任务状态完全交给 ARQ 的结果存储 | ARQ 不存失败原因，而「为什么失败」正是前端要展示的东西（§2.3） |
| 任务结果里带 ZIP 的 base64 | 一次 50 份导出的 ZIP 是几十 MB，进 Redis 等于把 broker 当文件系统（§2.5） |
| 上传的字节直接塞 job args | 同上，且 5 MB 的 value 会被 AOF 翻倍（§2.5） |
| 队列不可用时回落到「同步执行」 | 两套语义（§2.7），且回落那一支恰好是 CPU/内存最重的那条，正是要避免的 |
| 自动重试（ARQ 默认 5 次） | 每次重试都烧用户自己的 AI 额度 / 重跑 50 次渲染（§2.8） |
| 引入 S3 放产物 | 又一个外部服务，而产物的生命周期只有一个下载窗口（§2.5）；要换先过 §4.6 |
| 给 `import-pdf` 保留 `?sync=1` | 见 §2.7，两套形态的成本落在测试与错误语义上 |

## 7. 遗留与下一步

1. **取消任务没有入口**：ARQ 有 `Job.abort()`，但「取消一个已经在跑的 OCR」需要协作式中断，
   而我们的 OCR 调用是黑盒。不做，也不用「取消」按钮骗人。
2. **没有任务列表**：只有凭 `task_id` 轮询。要做「我的导入历史」得先有一张表 + 一篇 ADR。
3. **`downloads/` 的兜底清理靠 PDF worker 启动时扫一遍**（`on_startup` → `clear_stale_downloads`），
   而且是**按 mtime 只删过期的**，不是清空目录——三个 worker 共用一份盘，清空会误删
   另一条任务刚写好的产物。没有独立的定时清理任务：那需要 cron job 与「清理也被隔离」的
   口径，本批不做。
4. **`WORKER_DEFAULT_MAX_JOBS` 今天没有调用方**：它是老板点名要的三项之一，
   队列与 worker 都建好了，但没有任务注册进去。**这是一个空跑的进程**，如实记账；
   第一个常规 I/O 任务（例如「批量匹配」）落地时直接用它。
5. **多机部署**：任务记录与队列都在同一个 Redis 里，天然支持多机；但 `temp/imports/` 与
   `downloads/` 是**本地盘**，所以「上传在 A 机、worker 在 B 机」不成立。
   要跨机得先有共享存储（S3 / NFS），那是另一篇 ADR。
6. **超时被取消时线程还在跑**：重活走 `asyncio.to_thread()`（不占住事件循环），
   而 Python **杀不掉一个正在跑的线程**。`job_timeout` 到点后我们记 `failed` + `TASK_LOST`，
   但那个线程可能还在渲染/认字，直到自己跑完——它产出的文件会被下一次启动的过期清理收走，
   记录也不会被二次翻写（终态只写一次），所以不会出现「失败后又冒出一个结果」。
   真要强制中断，得把重活换成子进程（可以 `kill`），那是另一篇 ADR 的事。
7. **Redis 的持久化与内存策略没有定**：本批只要求「进程重启后任务不丢」这一档，
   `maxmemory-policy` 该是 `noeviction`（任务记录被淘汰等于任务凭空消失），
   部署文档写了这一句，但 compose 里没有显式配——留给运维那一批。
