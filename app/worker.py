"""ARQ worker 的配置与任务入口（第 30 轮，`ADR-033` §2.2）。

**三条队列三个进程**，各起各的：

```bash
arq app.worker.OcrWorkerSettings      # arq:queue:ocr     —— 导入（文本层 / OCR / DeepSeek）
arq app.worker.PdfWorkerSettings      # arq:queue:pdf     —— 打包导出（无头浏览器）
arq app.worker.DefaultWorkerSettings  # arq:queue:default —— 常规 I/O（今天没有任务注册）
```

为什么不是「一个 worker 里靠优先级调度」：ARQ 的 worker 一次取 N 个 job 之后就用
`asyncio` 并发跑，**没有抢占式优先级**；在同一个进程里做隔离只能自己写调度器，
而那种调度器的正确性没人能验收。三条队列是操作系统级隔离：PDF 那两条把内存吃光，
OCR 这四条照样转（`ADR-033` §2.2）。

**每个队列只注册自己那类任务**（`KIND_QUEUE` 是唯一口径）：`import_pdf_task` 只出现在
`OcrWorkerSettings.functions` 里，`batch_download_task` 只出现在 `PdfWorkerSettings` 里。
`tests/test_tasks.py::test_queue_isolation_is_wired` 逐项反查这张表，混装当场红。

**`max_jobs` 一律等于配置项**（`WORKER_OCR_MAX_JOBS` 等）：老板点名的三项并发上限
如果只写在 `.env` 里而 worker 读的是写死的数字，那条配置就是个摆设。

**三个类都是平的、不继承**：ARQ 的 CLI 用 `settings_cls.__dict__` 收集参数
（`arq/worker.py::get_kwargs`），继承来的属性**一个都读不到**——写成基类 + 子类会让
`redis_settings` / `max_tries` 静默丢失，表现是 worker 连到 `127.0.0.1:6379` 的默认地址
并按默认 5 次重试。所以 `max_tries = 1` 这类「每条都不能少」的取值逐类写一遍，
并由 `test_queue_isolation_is_wired` 逐类比对（漏一条当场红）。

`max_tries = 1`：重试一次 `import_pdf` 会重跑 OCR 并**再烧用户一次自己的 AI 额度**，
重试一次 `batch_download` 会重跑最多 50 次浏览器渲染。自动重试在这两条链路上都是
「用用户的钱换一个我们不确定会不会好的结果」（`ADR-033` §2.8）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import ClassVar

from arq.connections import RedisSettings

from app.core import task_files
from app.core.config import get_settings
from app.core.task_kinds import QUEUE_DEFAULT, QUEUE_OCR, QUEUE_PDF
from app.integrations.task_runtime import get_task_runtime, reset_task_runtime
from app.services import dependencies
from app.services.task_runner import TaskRunner

logger = logging.getLogger(__name__)


def redis_settings() -> RedisSettings:
    """worker 的 Redis 地址。**没配就启动即失败**（`AGENTS.md` §4.3 同一纪律）。

    与 web 侧的区别：web 可以在没配 Redis 时活着（只有两个任务端点 503），
    而 worker 的全部意义就是消费队列——连地址都没有，起来只会空转。
    """
    url = get_settings().redis_url
    if not url:
        raise RuntimeError(
            "REDIS_URL 未配置：worker 无法启动（见 docs/DEVELOPMENT.md 的 Redis 一节）"
        )
    return RedisSettings.from_dsn(url)


async def _runner(ctx: dict[str, object]) -> TaskRunner:
    """进程内只建一次执行器（它没有可变状态，但装配里有连接池与 session 工厂）。"""
    existing = ctx.get("task_runner")
    if isinstance(existing, TaskRunner):
        return existing
    runtime = await get_task_runtime()
    runner = TaskRunner(
        runtime.store,
        build_importer=dependencies.build_resume_pdf_import_service,
        teacher_scope=dependencies.teacher_tools_scope,
    )
    ctx["task_runner"] = runner
    return runner


async def import_pdf_task(ctx: dict[str, object], task_id: str) -> str:
    """导入一条 PDF 简历。**返回 task_id 只是为了让 ARQ 的结果里有个可读的值**：
    真正的结果在我们的 Redis 记录里（`ADR-033` §2.3），这个返回值没有读取方。
    """
    await (await _runner(ctx)).run(task_id)
    return task_id


async def batch_download_task(ctx: dict[str, object], task_id: str) -> str:
    await (await _runner(ctx)).run(task_id)
    return task_id


def make_on_startup(
    queue_name: str, *, warm_ocr: bool = False, sweep_downloads: bool = False
) -> Callable[[dict[str, object]], Awaitable[None]]:
    """生成启动钩子（**工厂而不是继承**：见模块 docstring 那句 `__dict__`）。"""

    async def _on_startup(ctx: dict[str, object]) -> None:
        """公共启动：建好连接池（第一个任务不付握手成本）。"""
        await get_task_runtime()
        if warm_ocr:
            # OCR worker 专属（老板第 30 轮指令）。模型加载原本发生在「第一个真正用到
            # OCR 的请求」上（`ADR-031` §2.9 的懒建单例）——在 web 进程里那是对的
            # （不导扫描件的用户一分钱不付）；worker 这辈子只干这一件事，所以把 2.2 秒的
            # 冷启动挪到启动阶段。引擎仍是全局单例，预热与任务里懒建拿到的是同一个实例。
            from app.integrations.pdf_ocr import get_ocr_engine

            get_ocr_engine()
        if sweep_downloads:
            # 只有**产物生产者**（pdf 队列）在启动时扫一遍过期文件；且按 mtime 判，
            # 不清空目录——三个 worker 共用一份盘（`ADR-033` §2.5）。
            removed = task_files.clear_stale_downloads(get_settings().worker_task_ttl_seconds)
            if removed:
                logger.info("清理了 %s 个过期导出产物", removed)
        logger.info("worker 就绪（queue=%s, ocr_warm=%s）", queue_name, warm_ocr)

    return _on_startup


async def on_shutdown(ctx: dict[str, object]) -> None:
    """关掉连接池。**不清理 `downloads/`**：那是下次启动的事（产物要活到用户来取）。"""
    await reset_task_runtime()
    logger.info("worker 已关闭")


class OcrWorkerSettings:
    """`arq app.worker.OcrWorkerSettings` —— 导入队列。"""

    redis_settings = redis_settings()
    max_tries = 1
    keep_result = 60
    job_timeout = get_settings().worker_job_timeout_seconds
    queue_name = QUEUE_OCR
    max_jobs = get_settings().worker_ocr_max_jobs
    on_startup = make_on_startup(QUEUE_OCR, warm_ocr=True)
    on_shutdown = on_shutdown
    #: 只注册本队列那一类任务（`ADR-033` §2.2 的隔离口径）。
    functions: ClassVar[list[Callable[..., object]]] = [import_pdf_task]


class PdfWorkerSettings:
    """`arq app.worker.PdfWorkerSettings` —— 打包队列（极其吃内存，默认只跑 2 个）。"""

    redis_settings = redis_settings()
    max_tries = 1
    keep_result = 60
    job_timeout = get_settings().worker_job_timeout_seconds
    queue_name = QUEUE_PDF
    max_jobs = get_settings().worker_pdf_max_jobs
    on_startup = make_on_startup(QUEUE_PDF, sweep_downloads=True)
    on_shutdown = on_shutdown
    functions: ClassVar[list[Callable[..., object]]] = [batch_download_task]


class DefaultWorkerSettings:
    """`arq app.worker.DefaultWorkerSettings` —— 常规 I/O 队列。

    **今天一个任务都没注册**（`ADR-033` §7 第 4 条如实记账）：队列、并发上限与进程
    都建好了，第一个常规 I/O 任务（例如批量匹配）落地时直接往 `functions` 里加。
    """

    redis_settings = redis_settings()
    max_tries = 1
    keep_result = 60
    job_timeout = get_settings().worker_job_timeout_seconds
    queue_name = QUEUE_DEFAULT
    max_jobs = get_settings().worker_default_max_jobs
    on_startup = make_on_startup(QUEUE_DEFAULT)
    on_shutdown = on_shutdown
    functions: ClassVar[list[Callable[..., object]]] = []
