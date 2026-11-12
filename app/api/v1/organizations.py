"""组织管理路由（批次 B5，前缀 `/organizations`）。

权限分工见 `docs/adr/ADR-021` §2.3：**路由层判「你有没有这个权限」，服务层判
「这个对象是不是你这个组织的」**。两者缺一都是漏洞，合并成一个同样是漏洞
（权限表里没有、也不该有「某条记录属于谁」的信息）。

所有 4xx/5xx 都由 `app/main.py` 的处理器转成 T6 统一错误模型。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.error_messages import ErrorMessage
from app.core.permissions import AUDIT_READ, ORG_MEMBER_MANAGE, ORG_MEMBER_READ, UserRole
from app.core.responses import success_response
from app.schemas.auth import OrganizationBrief
from app.schemas.organization import (
    AuditLogListData,
    AuditLogRead,
    MemberRead,
    OrganizationOverviewData,
    RoleUpdateRequest,
)
from app.services.auth_service import CurrentUserContext
from app.services.dependencies import (
    client_ip,
    get_current_context,
    get_organization_service,
    require_permission,
)
from app.services.organization_service import (
    CannotChangeOwnRoleError,
    CrossOrganizationError,
    InvalidRoleAssignmentError,
    MemberNotFoundError,
    OrganizationError,
    OrganizationNotFoundError,
    OrganizationService,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])

OrganizationServiceDep = Annotated[OrganizationService, Depends(get_organization_service)]
CurrentContextDep = Annotated[CurrentUserContext, Depends(get_current_context)]
MemberReaderDep = Annotated[CurrentUserContext, Depends(require_permission(ORG_MEMBER_READ))]
MemberManagerDep = Annotated[CurrentUserContext, Depends(require_permission(ORG_MEMBER_MANAGE))]
AuditReaderDep = Annotated[CurrentUserContext, Depends(require_permission(AUDIT_READ))]

# 文案一律来自 `ErrorMessage` 受信目录（G3 会拦路由里的中文字面量）：
# 直接 `detail=ErrorMessage.X`，不要转存成模块常量——常量引用的是枚举对象而不是
# 字符串字面量，G3 无法静态判定，会报「文案必须来自表」。


def _translate(error: OrganizationError) -> HTTPException:
    """域异常 → HTTP 状态与**人话**文案。

    一次翻译一处，路由里不再各写一遍 `except`：新增一种域错误只要加一行，
    也不会出现「同一个错误在 A 端点 404、在 B 端点 400」。
    """
    if isinstance(error, (OrganizationNotFoundError,)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ErrorMessage.NOT_FOUND)
    if isinstance(error, CrossOrganizationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ErrorMessage.ORGANIZATION_FORBIDDEN
        )
    if isinstance(error, MemberNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ErrorMessage.ORGANIZATION_MEMBER_NOT_FOUND
        )
    if isinstance(error, CannotChangeOwnRoleError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ErrorMessage.SELF_ROLE_CHANGE
        )
    if isinstance(error, InvalidRoleAssignmentError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ErrorMessage.ROLE_NOT_ASSIGNABLE
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ErrorMessage.ORGANIZATION_ACTION_FAILED,
    )


@router.get("/me")
def read_my_organization(
    request: Request,
    context: CurrentContextDep,
    service: OrganizationServiceDep,
) -> dict[str, Any]:
    """当前账号的组织概览。学生也能看（他需要知道自己在哪个学校/班级下）。"""
    try:
        overview = service.overview(context)
    except OrganizationError as error:
        raise _translate(error) from error
    return success_response(
        request,
        OrganizationOverviewData(
            organization=OrganizationBrief.model_validate(overview.organization),
            role=overview.role,
            member_count=overview.member_count,
        ).model_dump(mode="json"),
    )


@router.get("/{organization_id}/members")
def list_members(
    organization_id: str,
    request: Request,
    context: MemberReaderDep,
    service: OrganizationServiceDep,
) -> dict[str, Any]:
    try:
        rows = service.list_members(context, organization_id)
    except OrganizationError as error:
        raise _translate(error) from error
    return success_response(
        request,
        {
            "items": [
                MemberRead(
                    user_id=user.id,
                    username=user.username,
                    email=user.email,
                    role=UserRole(member.role),
                    joined_at=member.created_at,
                ).model_dump(mode="json")
                for member, user in rows
            ]
        },
    )


@router.patch("/{organization_id}/members/{user_id}")
def change_member_role(
    organization_id: str,
    user_id: str,
    payload: RoleUpdateRequest,
    request: Request,
    context: MemberManagerDep,
    service: OrganizationServiceDep,
) -> dict[str, Any]:
    try:
        service.change_role(
            context,
            organization_id=organization_id,
            target_user_id=user_id,
            new_role=UserRole(payload.role),
            ip=client_ip(request),
        )
    except OrganizationError as error:
        raise _translate(error) from error
    return success_response(request, {"user_id": user_id, "role": payload.role})


@router.get("/{organization_id}/audit-logs")
def list_audit_logs(
    organization_id: str,
    request: Request,
    context: AuditReaderDep,
    service: OrganizationServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    # 跨组织的读取在这里显式拒绝：`audit:read` 是「能读审计」，
    # 不是「能读任何组织的审计」（同样的两级判断，见模块 docstring）。
    if context.organization is None or context.organization.id != organization_id:
        raise _translate(CrossOrganizationError(organization_id))
    entries, total = service.list_audit_logs(context, limit=limit, offset=offset)
    return success_response(
        request,
        AuditLogListData(
            items=[AuditLogRead.model_validate(entry) for entry in entries],
            total=total,
        ).model_dump(mode="json"),
    )
