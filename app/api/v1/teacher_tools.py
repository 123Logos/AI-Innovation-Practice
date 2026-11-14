"""教师端工具与简历导出端点（FR-08 / B10a、B10b，`ADR-029`）。

三个入口，全部走同一条链：`require_permission`（参数依赖，先于处理器）→
处理器第一条语句调 service → service 第一条语句过
`app/core/action_gate.py::require_organization`。路由层不自己判「有没有组织」，
也不先读任何业务数据——那两件事都在 service 里，且被源码级用例钉住位置。

文件下载不用 `success_response()`：PDF / ZIP 是二进制，套统一信封只会让前端多一层
无意义的解包。错误仍走 T6 统一错误模型，且每条 `detail` 都来自
`app/core/error_messages.py`（G3）。
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.core.action_gate import OrganizationAction
from app.core.error_messages import ErrorMessage
from app.core.pdf_errors import PdfRendererUnavailableError
from app.core.permissions import DASHBOARD_READ, RESUME_DOWNLOAD, SKILL_CONFIRM
from app.core.responses import success_response
from app.schemas.teacher_tools import BatchDownloadRequest, SkillConfirmRequest
from app.services.auth_service import CurrentUserContext
from app.services.dependencies import (
    client_ip,
    get_teacher_tools_service,
    teacher_permission,
)
from app.services.teacher_tools_service import (
    NoDownloadableResumeError,
    ResumeExportNotFoundError,
    SkillConfirmNameInvalidError,
    SkillConfirmProfileNotFoundError,
    TeacherOrganizationRequiredError,
    TeacherToolsService,
)

router = APIRouter(prefix="/teacher", tags=["teacher-tools"])
#: 导出端点挂在 `/resumes` 下（与既有简历端点同一个资源族），但装配的是教师工具服务。
export_router = APIRouter(prefix="/resumes", tags=["resumes"])

TeacherToolsDep = Annotated[TeacherToolsService, Depends(get_teacher_tools_service)]
DashboardReaderDep = Annotated[
    CurrentUserContext,
    Depends(teacher_permission(DASHBOARD_READ, OrganizationAction.VIEW_DASHBOARD)),
]
DownloaderDep = Annotated[
    CurrentUserContext,
    Depends(teacher_permission(RESUME_DOWNLOAD, OrganizationAction.BULK_DOWNLOAD)),
]
ExportDep = Annotated[
    CurrentUserContext,
    Depends(teacher_permission(RESUME_DOWNLOAD, OrganizationAction.EXPORT_RESUME)),
]
#: 教师确认技能（B7 证据链）：独立权限，不与「看板」或「下载」共用——
#: 它是唯一一个**写别人档案**的教师动作，授权面不该被另两件事顺带放大。
ConfirmerDep = Annotated[
    CurrentUserContext,
    Depends(teacher_permission(SKILL_CONFIRM, OrganizationAction.CONFIRM_SKILL)),
]


def _attachment(filename: str, media_type: str, content: bytes) -> Response:
    """`filename*` 用 RFC 5987 编码：中文姓名直接放在引号里会被浏览器截断。"""
    disposition = f"attachment; filename=\"export\"; filename*=UTF-8''{quote(filename, safe='')}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )


def _translate(error: Exception) -> HTTPException:
    """域异常 → HTTP；五种失败各自的下一步动作不同，绝不合成一句「操作失败」。"""
    if isinstance(error, TeacherOrganizationRequiredError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.ORGANIZATION_ACTION_REQUIRED,
        )
    if isinstance(error, ResumeExportNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorMessage.RESUME_EXPORT_NOT_FOUND,
        )
    if isinstance(error, NoDownloadableResumeError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.NO_DOWNLOADABLE_RESUME,
        )
    if isinstance(error, PdfRendererUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorMessage.PDF_RENDERER_UNAVAILABLE,
        )
    if isinstance(error, SkillConfirmProfileNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorMessage.SKILL_CONFIRM_PROFILE_NOT_FOUND,
        )
    if isinstance(error, SkillConfirmNameInvalidError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.SKILL_CONFIRM_NAME_INVALID,
        )
    raise error


@router.get("/dashboard")
def read_dashboard(
    request: Request,
    service: TeacherToolsDep,
    context: DashboardReaderDep,
) -> dict[str, Any]:
    """标签分布 + 岗位技能覆盖率概况（只读、不调 AI、不含个人信息）。"""
    try:
        data = service.dashboard(context, ip=client_ip(request))
    except TeacherOrganizationRequiredError as error:
        raise _translate(error) from error
    return success_response(request, data.model_dump(mode="json"))


@router.get("/resumes")
def list_downloadable_resumes(
    request: Request,
    service: TeacherToolsDep,
    context: DashboardReaderDep,
) -> dict[str, Any]:
    """本组织可导出的简历清单（不含正文）；看板页的勾选列表读它。"""
    try:
        items = service.list_downloadable(context, ip=client_ip(request))
    except TeacherOrganizationRequiredError as error:
        raise _translate(error) from error
    return success_response(request, {"items": [item.model_dump(mode="json") for item in items]})


@router.post("/resumes/batch-download")
def batch_download(
    payload: BatchDownloadRequest,
    request: Request,
    service: TeacherToolsDep,
    context: DownloaderDep,
) -> Response:
    """打包下载：ZIP 内有 `README.txt` / `manifest.json` / `resumes/*.pdf`。"""
    try:
        result = service.batch_download(context, payload.resume_ids, ip=client_ip(request))
    except (
        TeacherOrganizationRequiredError,
        NoDownloadableResumeError,
        PdfRendererUnavailableError,
    ) as error:
        raise _translate(error) from error
    return _attachment(result.filename, "application/zip", result.content)


@export_router.post("/{resume_id}/export")
def export_resume(
    resume_id: str,
    request: Request,
    service: TeacherToolsDep,
    context: ExportDep,
    format: Annotated[str, Query(pattern="^pdf$")] = "pdf",
) -> Response:
    """单份 PDF 导出。打印 CSS 与实时预览同源，自然分页、段落不腰斩（`ADR-029` §2.5）。"""
    try:
        result = service.export_pdf(context, resume_id, ip=client_ip(request))
    except (
        TeacherOrganizationRequiredError,
        ResumeExportNotFoundError,
        PdfRendererUnavailableError,
    ) as error:
        raise _translate(error) from error
    return _attachment(result.filename, "application/pdf", result.content)


@router.post("/skills/confirm")
def confirm_skill(
    payload: SkillConfirmRequest,
    request: Request,
    service: TeacherToolsDep,
    context: ConfirmerDep,
) -> dict[str, Any]:
    """教师确认一个学生的技能（B7 证据链，`ADR-027` §5 第 3 条）。

    写两处、只升不建：`profile_tags` 落一条 `source='teacher'` 的标签；
    学生简历里已有同名技能时把它的来源升成 `teacher_confirmed`，没有就不新建。
    档案不在本组织 → 404（与「档案不存在」同一个响应）；散人 → 400 提示绑定组织。
    """
    try:
        result = service.confirm_skill(
            context, payload.profile_id, payload.skill_name, ip=client_ip(request)
        )
    except (
        TeacherOrganizationRequiredError,
        SkillConfirmProfileNotFoundError,
        SkillConfirmNameInvalidError,
    ) as error:
        raise _translate(error) from error
    return success_response(request, result.model_dump(mode="json"))
