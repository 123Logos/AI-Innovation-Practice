"""异步任务的编排：入队、轮询、取产物（第 30 轮，`ADR-033` §2.6）。

**这一层不碰 Redis，也不碰数据库**：它只通过 `TaskRuntime`（队列 + 记录存储两个适配器）
干活，所以本文件的每一行都能在 `fakeredis` 上跑，不需要真 Redis（`ADR-033` §4）。

**运行时是「按需取」而不是构造时注入**：`get_task_runtime()` 在没配 `REDIS_URL` 时抛
`TaskQueueUnavailableError`（对外 503），而想用本服务的**另一些**端点（例如教师看板）
一个字节都不需要队列。把解析推迟到真正用它的那一刻，才能让「Redis 没起」
只打断导入与打包两条功能，而不是把整个教师端一起打成 503。

三件事在这里定，且只在这里定：

1. **入队前把能立刻判的错判掉**（§2.7）：没带 Key → 422、体积超限 → 422。
   这两件不需要跑一遍就知道，留在同步路径上用户改得快。（一次打包的上限 50 条
   由请求模型 `app/schemas/teacher_tools.py::BatchDownloadRequest` 挡在 422，
   不在这里再判一遍——两处各写一个数，迟早会不一致。）
2. **`GET /tasks/{id}` 的判定表**（§2.3）：记录里的终态优先；非终态才去问 ARQ 还在不在。
   「ARQ 说 job 没了但记录还是 pending」→ 就地写成 `failed` + `TASK_LOST`，
   这样用户刷新几次看到的是同一句话，而不是一会儿 pending 一会儿 failed。
3. **产物是单次性的**（§2.5）：`take_download()` 在返回前就标记「已取走」，
   文件由路由在响应结束时删掉；第二次来拿只会得到 410。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core import task_files
from app.core.datetime_display import format_shanghai_datetime, utc_now
from app.core.task_errors import (
    TaskDownloadGoneError,
    TaskNotFoundError,
    TaskQueueUnavailableError,
    describe_failure,
)
from app.core.task_kinds import (
    FUNCTION_BATCH_DOWNLOAD,
    FUNCTION_IMPORT_PDF,
    KIND_QUEUE,
    TaskKind,
    TaskStage,
    TaskState,
    progress_for,
)
from app.core.task_secrets import encrypt_secret
from app.integrations.task_queue import JobLiveness
from app.integrations.task_runtime import TaskRuntime
from app.integrations.task_store import TaskRecord, TaskStore
from app.schemas.task import TaskError, TaskQueued, TaskStatus

logger = logging.getLogger(__name__)

RuntimeProvider = Callable[[], Awaitable[TaskRuntime]]


@dataclass(frozen=True)
class DownloadArtifact:
    """待发送的产物：磁盘路径 + 给用户看的文件名（只在 `Content-Disposition` 里出现）。"""

    path: Path
    filename: str


class TaskService:
    def __init__(self, runtime_provider: RuntimeProvider) -> None:
        self._runtime = runtime_provider

    # ------------------------------------------------------------------ 入队

    async def submit_import_pdf(
        self,
        *,
        owner_id: str,
        api_key: str,
        data: bytes,
        request_id: str | None,
        ip: str | None,
    ) -> TaskQueued:
        """落盘 → 建记录 → 存加密 Key → 入队。四步任何一步失败都**不留半截状态**。

        Key 先加密再进 Redis（§2.4）：明文在这个函数返回之后就只活在这个局部变量里，
        worker 取走密文并解密之后，Redis 里那一条已经被 `GETDEL` 删掉。
        """
        runtime = await self._runtime()
        task_id = uuid.uuid4().hex
        task_files.save_import_file(task_id, data)
        record = TaskRecord(
            task_id=task_id,
            kind=TaskKind.IMPORT_PDF,
            owner_id=owner_id,
            queue=KIND_QUEUE[TaskKind.IMPORT_PDF],
            state=TaskState.PENDING,
            stage=TaskStage.QUEUED,
            created_at=format_shanghai_datetime(utc_now()),
            request_id=request_id,
            ip=ip,
        )
        try:
            await runtime.store.create(record)
            await runtime.store.put_secret(task_id, encrypt_secret(api_key))
            await runtime.queue.enqueue(
                FUNCTION_IMPORT_PDF, task_id=task_id, queue=record.queue, args=(task_id,)
            )
        except Exception:
            # 入队失败必须把文件与记录一起撤掉：留着一个永远 pending 的记录，
            # 前端会一直轮询到 TTL 才 404，用户看到的是「转了半分钟然后没了」。
            task_files.discard_import_file(task_id)
            await _rollback(runtime.store, task_id)
            raise
        return TaskQueued(
            task_id=task_id, kind=record.kind, state=record.state, created_at=record.created_at
        )

    async def submit_batch_download(
        self,
        *,
        organization_id: str,
        owner_id: str,
        username: str,
        resume_ids: list[str],
        request_id: str | None,
        ip: str | None,
    ) -> TaskQueued:
        """建记录并入队。**动作闸不在这里**：它在 `TeacherToolsService.batch_download`
        的第一句（`ADR-029` §2.1 的既有契约），本方法被调到就说明闸已经过了。
        """
        runtime = await self._runtime()
        task_id = uuid.uuid4().hex
        record = TaskRecord(
            task_id=task_id,
            kind=TaskKind.BATCH_DOWNLOAD,
            owner_id=owner_id,
            queue=KIND_QUEUE[TaskKind.BATCH_DOWNLOAD],
            state=TaskState.PENDING,
            stage=TaskStage.QUEUED,
            created_at=format_shanghai_datetime(utc_now()),
            request_id=request_id,
            ip=ip,
            # 打包要的上下文一起进记录：worker 不重读「谁在哪个组织」，
            # 它只照着这条记录干活（审计也用这里的 actor）。
            payload={
                "organization_id": organization_id,
                "username": username,
                "resume_ids": list(resume_ids),
            },
        )
        try:
            await runtime.store.create(record)
            await runtime.queue.enqueue(
                FUNCTION_BATCH_DOWNLOAD, task_id=task_id, queue=record.queue, args=(task_id,)
            )
        except Exception:
            await _rollback(runtime.store, task_id)
            raise
        return TaskQueued(
            task_id=task_id, kind=record.kind, state=record.state, created_at=record.created_at
        )

    # ------------------------------------------------------------------ 轮询

    async def status(self, task_id: str, *, viewer_id: str) -> TaskStatus:
        runtime = await self._runtime()
        record = await _load_owned(runtime.store, task_id, viewer_id)
        if record.state is TaskState.COMPLETE:
            if record.kind is TaskKind.BATCH_DOWNLOAD and record.download_taken:
                # 产物被取走之后这条任务不再「可下载」：报 expired 而不是给一个 410 的死链接
                # （前端拿到 status 就能说清「请重新发起」，不必先点一次下载才知道）。
                return _failed_status(record, "TASK_EXPIRED")
            return _status(record)
        if record.state is TaskState.FAILED:
            return _failed_status(record, record.error_code)

        liveness = await runtime.queue.liveness(task_id, record.queue)
        if liveness in (JobLiveness.NOT_FOUND, JobLiveness.COMPLETE):
            # NOT_FOUND：记录还在、job 没了（worker 崩了 / 队列被清）。
            # COMPLETE：ARQ 说跑完了，但我们的记录没写终态（worker 写完记录之前被硬杀）。
            # 两种都拿不出结果，所以都是失败——**绝不**编一个空结果。就地写一次终态，
            # 让后面每一次轮询都读到同一句话。
            await _finish_failed(runtime.store, task_id, "TASK_LOST", stage=record.stage)
            return _failed_status(record, "TASK_LOST")
        # 记录说 running 就报 running：worker 在「推进到 running」与 ARQ 把 job 标成
        # in_progress 之间有几十毫秒的窗口，这期间按 ARQ 报 pending 会让进度条**倒退**。
        running = record.state is TaskState.RUNNING or liveness is JobLiveness.IN_PROGRESS
        state = TaskState.RUNNING if running else TaskState.PENDING
        return _status(record, state_override=state)

    # ------------------------------------------------------------------ 产物

    async def take_download(self, task_id: str, *, viewer_id: str) -> DownloadArtifact:
        """取产物：标记已取走 + 返回路径。文件本身由路由在响应结束时删（§2.5）。"""
        runtime = await self._runtime()
        record = await _load_owned(runtime.store, task_id, viewer_id)
        if record.kind is not TaskKind.BATCH_DOWNLOAD or record.state is not TaskState.COMPLETE:
            raise TaskDownloadGoneError(task_id)
        if record.download_taken or not record.file_name:
            raise TaskDownloadGoneError(task_id)
        path = task_files.download_file_path(task_id)
        if not path.is_file():
            # 记录说还在、盘上没了（进程重启 + 目录被清）：同一个 410 语义。
            raise TaskDownloadGoneError(task_id)
        await runtime.store.mark_download_taken(task_id)
        return DownloadArtifact(path=path, filename=record.file_name)


# --------------------------------------------------------------------------- #
# 模块级帮助函数：`TaskRunner`（worker 侧）也用它们，所以不挂在类上
# --------------------------------------------------------------------------- #


async def _load_owned(store: TaskStore, task_id: str, viewer_id: str) -> TaskRecord:
    record = await store.read(task_id)
    if record is None or record.owner_id != viewer_id:
        raise TaskNotFoundError(task_id)
    return record


async def _finish_failed(
    store: TaskStore, task_id: str, code: str, *, stage: TaskStage | None
) -> None:
    resolved_code, message, _ = describe_failure(code)
    try:
        await store.finish(
            task_id,
            state=TaskState.FAILED,
            stage=stage,
            error_code=resolved_code,
            error_message=message,
        )
    except TaskQueueUnavailableError:
        # 写不进去不该让「轮询」本身失败：上一次读到的记录仍然能回答这个请求。
        logger.warning("任务终态写入失败（task=%s, code=%s）", task_id, resolved_code)


async def _rollback(store: TaskStore, task_id: str) -> None:
    for action in (store.drop_secret, store.delete):
        try:
            await action(task_id)
        except TaskQueueUnavailableError:
            # 回滚本身失败只留日志：原始异常（例如 503）必须原样抛给用户。
            logger.warning("入队失败后的回滚没有完成（task=%s）", task_id)


def _status(record: TaskRecord, *, state_override: TaskState | None = None) -> TaskStatus:
    state = state_override or record.state
    result: dict[str, Any] | None = None
    if state is TaskState.COMPLETE:
        result = _result_payload(record)
    return TaskStatus(
        task_id=record.task_id,
        kind=record.kind,
        state=state,
        stage=record.stage if state is not TaskState.COMPLETE else None,
        progress=progress_for(state, record.stage),
        created_at=record.created_at,
        finished_at=record.finished_at,
        result=result,
        error=None,
    )


def _failed_status(record: TaskRecord, code: str | None) -> TaskStatus:
    resolved_code, message, retryable = describe_failure(code)
    return TaskStatus(
        task_id=record.task_id,
        kind=record.kind,
        state=TaskState.FAILED,
        stage=record.stage,
        progress=progress_for(TaskState.FAILED, record.stage),
        created_at=record.created_at,
        finished_at=record.finished_at,
        result=None,
        error=TaskError(code=resolved_code, message=message, retryable=retryable),
    )


def _result_payload(record: TaskRecord) -> dict[str, Any]:
    if record.kind is TaskKind.IMPORT_PDF:
        return dict(record.result)
    return {
        "filename": record.file_name,
        "bytes": record.file_bytes,
        "download_url": f"/api/v1/tasks/{record.task_id}/download",
    }
