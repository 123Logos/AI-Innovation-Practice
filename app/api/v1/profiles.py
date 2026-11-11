"""当前登录用户的档案接口：多份简历 + 回收站（D4.1）。

**路由顺序有讲究**：`/trash`、`/me` 这类静态段必须声明在 `/{profile_id}` 之前，
否则 FastAPI 会把 `trash` / `me` 当成简历 id 塞进 `/{profile_id}`，
表现为「回收站打不开，404」。

兼容策略：`GET /profiles/me` 与 `PUT /profiles/me` 保留为「默认简历」的快捷方式。
D4.2 之前的前端只认这两个，不能因为后端引入多份简历就让它保存失败。

越权处理：不属于当前用户的 id 一律 404（不是 403）——403 会暴露「该 id 存在」。
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.error_messages import ErrorMessage
from app.core.responses import success_response
from app.schemas.auth import UserRead
from app.schemas.profile import ProfileCreateRequest, ProfileTitleUpdate, ProfileUpsertRequest
from app.services.dependencies import get_current_user, get_profile_service
from app.services.profile_service import (
    ProfileLimitExceededError,
    ProfileNotFoundError,
    ProfileService,
    ProfileStateError,
    ProfileTitleConflictError,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])

ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]


def _not_found() -> HTTPException:
    """越权与不存在共用同一个 404（T6 错误模型：404 的 message 由全局处理器统一）。"""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简历不存在。")


def _title_conflict() -> HTTPException:
    """重名固定 400 + 固定文案（D4.3）。"""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="该简历名称已存在",
    )


@router.get("")
def list_my_profiles(
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
    order_by: Literal["updated_at", "created_at"] = "updated_at",
    order: Literal["desc", "asc"] = "desc",
) -> dict[str, Any]:
    """档案库列表（不含回收站）。

    排序（D4.3）：`order_by` 二选一，非法值由 FastAPI 直接 422；
    默认「最近修改的在前」，这也是顶栏下拉的默认顺序。
    """
    profiles = service.list_profiles(
        current_user.id,
        order_by=order_by,
        descending=order != "asc",
    )
    return success_response(
        request,
        {"profiles": [profile.model_dump(mode="json") for profile in profiles]},
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_my_profile(
    payload: ProfileCreateRequest,
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """新建一份简历；`copy_from_id` 走服务端深拷贝。"""
    try:
        profile = service.create_profile(current_user.id, payload)
    except ProfileNotFoundError as error:
        raise _not_found() from error
    except ProfileTitleConflictError as error:
        raise _title_conflict() from error
    except ProfileLimitExceededError as error:
        # 不再 `detail=str(error)`（批次 F1c / G3）：异常文本不是文案来源，
        # 上屏的话由表里那一句负责。原始信息留在日志/链路里（`raise ... from error`）。
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.PROFILE_LIMIT,
        ) from error
    return success_response(request, {"profile": profile.model_dump(mode="json")})


@router.get("/trash")
def list_my_trash(
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """回收站：只含已软删除的简历，最近删除的在前。"""
    profiles = service.list_trash(current_user.id)
    return success_response(
        request,
        {"profiles": [profile.model_dump(mode="json") for profile in profiles]},
    )


@router.get("/me")
def read_my_profile(
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """兼容入口：默认简历。没有默认简历时返回 `{"profile": null}`（不是 404）。"""
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
    """兼容入口：整体覆盖默认简历；一份都没有时先建一份再写。"""
    profile = service.save_profile(current_user.id, payload)
    return success_response(request, {"profile": profile.model_dump(mode="json")})


@router.get("/{profile_id}")
def read_profile(
    profile_id: str,
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """单份简历详情（含技能 / 经历 / 教育）。"""
    try:
        profile = service.get_profile_by_id(current_user.id, profile_id)
    except ProfileNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"profile": profile.model_dump(mode="json")})


@router.put("/{profile_id}")
def update_profile(
    profile_id: str,
    payload: ProfileUpsertRequest,
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """整体覆盖指定简历的内容（PUT 语义：子表全量替换）。"""
    try:
        profile = service.update_profile(current_user.id, profile_id, payload)
    except ProfileNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"profile": profile.model_dump(mode="json")})


@router.put("/{profile_id}/default")
def set_default_profile(
    profile_id: str,
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """把这份设为默认简历（其余未删除简历自动取消默认）。"""
    try:
        profile = service.set_default(current_user.id, profile_id)
    except ProfileNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"profile": profile.model_dump(mode="json")})


@router.put("/{profile_id}/title")
def rename_profile(
    profile_id: str,
    payload: ProfileTitleUpdate,
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """只改标题（D4.3）。

    为什么单独开一个端点而不是并进 `PUT /profiles/{profile_id}`：
    后者是「整份内容全量替换」，改名若走那条路，前端得先 GET 全量再 PUT 回去，
    中间任何一次并发保存都会把内容覆盖掉，而且改个名字要传几十个字段。

    方法用 `PUT` 而不是 `PATCH`：语义上都是「替换标题」，但 `app/main.py` 的
    CORS 白名单只放行 GET/POST/PUT/DELETE，本轮不动那份配置。
    """
    try:
        profile = service.rename_profile(current_user.id, profile_id, payload.title)
    except ProfileNotFoundError as error:
        raise _not_found() from error
    except ProfileTitleConflictError as error:
        raise _title_conflict() from error
    return success_response(request, {"profile": profile.model_dump(mode="json")})


@router.delete("/{profile_id}")
def delete_profile(
    profile_id: str,
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """软删除：进回收站，30 天后由惰性清理物理删除。"""
    try:
        service.delete_profile(current_user.id, profile_id)
    except ProfileNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"profile_id": profile_id, "deleted": True})


@router.put("/{profile_id}/restore")
def restore_profile(
    profile_id: str,
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """从回收站恢复。"""
    try:
        profile = service.restore_profile(current_user.id, profile_id)
    except ProfileNotFoundError as error:
        raise _not_found() from error
    return success_response(request, {"profile": profile.model_dump(mode="json")})


@router.delete("/{profile_id}/permanent")
def purge_profile(
    profile_id: str,
    request: Request,
    service: ProfileServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """从回收站彻底删除（物理删除父行 + 全部子行）。"""
    try:
        service.purge_profile(current_user.id, profile_id)
    except ProfileNotFoundError as error:
        raise _not_found() from error
    except ProfileStateError as error:
        # 同上：异常族只用来选分支，句子一律取自 G3 那张表。
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorMessage.PROFILE_NOT_TRASHED,
        ) from error
    return success_response(request, {"profile_id": profile_id, "purged": True})
