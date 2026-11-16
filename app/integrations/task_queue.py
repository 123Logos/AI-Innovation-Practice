"""ARQ 生产侧：入队与在途状态（第 30 轮，`ADR-033` §2.2 / §2.3）。

**这里只做两件事**：把任务塞进指定的队列、回答「这条 job 现在处于哪一步」。
任务的成功/失败与结果**不经过这里**——那是我们自己的 Redis 记录
（`app/integrations/task_store.py`）的事，理由见 `ADR-033` §2.3。

`enqueue` 的 `function` 是 `app/worker.py` 里注册的函数名（ARQ 用 `__name__` 找），
所以「任务函数改名」与「入队点没跟着改」会表现为 `KeyError`/永远 pending；
`tests/test_tasks.py::test_enqueued_function_names_exist_in_worker` 用两条队列的
`functions` 清单反查，把这个错挡在提交前。
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.jobs import Job, JobStatus
from redis.exceptions import RedisError

from app.core.task_errors import TaskQueueUnavailableError

logger = logging.getLogger(__name__)


class JobLiveness(StrEnum):
    """ARQ 视角的四态，**只用于 pending / running 的判定**（`ADR-033` §2.3 那张表）。"""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    NOT_FOUND = "not_found"


_ARQ_STATUS = {
    JobStatus.deferred: JobLiveness.QUEUED,
    JobStatus.queued: JobLiveness.QUEUED,
    JobStatus.in_progress: JobLiveness.IN_PROGRESS,
    JobStatus.complete: JobLiveness.COMPLETE,
    JobStatus.not_found: JobLiveness.NOT_FOUND,
}


class TaskQueue(Protocol):
    async def enqueue(
        self,
        function: str,
        *,
        task_id: str,
        queue: str,
        args: tuple[Any, ...] = (),
    ) -> None: ...

    async def liveness(self, task_id: str, queue: str) -> JobLiveness: ...

    async def close(self) -> None: ...


class ArqTaskQueue:
    """`TaskQueue` 的 ARQ 实现。进程内复用同一条连接池。"""

    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    @classmethod
    async def create(cls, url: str) -> ArqTaskQueue:
        try:
            pool = await create_pool(RedisSettings.from_dsn(url))
        except (RedisError, OSError, ValueError) as error:
            # 连不上就立刻失败：把「队列没起来」翻译成 503，而不是让它等到第一次入队才炸。
            raise TaskQueueUnavailableError("任务队列连接失败") from error
        return cls(pool)

    async def enqueue(
        self,
        function: str,
        *,
        task_id: str,
        queue: str,
        args: tuple[Any, ...] = (),
    ) -> None:
        """`_job_id` 用 `task_id`：让 ARQ 的 job 与我们自己的记录是**同一个标识**，
        排障时 `redis-cli hgetall task:<id>` 与 `arq:job:<id>` 能对上，不需要第二张对照表。
        """
        try:
            job = await self._redis.enqueue_job(function, *args, _job_id=task_id, _queue_name=queue)
        except RedisError as error:
            raise TaskQueueUnavailableError("任务入队失败") from error
        if job is None:
            # ARQ 在 `_job_id` 已存在时返回 None（幂等语义）。任务 id 是 uuid4，
            # 撞上只可能是「同一个 id 被入队两次」——那是一个程序错误，不是用户错误。
            logger.warning("任务 id 已存在，未重复入队（task=%s）", task_id)
            raise TaskQueueUnavailableError("任务标识冲突")

    async def liveness(self, task_id: str, queue: str) -> JobLiveness:
        try:
            status = await Job(task_id, self._redis, _queue_name=queue).status()
        except RedisError as error:
            raise TaskQueueUnavailableError("任务状态读取失败") from error
        return _ARQ_STATUS.get(status, JobLiveness.NOT_FOUND)

    async def close(self) -> None:
        await self._redis.aclose()
