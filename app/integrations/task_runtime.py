"""任务运行时（队列 + 记录存储）的进程内单例（第 30 轮，`ADR-033`）。

**为什么要单例**：`ArqRedis` 与 `redis.asyncio.Redis` 各自持有一条连接池，
每个请求建一份等于每请求开一条 TCP（还要付 TLS / 握手），而任务端点本来就是
「一次上传 + 若干次轮询」。进程内一份，随进程生死。

**为什么 `get_task_runtime()` 是 async**：ARQ 的 `create_pool()` 本身要 await
（它是唯一能把 `RedisSettings` 变成连接池的入口）。首次调用建好之后就只是读一个变量。

**测试怎么替换**：`tests/task_support.py` 把 `app.services.dependencies.get_task_runtime`
换成一个「每次新建、共享同一个 `FakeServer`」的工厂——因为 fakeredis 的客户端绑定
在创建它的事件循环上，而测试里「API 请求」与「跑 worker」分别在两个循环里。
生产代码不需要知道这件事（生产只有一个循环）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.task_errors import TaskQueueUnavailableError
from app.integrations.task_queue import ArqTaskQueue, TaskQueue
from app.integrations.task_store import RedisTaskStore, TaskStore


@dataclass(frozen=True)
class TaskRuntime:
    queue: TaskQueue
    store: TaskStore

    async def close(self) -> None:
        await self.queue.close()
        await self.store.close()


_runtime: TaskRuntime | None = None
#: 进程内互斥：两个并发请求同时首次触发时只建一份连接池。
#: `asyncio.Lock()` 在 3.10+ 不绑定事件循环，模块级创建是安全的。
_lock = asyncio.Lock()


async def get_task_runtime() -> TaskRuntime:
    """取运行时；没有 `REDIS_URL` 时抛 `TaskQueueUnavailableError`（对外 503）。"""
    global _runtime
    if _runtime is not None:
        return _runtime
    async with _lock:
        if _runtime is not None:
            return _runtime
        url = get_settings().redis_url
        if not url:
            raise TaskQueueUnavailableError("未配置 REDIS_URL")
        _runtime = TaskRuntime(
            queue=await ArqTaskQueue.create(url),
            store=RedisTaskStore.from_url(url),
        )
        return _runtime


async def reset_task_runtime() -> None:
    """关掉并清空（应用 shutdown 与测试用）。重复调用幂等。"""
    global _runtime
    runtime, _runtime = _runtime, None
    if runtime is not None:
        await runtime.close()
