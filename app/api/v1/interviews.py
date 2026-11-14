"""文本模拟面试接口（FR-07 / B11a，ADR-022）。

五个端点：建会话、列会话、读会话详情（带 turns）、发一条用户回答并拿 AI 回复、结束会话。

越权与不可见一律 **404（不是 403）**：`get_owned` 在 SQL 层就按 `user_id` 过滤，
别人的会话对当前用户而言与「不存在」同义，不该用 403 暴露「该 id 存在」（与
`job_repository` / `profiles` 同一口径）。

AI 失败**不往外抛**：`InterviewService` 已就地翻译成 `ai_degrade_reason` 并继续面试，
所以本文件没有任何一处 `except EvidenceScore*`，也不会触发 `app/main.py` 的 AI 处理器——
用户在 UI 上看到的是显式降级标记而不是 500（ADR-022 (b)）。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.error_messages import ErrorMessage
from app.core.responses import success_response
from app.schemas.auth import UserRead
from app.schemas.interview import (
    InterviewListResult,
    InterviewMessageCreate,
    InterviewMessageResult,
    InterviewSessionCreate,
    InterviewSessionDetailRead,
    InterviewSessionRead,
    InterviewTurnRead,
)
from app.services.dependencies import get_current_user, get_interview_service
from app.services.interview_service import (
    InterviewNotFoundError,
    InterviewService,
    InterviewSessionEndedError,
)

router = APIRouter(prefix="/interviews", tags=["interviews"])

InterviewServiceDep = Annotated[InterviewService, Depends(get_interview_service)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]


def _not_found() -> HTTPException:
    """越权与不存在共用同一个 404（T6 错误模型：404 的 message 由全局处理器统一）。"""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=ErrorMessage.INTERVIEW_NOT_FOUND
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_interview(
    payload: InterviewSessionCreate,
    request: Request,
    service: InterviewServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """创建一场模拟面试，并立即拿到面试官的开场问题。

    `job_id` 若提供但不可见（不存在/未发布/回收站）会 404；AI 在开场失败时也不中断，
    开场问题走本地降级模板并带 `ai_degrade_reason`（可见降级）。
    """
    try:
        session, opening, degrade_reason = service.create_session(
            current_user.id, payload.job_id, payload.target_role
        )
    except InterviewNotFoundError as error:
        raise _not_found() from error
    return success_response(
        request,
        {
            "session": InterviewSessionRead.model_validate(session).model_dump(mode="json"),
            "opening_turn": InterviewTurnRead.model_validate(opening).model_dump(mode="json"),
            "ai_degrade_reason": degrade_reason,
        },
    )


@router.get("")
def list_interviews(
    request: Request,
    service: InterviewServiceDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """当前用户自己的面试会话列表（按最近更新倒序，带分页）。"""
    sessions, total = service.list_sessions(current_user.id, page, page_size)
    result = InterviewListResult(
        sessions=[InterviewSessionRead.model_validate(s) for s in sessions],
        page=page,
        page_size=page_size,
        total=total,
    )
    return success_response(request, result.model_dump(mode="json"))


@router.get("/{session_id}")
def read_interview(
    session_id: str,
    request: Request,
    service: InterviewServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """会话详情：元信息 + 全部轮次（按 sequence 顺序回放）。非所有者或不存在 → 404。"""
    try:
        session, turns = service.get_session(current_user.id, session_id)
    except InterviewNotFoundError as error:
        raise _not_found() from error
    detail = InterviewSessionDetailRead(
        session=InterviewSessionRead.model_validate(session),
        turns=[InterviewTurnRead.model_validate(t) for t in turns],
    )
    return success_response(request, detail.model_dump(mode="json"))


@router.post("/{session_id}/messages")
def send_interview_message(
    session_id: str,
    payload: InterviewMessageCreate,
    request: Request,
    service: InterviewServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """发送一条候选人作答，并拿到面试官的下一段（真实或降级）。

    返回候选人的轮次、面试官的新轮次、以及本轮降级原因（`ai_degrade_reason` 非空即代表
    AI 不可用，前端必须给出可见提示，绝不能让用户误以为「AI 说了这句」）。
    会话不存在/非所有者 → 404；已结束 → 400。
    """
    try:
        candidate_turn, interviewer_turn, degrade_reason = service.send_message(
            current_user.id, session_id, payload.content, payload.input_mode.value
        )
    except InterviewNotFoundError as error:
        raise _not_found() from error
    except InterviewSessionEndedError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ErrorMessage.INTERVIEW_ENDED
        ) from error
    result = InterviewMessageResult(
        candidate_turn=InterviewTurnRead.model_validate(candidate_turn),
        interviewer_turn=InterviewTurnRead.model_validate(interviewer_turn),
        ai_degrade_reason=degrade_reason,
    )
    return success_response(request, result.model_dump(mode="json"))


@router.post("/{session_id}/end")
def end_interview(
    session_id: str,
    request: Request,
    service: InterviewServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """结束会话。非所有者或不存在 → 404。"""
    try:
        session = service.end_session(current_user.id, session_id)
    except InterviewNotFoundError as error:
        raise _not_found() from error
    return success_response(
        request, {"session": InterviewSessionRead.model_validate(session).model_dump(mode="json")}
    )
