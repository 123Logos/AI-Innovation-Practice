"""任务记录的 Redis 存储（第 30 轮，`ADR-033` §2.3 / §2.4）。

**一份 hash + 一个短存密钥**，两个键分开：

```
task:<task_id>       hash   状态机 + 结果 + 产物信息（TTL = WORKER_TASK_TTL_SECONDS）
task:key:<task_id>   string BYOK Key 的 Fernet 密文（TTL = WORKER_JOB_TIMEOUT_SECONDS，读到即删）
```

**为什么状态不交给 ARQ 的结果存储**：ARQ 的 `ResultInfo` 里没有失败原因
（`success` / `result` 两格），而前端要展示的恰恰是「为什么失败」——那五个码
（`ADR-031` §2.5）必须原样带出来。ARQ 的 job 在本篇里只回答一件事：
**这条任务还在不在跑**（§2.3 那张判定表）。

**终态只写一次**：`finish()` 先读当前 state，已经是 `complete` / `failed` 就返回 `False`
且一个字段都不改。理由不是洁癖：`job_timeout` 触发时 ARQ 会取消协程并自己记一次失败，
而任务自己的 `finally` 也可能再写一次；两笔都写会让「超时」被后到的「成功」翻掉。
这里没有用 `WATCH/MULTI`（同一个 task_id 只可能有一个执行者，`max_tries=1`），
所以是「先读后写」而不是事务——这一点写在代码里，别让下一个读的人以为它是原子的。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.datetime_display import format_shanghai_datetime, utc_now
from app.core.task_errors import TaskQueueUnavailableError
from app.core.task_kinds import TaskKind, TaskStage, TaskState

logger = logging.getLogger(__name__)

#: 连接与读写超时。Redis 是**同一个机房**的组件，5 秒还没回来说明它不可用，
#: 让请求立刻 503 比挂住前端 15 秒（axios 默认超时）好。
SOCKET_TIMEOUT_SECONDS = 5

RECORD_PREFIX = "task:"
SECRET_PREFIX = "task:key:"


def record_key(task_id: str) -> str:
    return f"{RECORD_PREFIX}{task_id}"


def secret_key(task_id: str) -> str:
    return f"{SECRET_PREFIX}{task_id}"


@dataclass(frozen=True)
class TaskRecord:
    """任务记录。字段形状与 `GET /tasks/{id}` 的响应一一对应（`ADR-033` §2.6）。"""

    task_id: str
    kind: TaskKind
    owner_id: str
    queue: str
    state: TaskState
    stage: TaskStage | None = None
    created_at: str = ""
    finished_at: str | None = None
    request_id: str | None = None
    ip: str | None = None
    #: **入队输入**（worker 干活要用的上下文）：`batch_download` 放
    #: `{"organization_id", "username", "resume_ids"}`；`import_pdf` 空（它只要文件与 Key）。
    #: 与 `result` 分开两格不是洁癖：共用一个字段时，worker 写结果会把「要打哪几份」
    #: 覆盖掉，而那种 bug 只在重试路径上才现形。
    payload: dict[str, Any] = field(default_factory=dict)
    #: **产物结果**：`import_pdf` 是 `{"profile": {...}, "meta": {...}}`；`batch_download` 不用它
    #:（文件名与字节数在 `file_name` / `file_bytes` 两格里）。
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    file_name: str | None = None
    file_bytes: int | None = None
    #: 产物是否已经被取走（单次性下载）。取走之后 `/tasks` 报 `failed` + `TASK_EXPIRED`。
    download_taken: bool = False


class TaskStore(Protocol):
    """记录存储的抽象：测试用 fakeredis 实现同一个协议（`tests/task_support.py`）。"""

    async def create(self, record: TaskRecord) -> None: ...

    async def read(self, task_id: str) -> TaskRecord | None: ...

    async def advance(self, task_id: str, *, state: TaskState, stage: TaskStage | None) -> None: ...

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
    ) -> bool: ...

    async def mark_download_taken(self, task_id: str) -> None: ...

    async def delete(self, task_id: str) -> None: ...

    async def put_secret(self, task_id: str, encrypted: str) -> None: ...

    async def take_secret(self, task_id: str) -> str | None: ...

    async def drop_secret(self, task_id: str) -> None: ...

    async def close(self) -> None: ...


class RedisTaskStore:
    """`TaskStore` 的 Redis 实现。**一个实例占一条连接池**，进程内复用。"""

    def __init__(self, client: Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> RedisTaskStore:
        return cls(
            Redis.from_url(
                url,
                decode_responses=True,
                socket_timeout=SOCKET_TIMEOUT_SECONDS,
                socket_connect_timeout=SOCKET_TIMEOUT_SECONDS,
            )
        )

    # ------------------------------------------------------------------ 记录

    async def create(self, record: TaskRecord) -> None:
        mapping = {
            "task_id": record.task_id,
            "kind": str(record.kind),
            "owner_id": record.owner_id,
            "queue": record.queue,
            "state": str(record.state),
            "stage": str(record.stage) if record.stage is not None else "",
            "created_at": record.created_at or format_shanghai_datetime(utc_now()),
            "request_id": record.request_id or "",
            "ip": record.ip or "",
        }
        if record.payload:
            mapping["payload_json"] = json.dumps(record.payload, ensure_ascii=False)
        try:
            pipe = self._client.pipeline()
            pipe.hset(record_key(record.task_id), mapping=mapping)  # type: ignore[misc]
            pipe.expire(record_key(record.task_id), get_settings().worker_task_ttl_seconds)
            await pipe.execute()
        except RedisError as error:
            raise TaskQueueUnavailableError("任务记录写入失败") from error

    async def read(self, task_id: str) -> TaskRecord | None:
        try:
            # `cast` 的理由：`redis.asyncio.Redis` 的命令方法在类型桩里同时挂了同步与异步两种签名
            # （`Awaitable[dict] | dict`），mypy 因此不肯认定 `await` 是合法的。运行时只有异步那一支。
            raw = await cast(Awaitable[dict[str, str]], self._client.hgetall(record_key(task_id)))
        except RedisError as error:
            raise TaskQueueUnavailableError("任务记录读取失败") from error
        if not raw:
            return None
        return _record_from_hash(task_id, raw)

    async def advance(self, task_id: str, *, state: TaskState, stage: TaskStage | None) -> None:
        """推进到 `running`（并更新阶段）。终态记录**不覆盖**：那条判据只在 `finish` 里。"""
        try:
            current = await cast(
                Awaitable[str | None], self._client.hget(record_key(task_id), "state")
            )
            if current in (TaskState.COMPLETE, TaskState.FAILED):
                return
            pipe = self._client.pipeline()
            pipe.hset(
                record_key(task_id),
                mapping={"state": str(state), "stage": str(stage) if stage is not None else ""},
            )
            pipe.expire(record_key(task_id), get_settings().worker_task_ttl_seconds)
            await pipe.execute()
        except RedisError as error:
            raise TaskQueueUnavailableError("任务状态推进失败") from error

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
        """写终态；已经是终态时返回 `False` 且什么都不改（§2.3 最后一段）。"""
        try:
            current = await cast(
                Awaitable[str | None], self._client.hget(record_key(task_id), "state")
            )
            if current in (TaskState.COMPLETE, TaskState.FAILED):
                return False
            mapping: dict[str, str] = {
                "state": str(state),
                "stage": str(stage) if stage is not None else "",
                "finished_at": format_shanghai_datetime(utc_now()),
            }
            if result is not None:
                mapping["result_json"] = json.dumps(result, ensure_ascii=False)
            if error_code is not None:
                mapping["error_code"] = error_code
            if error_message is not None:
                mapping["error_message"] = error_message
            if file_name is not None:
                mapping["file_name"] = file_name
            if file_bytes is not None:
                mapping["file_bytes"] = str(file_bytes)
            pipe = self._client.pipeline()
            pipe.hset(record_key(task_id), mapping=mapping)
            pipe.expire(record_key(task_id), get_settings().worker_task_ttl_seconds)
            await pipe.execute()
        except RedisError as error:
            raise TaskQueueUnavailableError("任务终态写入失败") from error
        return True

    async def mark_download_taken(self, task_id: str) -> None:
        """标记产物已取走。**产物文件本身由调用方删除**（文件系统的事不混进 Redis）。"""
        try:
            await cast(
                Awaitable[int],
                self._client.hset(record_key(task_id), "download_taken", "1"),
            )
        except RedisError as error:
            raise TaskQueueUnavailableError("任务标记失败") from error

    async def delete(self, task_id: str) -> None:
        """删掉整条记录（入队失败时的回滚）。**不删密钥**——那是 `drop_secret` 的事，
        两件事分开调，调用方看得见自己删了什么。"""
        try:
            await self._client.delete(record_key(task_id))
        except RedisError as error:
            raise TaskQueueUnavailableError("任务记录删除失败") from error

    # ------------------------------------------------------------------ 密钥

    async def put_secret(self, task_id: str, encrypted: str) -> None:
        try:
            await self._client.set(
                secret_key(task_id),
                encrypted,
                ex=get_settings().worker_job_timeout_seconds,
            )
        except RedisError as error:
            raise TaskQueueUnavailableError("任务凭据写入失败") from error

    async def take_secret(self, task_id: str) -> str | None:
        """读到即删（`GETDEL`）：同一把 Key 只可能被一个任务执行读走一次。"""
        try:
            return await cast(Awaitable[str | None], self._client.getdel(secret_key(task_id)))
        except RedisError as error:
            raise TaskQueueUnavailableError("任务凭据读取失败") from error

    async def drop_secret(self, task_id: str) -> None:
        try:
            await self._client.delete(secret_key(task_id))
        except RedisError as error:
            raise TaskQueueUnavailableError("任务凭据清理失败") from error

    async def close(self) -> None:
        await self._client.aclose()


def _record_from_hash(task_id: str, raw: dict[str, str]) -> TaskRecord:
    """hash → 记录。缺失字段一律按「没有」处理，**不做补默认值的猜测**。"""
    result = _load_json(task_id, raw.get("result_json"), "result_json")
    payload = _load_json(task_id, raw.get("payload_json"), "payload_json")
    stage_text = raw.get("stage") or ""
    file_bytes = raw.get("file_bytes") or ""
    return TaskRecord(
        task_id=task_id,
        kind=TaskKind(raw.get("kind") or TaskKind.IMPORT_PDF),
        owner_id=raw.get("owner_id") or "",
        queue=raw.get("queue") or "",
        state=TaskState(raw.get("state") or TaskState.PENDING),
        stage=TaskStage(stage_text) if stage_text else None,
        created_at=raw.get("created_at") or "",
        finished_at=raw.get("finished_at") or None,
        request_id=raw.get("request_id") or None,
        ip=raw.get("ip") or None,
        payload=payload,
        result=result,
        error_code=raw.get("error_code") or None,
        error_message=raw.get("error_message") or None,
        file_name=raw.get("file_name") or None,
        file_bytes=int(file_bytes) if file_bytes.isdigit() else None,
        download_taken=raw.get("download_taken") == "1",
    )


def _load_json(task_id: str, raw: str | None, field_name: str) -> dict[str, Any]:
    """读一格 JSON 字段。写坏时当作空字典并留一条日志——**不抛**：
    记录里一格坏掉就把整条轮询变成 500，用户连「失败」都看不到。"""
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("任务记录里的 %s 不是合法 JSON（task=%s）", field_name, task_id)
        return {}
    return loaded if isinstance(loaded, dict) else {}
