"""当前登录用户的档案接口：读取与整体保存。

`GET /api/v1/profiles/me` 在用户未填写档案时返回 200 + `{"profile": null}`，
让前端能区分「还没填」和「请求出错」，不把空档案当成错误处理。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.core.responses import success_response
from app.schemas.auth import UserRead
from app.schemas.profile import ProfileUpsertRequest
from app.services.dependencies import get_current_user, get_profile_service
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/profiles", tags=["profiles"])

ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]


@router.get("/me")
def read_my_profile(
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    profile = service.get_profile(current_user.id)
    return success_response(
        request,
        {"profile": None if profile is None else profile.model_dump(mode="json")},
    )


@router.put("/me")
def upsert_my_profile(
    payload: ProfileUpsertRequest,
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    profile = service.save_profile(current_user.id, payload)
    return success_response(request, {"profile": profile.model_dump(mode="json")})
