"""岗位库接口（Phase 4 · B1，ADR-011 1.F / 2.A / 2.G / 议题 3.C）。

**路由顺序有讲究**：`/trash` 这类静态段必须声明在 `/{job_id}` 之前，
否则 FastAPI 会把 `trash` 当成岗位 id 塞进路径参数，表现为「回收站打不开，404」。
同一个坑的既有记录见 `app/api/v1/profiles.py:3-5`。

越权处理：不属于当前用户、且未发布的岗位一律 **404（不是 403）**——
403 会暴露「该 id 存在」（`app/api/v1/profiles.py:10` 的先例，理由见 ADR-011 1.F）。

身份只从 JWT 取（`get_current_user`）：`status` / `created_by_user_id` / `origin`
都不从请求体读，因此本文件没有任何一处把请求体的这些字段传给服务层。

本轮**不含任何外部调用**：`POST /jobs/extract-skills` 与 `EXTRACT_RATE_LIMIT`
属于下一轮（C 轮），这里连 import 都没有。
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.responses import success_response
from app.schemas.auth import UserRead
from app.schemas.job import MAX_PAGE_SIZE, JobWriteRequest
from app.services.dependencies import get_current_user, get_job_service
from app.services.job_service import JobNotFoundError, JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])

JobServiceDep = Annotated[JobService, Depends(get_job_service)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]

#: 1.F #1：列表默认只给已发布，自己的草稿要显式传 `status=draft`。
#: 用 `Literal` 而不是自由字符串：白名单外的值由 FastAPI 直接 422，
#: 不拿用户输入拼 SQL（与 `profiles.py` 的 `order_by` 同一做法）。
StatusFilter = Literal["draft", "published"]


def _not_found() -> HTTPException:
    """越权与不存在共用同一个 404（T6 错误模型：404 的 message 由全局处理器统一）。"""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="岗位不存在。")


@router.get("")
def list_jobs(
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
    status: StatusFilter = "published",
    keyword: str | None = Query(default=None, max_length=200),
    include_expired: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> dict[str, Any]:
    """岗位列表（1.F #1）。

    筛选本轮只有 `keyword`（企业名/岗位名）+ `status`（议题 3.C）：
    地点、薪资、经验都**存而不筛**（1.G），字段已经在库里，加筛选只改读路径。

    过期岗位默认隐藏（2.G）：`deadline < 今天` 不出现在默认结果里，
    要一起看到必须显式传 `include_expired=true`；`deadline = 今天` 仍出现。
    """
    jobs, total = service.list_jobs(
        current_user.id,
        status=status,
        keyword=keyword,
        include_expired=include_expired,
        page=page,
        page_size=page_size,
    )
    return success_response(
        request,
        {"jobs": [job.model_dump(mode="json") for job in jobs]},
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_job(
    payload: JobWriteRequest,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """录入岗位（1.F #3）。一律落 `draft`，发布是显式动作。"""
    job = service.create_job(current_user.id, payload)
    return success_response(request, {"job": job.model_dump(mode="json")})


@router.get("/trash")
def list_job_trash(
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """回收站（1.F #8）：只含**自己**软删除的岗位，过滤在 service/SQL 里做。

    声明位置必须在 `/{job_id}` 之前，见文件头注释。
    """
    jobs = service.list_trash(current_user.id)
    return success_response(
        request,
        {"jobs": [job.model_dump(mode="json") for job in jobs]},
    )


@router.get("/{job_id}")
def read_job(
    job_id: str,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """详情（1.F #2）：`published` 任何登录用户可读，他人 `draft` 404。"""
    try:
        job = service.get_job(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job": job.model_dump(mode="json")})


@router.put("/{job_id}")
def update_job(
    job_id: str,
    payload: JobWriteRequest,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """整体覆盖（1.F #4，仅创建者）：字段 + 技能子表全量替换，`status` 不变。"""
    try:
        job = service.update_job(current_user.id, job_id, payload)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job": job.model_dump(mode="json")})


@router.put("/{job_id}/publish")
def publish_job(
    job_id: str,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """发布（1.F #6，仅创建者）：进入广场，任何登录用户可读。"""
    try:
        job = service.publish_job(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job": job.model_dump(mode="json")})


@router.put("/{job_id}/unpublish")
def unpublish_job(
    job_id: str,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """下架回草稿（1.F #6，仅创建者）：广场立刻读不到，仅创建者可见。"""
    try:
        job = service.unpublish_job(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job": job.model_dump(mode="json")})


@router.delete("/{job_id}")
def delete_job(
    job_id: str,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """软删除（1.F #5，仅创建者）：进回收站，子表一行不动。"""
    try:
        service.delete_job(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job_id": job_id, "deleted": True})


@router.put("/{job_id}/restore")
def restore_job(
    job_id: str,
    request: Request,
    service: JobServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """从回收站恢复（1.F #9，仅创建者）：`deleted_at` 清回 `NULL`，`status` 原样保留。

    写法与响应形状对齐 `app/api/v1/profiles.py:221` 的 `PUT /{profile_id}/restore`——
    回收站没有恢复入口就是半个功能（ADR-011 2.A 草图第 9 行 v6 才补上）。
    非创建者、以及「岗位存在但不在回收站」都返回 404：与 1.F 同口径，
    不给「存在但无权」或「存在但没被删」留信息通道。
    """
    try:
        job = service.restore_job(current_user.id, job_id)
    except JobNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"job": job.model_dump(mode="json")})
