"""异步任务的测试支撑：fakeredis + 真的 ARQ worker（第 30 轮，`ADR-033` §4）。

**为什么不是「把队列整个换成假对象」**：那样一来 `enqueue_job` 的参数形状、
`_job_id` 的语义、`arq:queue:<name>` 的键名全都测不到，而这三件恰恰是「入队点与
worker 注册点对不上」时最先坏的地方。这里用 `fakeredis` 顶掉 Redis 本身，
**ARQ 与我们的适配器都是真的**：入队走 `enqueue_job`，出队走 `Worker`。

**为什么要按事件循环分桶**：`fakeredis` 的客户端绑定在创建它的事件循环上，
而测试里「HTTP 请求」跑在 `TestClient` 的 portal 循环里、「跑 worker」跑在
另一个新的循环里。所以每次取运行时都按当前循环开一份**新的客户端**，
但所有客户端共享同一个 `FakeServer`——数据在服务端，不在连接里。
生产代码没有这个问题（一个进程一个循环），所以这段特例只活在测试里。
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import fakeredis
import fakeredis.aioredis
from arq.connections import ArqRedis
from arq.worker import Worker

from app.core.task_kinds import ALL_QUEUES
from app.integrations.task_queue import ArqTaskQueue
from app.integrations.task_runtime import TaskRuntime
from app.integrations.task_store import RedisTaskStore

#: 全部客户端共享的服务端（数据在这里，与事件循环无关）。
SERVER = fakeredis.FakeServer()


def reset_server() -> None:
    """清空 fakeredis 里的所有键（每个用例一次，别让上一条任务的记录漏给下一条）。

    `FakeServer` 本身没有 `keys()` / `delete()`（只有 `dbs` 这件内部结构），
    所以借一个客户端 `flushall`——这是 fakeredis 官方推荐的清库方式。
    """

    async def _flush() -> None:
        client = plain_client()
        try:
            await client.flushall()
        finally:
            await client.aclose()

    asyncio.run(_flush())


def plain_client() -> fakeredis.aioredis.FakeRedis:
    """给断言用的裸客户端（读 `task:<id>`、`task:key:<id>` 这些键）。"""
    return fakeredis.aioredis.FakeRedis(server=SERVER, decode_responses=True)


class FakeRuntimeProvider:
    """`get_task_runtime` 的替身：同一事件循环内复用一份，跨循环各开一份。

    两半都是必须的：
    - **同循环复用**：一个 HTTP 用例里入队与轮询在同一个循环，共用一份连接池才像生产；
    - **跨循环各开一份**：`asyncio.run` 跑 worker 是另一个循环，复用旧客户端会报
      「attached to a different loop」。
    """

    def __init__(self) -> None:
        self._by_loop: dict[int, TaskRuntime] = {}

    async def __call__(self) -> TaskRuntime:
        loop = asyncio.get_running_loop()
        key = id(loop)
        runtime = self._by_loop.get(key)
        if runtime is None:
            runtime = TaskRuntime(
                queue=ArqTaskQueue(
                    ArqRedis(fakeredis.aioredis.FakeRedis(server=SERVER).connection_pool)
                ),
                store=RedisTaskStore(plain_client()),
            )
            self._by_loop[key] = runtime
        return runtime

    async def close(self) -> None:
        for runtime in self._by_loop.values():
            await runtime.close()
        self._by_loop.clear()


@contextmanager
def _worker_compat_shims() -> Iterator[None]:
    """两处**Windows + fakeredis 特有**的兼容垫片，都不改变被测行为。

    1. `arq.Worker.close()` 在 `handle_signals=False` 时要 `signal.SIGUSR1` 才能走
       「模拟收到信号」那条路，而 Windows 上没有这个常量（`AttributeError`）。
       垫片把它指向 `SIGTERM`——只是让「关掉 worker」这一步能跑完。
    2. ARQ 启动时会 `log_redis_info()`，它要 `INFO` 命令，而 fakeredis 没实现
       （`unknown command 'info'`）。那个函数**只打一行日志**，与任务是否被执行无关，
       因此在测试里换成空实现。

    两条都必须写在注释里而不是悄悄改 `arq`：下一个看到「测试能跑、线上跑了没」的人
    需要知道差在哪。
    """
    import arq.worker as arq_worker

    if not hasattr(signal, "SIGUSR1"):
        signal.SIGUSR1 = signal.SIGTERM  # type: ignore[attr-defined]
    original = arq_worker.log_redis_info

    async def _skip(_: object, *args: object, **kwargs: object) -> None:
        return None

    arq_worker.log_redis_info = _skip  # type: ignore[assignment]
    try:
        yield
    finally:
        arq_worker.log_redis_info = original  # type: ignore[assignment]


def run_worker(
    *,
    queue_name: str,
    functions: Sequence[Callable[..., Any]],
    job_timeout: int = 60,
) -> None:
    """在**新的**事件循环里跑一次真 ARQ worker（`burst`：队列空了就退出）。

    `max_tries=1` 与生产一致（`ADR-033` §2.8）；`on_startup` 一律传空实现——
    worker 真正的启动钩子会连真 Redis，而这里的数据在 fakeredis 里。
    """

    async def _run() -> None:
        pool = ArqRedis(fakeredis.aioredis.FakeRedis(server=SERVER).connection_pool)
        worker = Worker(
            functions=list(functions),
            queue_name=queue_name,
            redis_pool=pool,
            burst=True,
            max_tries=1,
            job_timeout=job_timeout,
            keep_result=60,
            handle_signals=False,
        )
        try:
            await worker.async_run()
        finally:
            await worker.close()

    with _worker_compat_shims():
        asyncio.run(_run())


def run_all_workers() -> None:
    """把三条队列都跑一遍（只用一次 burst，正常只会有一条队列里有活）。"""
    from app.worker import batch_download_task, import_pdf_task

    run_worker(queue_name=ALL_QUEUES[0], functions=[import_pdf_task])
    run_worker(queue_name=ALL_QUEUES[1], functions=[batch_download_task])


def read_record(task_id: str) -> dict[str, str]:
    """同步读一条任务记录（断言用）；没有这条记录返回空字典。"""

    async def _read() -> dict[str, str]:
        client = plain_client()
        try:
            return dict(await client.hgetall(f"task:{task_id}"))
        finally:
            await client.aclose()

    return asyncio.run(_read())


def read_secret(task_id: str) -> str | None:
    async def _read() -> str | None:
        client = plain_client()
        try:
            return await client.get(f"task:key:{task_id}")
        finally:
            await client.aclose()

    return asyncio.run(_read())


def queue_depth(queue_name: str) -> int:
    async def _depth() -> int:
        client = plain_client()
        try:
            return int(await client.zcard(queue_name))
        finally:
            await client.aclose()

    return asyncio.run(_depth())


def drop_job(task_id: str, queue_name: str) -> None:
    """把 ARQ 的 job 从队列与 job key 里删掉，模拟「worker 崩了 / 队列被清」。

    用来钉住 `ADR-033` §2.3 那条「记录还在、job 没了 → failed + TASK_LOST」：
    真实的崩溃没法在用例里复现，而它的可观测后果恰好就是这两条键消失。
    """

    async def _drop() -> None:
        client = plain_client()
        try:
            await client.zrem(queue_name, task_id)
            await client.delete(f"arq:job:{task_id}")
            await client.delete(f"arq:in-progress:{task_id}")
        finally:
            await client.aclose()

    asyncio.run(_drop())


def job_raw(task_id: str) -> str:
    """ARQ job 的原始内容（用来断言 **Key 明文没有进 job args**）。

    ARQ 用 pickle 存 job，所以这里**必须**用 `decode_responses=False` 的客户端读字节，
    再以 `latin-1` 无损解码成 str 供 `in` 断言——用 `decode_responses=True` 读会当场
    `UnicodeDecodeError: 0x80`（pickle 协议头），而那与「Key 在不在里面」毫无关系。
    """

    async def _read() -> str:
        client = fakeredis.aioredis.FakeRedis(server=SERVER, decode_responses=False)
        try:
            raw = await client.get(f"arq:job:{task_id}")
        finally:
            await client.aclose()
        return raw.decode("latin-1") if raw else ""

    return asyncio.run(_read())
