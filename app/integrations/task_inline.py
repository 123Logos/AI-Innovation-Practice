"""内存降级运行时的两个适配器：记录存储与「队列」（第 31 轮，`ADR-034` §2.2）。

只在**没有 `REDIS_URL` 且环境允许**时被用上（`Settings.inline_task_runtime`）。
生产与测试永远走 Redis / fakeredis（`ADR-033` §4 的证据链不许被这条路径影响）。

这里有两个实现，都对着 `ADR-033` 定下的 Protocol 写：

| | 生产 | 降级 |
|---|---|---|
| 记录 | `RedisTaskStore`（hash + TTL） | `MemoryTaskStore`（进程内 dict，惰性过期） |
| 队列 | `ArqTaskQueue`（真 job） | `InlineTaskQueue`（`asyncio.create_task`） |

**为什么内存存储要一条条对齐 Redis 的语义**（`finish` 只写一次、`advance` 不覆盖终态、
TTL、密钥读到即删）：降级模式的用处是「本地跑通」，而「本地跑通」如果靠的是
一套更宽松的语义，那它证明的东西就不是生产那条链路。宁可多写几十行去对齐。

**为什么并发安全不是问题**：`uvicorn` 单进程 = 单事件循环 + 单线程跑 async 代码，
字典的读写都在这个循环里串行发生。`TaskRunner` 的重活走 `asyncio.to_thread`，
但它对存储的访问仍然回到循环上（`await` 之间不共享可变中间态）。
多进程部署下这条路径本来就不成立——`ADR-034` §3 第 1 条如实记着。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from app.core.datetime_display import format_shanghai_datetime, utc_now
from app.core.task_errors import TaskQueueUnavailableError
from app.core.task_kinds import ALL_TASK_FUNCTIONS, TaskStage, TaskState
from app.integrations.task_queue import JobLiveness
from app.integrations.task_store import TaskRecord

logger = logging.getLogger(__name__)


class MemoryTaskStore:
    """`TaskStore` 的内存实现：一份 dict 存记录，一份 dict 存密钥密文。

    过期用**惰性清扫**：每次读写顺手把过期项删掉。不做后台清理任务的理由与
    `ADR-033` §7 第 3 条同一条——本地开发不需要一个自己的定时器，
    而「访问时发现过期」这件事在语义上已经够了（`read()` 返回 `None` = 404）。
    """

    def __init__(self, *, record_ttl_seconds: int, secret_ttl_seconds: int) -> None:
        self._records: dict[str, tuple[TaskRecord, float]] = {}
        self._secrets: dict[str, tuple[str, float]] = {}
        self._record_ttl = record_ttl_seconds
        self._secret_ttl = secret_ttl_seconds

    # ------------------------------------------------------------------ 记录

    async def create(self, record: TaskRecord) -> None:
        self._sweep()
        self._records[record.task_id] = (record, time.monotonic() + self._record_ttl)

    async def read(self, task_id: str) -> TaskRecord | None:
        entry = self._records.get(task_id)
        if entry is None:
            return None
        record, expires_at = entry
        if expires_at <= time.monotonic():
            # 过期与「没有」在对外语义上是同一件事（`ADR-033` §2.6：TTL 到点 → 404）。
            del self._records[task_id]
            return None
        return record

    async def advance(self, task_id: str, *, state: TaskState, stage: TaskStage | None) -> None:
        """推进状态。**终态不覆盖**（那条判据只在 `finish` 里），与 Redis 实现逐条同义。"""
        record = await self.read(task_id)
        if record is None or record.state in (TaskState.COMPLETE, TaskState.FAILED):
            return
        self._put(replace(record, state=state, stage=stage))

    async def finish(
        self,
        task_id: str,
        *,
        state: TaskState,
        stage: TaskStage | None = None,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        file_name: str | None = None,
        file_bytes: int | None = None,
    ) -> bool:
        """写终态；已经是终态时返回 `False` 且**什么都不改**（`ADR-033` §2.3 最后一段）。

        **与 Redis 实现的一处刻意差别**：记录不存在时这里返回 `False`，而
        `RedisTaskStore.finish` 会 `hset` 出一条只剩终态字段的半截记录。降级模式
        不复制那个副作用——一条没有 `kind` / `owner_id` 的记录在轮询里只会变成
        一个解释不清的 500，少写比写坏好。
        """
        record = await self.read(task_id)
        if record is None:
            return False
        if record.state in (TaskState.COMPLETE, TaskState.FAILED):
            return False
        updated = replace(
            record,
            state=state,
            stage=stage,
            finished_at=format_shanghai_datetime(utc_now()),
            result=result if result is not None else record.result,
            error_code=error_code,
            error_message=error_message,
            file_name=file_name if file_name is not None else record.file_name,
            file_bytes=file_bytes if file_bytes is not None else record.file_bytes,
        )
        self._put(updated)
        return True

    async def mark_download_taken(self, task_id: str) -> None:
        record = await self.read(task_id)
        if record is None:
            return
        self._put(replace(record, download_taken=True))

    async def delete(self, task_id: str) -> None:
        self._records.pop(task_id, None)

    # ------------------------------------------------------------------ 密钥

    async def put_secret(self, task_id: str, encrypted: str) -> None:
        self._secrets[task_id] = (encrypted, time.monotonic() + self._secret_ttl)

    async def take_secret(self, task_id: str) -> str | None:
        """读到即删（对应 Redis 的 `GETDEL`）：同一把 Key 只可能被一个执行者读走一次。"""
        entry = self._secrets.pop(task_id, None)
        if entry is None:
            return None
        encrypted, expires_at = entry
        return None if expires_at <= time.monotonic() else encrypted

    async def drop_secret(self, task_id: str) -> None:
        self._secrets.pop(task_id, None)

    async def close(self) -> None:
        """没有连接可关。**也不清数据**：清理是 `reset_*` 的事，两件事分开看得见。"""

    # ------------------------------------------------------------------ 内部

    def _put(self, record: TaskRecord) -> None:
        """写回一条记录并**重置 TTL**（与 Redis 每次 `hset` 之后 `expire` 同义：
        一条任务从最后一次**被写**起算，过 `WORKER_TASK_TTL_SECONDS` 才消失。
        纯读不续命——`read()` 不刷新过期时间，与 Redis 的 `hgetall` 一致）。"""
        self._records[record.task_id] = (record, time.monotonic() + self._record_ttl)

    def _sweep(self) -> None:
        now = time.monotonic()
        for task_id, (_, expires_at) in list(self._records.items()):
            if expires_at <= now:
                del self._records[task_id]
        for task_id, (_, expires_at) in list(self._secrets.items()):
            if expires_at <= now:
                del self._secrets[task_id]


class InlineTaskQueue:
    """`TaskQueue` 的内存实现：入队 = 在当前进程里 `create_task`。

    **它是「队列」而不是「直接执行」**（`ADR-034` §2.2）：`enqueue` 只负责把任务
    挂到事件循环上，HTTP 请求立刻返回 202。所以降级模式下的契约与生产**完全同形**
    ——前端不需要第二条代码路径。

    执行器（`executor`）由 `app/services/inline_runtime.py` 注入：本文件在分层里
    位于 `services/` 之下，不能 import `TaskRunner`（`AGENTS.md` §4.1）。
    """

    def __init__(self, executor: Callable[[str], Awaitable[None]]) -> None:
        self._executor = executor
        #: `asyncio` 只持弱引用：不自己留一份强引用，任务可能被 GC 掉（官方文档明写）。
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._started: set[str] = set()

    async def enqueue(
        self,
        function: str,
        *,
        task_id: str,
        queue: str,
        args: tuple[Any, ...] = (),
    ) -> None:
        """把任务挂到当前事件循环上。

        `function` 与 `args` 都做校验，理由与 ARQ 那边一致：名字写错在 Redis 模式下的
        表现是 `KeyError`（worker 找不到这个函数），在内存模式下如果直接忽略名字，
        就变成「静默跑了另一个任务」——那是更难查的一种坏。
        """
        if function not in ALL_TASK_FUNCTIONS:
            raise TaskQueueUnavailableError(f"未知任务函数：{function}")
        if args != (task_id,):
            # 生产侧 `args` 恒为 `(task_id,)`（`task_service` 两个入队点）。
            # 内存模式下任务函数就是「跑这条 task_id」，多传的参数没有接收方。
            raise TaskQueueUnavailableError("内存降级模式只接受 (task_id,) 作为任务参数")
        existing = self._tasks.get(task_id)
        if existing is not None and not existing.done():
            # 对应 ARQ 在 `_job_id` 已存在时返回 None（幂等语义）→ 我们抛同一个错。
            logger.warning("任务已在执行，未重复入队（task=%s）", task_id)
            raise TaskQueueUnavailableError("任务标识冲突")
        loop = asyncio.get_running_loop()
        self._tasks[task_id] = loop.create_task(self._run(task_id), name=f"inline-task:{task_id}")

    async def liveness(self, task_id: str, queue: str) -> JobLiveness:
        """「这条任务还在不在跑」。四态与 ARQ 那一侧对齐（`ADR-033` §2.3 那张表）。"""
        task = self._tasks.get(task_id)
        if task is None:
            # 从未入队 / 已被丢弃：对判定表来说与「job 没了」同义 → `TASK_LOST`。
            return JobLiveness.NOT_FOUND
        if task.done():
            # 跑完了但记录没写终态（执行器内部异常被吞在日志里）→ 与 ARQ 的 COMPLETE
            # 同义：拿不出结果，判 `TASK_LOST` 比永远 pending 诚实。
            return JobLiveness.COMPLETE
        return JobLiveness.IN_PROGRESS if task_id in self._started else JobLiveness.QUEUED

    async def drain(self) -> None:
        """等在途任务跑完（`close()` 与用例用）。

        生产路径上没有调用方——**降级模式不该有「等任务」这个动作**，202 之后就是前端
        轮询的事。它存在的理由是让「跑一条任务」在测试里可确定（否则同一个断言要靠
        轮询 1.5 秒 × N 次）。
        """
        pending = [task for task in self._tasks.values() if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def close(self) -> None:
        """取消在途任务（进程关闭 / 热重载时用）。

        **不 await 它们跑完**：一条正在 OCR 的任务可能要几十秒，而关闭流程不该被它拖住。
        取消后 `TaskRunner` 的 `except CancelledError` 会写一次 `TASK_LOST`
        （存储马上也要被丢弃，那笔写只影响日志）。

        两个 `RuntimeError` 的兜底都是真事：`close()` 可能在另一个事件循环里被调用
        （用例的 `asyncio.run`、或者是热重载新起的循环），那时在途任务属于一个**已关闭**的
        循环，`cancel()` / `gather()` 会抛 `Event loop is closed`。抛出来只会让
        「关服务」变成一次报错，而本来要做的取消已经做了。
        """
        pending = [task for task in self._tasks.values() if not task.done()]
        for task in pending:
            try:
                task.cancel()
            except RuntimeError:
                logger.debug("在途任务属于已关闭的事件循环，跳过取消")
        if pending:
            try:
                await asyncio.gather(*pending, return_exceptions=True)
            except RuntimeError:
                logger.debug("在途任务属于已关闭的事件循环，跳过等待")
        self._tasks.clear()
        self._started.clear()

    # ------------------------------------------------------------------ 内部

    async def _run(self, task_id: str) -> None:
        self._started.add(task_id)
        try:
            await self._executor(task_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 执行器内部已经把域错误翻成终态了（`TaskRunner`）；能漏到这里的是
            # 「连记录都写不进去」那种，除了留在日志里没有别的办法。
            logger.exception("内存降级任务未能完成（task=%s）", task_id)
        finally:
            self._started.discard(task_id)
