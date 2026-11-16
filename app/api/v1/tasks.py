"""异步任务的查询与产物下载端点（第 30 轮，`ADR-033` §2.6）。

两个端点，一条判据：**任务不是资源本身，任务的「结果」才是**。

- `GET /tasks/{task_id}`：四态轮询。**任务失败不是 HTTP 错误**——请求成功地被回答了，
  失败信息在 `data.error` 里（`code` 走 `ADR-031` §2.5 那套词表）。这一条如果做成
  「失败就 4xx/5xx」，前端的轮询循环会把它当成网络错误，用户看到的是「连不上服务」，
  而真话是「这份 PDF 没识别到文字」。
- `GET /tasks/{task_id}/download`：产物**单次性**下载。发完就删，第二次 410
  （`ADR-033` §2.5）。所以「短时下载 URL」不是靠过期时间假装，而是真的只有一个窗口。

两个端点都要过归属：`task.owner_id != 当前用户` 与「任务不存在」返回**同一个 404**，
不区分原因（区分就等于送出一个任务 id 的探测器，与版本 id 那条既有口径一致）。
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.core.error_messages import ErrorMessage
from app.core.responses import success_response
from app.core.task_errors import (
    TaskDownloadGoneError,
    TaskNotFoundError,
    TaskQueueUnavailableError,
)
from app.core.task_files import discard_download_file
from app.schemas.auth import UserRead
from app.services.dependencies import get_current_user, get_task_service
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])

TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]

ZIP_MEDIA_TYPE = "application/zip"


def _disposition(filename: str) -> str:
    """`filename*` 用 RFC 5987 编码：中文名直接放在引号里会被浏览器截断。

    与 `app/api/v1/teacher_tools.py::_attachment` 同一写法——两处必须一致，
    否则「同一个 ZIP 从同步端点下叫 A、从任务端点下叫 B」。
    """
    return f"attachment; filename=\"export\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/{task_id}")
async def read_task(
    task_id: str,
    request: Request,
    service: TaskServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """轮询任务状态。`state` 只有 pending / running / complete / failed 四态。"""
    try:
        current = await service.status(task_id, viewer_id=current_user.id)
    except TaskNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorMessage.TASK_NOT_FOUND,
        ) from error
    except TaskQueueUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorMessage.TASK_QUEUE_UNAVAILABLE,
        ) from error
    return success_response(request, current.model_dump(mode="json"))


@router.get("/{task_id}/download")
async def download_task_file(
    task_id: str,
    service: TaskServiceDep,
    current_user: CurrentUserDep,
    background: BackgroundTasks,
) -> FileResponse:
    """取产物（一次性）。

    用 `FileResponse` 而不是 `Response(content=...)`：50 份简历的 ZIP 是几十 MB，
    读进内存再写出去等于把「异步导出省下来的内存」原样还回去。文件在响应**结束之后**
    由后台任务删除（`BackgroundTasks`），所以「发完就删」与「发的时候还在」不矛盾。
    """
    try:
        artifact = await service.take_download(task_id, viewer_id=current_user.id)
    except TaskNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorMessage.TASK_NOT_FOUND,
        ) from error
    except TaskDownloadGoneError as error:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=ErrorMessage.TASK_DOWNLOAD_GONE,
        ) from error
    except TaskQueueUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorMessage.TASK_QUEUE_UNAVAILABLE,
        ) from error
    background.add_task(discard_download_file, task_id)
    return FileResponse(
        path=artifact.path,
        media_type=ZIP_MEDIA_TYPE,
        headers={
            "Content-Disposition": _disposition(artifact.filename),
            "Cache-Control": "no-store",
        },
    )
