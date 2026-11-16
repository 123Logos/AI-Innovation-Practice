"""异步任务的契约与隔离（第 30 轮，`ADR-033`）。

这个文件只测**新契约本身**，不重复业务断言：

1. **202 的字面含义**：两个入队端点回 202 + `task_id`，体里没有 `profile`（§2.6）；
2. **四态判定表**（§2.3）：`pending` →（job 没了）`failed TASK_LOST`；
   记录写 `running` → 报 `running`；`complete` → `progress=100` 且 `stage=None`；
3. **归属**：别人的 `task_id` 与不存在的 `task_id` 同一个 404；
4. **队列不可用就 503**（§2.6）：队列抛错时**不许假装 202**；没配 `REDIS_URL` 时
   在 `test` 环境仍是 503，而在 `development` 下会走内存降级（第 31 轮 `ADR-034`，
   那一条另有 `tests/test_inline_runtime.py` 专门测）；
5. **BYOK 跨进程**（§2.4）：入队后 Redis 里只有密文，worker 读走即删，
   ARQ job 的 args 里没有明文；
6. **队列隔离**（§2.2）：三个 `WorkerSettings` 的 `queue_name` / `max_jobs` / `functions`
   逐项与配置和 `KIND_QUEUE` 对齐——**一条队列混装两类任务当场红**；
7. **失败码表**（`app/core/task_errors.py`）：十一个码都有句子，且 `AI_SERVICE_UNAVAILABLE`
   这个与枚举名不同形的码不许 KeyError。

业务侧（五个码、三级降级、`dropped`、ZIP 结构、审计）仍在各自的老文件里，
只是取结果的方式换成了「上传 → 跑 worker → 轮询」。
"""

from __future__ import annotations

import ast
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.task_errors import TASK_ERROR_MESSAGES, describe_failure
from app.core.task_kinds import (
    KIND_QUEUE,
    QUEUE_DEFAULT,
    QUEUE_OCR,
    QUEUE_PDF,
    TaskKind,
    TaskStage,
    TaskState,
    progress_for,
)
from app.integrations import task_runtime
from app.services import dependencies
from tests.accounts import login, register
from tests.task_support import (
    drop_job,
    job_raw,
    queue_depth,
    read_record,
    read_secret,
    run_all_workers,
)

API = "/api/v1"
IMPORT_URL = f"{API}/resumes/import-pdf"
PASSWORD = "Strong-password-1"
API_KEY = "sk-deepseek-task-contract-0123456789"
ROOT = Path(__file__).resolve().parents[1]


def _auth(client: TestClient, username: str | None = None) -> tuple[dict[str, str], str]:
    username = username or f"task_{uuid.uuid4().hex[:8]}"
    assert register(client, username).status_code == 201
    headers = login(client, username)
    me = client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    return headers, str(me.json()["data"]["user"]["id"])


def _blank_pdf() -> bytes:
    """一份**没有文字层**的 PDF：足以让导入任务跑到 OCR 那一级并失败。

    用它而不是伪造「成功」：失败路径是这条链路里用户最常遇到的那一条，
    而它的可观测形状（`state=failed` + `code=PDF_INVALID`）正是前端要分叉的依据。
    """
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _upload(client: TestClient, headers: dict[str, str], **kwargs: Any) -> Any:
    return client.post(
        IMPORT_URL,
        files={"file": ("resume.pdf", _blank_pdf(), "application/pdf")},
        headers={**headers, "X-DeepSeek-Key": API_KEY},
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# 一、202 与轮询四态
# --------------------------------------------------------------------------- #


def test_import_returns_202_and_no_payload(client: TestClient) -> None:
    """202 + 只有四个字段：**体里不许出现 `profile`**，否则前端会拿空档案填表单。"""
    headers, _ = _auth(client)

    response = _upload(client, headers)

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert set(data) == {"task_id", "kind", "state", "created_at"}
    assert data["kind"] == "import_pdf"
    assert data["state"] == "pending"
    assert len(data["task_id"]) == 32
    assert "profile" not in response.text


def test_pending_then_failed_with_error_code_and_progress(client: TestClient) -> None:
    """四态里的三段：`pending` →（跑一次 worker）→ `failed`，且进度**停在它跑到的那一档**。

    第 31 轮修了一次倒退：这条用例原来断言「停在抽取那一档（35）」，而 runner 在调
    `import_profile()` 之前已经推进到 `structuring`（70）——于是前端看到 70 掉回 35。
    现在失败只改 `state` + `error`，`stage` 保留，所以这里断言 70。
    """
    headers, _ = _auth(client)
    task_id = _upload(client, headers).json()["data"]["task_id"]

    before = client.get(f"{API}/tasks/{task_id}", headers=headers)
    assert before.status_code == 200, before.text
    assert before.json()["data"]["state"] == "pending"
    assert before.json()["data"]["progress"] == 5
    assert before.json()["data"]["stage"] == "queued"

    run_all_workers()

    after = client.get(f"{API}/tasks/{task_id}", headers=headers)
    data = after.json()["data"]
    assert after.status_code == 200, after.text
    assert data["state"] == "failed"
    # 空白页既没有文字层、OCR 也认不出：这是 `ADR-031` §2.9 的第三级。
    assert data["error"]["code"] == "PDF_INVALID"
    assert data["error"]["retryable"] is False
    # 句子来自受信目录，不是上游异常文本。
    assert (
        data["error"]["message"]
        == "这份文件没识别到文字，请上传含文字层的 PDF，或在下面手动填写档案。"
    )
    # 失败停在**它跑到的那一档**（抽取与结构化都在 `import_profile()` 里，
    # 调它之前 runner 已经把 stage 推到 structuring）：进度条只进不退。
    # 「是文件读不出字」这件事由 `error.code` 说，不再靠把 stage 往回拨来表达。
    assert data["stage"] == "structuring"
    assert data["progress"] == progress_for(TaskState.FAILED, TaskStage.STRUCTURING)
    assert data["finished_at"]
    assert data["result"] is None


def test_import_failure_keeps_the_structuring_stage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """在 `structuring` 抛错：终态 `stage` 必须还是 `structuring`，**不许退回 `extracting`**。

    判据是「抛错那一帧的 stage == 终态的 stage」——不是在终态里看一眼 70 就算了。
    所以导出服务换成会在 `import_profile()` 里当场炸的桩，并在**抛之前**读一次记录：
    记录里那一格就是 runner 推进到的位置，终态必须与它一致。

    换成桩还顺手让这条用例不依赖 OCR：真实空白 PDF 也能复现，只是要等 2 秒起模型。
    """
    from app.services import dependencies
    from app.services.resume_import import PdfInvalidError
    from tests.task_support import read_record

    headers, _ = _auth(client)
    task_id = _upload(client, headers).json()["data"]["task_id"]
    observed: dict[str, str] = {}

    class _ExplodingImporter:
        def import_profile(self, data: bytes) -> Any:
            # 这一格里是 `structuring`：runner 在调 `import_profile()` 之前推进过一次。
            observed["stage_at_failure"] = read_record(task_id).get("stage", "")
            raise PdfInvalidError("这份文件读不出文字")

    monkeypatch.setattr(
        dependencies, "build_resume_pdf_import_service", lambda api_key: _ExplodingImporter()
    )

    run_all_workers()

    data = client.get(f"{API}/tasks/{task_id}", headers=headers).json()["data"]
    assert data["state"] == "failed"
    # 失败原因仍然由 code 表达（「读不出字」这件事没有丢）。
    assert data["error"]["code"] == "PDF_INVALID"
    assert observed["stage_at_failure"] == "structuring"
    # 核心断言：终态 stage == 抛错那一帧的 stage（70 没掉回 35）。
    assert data["stage"] == observed["stage_at_failure"]
    assert data["progress"] == progress_for(TaskState.FAILED, TaskStage.STRUCTURING)
    assert data["progress"] == 70
    assert "extracting" not in json.dumps(data)


def test_running_state_is_reported_from_the_record(client: TestClient) -> None:
    """记录写 `running` 就报 `running`，**哪怕 ARQ 还认为是 queued**。

    这个窗口真实存在：worker 先推进记录、ARQ 之后才把 job 标成 in_progress。
    按 ARQ 报 pending 会让进度条倒退一次，用户看到的是「刚动了一下又回去了」。
    """
    import asyncio

    import fakeredis.aioredis

    from tests.task_support import SERVER

    headers, _ = _auth(client)
    task_id = _upload(client, headers).json()["data"]["task_id"]

    async def _mark_running() -> None:
        client_ = fakeredis.aioredis.FakeRedis(server=SERVER, decode_responses=True)
        try:
            await client_.hset(
                f"task:{task_id}", mapping={"state": "running", "stage": "extracting"}
            )
        finally:
            await client_.aclose()

    asyncio.run(_mark_running())

    data = client.get(f"{API}/tasks/{task_id}", headers=headers).json()["data"]
    assert data["state"] == "running"
    assert data["stage"] == "extracting"
    assert data["progress"] == progress_for(TaskState.RUNNING, TaskStage.EXTRACTING)


def test_lost_job_becomes_failed_and_stays_failed(client: TestClient) -> None:
    """记录还在、ARQ 的 job 没了 → `failed` + `TASK_LOST`，**并且写进记录**。

    第二次轮询必须读到同一句话（而不是一会儿 pending 一会儿 failed）：
    这是「用户刷新几次都不该看到状态来回跳」的机械判据。
    """
    headers, _ = _auth(client)
    task_id = _upload(client, headers).json()["data"]["task_id"]

    drop_job(task_id, QUEUE_OCR)

    first = client.get(f"{API}/tasks/{task_id}", headers=headers).json()["data"]
    second = client.get(f"{API}/tasks/{task_id}", headers=headers).json()["data"]

    assert first["state"] == "failed"
    assert first["error"]["code"] == "TASK_LOST"
    assert first["error"]["retryable"] is True
    assert second["state"] == "failed" and second["error"]["code"] == "TASK_LOST"
    assert read_record(task_id)["state"] == "failed"


def test_unknown_and_foreign_tasks_are_both_404(client: TestClient) -> None:
    """别人的任务与不存在的任务**同一个响应**：不区分，免得任务 id 变成探测器。"""
    owner, _ = _auth(client)
    other, _ = _auth(client)
    task_id = _upload(client, owner).json()["data"]["task_id"]

    foreign = client.get(f"{API}/tasks/{task_id}", headers=other)
    unknown = client.get(f"{API}/tasks/{uuid.uuid4().hex}", headers=other)

    assert foreign.status_code == 404, foreign.text
    assert unknown.status_code == 404, unknown.text
    assert foreign.json()["error"]["code"] == unknown.json()["error"]["code"]


def test_task_endpoints_require_login(client: TestClient) -> None:
    assert client.get(f"{API}/tasks/{uuid.uuid4().hex}").status_code == 401
    assert client.get(f"{API}/tasks/{uuid.uuid4().hex}/download").status_code == 401


# --------------------------------------------------------------------------- #
# 二、队列不可用必须 503（不许假装 202）
# --------------------------------------------------------------------------- #


def test_queue_failure_returns_503_not_202(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入队抛错时：503 + 一句能动的下一步，且**没有**任务被创建。"""
    from app.core.task_errors import TaskQueueUnavailableError

    class _Broken:
        async def __call__(self) -> Any:
            raise TaskQueueUnavailableError("队列挂了")

    monkeypatch.setattr(dependencies, "get_task_runtime", _Broken())
    headers, _ = _auth(client)

    response = _upload(client, headers)

    assert response.status_code == 503, response.text
    error = response.json()["error"]
    # 5xx 的**消息**是全站固定那一句（`app/main.py`：细节只进日志，不上屏）；
    # 「是哪一类 503」留给 `details.reason`——与 `PDF_RENDERER_UNAVAILABLE` 同一口径。
    assert error["details"]["reason"] == "TASK_QUEUE_UNAVAILABLE"
    assert error["code"] == "INTERNAL_ERROR"
    # 任务记录一个都不该留下（`_rollback` 会删掉记录与密文）。
    assert queue_depth(QUEUE_OCR) == 0


def test_missing_redis_url_is_503_where_degradation_is_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配 `REDIS_URL` 且**不许降级**（`environment=test`）：启动不失败，但任务端点 503。

    第 31 轮（`ADR-034`）之后「没配 Redis」不再等于 503——`development` / `local` 会走
    内存降级（见下一条用例）。但**测试环境绝不降级**这条边界必须自己有一条用例：
    一旦它在 `test` 下也生效，`tests/test_tasks.py` 与 `tests/test_resumes_import_pdf.py`
    里那套 fakeredis + 真 `arq.Worker` 的证据链就会被悄悄绕过，而它们不会因此变红。
    """
    settings = task_runtime.get_settings()
    monkeypatch.setattr(
        task_runtime,
        "get_settings",
        lambda: settings.model_copy(update={"redis_url": None, "environment": "test"}),
    )
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: settings.model_copy(update={"redis_url": None, "environment": "test"}),
    )
    monkeypatch.setattr(dependencies, "get_task_runtime", task_runtime.get_task_runtime)
    monkeypatch.setattr(task_runtime, "_runtime", None)
    headers, _ = _auth(client)

    response = _upload(client, headers)

    assert response.status_code == 503, response.text
    assert response.json()["error"]["details"]["reason"] == "TASK_QUEUE_UNAVAILABLE"


def test_missing_redis_url_in_development_returns_202(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`development` + 没配 `REDIS_URL` → **202，不是 503**（本地零依赖降级，`ADR-034`）。

    执行器换成空实现：本用例要验的是「端点不再 503、契约仍是 202」，
    而「任务真的跑完并写回结果」由 `tests/test_inline_runtime.py` 的端到端用例负责。
    两件分开的理由很实际——这里若真跑一遍解析，就会在 CI 里拉起 OCR 与真实外呼。
    """
    from app.services import inline_runtime

    async def _noop(task_id: str) -> None:
        return None

    settings = task_runtime.get_settings()
    inline_settings = settings.model_copy(update={"redis_url": None, "environment": "development"})
    monkeypatch.setattr(task_runtime, "get_settings", lambda: inline_settings)
    monkeypatch.setattr(dependencies, "get_settings", lambda: inline_settings)
    monkeypatch.setattr(inline_runtime, "get_settings", lambda: inline_settings)
    monkeypatch.setattr(inline_runtime, "_make_executor", lambda store: _noop)
    monkeypatch.setattr(dependencies, "get_task_runtime", task_runtime.get_task_runtime)
    monkeypatch.setattr(task_runtime, "_runtime", None)
    headers, _ = _auth(client)

    response = _upload(client, headers)

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert set(data) == {"task_id", "kind", "state", "created_at"}
    assert data["kind"] == "import_pdf"
    # 记录真的落进了内存存储（不是「202 了但什么都没建」）。
    runtime = asyncio.run(dependencies._task_runtime())
    record = asyncio.run(runtime.store.read(data["task_id"]))
    assert record is not None
    assert record.kind is TaskKind.IMPORT_PDF
    asyncio.run(inline_runtime.reset_inline_task_runtime())


# --------------------------------------------------------------------------- #
# 三、BYOK 跨进程（`ADR-033` §2.4）
# --------------------------------------------------------------------------- #


def test_key_is_encrypted_in_redis_and_deleted_after_use(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入队后：Redis 里有密文、**没有明文**，ARQ job 的 args 里也没有；
    任务跑完之后那条密文**不存在**（读到即删 + `finally` 兜底）。
    """
    headers, _ = _auth(client)
    task_id = _upload(client, headers).json()["data"]["task_id"]

    secret = read_secret(task_id)
    assert secret, "入队后应当有 Key 密文"
    assert API_KEY not in secret, "Key 明文不许进 Redis"
    assert API_KEY not in job_raw(task_id), "Key 明文不许进 ARQ job 的 args"
    assert API_KEY not in json.dumps(read_record(task_id)), "Key 明文不许进任务记录"

    run_all_workers()

    assert read_secret(task_id) is None, "任务读完必须立刻删掉密文"


def test_key_missing_after_use_is_a_key_error_not_a_silent_success(client: TestClient) -> None:
    """密文被删掉之后重跑同一条任务：**失败得很清楚**（`AI_KEY_MISSING`），不是静默成功。

    「重试一次就烧一次 AI 额度」，所以拿不到 Key 时必须立刻失败——不能拿空 Key 去撞上游，
    也不能把「没 Key」静默当成「解析失败」。
    """
    headers, _ = _auth(client)
    task_id = _upload(client, headers).json()["data"]["task_id"]
    # 先把密文取走（模拟「另一个执行者已经读过了」）。
    from app.core.task_secrets import decrypt_secret

    secret = read_secret(task_id)
    assert secret and decrypt_secret(secret) == API_KEY

    import asyncio

    import fakeredis.aioredis

    from tests.task_support import SERVER

    async def _take() -> None:
        client_ = fakeredis.aioredis.FakeRedis(server=SERVER, decode_responses=True)
        try:
            await client_.getdel(f"task:key:{task_id}")
        finally:
            await client_.aclose()

    asyncio.run(_take())
    run_all_workers()

    data = client.get(f"{API}/tasks/{task_id}", headers=headers).json()["data"]
    assert data["state"] == "failed"
    assert data["error"]["code"] == "AI_KEY_MISSING"


# --------------------------------------------------------------------------- #
# 四、队列隔离（`ADR-033` §2.2）
# --------------------------------------------------------------------------- #


def test_queue_isolation_is_wired() -> None:
    """三条队列的 `queue_name` / `max_jobs` / `functions` 与配置逐项对齐。

    这条用例的意义不是「读一遍配置」，而是**把「混装」与「max_jobs 没接配置」变红**：
    两者都不会让任何 HTTP 用例失败，只会在生产上表现为「PDF 导出把 OCR 挤死」。
    """
    from app.core.config import get_settings
    from app.worker import (
        DefaultWorkerSettings,
        OcrWorkerSettings,
        PdfWorkerSettings,
        batch_download_task,
        import_pdf_task,
    )

    settings = get_settings()
    cases = [
        (OcrWorkerSettings, QUEUE_OCR, settings.worker_ocr_max_jobs, [import_pdf_task]),
        (PdfWorkerSettings, QUEUE_PDF, settings.worker_pdf_max_jobs, [batch_download_task]),
        (DefaultWorkerSettings, QUEUE_DEFAULT, settings.worker_default_max_jobs, []),
    ]
    for settings_cls, queue, expected_jobs, expected_functions in cases:
        assert settings_cls.queue_name == queue, settings_cls.__name__
        assert settings_cls.max_jobs == expected_jobs, settings_cls.__name__
        assert [fn.__name__ for fn in settings_cls.functions] == [
            fn.__name__ for fn in expected_functions
        ], settings_cls.__name__

    assert len({cls.queue_name for cls, *_ in cases}) == 3

    # 更强的一条：用 **ARQ 自己的** `get_kwargs()`（`arq` CLI 就是靠它把 WorkerSettings
    # 变成 `Worker(**kwargs)` 的）看一遍。它只读 `settings_cls.__dict__`，所以
    # 「基类 + 子类」那种写法会让 `redis_settings` / `max_tries` 静默丢失，
    # 表现是「连到默认 127.0.0.1:6379 并按默认 5 次重试」——HTTP 用例一条都测不出来。
    from arq.worker import get_kwargs

    for settings_cls, queue, expected_jobs, _ in cases:
        kwargs = get_kwargs(settings_cls)
        assert kwargs["queue_name"] == queue, settings_cls.__name__
        assert kwargs["max_jobs"] == expected_jobs, settings_cls.__name__
        assert kwargs["max_tries"] == 1, settings_cls.__name__
        assert kwargs["redis_settings"] is not None, settings_cls.__name__
        assert kwargs["job_timeout"] == get_settings().worker_job_timeout_seconds


def test_kind_queue_table_matches_the_isolation() -> None:
    """`KIND_QUEUE` 是隔离的唯一口径：导入进 ocr 队列、打包进 pdf 队列，一格都不许换。"""
    assert KIND_QUEUE[TaskKind.IMPORT_PDF] == QUEUE_OCR
    assert KIND_QUEUE[TaskKind.BATCH_DOWNLOAD] == QUEUE_PDF
    # 默认队列今天没有任务种类指向它（`ADR-033` §7 第 4 条：空跑，如实记账）。
    assert QUEUE_DEFAULT not in KIND_QUEUE.values()


def test_enqueued_names_exist_in_worker() -> None:
    """入队用的函数名必须真的注册在 worker 里（改名而漏改一边 = 任务永远 pending）。"""
    from app.services import task_service
    from app.worker import batch_download_task, import_pdf_task

    assert task_service.FUNCTION_IMPORT_PDF == import_pdf_task.__name__
    assert task_service.FUNCTION_BATCH_DOWNLOAD == batch_download_task.__name__


def test_import_route_uses_the_async_entrypoint() -> None:
    """源码级：`/resumes/import-pdf` 的处理器**不许**再直接调同步的 `import_profile`。

    这条挡的是「有人为了让某条用例过，在路由里顺手把解析同步跑一遍」——
    那会绕过队列、绕过并发上限，而且 HTTP 用例全绿。
    """
    source = (ROOT / "app/api/v1/resumes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "import_resume_pdf"
    )
    body = ast.get_source_segment(source, handler) or ""
    assert "submit_import_pdf" in body
    assert "import_profile" not in body
    assert "HTTP_202_ACCEPTED" in body


# --------------------------------------------------------------------------- #
# 五、失败码表与工具函数
# --------------------------------------------------------------------------- #


def test_every_failure_code_has_a_sentence() -> None:
    """十一个码都有句子、都可重试或不可重试，且**与 `ADR-031` §2.5 的五个码同名**。"""
    expected = {
        "PDF_TOO_LARGE",
        "PDF_INVALID",
        "AI_KEY_MISSING",
        "AI_SERVICE_UNAVAILABLE",
        "AI_OUTPUT_INVALID",
        "PDF_RENDERER_UNAVAILABLE",
        "NO_DOWNLOADABLE_RESUME",
        "ORGANIZATION_ACTION_REQUIRED",
        "TASK_LOST",
        "TASK_EXPIRED",
        "TASK_INTERNAL",
    }
    assert expected <= set(TASK_ERROR_MESSAGES)
    for code in expected:
        resolved_code, message, retryable = describe_failure(code)
        assert resolved_code == code
        assert message and "。" in message
        assert isinstance(retryable, bool)
    # `AI_SERVICE_UNAVAILABLE` 的枚举成员叫 `AI_UNAVAILABLE`：按名字查会 KeyError，
    # 这条用例就是那次「AI 超时 → 500」的反证。
    assert describe_failure("AI_SERVICE_UNAVAILABLE")[1]
    # 认不得的码折成 TASK_INTERNAL，而不是把未知字符串丢给前端。
    assert describe_failure("SOMETHING_NEW")[0] == "TASK_INTERNAL"


def test_progress_never_goes_backwards() -> None:
    """阶段推进时进度单调不减（前端进度条直接用它，倒退一次就是一次「页面在抖」）。"""
    ordered = [
        TaskStage.QUEUED,
        TaskStage.EXTRACTING,
        TaskStage.STRUCTURING,
    ]
    values = [progress_for(TaskState.RUNNING, stage) for stage in ordered]
    assert values == sorted(values)
    assert progress_for(TaskState.COMPLETE, TaskStage.STRUCTURING) == 100
    assert progress_for(TaskState.FAILED, None) == progress_for(TaskState.PENDING, None)


def test_task_files_reject_path_traversal(tmp_path: Path) -> None:
    """任务 id 是唯一的安全边界：`../` 之类一律在拼路径之前就被拒。"""
    from app.core.task_files import InvalidTaskIdError, download_file_path, save_import_file

    for bad in ("../evil", "a/b", "", "x" * 32, "ABCDEF" * 5 + "ab"):
        with pytest.raises(InvalidTaskIdError):
            download_file_path(bad)
    with pytest.raises(InvalidTaskIdError):
        save_import_file("..\\..\\evil", b"%PDF-1.4")
