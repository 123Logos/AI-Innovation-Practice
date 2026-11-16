"""worker 侧的任务执行器：把「一条任务」跑完并写终态（第 30 轮，`ADR-033`）。

**为什么单独一层而不是把逻辑塞进 `app/worker.py`**：`worker.py` 只该有「ARQ 怎么配」，
而「一条任务失败该记哪个码」是可以被单测的纯编排。`tests/tests_*.py` 都直接构造
`TaskRunner` 跑，不需要起 ARQ 进程。

四条纪律（违反任意一条都会让「失败」变得看不见）：

1. **先写 `running` 再干活**：用户轮询时才不会在任务已经开始之后还看到 `pending`
   （`stage` 也在这几处推进，前端进度条靠它）。
2. **终态只写一次**（`store.finish` 的语义）：超时被取消与正常结束不会互相翻写。
3. **异常一律翻译成码**：`PdfImportError` / 教师端三个域错误 → 各自的码；
   其余 → `TASK_INTERNAL`。**原始异常文本只进日志**，记录里只放受信目录里的句子（G3）。
4. **重活丢到线程里**（`asyncio.to_thread`）：OCR、浏览器渲染、ZIP 打包都是同步阻塞调用，
   直接在事件循环里跑会把同进程的其它 job 一起卡住——那样 `max_jobs=4` 就只是个数。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from app.core import task_files
from app.core.pdf_errors import PdfRendererUnavailableError
from app.core.task_errors import TaskQueueUnavailableError, describe_failure
from app.core.task_kinds import (
    FUNCTION_BATCH_DOWNLOAD,
    FUNCTION_IMPORT_PDF,
    TaskKind,
    TaskStage,
    TaskState,
)
from app.core.task_secrets import SecretDecryptError, decrypt_secret
from app.integrations.task_store import TaskRecord, TaskStore
from app.services.resume_import import PdfImportError, ResumePdfImportService
from app.services.teacher_tools_service import (
    DownloadResult,
    NoDownloadableResumeError,
    TeacherOrganizationRequiredError,
    TeacherToolsService,
)

logger = logging.getLogger(__name__)

#: 任务函数的注册名（与 `task_service.py` 共用一份常量）。
TASK_FUNCTIONS = {FUNCTION_IMPORT_PDF, FUNCTION_BATCH_DOWNLOAD}


@dataclass(frozen=True)
class TaskActor:
    """worker 里的「谁」：没有 `CurrentUserContext`（那是查库查出来的），只有记录里的三格。"""

    user_id: str
    username: str
    organization_id: str


class TaskRunner:
    """执行一条任务。**同一个实例可以被多条任务并发调用**（无内部可变状态）。"""

    def __init__(
        self,
        store: TaskStore,
        *,
        build_importer: Callable[[str], ResumePdfImportService],
        teacher_scope: Callable[[], AbstractContextManager[TeacherToolsService]],
    ) -> None:
        self._store = store
        self._build_importer = build_importer
        self._teacher_scope = teacher_scope

    async def run(self, task_id: str) -> None:
        record = await self._store.read(task_id)
        if record is None:
            # 记录已过期（TTL 到点）而 job 还在队列里：没什么可做的，直接结束。
            # 这不是错误——用户那边早就 404 了，再抛异常只会污染 worker 日志。
            logger.info("任务记录已不存在，跳过执行（task=%s）", task_id)
            return
        if record.kind is TaskKind.IMPORT_PDF:
            await self._run_import_pdf(record)
        else:
            await self._run_batch_download(record)

    # ------------------------------------------------------------------ 导入

    async def _run_import_pdf(self, record: TaskRecord) -> None:
        task_id = record.task_id
        try:
            await self._advance(task_id, TaskState.RUNNING, TaskStage.EXTRACTING)
            api_key = await self._take_key(task_id)
            if api_key is None:
                await self._fail(task_id, "AI_KEY_MISSING", TaskStage.EXTRACTING)
                return
            try:
                data = task_files.read_import_file(task_id)
            except FileNotFoundError:
                await self._fail(task_id, "TASK_EXPIRED", TaskStage.EXTRACTING)
                return

            service = self._build_importer(api_key)
            await self._advance(task_id, TaskState.RUNNING, TaskStage.STRUCTURING)
            try:
                result = await asyncio.to_thread(service.import_profile, data)
            except PdfImportError as error:
                # 五个码之一（`ADR-031` §2.5）：码进记录，句子由受信目录给。
                logger.info("导入任务失败（task=%s, code=%s）", task_id, error.code)
                await self._fail(task_id, error.code, _import_failure_stage(error.code))
                return

            await self._store.finish(
                task_id,
                state=TaskState.COMPLETE,
                stage=None,
                result=result.model_dump(mode="json"),
            )
        except asyncio.CancelledError:
            # 超时被 ARQ 取消：写一次失败再**原样抛出**，否则 ARQ 会把这次取消记成成功。
            await self._fail(task_id, "TASK_LOST", TaskStage.EXTRACTING)
            raise
        except TaskQueueUnavailableError:
            # 记录本身都写不进去了，没什么能做的：让异常冒出去进 worker 日志。
            raise
        except Exception:
            logger.exception("导入任务出现未分类异常（task=%s）", task_id)
            await self._fail(task_id, "TASK_INTERNAL", TaskStage.STRUCTURING)
        finally:
            # 暂存文件与 Key 密文都在这条任务的边界内：成功与否都必须消失（§2.4 / §2.5）。
            task_files.discard_import_file(task_id)
            await self._drop_key(task_id)

    # ------------------------------------------------------------------ 打包

    async def _run_batch_download(self, record: TaskRecord) -> None:
        task_id = record.task_id
        actor = _actor_from(record)
        resume_ids = [str(item) for item in record.payload.get("resume_ids", [])]
        try:
            await self._advance(task_id, TaskState.RUNNING, TaskStage.RENDERING)
            if actor is None:
                # 记录里缺组织（不该发生：入队前已过闸）：宁可显式失败也不打一份没有归属的包。
                await self._fail(task_id, "TASK_INTERNAL", TaskStage.RENDERING)
                return

            def build() -> DownloadResult:
                with self._teacher_scope() as service:
                    return service.build_batch_archive(
                        organization_id=actor.organization_id,
                        actor_user_id=actor.user_id,
                        actor_username=actor.username,
                        resume_ids=resume_ids,
                        ip=record.ip,
                    )

            try:
                archive = await asyncio.to_thread(build)
            except (
                TeacherOrganizationRequiredError,
                NoDownloadableResumeError,
                PdfRendererUnavailableError,
            ) as error:
                code = next(
                    mapped for klass, mapped in _BATCH_CODE.items() if isinstance(error, klass)
                )
                logger.info("打包任务失败（task=%s, code=%s）", task_id, code)
                await self._fail(task_id, code, TaskStage.RENDERING)
                return

            await self._advance(task_id, TaskState.RUNNING, TaskStage.PACKAGING)
            task_files.save_download_file(task_id, archive.content)
            await self._store.finish(
                task_id,
                state=TaskState.COMPLETE,
                stage=None,
                file_name=archive.filename,
                file_bytes=len(archive.content),
            )
        except asyncio.CancelledError:
            await self._fail(task_id, "TASK_LOST", TaskStage.RENDERING)
            raise
        except TaskQueueUnavailableError:
            raise
        except Exception:
            logger.exception("打包任务出现未分类异常（task=%s）", task_id)
            await self._fail(task_id, "TASK_INTERNAL", TaskStage.RENDERING)

    # ------------------------------------------------------------------ 内部

    async def _advance(self, task_id: str, state: TaskState, stage: TaskStage) -> None:
        try:
            await self._store.advance(task_id, state=state, stage=stage)
        except TaskQueueUnavailableError:
            # 推进失败不中断业务：任务照跑，只是前端看不到 stage 的变化。
            logger.warning("任务阶段推进失败（task=%s, stage=%s）", task_id, stage)

    async def _fail(self, task_id: str, code: str, stage: TaskStage) -> None:
        resolved_code, message, _ = describe_failure(code)
        try:
            await self._store.finish(
                task_id,
                state=TaskState.FAILED,
                stage=stage,
                error_code=resolved_code,
                error_message=message,
            )
        except TaskQueueUnavailableError:
            logger.warning("任务失败态写入失败（task=%s, code=%s）", task_id, resolved_code)

    async def _take_key(self, task_id: str) -> str | None:
        """读一次 Key 密文并解密；读不到或解不开都返回 `None`（调用方记 `AI_KEY_MISSING`）。"""
        encrypted = await self._store.take_secret(task_id)
        if not encrypted:
            return None
        try:
            return decrypt_secret(encrypted)
        except SecretDecryptError:
            logger.info("任务凭据无法解密（task=%s）：多半是 JWT 密钥换过了", task_id)
            return None

    async def _drop_key(self, task_id: str) -> None:
        try:
            await self._store.drop_secret(task_id)
        except TaskQueueUnavailableError:
            # TTL 会兜住（§2.4）：这里失败只意味着密文多活一会儿。
            logger.warning("任务凭据未能立即删除（task=%s），等待 TTL 过期", task_id)


def _import_failure_stage(code: str) -> TaskStage:
    """导入失败落在哪个阶段：抽字那两级归 `EXTRACTING`，模型那一级归 `STRUCTURING`。

    进度条要能解释「跑到哪一步断的」：`PDF_INVALID` 是「这份文件没识别到文字」，
    它发生在抽取（文本层 + OCR）；把它标成 `structuring` 会让用户以为是 AI 的问题。
    """
    if code in {"PDF_INVALID", "PDF_TOO_LARGE"}:
        return TaskStage.EXTRACTING
    return TaskStage.STRUCTURING


#: 打包侧三种域错误 → 失败码。成一对方便逐条对照 `ADR-029` / `ADR-033` 的码表；
#: 四种以外的一切（包括将来新增的域错误）落到 `TASK_INTERNAL`，不会静默变成 200。
_BATCH_CODE = {
    TeacherOrganizationRequiredError: "ORGANIZATION_ACTION_REQUIRED",
    NoDownloadableResumeError: "NO_DOWNLOADABLE_RESUME",
    PdfRendererUnavailableError: "PDF_RENDERER_UNAVAILABLE",
}


def _actor_from(record: TaskRecord) -> TaskActor | None:
    payload: dict[str, Any] = record.payload
    organization_id = payload.get("organization_id")
    username = payload.get("username")
    if not organization_id or not username:
        return None
    return TaskActor(
        user_id=record.owner_id, username=str(username), organization_id=str(organization_id)
    )
