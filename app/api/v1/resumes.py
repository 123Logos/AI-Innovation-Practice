"""简历路由：生成（必须登录，档案从数据库读取）。

请求体只收 `title` 与可选的 `profile_id`；档案、技能、经历一律按登录用户从
`student_profiles` / `skills` / `experiences` 读取，客户端的任何档案字段都不再被采信。
`profile_id` 省略时用默认简历（D4.1 之前的前端只发 `title`，必须继续可用）。

依赖顺序刻意保持 `service` 在 `current_user` 之前：这样 `get_resume_service`
的故障（500）不会被鉴权失败（401）掩盖，保持 T6 统一错误模型中 500 用例的语义。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.responses import success_response
from app.schemas.auth import UserRead
from app.schemas.resume import ResumeGenerateRequest
from app.services.dependencies import get_current_user, get_resume_service
from app.services.profile_service import ProfileNotFoundError
from app.services.resume_service import ProfileIncompleteError, ResumeService

router = APIRouter(prefix="/resumes", tags=["resumes"])

ResumeServiceDep = Annotated[ResumeService, Depends(get_resume_service)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]


@router.post("/generate")
def generate_resume(
    payload: ResumeGenerateRequest,
    request: Request,
    service: ResumeServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    try:
        resume = service.generate(current_user.id, payload)
    except ProfileNotFoundError as error:
        # 指定的简历不存在 / 不属于自己 / 已在回收站：统一 404，不区分原因。
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="简历不存在。",
        ) from error
    except ProfileIncompleteError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先完善个人档案（基本信息、技能、经历）后再生成简历。",
        ) from error
    return success_response(request, resume.model_dump(mode="json"))
