# ADR-034: 本地零依赖降级——没有 Redis 时用进程内内存运行时跑通重任务

- 状态：已采纳（Accepted，ADR 先行，代码随本文件同分支落地）
- 日期：2026-11-16（第 31 轮）
- 关联需求：FR-01（档案 / PDF 导入）、FR-08（教师端导出）；`ADR-033`（Redis + ARQ 异步任务）、`ADR-031`（PDF 导入的三级链路）、`ADR-029`（批量下载）
- 关联裁定：第 31 轮老板指令「为 Redis + ARQ 异步任务架构实现『本地零依赖降级』，使开发者在不启动 Redis 和 ARQ Worker 时，依然能在本地跑通 PDF 导入和导出功能」「如果 ENV == 'development'（或者 .env 中 REDIS_URL 为空），则启用内存降级模式」「降级逻辑仅在『未配置 REDIS_URL』且『非 test 环境』时生效」

## 1. 背景：第 30 轮把两条链路搬进了队列，也把一个开发门槛搬了进来

第 30 轮（`ADR-033`）之后，`POST /resumes/import-pdf` 与
`POST /teacher/resumes/batch-download` 只做「落盘 + 入队 + 202」，真正的解析与渲染
在 `arq:queue:ocr` / `arq:queue:pdf` 的 worker 进程里跑。这个决定解决的是 OOM 与
资源隔离（那两个问题是真的），但它引入了一个**新的失败模式**：

> 一个只想改简历表单的前端同学，`uvicorn app.main:app` + `npm run dev` 起来之后，
> 点「导入 PDF」会得到一个 503「服务暂时不可用」——或更糟：如果他有 `REDIS_URL`
> 却没起 worker，得到的是 202 加一个**永远 pending** 的任务。

这条降级要解决的就是它：**开发机不装 Redis、不起 worker，也能把两条重链路走完**。

三条不能破的既有约束（决定了下面每一个取舍）：

1. **生产必须走真队列**（`ADR-033` §2.2 的并发上限只有在真 worker 里成立）；
2. **测试必须继续走 fakeredis + 真 `arq.Worker`**（那是 `ADR-033` §4 的证据链，
   换成内存实现就等于把「入队参数 / `_job_id` / 队列键名」这些最易坏的地方从覆盖里删掉）；
3. **契约不许分叉**：前端只认识 202 + `GET /tasks/{id}` 四态，降级模式**不得**改成
   「同步返回结果」——那会让前端出现两条代码路径，而本地跑通的那条不是生产那条。

## 2. 决策

### 2.1 触发条件：`REDIS_URL` 为空 **且** 环境在允许清单里

判定收在 `Settings.inline_task_runtime` 一个属性里（`app/core/config.py`），
判据是两件的**与**，不是或：

```text
没有 REDIS_URL  ∧  environment ∈ {"development", "local"}
```

| 情形 | 结果 | 为什么 |
|---|---|---|
| `development` / 无 `REDIS_URL` | **内存降级** | 老板要的那个场景：一把梭能跑全功能 |
| `development` / 有 `REDIS_URL` | 走真 Redis + ARQ | 有队列地址就是「我要测完整异步链路」的信号，不该被降级偷偷盖掉 |
| `test`（任何情况） | **绝不降级** | 测试的证据链是 fakeredis + 真 `arq.Worker`（`ADR-033` §4）；降级会把这条链路的覆盖悄悄删掉 |
| `production` / 无 `REDIS_URL` | **启动即失败** | `config.py::validate_production_redis_url` 既有判据，一个字没松 |
| 其它环境名（如 `staging`） | **503**，不降级 | 降级是**开发便利**，不是运行时兜底。一个挂着真实用户的 `staging` 悄悄把 OCR 跑进 web 进程，是那种「上线才发现」的错 |

「或」被改成「与」的理由：老板原话是「ENV == development（或者 REDIS_URL 为空）」，
而后面那句边界又写了「仅在**未配置 REDIS_URL** 且**非 test** 时生效」。两句取交集才
同时满足：`development` 里配了 Redis 的人是想测真队列的，不该被降级拦下来。

### 2.2 形状：换的是「运行时」，不是「任务」

`ADR-033` 已经把任务编排拆成了三层，降级只动最底下那一层的**两个适配器**：

```
TaskService / TaskRunner（一行不改，两种模式共用）
        │  依赖两个 Protocol
        ├── TaskStore   ← RedisTaskStore        │ MemoryTaskStore（新）
        └── TaskQueue   ← ArqTaskQueue          │ InlineTaskQueue（新）
```

| | Redis 模式 | 内存模式 |
|---|---|---|
| 状态与结果 | `task:<id>` hash + `task:key:<id>` 密钥（TTL 由 Redis 管） | 进程内 `dict[str, (TaskRecord, 过期时刻)]`，过期在访问时惰性清扫 |
| 执行 | 另一个进程（`arq`）取 job | 当前进程 `asyncio.create_task` 直接 `await TaskRunner.run(task_id)` |
| 「还在不在跑」 | `arq:job:<id>` 的 `JobStatus` | 我们自己持有的 `asyncio.Task` 句柄 |
| 并发上限 | `WORKER_*_MAX_JOBS` | **没有**（见 §3 第 2 条，本地妥协） |

**为什么不是「把 `enqueue` 写成 `run()` 然后直接 `await`」**：那样一次上传会在请求里
跑完 OCR（6–40 秒），HTTP 连接一直挂着，前端进度条永远看不到中间态——而这正是
`ADR-033` 要消灭的东西。`asyncio.create_task` 让**契约与生产完全一致**：202 立刻返回，
进度与结果都从 `GET /tasks/{id}` 来，前端一行都不用改。

**为什么 `InlineTaskQueue` 拿的是「执行器」而不是自己去 import `TaskRunner`**：
`app/integrations/` 在 `AGENTS.md` §4.1 的分层里位于 `services/` **下面**，
import-linter 的 `Application layers` 契约会当场拦下反向依赖。所以队列只持有一个
`Callable[[str], Awaitable[None]]`，由 `app/services/inline_runtime.py` 注入
（它把 `TaskRunner` 与 `dependencies` 里那两个装配函数接起来）。

### 2.3 内存存储的语义要与 Redis 对齐（否则降级会「本地绿、线上红」）

`MemoryTaskStore` 逐条对齐 `RedisTaskStore` 的**可观测**语义，不是「差不多」：

| 语义 | 为什么必须一致 |
|---|---|
| `finish()` 终态只写一次（已终态返回 `False` 且不改任何字段） | 超时与正常结束的取消竞态靠它；降级模式下这里松一档，本地就测不出那个 bug |
| `advance()` 不覆盖终态 | 同上 |
| 记录 TTL = `WORKER_TASK_TTL_SECONDS` | 「30 分钟内可轮询、之后 404」是 `ADR-033` §2.6 写下的契约 |
| 密钥 TTL = `WORKER_JOB_TIMEOUT_SECONDS`，`take_secret()` **读到即删** | BYOK 的 Key 生命周期（`ADR-009` / `ADR-033` §2.4）在本地也必须成立：**明文不落盘、只存密文、用完即消失** |
| `read()` 读到过期记录返回 `None` | 过期后的表现是 404 而不是「还能取到结果」 |

**密钥仍走 Fernet 加密**（`encrypt_secret`）：内存里存明文也能跑，但那样
「Key 的加密/解密链路」在本地就从来没被跑过，而它是这条链路里最容易被改坏的一环。

### 2.4 进程生命周期：随 web 进程生死，shutdown 取消在途任务

- 单例在 `app/services/inline_runtime.py`（进程内一份，与 `task_runtime.py` 的 Redis 单例同形）；
- `app/main.py` 的 lifespan 在启动时打一行**明确的**日志（`任务运行时：内存降级（无 REDIS_URL）`
  / `任务运行时：Redis`），让「我现在跑的是哪一种」可观测，而不是靠猜；
- shutdown 时先 `cancel()` 在途的 `asyncio.Task` 再丢弃存储：不取消的话
  「关掉服务」会留下几个还在跑 OCR 的线程，本地热重载时表现为端口没释放。

### 2.5 后端唯一的行为分叉：`get_task_service()` 的运行时提供者

`app/services/dependencies.py` 多一个内部函数 `_task_runtime()`：

```
inline_task_runtime 开启 → get_inline_task_runtime()
否则                     → get_task_runtime()（原样；没配 REDIS_URL 时抛 → 503）
```

两个分支返回的**都是** `TaskRuntime`，所以 `TaskService` / `TaskRunner` / 两个路由 /
前端一行都不改。测试里 `monkeypatch.setattr("app.services.dependencies.get_task_runtime", provider)`
照旧生效（`_task_runtime()` 在调用时才读这个名字），所以第 30 轮那套 fakeredis 链路
**完全不受影响**——这也是为什么本轮的降级逻辑不需要测试感知：`environment=test`
时它压根不进内存分支。

## 3. 代价与遗留（如实记账）

1. **单进程限制**：任务在 web 进程内跑，`uvicorn --workers 4` 下「A 进程入队、B 进程轮询」
   会 404（记录在 A 的内存里）。降级模式**只对单实例有效**，`README` 与 `.env.example`
   都把这句话写死。多进程要测就必须起 Redis（`ADR-033` 的形态）。
2. **没有并发上限**：`WORKER_OCR_MAX_JOBS` / `WORKER_PDF_MAX_JOBS` 在内存模式下**不生效**
   ——没有队列就没有「同时几个」。本地同时点三次导入会跑三个 OCR 任务，这是
   老板明确接受的妥协（「重任务在 Web 进程内运行可能会短暂阻塞请求，这是本地开发的正常妥协」）。
   重活仍走 `asyncio.to_thread`（`TaskRunner` 既有实现），所以**阻塞的是线程，不是事件循环**。
3. **进程重启 = 任务与产物全丢**：记录在内存里，`temp/imports/` 下的暂存文件成了孤儿。
   与 `ADR-033` §7 第 3 条同类：不做定时清理，靠下一次启动的过期清扫与
   `discard_import_file` 兜底；本地开发可接受（重启本来就该重来一次）。
4. **不做「降级时同步执行」**：契约必须与生产同形（§2.1 第 3 条约束）。代价是本地
   必须真的轮询一次才看到结果——这恰好是前端那条路径，等于顺手把前端也测了。
5. **worker 在降级模式下没有意义**：`arq app.worker.*` 仍会因缺 `REDIS_URL` 拒绝启动，
   错误消息里加了「内存降级模式下不需要 worker」的提示，避免有人以为起错了。

## 4. 判据（怎么证明它真的成立）

| 判据 | 用例 |
|---|---|
| `development` + 无 `REDIS_URL` → 内存运行时（`queue` 是 `InlineTaskQueue`） | `tests/test_inline_runtime.py::test_development_without_redis_uses_the_inline_runtime` |
| `test` 环境 + 无 `REDIS_URL` → **仍然 503**（降级被禁用） | `tests/test_inline_runtime.py::test_test_environment_never_degrades` |
| 内存运行时跑完一条导入：`pending` → `complete`，`result` 与 Redis 模式同形 | `tests/test_inline_runtime.py::test_inline_runtime_completes_a_task_end_to_end` |
| 失败也是任务终态（`failed` + 码），不是 4xx | `tests/test_inline_runtime.py::test_inline_runtime_records_a_failure_code` |
| 任务句柄丢了（异常路径）→ `TASK_LOST`，且只写一次终态 | `tests/test_inline_runtime.py::test_lost_inline_task_becomes_failed_once` |
| 记录过期 → `read()` 返回 `None`（等价 404） | `tests/test_inline_runtime.py::test_memory_store_expires_records_by_ttl` |
| 密钥读到即删、明文不出现 | `tests/test_inline_runtime.py::test_memory_store_takes_the_secret_once` |
| 既有 fakeredis 链路不受影响 | `tests/test_tasks.py` / `test_resumes_import_pdf.py` / `test_teacher_tools.py` 全量绿 |
