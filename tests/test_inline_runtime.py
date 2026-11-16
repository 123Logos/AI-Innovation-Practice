"""内存降级运行时（第 31 轮，`ADR-034`）。

**这个文件只测降级本身**，不重复第 30 轮那套 HTTP 契约：`tests/test_tasks.py` 用
fakeredis + 真 `arq.Worker` 钉住「202 / 四态 / 归属 / 队列不可用 503」，那条链路
一个字都不许因为本轮改动而变化。这里要证明的是另外三件事：

1. **触发条件**（`ADR-034` §2.1）：没有 `REDIS_URL` 时，`development` 走内存、
   `test` **绝不**降级（否则 fakeredis 那条证据链会被悄悄绕过）；
2. **内存存储的语义与 Redis 对齐**（§2.3）：终态只写一次、TTL 到点读不到、密钥读到即删；
3. **一条任务真的能在进程内跑完**（§2.2）：`pending` → `complete` 带结果、
   失败落成 `failed` + 码、任务句柄丢了落成 `TASK_LOST`。

用例里的 `TaskRunner` 是**真的**，只有「取导入服务」这一步换成桩——因为真实那条会
去读 `pypdf` + RapidOCR + DeepSeek，而这里要验的是运行时的接线，不是 PDF 解析
（那是 `test_resumes_import_pdf.py` 的事）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.task_errors import TaskQueueUnavailableError
from app.core.task_kinds import FUNCTION_IMPORT_PDF, TaskKind, TaskState
from app.integrations import task_runtime
from app.integrations.task_inline import InlineTaskQueue, MemoryTaskStore
from app.integrations.task_runtime import TaskRuntime
from app.integrations.task_store import TaskRecord
from app.schemas.profile import ProfileUpsertRequest
from app.schemas.resume_import import PdfImportDropped, PdfImportMeta, PdfImportResult
from app.services import dependencies, inline_runtime
from app.services.resume_import import PdfInvalidError
from app.services.task_runner import TaskRunner
from app.services.task_service import TaskService

OWNER = "user-inline-1"
API_KEY = "sk-deepseek-inline-0123456789"


def _settings(**overrides: Any) -> Settings:
    """一份可控的 `Settings`。

    `redis_url=None` **必须显式传**：`pydantic-settings` 会把仓库根的 `.env` 填进没传的
    字段，而本机 `.env` 里恰好有 `REDIS_URL`——不写这一格，用例会在有 `.env` 的机器上
    恒绿、在 CI 裸环境里才红。
    """
    base: dict[str, Any] = {
        "environment": "development",
        "database_url": "mysql+pymysql://test",
        "jwt_secret_key": "fake-secret-for-testing-only-1234567890",
        "redis_url": None,
    }
    return Settings(**{**base, **overrides})


@pytest.fixture(autouse=True)
def _clean_inline_runtime() -> Iterator[None]:
    """每个用例前后丢掉内存单例：它是模块级全局，漏了就串味到下一个用例。"""
    asyncio.run(inline_runtime.reset_inline_task_runtime())
    try:
        yield
    finally:
        asyncio.run(inline_runtime.reset_inline_task_runtime())


def _imported() -> PdfImportResult:
    """一次「成功」的导入结果（真模型，不是 duck-typed 假件）。"""
    return PdfImportResult(
        profile=ProfileUpsertRequest(),
        meta=PdfImportMeta(
            pages_total=1,
            pages_used=1,
            text_chars=42,
            text_truncated=False,
            prompt_version="v1",
            source="text_layer",
            dropped=PdfImportDropped(),
        ),
    )


class _StubImporter:
    """`ResumePdfImportService` 的替身：只提供 `import_profile`。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[bytes] = []

    def import_profile(self, data: bytes) -> PdfImportResult:
        self.calls.append(data)
        if self._error is not None:
            raise self._error
        return _imported()


def _build_runtime(
    *,
    importer: _StubImporter,
    store: MemoryTaskStore | None = None,
) -> tuple[TaskRuntime, InlineTaskQueue, MemoryTaskStore]:
    """内存运行时 + 真 `TaskRunner`（只把「取导入服务」换成桩）。"""
    memory = store or MemoryTaskStore(record_ttl_seconds=1800, secret_ttl_seconds=900)

    async def _execute(task_id: str) -> None:
        runner = TaskRunner(
            memory,
            build_importer=lambda api_key: importer,  # type: ignore[arg-type,return-value]
            teacher_scope=lambda: _unused_scope(),
        )
        await runner.run(task_id)

    queue = InlineTaskQueue(executor=_execute)
    return TaskRuntime(queue=queue, store=memory), queue, memory


def _unused_scope() -> Any:
    """打包任务才用 `teacher_scope`；本文件的用例只跑导入，调到它就是设计错了。"""
    raise AssertionError("导入用例不该装配教师端作用域")


def _service(runtime: TaskRuntime) -> TaskService:
    async def _provider() -> TaskRuntime:
        return runtime

    return TaskService(_provider)


# --------------------------------------------------------------------------- #
# 一、触发条件（`ADR-034` §2.1）
# --------------------------------------------------------------------------- #


def test_inline_flag_is_the_and_of_two_conditions() -> None:
    """判据是「没有 `REDIS_URL`」**且**「环境在允许清单里」，写成两件的与。"""
    assert _settings().inline_task_runtime is True
    # 有地址就不降级：那是「我要测完整异步链路」的信号。
    assert _settings(redis_url="redis://127.0.0.1:6379/0").inline_task_runtime is False
    # 测试环境永不降级（`ADR-033` §4 的证据链）。
    assert _settings(environment="test").inline_task_runtime is False
    # 没登记的环境名也不降级：宁可 503，也不在挂着真实用户的环境里悄悄换实现。
    assert _settings(environment="staging").inline_task_runtime is False
    # 大小写与空格不该决定行为。
    assert _settings(environment=" Development ").inline_task_runtime is True


def test_development_without_redis_uses_the_inline_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`development` + 无 `REDIS_URL` → 装配层拿到的就是内存运行时。"""
    settings = _settings()
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(inline_runtime, "get_settings", lambda: settings)

    runtime = asyncio.run(dependencies._task_runtime())

    assert isinstance(runtime.queue, InlineTaskQueue)
    assert isinstance(runtime.store, MemoryTaskStore)


def test_test_environment_never_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """`test` 环境 + 无 `REDIS_URL` → **仍然 503**（抛 `TaskQueueUnavailableError`）。

    这条是「不许破坏 fakeredis 链路」的机械判据：降级一旦在 test 下生效，
    `tests/test_tasks.py` 里那些「队列不可用 → 503」的断言就会全部变成 202。
    """
    settings = _settings(environment="test")
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(inline_runtime, "get_settings", lambda: settings)
    # 把 autouse 夹具换掉的「取运行时」换回真的那个，并且清掉缓存。
    monkeypatch.setattr(dependencies, "get_task_runtime", task_runtime.get_task_runtime)
    monkeypatch.setattr(task_runtime, "_runtime", None)

    with pytest.raises(TaskQueueUnavailableError):
        asyncio.run(dependencies._task_runtime())


# --------------------------------------------------------------------------- #
# 二、内存存储的语义（`ADR-034` §2.3）
# --------------------------------------------------------------------------- #


def _record(task_id: str = "task-1") -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        kind=TaskKind.IMPORT_PDF,
        owner_id=OWNER,
        queue="arq:queue:ocr",
        state=TaskState.PENDING,
    )


def test_memory_store_keeps_the_terminal_state_once() -> None:
    """终态只写一次：第二次 `finish` 返回 `False` 且一个字段都不改。

    这是「超时被取消」与「正常结束」不会互相翻写的那条判据（`ADR-033` §2.3），
    降级模式必须同样成立，否则本地测不出那个 bug。
    """

    async def scenario() -> tuple[bool, bool, TaskRecord | None]:
        store = MemoryTaskStore(record_ttl_seconds=60, secret_ttl_seconds=60)
        await store.create(_record())
        first = await store.finish(
            "task-1", state=TaskState.COMPLETE, result={"profile": {}, "meta": {}}
        )
        second = await store.finish(
            "task-1", state=TaskState.FAILED, error_code="TASK_LOST", error_message="x"
        )
        return first, second, await store.read("task-1")

    first, second, record = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert record is not None
    assert record.state is TaskState.COMPLETE
    assert record.error_code is None


def test_memory_store_does_not_advance_a_terminal_record() -> None:
    """`advance()` 不覆盖终态（与 Redis 实现逐条同义）。"""

    async def scenario() -> TaskRecord | None:
        store = MemoryTaskStore(record_ttl_seconds=60, secret_ttl_seconds=60)
        await store.create(_record())
        await store.finish("task-1", state=TaskState.COMPLETE, result={"profile": {}})
        await store.advance("task-1", state=TaskState.RUNNING, stage=None)
        return await store.read("task-1")

    record = asyncio.run(scenario())
    assert record is not None
    assert record.state is TaskState.COMPLETE


def test_memory_store_expires_records_by_ttl() -> None:
    """TTL 到点后 `read()` 返回 `None`（对外等价于 404，`ADR-033` §2.6）。"""

    async def scenario() -> TaskRecord | None:
        store = MemoryTaskStore(record_ttl_seconds=0, secret_ttl_seconds=60)
        await store.create(_record())
        return await store.read("task-1")

    assert asyncio.run(scenario()) is None


def test_memory_store_takes_the_secret_once() -> None:
    """密钥**读到即删**（对应 Redis 的 `GETDEL`）：第二个执行者拿不到。"""

    async def scenario() -> tuple[str | None, str | None]:
        store = MemoryTaskStore(record_ttl_seconds=60, secret_ttl_seconds=60)
        await store.put_secret("task-1", "cipher-text")
        first = await store.take_secret("task-1")
        second = await store.take_secret("task-1")
        return first, second

    first, second = asyncio.run(scenario())
    assert first == "cipher-text"
    assert second is None


# --------------------------------------------------------------------------- #
# 三、任务真的跑完（`ADR-034` §2.2）
# --------------------------------------------------------------------------- #


def test_inline_runtime_completes_a_task_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pending` →（进程内跑）→ `complete`，`result` 与 Redis 模式同形。

    顺带钉住两件容易漏的事：**Key 只以密文存在于内存**，且任务跑完密文消失
    （读到即删 + `finally` 兜底，`ADR-033` §2.4）。
    """
    importer = _StubImporter()
    runtime, queue, store = _build_runtime(importer=importer)
    service = _service(runtime)

    async def scenario() -> tuple[str, TaskState, dict[str, Any] | None, str | None, str | None]:
        queued = await service.submit_import_pdf(
            owner_id=OWNER,
            api_key=API_KEY,
            data=b"%PDF-1.4 fake",
            request_id="req-1",
            ip="127.0.0.1",
        )
        early = await service.status(queued.task_id, viewer_id=OWNER)
        assert early.state in (TaskState.PENDING, TaskState.RUNNING)
        # 入队之后、执行之前：内存里只有密文。
        secret_before = await store.take_secret(queued.task_id)
        # 上面这一读把它取走了（GETDEL 语义）——所以这里要再放回去，
        # 否则任务会因为拿不到 Key 而失败。这正是「读到即删」的证据。
        assert secret_before is not None
        assert API_KEY not in secret_before
        await store.put_secret(queued.task_id, secret_before)

        await queue.drain()
        done = await service.status(queued.task_id, viewer_id=OWNER)
        leftover = await store.take_secret(queued.task_id)
        return queued.task_id, done.state, done.result, queued.created_at, leftover

    task_id, state, result, created_at, leftover = asyncio.run(scenario())
    assert state is TaskState.COMPLETE
    assert result is not None
    # 结果与 Redis 模式同形：`{profile, meta}`，且 `profile` 是**能直接 PUT** 的形状
    # （`PdfImportResult` 的 `model_dump(mode="json")`，不是手拼的字典）。
    assert set(result) == {"profile", "meta"}
    assert "full_name" in result["profile"]
    assert result["meta"]["source"] == "text_layer"
    assert created_at, "202 的响应里 created_at 必须有值"
    assert leftover is None, "任务跑完密文必须消失"
    assert importer.calls == [b"%PDF-1.4 fake"]
    assert task_id


def test_inline_runtime_records_a_failure_code() -> None:
    """失败也是**任务终态**（`failed` + 码），不是 4xx——与 Redis 模式同形。"""
    importer = _StubImporter(error=PdfInvalidError("no text layer"))
    runtime, queue, _ = _build_runtime(importer=importer)
    service = _service(runtime)

    async def scenario() -> Any:
        queued = await service.submit_import_pdf(
            owner_id=OWNER,
            api_key=API_KEY,
            data=b"%PDF-1.4 blank",
            request_id=None,
            ip=None,
        )
        await queue.drain()
        return await service.status(queued.task_id, viewer_id=OWNER)

    status = asyncio.run(scenario())
    assert status.state is TaskState.FAILED
    assert status.error is not None
    assert status.error.code == "PDF_INVALID"
    assert status.error.message, "失败必须带一句能读的人话（受信目录）"


def test_lost_inline_task_becomes_failed_once() -> None:
    """任务句柄没了（进程内丢了 / 执行器被清）：轮询判 `TASK_LOST`，且只写一次终态。

    与 `tests/test_tasks.py::test_lost_job_becomes_failed_and_stays_failed` 同一条判据，
    只是这边「job 没了」的表现是 `InlineTaskQueue.liveness` 返回 `NOT_FOUND`。
    """
    runtime, _queue, store = _build_runtime(importer=_StubImporter())
    service = _service(runtime)

    async def scenario() -> tuple[Any, Any]:
        record = TaskRecord(
            task_id="lost-1",
            kind=TaskKind.IMPORT_PDF,
            owner_id=OWNER,
            queue="arq:queue:ocr",
            state=TaskState.PENDING,
        )
        await store.create(record)
        first = await service.status("lost-1", viewer_id=OWNER)
        second = await service.status("lost-1", viewer_id=OWNER)
        return first, second

    first, second = asyncio.run(scenario())
    assert first.state is TaskState.FAILED
    assert first.error is not None and first.error.code == "TASK_LOST"
    # 第二次还是同一句话：终态写一次，后面都读它。
    assert second.state is TaskState.FAILED
    assert second.error is not None and second.error.code == "TASK_LOST"


def test_inline_queue_rejects_unknown_function_names() -> None:
    """未注册的函数名当场拒绝：内存模式不能「静默跑了另一个任务」。"""

    async def scenario() -> None:
        queue = InlineTaskQueue(executor=_noop)
        await queue.enqueue("no_such_task", task_id="t-1", queue="arq:queue:ocr", args=("t-1",))

    with pytest.raises(TaskQueueUnavailableError):
        asyncio.run(scenario())


def test_inline_queue_accepts_only_task_id_arguments() -> None:
    """参数形状与生产一致：`(task_id,)`。多传的参数没有接收方，只会掩盖调用点的错。"""

    async def scenario() -> None:
        queue = InlineTaskQueue(executor=_noop)
        await queue.enqueue(
            FUNCTION_IMPORT_PDF, task_id="t-1", queue="arq:queue:ocr", args=("t-1", "extra")
        )

    with pytest.raises(TaskQueueUnavailableError):
        asyncio.run(scenario())


def test_inline_queue_refuses_duplicate_task_ids() -> None:
    """同一条任务不许被入队两次（对应 ARQ 的 `_job_id` 幂等语义）。"""

    async def scenario() -> None:
        queue = InlineTaskQueue(executor=_slow_executor)
        await queue.enqueue(
            FUNCTION_IMPORT_PDF, task_id="dup", queue="arq:queue:ocr", args=("dup",)
        )
        await queue.enqueue(
            FUNCTION_IMPORT_PDF, task_id="dup", queue="arq:queue:ocr", args=("dup",)
        )

    with pytest.raises(TaskQueueUnavailableError):
        asyncio.run(scenario())


async def _noop(task_id: str) -> None:
    return None


async def _slow_executor(task_id: str) -> None:
    await asyncio.sleep(5)


# --------------------------------------------------------------------------- #
# 四、装配点没有第二条路径（防止「降级只在文档里」）
# --------------------------------------------------------------------------- #


def test_task_service_uses_the_switching_provider() -> None:
    """`get_task_service()` 交给 `TaskService` 的必须是那个**会分叉**的提供者。

    写成 AST 断言而不是行为断言：写死 `TaskService(get_task_runtime)` 也能通过所有
    行为用例（fakeredis 把那一条盖住了），但降级就永远不会生效——这正是
    `AGENTS.md` 里「闸要先红过」那条纪律要挡的形状。
    """
    import ast

    source = Path(dependencies.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    target = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_task_service"
    )
    returns = [node for node in ast.walk(target) if isinstance(node, ast.Return)]
    assert returns, "get_task_service 必须 return 一个 TaskService"
    call = returns[-1].value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Name) and call.func.id == "TaskService"
    argument = call.args[0]
    assert isinstance(argument, ast.Name)
    assert argument.id == "_task_runtime", "装配点必须走会分叉的 _task_runtime"


def test_both_runtime_providers_are_awaited_not_bound_at_import() -> None:
    """两个提供者都是 async：「用到才建」是这一层的既定形状（不建连接、不起进程）。"""
    assert asyncio.iscoroutinefunction(dependencies._task_runtime)
    assert asyncio.iscoroutinefunction(dependencies.get_inline_task_runtime)
    assert asyncio.iscoroutinefunction(task_runtime.get_task_runtime)


def test_inline_executor_builds_a_real_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """`inline_runtime` 里的执行器用的是**真的** `TaskRunner` 与真装配函数。

    这条是「降级跑的是生产那条代码」的机械判据：如果哪天有人图快，在降级路径里
    直接 `service.import_profile()` 并手写结果，这里就会红。
    """
    captured: dict[str, Any] = {}
    original = inline_runtime.TaskRunner

    def _spy(store: Any, *, build_importer: Any, teacher_scope: Any) -> Any:
        captured["build_importer"] = build_importer
        captured["teacher_scope"] = teacher_scope
        return original(store, build_importer=build_importer, teacher_scope=teacher_scope)

    monkeypatch.setattr(inline_runtime, "TaskRunner", _spy)
    settings = _settings()
    monkeypatch.setattr(inline_runtime, "get_settings", lambda: settings)

    async def scenario() -> None:
        runtime = await inline_runtime.get_inline_task_runtime()
        importer = _StubImporter()
        store = runtime.store
        await store.create(
            TaskRecord(
                task_id="spy-1",
                kind=TaskKind.IMPORT_PDF,
                owner_id=OWNER,
                queue="arq:queue:ocr",
                state=TaskState.PENDING,
            )
        )
        await store.put_secret("spy-1", "cipher")
        # 直接驱动执行器：真正会被调用的就是它。
        executor: Callable[[str], Awaitable[None]] = inline_runtime._make_executor(store)
        monkeypatch.setattr(dependencies, "build_resume_pdf_import_service", lambda key: importer)
        await executor("spy-1")
        await runtime.close()

    asyncio.run(scenario())
    # 执行器必须把**模块上那个**工厂原样交给 `TaskRunner`：另起一条私有实现
    # （比如直接调 `service.import_profile()` 再手拼结果）在这里会 red。
    assert captured["build_importer"] is dependencies.build_resume_pdf_import_service
    assert captured["teacher_scope"] is dependencies.teacher_tools_scope
