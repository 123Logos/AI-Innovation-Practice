"""异步任务的共享词表：队列名、任务种类、状态、阶段、进度（第 30 轮，`ADR-033`）。

住在 `app/core` 而不是某个服务里，因为**四个层都要认同一套字**：
`integrations/`（入队与记录）、`services/`（编排与轮询）、`api/`（响应契约）、
`worker.py`（消费与状态推进）。任何一处自己写字符串，就会出现「worker 写 `running`、
API 判 `in_progress`」这类**只在真跑起来才炸**的分叉。

**队列名带 `arq:` 前缀**：ARQ 的队列是 Redis 的 list，键名就是队列名；三个队列平铺在
同一个 db 里，前缀让它们在 `redis-cli keys 'arq:queue:*'` 下自成一组，与 `task:<id>`
（任务记录）互不混淆。
"""

from __future__ import annotations

from enum import StrEnum


class TaskKind(StrEnum):
    """任务种类。取值**进 Redis 记录、进 API 响应**，改了就是契约变更。"""

    IMPORT_PDF = "import_pdf"
    BATCH_DOWNLOAD = "batch_download"


class TaskState(StrEnum):
    """对外只有这四态（老板第 30 轮点名）：pending / running / complete / failed。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class TaskStage(StrEnum):
    """细一档的进度标记，只为了给前端画进度条。**不是**状态机的一部分：
    状态机只有 `TaskState` 那四格，stage 在 `complete` / `failed` 时一律清成 `None`。
    """

    QUEUED = "queued"
    EXTRACTING = "extracting"  # 文本层 / OCR
    STRUCTURING = "structuring"  # DeepSeek 结构化
    RENDERING = "rendering"  # 无头浏览器逐份渲染
    PACKAGING = "packaging"  # 拼 ZIP


#: 三条队列（`ADR-033` §2.2）。名字即 Redis 键名。
QUEUE_OCR = "arq:queue:ocr"
QUEUE_PDF = "arq:queue:pdf"
QUEUE_DEFAULT = "arq:queue:default"

ALL_QUEUES = (QUEUE_OCR, QUEUE_PDF, QUEUE_DEFAULT)

#: 哪个任务进哪条队列。**这张表就是隔离的口径**：`worker.py` 的三个 `WorkerSettings`
#: 按它注册函数，`tests/test_tasks.py` 按它反查，所以「把 import_pdf 塞进 pdf 队列」
#: 这种改动会当场红，而不是等到生产上 PDF 导出把 OCR 挤死。
KIND_QUEUE: dict[TaskKind, str] = {
    TaskKind.IMPORT_PDF: QUEUE_OCR,
    TaskKind.BATCH_DOWNLOAD: QUEUE_PDF,
}

#: worker 里注册的函数名（`app/worker.py`）。**入队点与注册点共用这两个常量**：
#: 一边改名、另一边忘了改，任务就会永远 pending，而那种错只能靠「名字对得上」的用例发现。
#:
#: 第 31 轮（`ADR-034`）从 `app/services/task_service.py` 挪到这里的唯一原因：
#: 内存降级队列（`app/integrations/task_inline.py`）也要按名字校验，
#: 而 `integrations/` 在分层里位于 `services/` 之下，不能反向 import。
#: `task_service` 仍然 `from app.core.task_kinds import FUNCTION_IMPORT_PDF` 再导出，
#: 所以老的引用点一个字都不用改。
FUNCTION_IMPORT_PDF = "import_pdf_task"
FUNCTION_BATCH_DOWNLOAD = "batch_download_task"

ALL_TASK_FUNCTIONS = frozenset({FUNCTION_IMPORT_PDF, FUNCTION_BATCH_DOWNLOAD})

#: 阶段 → 进度百分比（0–100）。**唯一算法在这里**：前端只画，不自己推。
#: 数值不求精确（我们拿不到真实的字节进度），只求「单调不减」且让用户看出在动：
#: 排队 5、抽字 35、结构化 70、渲染 45、打包 85、完成 100。
STAGE_PROGRESS: dict[TaskStage, int] = {
    TaskStage.QUEUED: 5,
    TaskStage.EXTRACTING: 35,
    TaskStage.STRUCTURING: 70,
    TaskStage.RENDERING: 45,
    TaskStage.PACKAGING: 85,
}

#: 终态的进度（`complete` 一律 100；`failed` 保留最后阶段的值，别把进度条拉回 0——
#: 「跑到 70% 才失败」和「根本没开始」是两件不同的事，用户看进度条就分辨得出）。
COMPLETE_PROGRESS = 100


def progress_for(state: TaskState, stage: TaskStage | None) -> int:
    """状态 + 阶段 → 进度百分比。

    `failed` 落在哪个阶段就停在哪个阶段的数（见上）；没有 stage 的 `pending` 是 5。
    """
    if state is TaskState.COMPLETE:
        return COMPLETE_PROGRESS
    if stage is None:
        return STAGE_PROGRESS[TaskStage.QUEUED]
    return STAGE_PROGRESS.get(stage, STAGE_PROGRESS[TaskStage.QUEUED])
