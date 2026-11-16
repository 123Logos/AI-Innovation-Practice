"""异步任务的响应契约（第 30 轮，`ADR-033` §2.6）。

两份模型，对应两个端点：

- `TaskQueued`：`POST /resumes/import-pdf` 与 `POST /teacher/resumes/batch-download`
  的 202 响应体。**只有四个字段**，故意不含 `profile` —— 请求确实还没被处理完，
  把「排队了」写成 200 + 一个空档案，前端就会把空表单填给用户。
- `TaskStatus`：`GET /tasks/{task_id}` 的响应体。`state` 只有四态
  （`pending` / `running` / `complete` / `failed`），`error` 只在 `failed` 时非空，
  `result` 只在 `complete` 时非空——**两者互斥**，由 §2.3 那条判定表保证。

`progress` 由 `app/core/task_kinds.py::progress_for()` 算，**不在前端算**：
两处各算一遍就会出现「进度条走到 100% 但状态还是 running」这种自相矛盾的画面。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.core.task_kinds import TaskKind, TaskStage, TaskState


class TaskQueued(BaseModel):
    """202 的响应体。`state` 恒为 `pending`，留着是为了让前端只认一种形状。"""

    task_id: str
    kind: TaskKind
    state: TaskState = TaskState.PENDING
    created_at: str


class TaskError(BaseModel):
    """任务失败的原因。`code` 与 `ADR-031` §2.5 的五个码同一套词表。"""

    code: str
    message: str
    retryable: bool = False


class TaskStatus(BaseModel):
    task_id: str
    kind: TaskKind
    state: TaskState
    stage: TaskStage | None = None
    progress: int = Field(ge=0, le=100)
    created_at: str
    finished_at: str | None = None
    #: `import_pdf` → `{"profile": …, "meta": …}`；
    #: `batch_download` → `{"filename": …, "bytes": …, "download_url": …}`。
    result: dict[str, Any] | None = None
    error: TaskError | None = None


__all__ = ["TaskError", "TaskQueued", "TaskStatus"]
