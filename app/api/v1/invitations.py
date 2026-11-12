"""组织邀请路由（批次 B6，`ADR-023`）。

一个不带 prefix 的 router，路径写全，这样 `app/api/v1/__init__.py` 只需 include 一行：
管理端挂 `/organizations/{id}/invitations`（与 B5 的 `/organizations/{id}/members`
同前缀但路径段不同，没有排序陷阱），兑换端挂 `/invitations/redeem`。

权限分工照 `ADR-021` §2.3：路由层判「有没有权限」，服务层判「是不是你这个组织」。
兑换端点**不要求任何组织权限**——还没加入组织的人按定义不可能有。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.error_messages import ErrorMessage
from app.core.permissions import ORG_MEMBER_MANAGE, UserRole
from app.core.responses import success_response
from app.schemas.auth import OrganizationBrief
from app.schemas.invitation import (
    InvitationCreateData,
    InvitationCreateRequest,
    InvitationListData,
    InvitationRead,
    InvitationRedeemData,
    InvitationRedeemRequest,
)
from app.services.auth_service import CurrentUserContext
from app.services.dependencies import (
    client_ip,
    get_current_context,
    get_invitation_service,
    require_permission,
)
from app.services.invitation_service import (
    AlreadyInOrganizationError,
    CrossOrganizationError,
    InvitationError,
    InvitationNotFoundError,
    InvitationService,
    InvitationUnusableError,
    OrganizationMissingError,
)

router = APIRouter(tags=["invitations"])

InvitationServiceDep = Annotated[InvitationService, Depends(get_invitation_service)]
CurrentContextDep = Annotated[CurrentUserContext, Depends(get_current_context)]
MemberManagerDep = Annotated[CurrentUserContext, Depends(require_permission(ORG_MEMBER_MANAGE))]

# 文案一律来自 `ErrorMessage` 受信目录，且 `detail` 直接引用枚举：
# 转存成模块常量时 G3 无法静态判定，会判「文案必须来自表」。


def _translate(error: InvitationError) -> HTTPException:
    """域异常 → HTTP 状态与人话文案，一次翻译一处。"""
    if isinstance(error, InvitationNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ErrorMessage.NOT_FOUND)
    if isinstance(error, CrossOrganizationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ErrorMessage.ORGANIZATION_FORBIDDEN
        )
    if isinstance(error, OrganizationMissingError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.INVITATION_NO_ORGANIZATION,
        )
    if isinstance(error, AlreadyInOrganizationError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.INVITATION_ALREADY_JOINED,
        )
    if isinstance(error, InvitationUnusableError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ErrorMessage.INVITATION_INVALID
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ErrorMessage.ORGANIZATION_ACTION_FAILED,
    )


@router.post("/organizations/{organization_id}/invitations", status_code=status.HTTP_201_CREATED)
def create_invitation(
    organization_id: str,
    payload: InvitationCreateRequest,
    request: Request,
    context: MemberManagerDep,
    service: InvitationServiceDep,
) -> dict[str, Any]:
    try:
        invitation = service.create(
            context,
            organization_id=organization_id,
            role=payload.role,
            max_uses=payload.max_uses,
            ttl_days=payload.ttl_days,
            ip=client_ip(request),
        )
    except InvitationError as error:
        raise _translate(error) from error
    return success_response(
        request,
        InvitationCreateData(invitation=InvitationRead.model_validate(invitation)).model_dump(
            mode="json"
        ),
    )


@router.get("/organizations/{organization_id}/invitations")
def list_invitations(
    organization_id: str,
    request: Request,
    context: MemberManagerDep,
    service: InvitationServiceDep,
) -> dict[str, Any]:
    try:
        rows = service.list_for_organization(context, organization_id, ip=client_ip(request))
    except InvitationError as error:
        raise _translate(error) from error
    return success_response(
        request,
        InvitationListData(items=[InvitationRead.model_validate(row) for row in rows]).model_dump(
            mode="json"
        ),
    )


@router.delete("/organizations/{organization_id}/invitations/{invitation_id}")
def revoke_invitation(
    organization_id: str,
    invitation_id: str,
    request: Request,
    context: MemberManagerDep,
    service: InvitationServiceDep,
) -> dict[str, Any]:
    try:
        invitation = service.revoke(
            context,
            organization_id=organization_id,
            invitation_id=invitation_id,
            ip=client_ip(request),
        )
    except InvitationError as error:
        raise _translate(error) from error
    return success_response(
        request,
        InvitationCreateData(invitation=InvitationRead.model_validate(invitation)).model_dump(
            mode="json"
        ),
    )


@router.post("/invitations/redeem")
def redeem_invitation(
    payload: InvitationRedeemRequest,
    request: Request,
    context: CurrentContextDep,
    service: InvitationServiceDep,
) -> dict[str, Any]:
    try:
        result = service.redeem(context, payload.code, ip=client_ip(request))
    except InvitationError as error:
        raise _translate(error) from error
    return success_response(
        request,
        InvitationRedeemData(
            organization=OrganizationBrief.model_validate(result.organization),
            role=UserRole(result.role),
        ).model_dump(mode="json"),
    )
