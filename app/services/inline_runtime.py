"""内存降级运行时（第 31 轮，`ADR-034`）。

没配 `REDIS_URL` 且环境允许时（`Settings.inline_task_runtime`），任务在本进程里跑：
记录进内存，执行走 `asyncio.create_task`。这条路径**只服务本地开发**——
单进程、没有并发上限、进程重启即丢（三条代价写在 `ADR-034` §3）。

**为什么这个文件在 `services/` 而不是 `integrations/`**：内存适配器本身
（`MemoryTaskStore` / `InlineTaskQueue`）在 `integrations/task_inline.py`，
但它们要执行的 `TaskRunner` 属于服务层，而 `AGENTS.md` §4.1 的分层是
`api → services → repositories → integrations`（下面不许 import 上面）。
所以「把两者接起来」这件事只能住在这里：队列拿到的是一个**执行器函数**，
不是对 `TaskRunner` 的硬依赖。

**它与 `integrations/task_runtime.py` 的形状刻意一致**（单例 + `reset_*`）：
两个 runtime 提供者对被换进来这件事完全无感，`TaskService` 一行都不用改。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.core.config import get_settings
from app.integrations.task_inline import InlineTaskQueue, MemoryTaskStore
from app.integrations.task_runtime import TaskRuntime
from app.integrations.task_store import TaskStore
from app.services.task_runner import TaskRunner

logger = logging.getLogger(__name__)

_inline_runtime: TaskRuntime | None = None


async def get_inline_task_runtime() -> TaskRuntime:
    """取内存运行时（进程内一份）。第一次调用时把存储与队列接好。"""
    global _inline_runtime
    if _inline_runtime is not None:
        return _inline_runtime
    settings = get_settings()
    store = MemoryTaskStore(
        record_ttl_seconds=settings.worker_task_ttl_seconds,
        secret_ttl_seconds=settings.worker_job_timeout_seconds,
    )
    queue = InlineTaskQueue(executor=_make_executor(store))
    _inline_runtime = TaskRuntime(queue=queue, store=store)
    logger.info(
        "任务运行时：内存降级（未配置 REDIS_URL）——任务在本进程内执行，"
        "仅单实例有效，且不应用 WORKER_*_MAX_JOBS 并发上限（ADR-034）"
    )
    return _inline_runtime


async def reset_inline_task_runtime() -> None:
    """关掉并清空（应用 shutdown 与测试用）。重复调用幂等。

    内存模式没有连接要关，但**在途任务必须取消**：不取消的话热重载之后
    上一轮的 OCR 线程还在占 CPU，表现是「改完代码重启，机器越来越慢」。
    """
    global _inline_runtime
    runtime, _inline_runtime = _inline_runtime, None
    if runtime is not None:
        await runtime.close()


def _make_executor(store: TaskStore) -> Callable[[str], Awaitable[None]]:
    """把一个 `task_id` 跑完的执行器。

    **每次执行都新建 `TaskRunner`**：它没有可变状态，早建晚建没有收益；而延迟 import
    `dependencies` 是为了绕开循环导入（`dependencies` 反过来要 import 本模块）。
    """

    async def _execute(task_id: str) -> None:
        from app.services import dependencies

        runner = TaskRunner(
            store,
            build_importer=dependencies.build_resume_pdf_import_service,
            teacher_scope=dependencies.teacher_tools_scope,
        )
        await runner.run(task_id)

    return _execute
