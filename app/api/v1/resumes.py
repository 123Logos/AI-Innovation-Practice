"""简历路由：生成（纯模板）与润色（AI，纯计算）。

两条路径的分工见 ADR-010：

- `POST /resumes/generate`：从数据库取档案 → 套模板 → 落库。**不调用 AI，因此不需要
  `X-DeepSeek-Key`**，AI 挂了也不影响它出简历。
- `POST /resumes/polish`：把前端草稿态的文案交给 AI 改写，**只返回结果，不落库、
  不碰任何表**。请求体就是前端 Pinia 里的草稿，允许尚未保存。

`/generate` 的请求体只收 `title` 与可选的 `profile_id`；档案、技能、经历一律按登录
用户从 `student_profiles` / `skills` / `experiences` 读取，客户端的任何档案字段都不再
被采信（T7.2 的边界）。`profile_id` 省略时用默认简历（D4.1 之前的前端只发 `title`，
必须继续可用）。
`/polish` 反过来：它不参与生成，输出的文案要用户确认后才写进表单，所以采信的是
请求体里的草稿，但**不把它当事实持久化**——服务端不写任何一行。

依赖顺序刻意保持 `service` 在 `current_user` 之前：这样 `get_resume_service`
的故障（500）不会被鉴权失败（401）掩盖，保持 T6 统一错误模型中 500 用例的语义。

`/polish` 挂 IP 限流（10 次/分钟）：它烧的是**用户自己的** DeepSeek 额度，
前端 bug 造成的循环调用或脚本刷量都会变成真金白银的损失，所以在服务端设闸；
`/generate` 不挂——它是本地计算，成本与本项目的其它写接口同级。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.rate_limit import POLISH_RATE_LIMIT, limiter
from app.core.responses import success_response
from app.schemas.auth import UserRead
from app.schemas.resume import ResumeGenerateRequest, ResumePolishRequest
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


@router.post("/polish")
@limiter.limit(POLISH_RATE_LIMIT)
def polish_resume(
    payload: ResumePolishRequest,
    request: Request,
    service: ResumeServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """AI 润色：纯计算端点。失败时抛出的三个 AI 异常由 `app/main.py` 的专用处理器
    转成 400/401/502 统一错误模型；本路由不 catch，因为没有「模板兜底」这条路可走
    ——用户看到错误后重试或改 Key，而不是拿到一份悄悄被替换过的文案。

    `current_user` 的依赖不能省：本端点消耗用户自己的 AI 额度，必须登录，
    且与 `/generate` 一样受鉴权中间件约束（返回值里也不含任何用户身份信息）。
    """
    copy = service.polish(payload)
    return success_response(request, copy.model_dump(mode="json"))
