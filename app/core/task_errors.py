"""任务层对外的域错误与失败码表（第 30 轮，`ADR-033`）。

住在 `app/core` 而不是 `app/integrations`：`api/` 被 import-linter 禁止直接 import
集成层，但路由必须能把「任务不存在」「产物已取走」「队列不可用」翻成 404 / 410 / 503。
把异常放在两层都能引用的 core 里，路由只需要认识这几个异常，不需要认识 Redis 或 ARQ
（与 `app/core/pdf_errors.py` 同一处置）。

**这四个都不是 5xx**：它们是「这个请求在当前状态下无法完成」，而不是「服务坏了」。
唯一例外是 `TaskQueueUnavailableError`——它确实是外部依赖不可用，对外 503。
"""

from __future__ import annotations

from app.core.error_messages import ErrorMessage


class TaskNotFoundError(Exception):
    """任务不存在 / 已过期 / 不属于当前用户。三者对外同一个 404。

    为什么不区分：任务 id 是 uuid4，但「存在但不是你的」与「不存在」分开报
    等于送出一个任务 id 的探测器（与版本 id 那条既有口径一致）。
    """


class TaskDownloadGoneError(Exception):
    """产物已经被取走（单次性下载）或已被清理：对外 410。

    410 而不是 404：这个任务曾经存在过、产物也曾经在，用户该做的是**重新发起导出**，
    而不是怀疑自己做错了什么。
    """


class TaskQueueUnavailableError(Exception):
    """队列 / 任务记录不可用（没配 `REDIS_URL`、Redis 连不上）：对外 503。

    **绝不假装 202**：一个永远不会被消费的任务比一次明确的失败更糟。
    """


#: 失败码 → （上屏那句话，是否可重试）。
#:
#: **为什么是一张显式的表而不是 `ErrorMessage[code]`**：`PDF` 那一族的码与枚举名并不一一对应
#: （`PdfAiUnavailableError.code == "AI_SERVICE_UNAVAILABLE"`，而枚举成员叫 `AI_UNAVAILABLE`），
#: 按名字查会在第一个 AI 超时上抛 `KeyError` → 500，而那正是用户最常遇到的一种失败。
#: 表里这十一条覆盖：`ADR-031` §2.5 的五个码 + 导出侧三个 + 任务自身三个。
TASK_ERROR_MESSAGES: dict[str, tuple[ErrorMessage, bool]] = {
    "PDF_TOO_LARGE": (ErrorMessage.PDF_TOO_LARGE, False),
    "PDF_INVALID": (ErrorMessage.PDF_INVALID, False),
    "AI_KEY_MISSING": (ErrorMessage.AI_KEY_MISSING, False),
    "AI_SERVICE_UNAVAILABLE": (ErrorMessage.AI_UNAVAILABLE, True),
    "AI_OUTPUT_INVALID": (ErrorMessage.AI_OUTPUT_INVALID, True),
    "PDF_RENDERER_UNAVAILABLE": (ErrorMessage.PDF_RENDERER_UNAVAILABLE, True),
    "NO_DOWNLOADABLE_RESUME": (ErrorMessage.NO_DOWNLOADABLE_RESUME, False),
    "ORGANIZATION_ACTION_REQUIRED": (ErrorMessage.ORGANIZATION_ACTION_REQUIRED, False),
    "TASK_LOST": (ErrorMessage.TASK_LOST, True),
    "TASK_EXPIRED": (ErrorMessage.TASK_EXPIRED, False),
    "TASK_INTERNAL": (ErrorMessage.TASK_INTERNAL, True),
}


def describe_failure(code: str | None) -> tuple[str, str, bool]:
    """失败码 → `(code, message, retryable)`。认不得的码一律折成 `TASK_INTERNAL`。

    折成 `TASK_INTERNAL` 而不是把未知码原样带出去：前端按码分叉（例如
    `AI_KEY_MISSING` 才显示「去设置」），一个我们没登记过的码对它是无意义的字符串。
    """
    resolved = TASK_ERROR_MESSAGES.get(code or "")
    if resolved is None:
        return ("TASK_INTERNAL", str(ErrorMessage.TASK_INTERNAL), True)
    message, retryable = resolved
    return (str(code), str(message), retryable)
