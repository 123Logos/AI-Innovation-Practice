"""标准化题库测评与画像接口（FR-04 / B9，`ADR-028`）。

八个端点：题库目录、开考、读会话、写答案、提交、放弃、AI 概述、我的画像。

两条贯穿全文件的口径：

1. **标准答案永不出现在响应里**：所有返回题目的地方都用 `AssessmentQuestionRead`
   （只有 `id / skill_name / prompt / options`）。这不是「忘了加字段」，
   是 `ADR-028` §2.1 第 2 条——把 `answer_key` 放进响应，测评当场变开卷，
   而这件事不会有任何报错提醒你。
2. **越权与不存在一律 404（不是 403）**：`AssessmentRepository.get_session` 在 SQL 层
   就按 `user_id` 过滤，别人的会话对当前用户与「不存在」同义，不该用 403 暴露
   「该 id 存在」（与岗位、简历、面试同一口径）。

AI 失败**不往外抛**：`AssessmentService.summarize()` 已就地降级成 `source='fallback'`，
所以本文件没有任何一处捕获 `EvidenceScore*`，也不会触发 `app/main.py` 的 AI 处理器——
用户在界面上看到的是「自动概述」而不是 500（`ADR-028` §2.5）。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.error_messages import ErrorMessage
from app.core.responses import success_response
from app.schemas.assessment import (
    AnswerBatchUpsert,
    AnswerRead,
    AssessmentCatalogRead,
    AssessmentProfileRead,
    AssessmentQuestionRead,
    AssessmentSessionCreate,
    AssessmentSessionRead,
    CatalogSkillRead,
    ProfileTagRead,
    SummaryRead,
)
from app.schemas.auth import UserRead
from app.services.assessment_service import (
    AssessmentAnswerInvalidError,
    AssessmentBankEmptyError,
    AssessmentNotFoundError,
    AssessmentProfileRequiredError,
    AssessmentService,
    AssessmentStateError,
    degrade_note,
)
from app.services.dependencies import get_assessment_service, get_current_user

router = APIRouter(prefix="/assessments", tags=["assessments"])

AssessmentServiceDep = Annotated[AssessmentService, Depends(get_assessment_service)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]


def _not_found() -> HTTPException:
    """越权与不存在共用同一个 404（T6 错误模型：404 的 message 由全局处理器统一）。"""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=ErrorMessage.ASSESSMENT_NOT_FOUND
    )


def _session_json(session: Any) -> dict[str, Any]:
    return AssessmentSessionRead.model_validate(session).model_dump(mode="json")


def _questions_json(questions: Any) -> list[dict[str, Any]]:
    return [
        AssessmentQuestionRead.model_validate(question).model_dump(mode="json")
        for question in questions
    ]


@router.get("")
def read_catalog(
    request: Request,
    service: AssessmentServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """题库目录：能测哪些技能、各几道题、题库版本。**不含题干与答案**。"""
    version, skills = service.catalog()
    catalog = AssessmentCatalogRead(
        bank_version=version,
        total_questions=sum(count for _, _, count in skills),
        skills=[
            CatalogSkillRead(skill_key=key, skill_name=name, question_count=count)
            for key, name, count in skills
        ],
    )
    return success_response(request, {"catalog": catalog.model_dump(mode="json")})


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def start_session(
    payload: AssessmentSessionCreate,
    request: Request,
    service: AssessmentServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """开一场测评（`201`），返回题目与本次会话。

    `profile_id` 省略时用默认简历；指定了不属于自己的 id → 404（不透露它是否存在）。
    """
    try:
        session, questions = service.start_session(current_user.id, payload.profile_id)
    except AssessmentNotFoundError as error:
        raise _not_found() from error
    except AssessmentProfileRequiredError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ErrorMessage.PROFILE_REQUIRED
        ) from error
    except AssessmentBankEmptyError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ErrorMessage.ASSESSMENT_BANK_EMPTY
        ) from error
    return success_response(
        request,
        {"session": _session_json(session), "questions": _questions_json(questions)},
    )


@router.get("/profile")
def read_profile(
    request: Request,
    service: AssessmentServiceDep,
    current_user: CurrentUserDep,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """我的画像：标签列表 + 最近一次已提交会话的概述与来源。

    `latest_summary_source` 与文案一起返回，前端据此决定标题写「AI 分析」还是
    「自动概述」——降级不许伪装成 AI（`ADR-028` §2.5）。
    """
    try:
        student_profile_id, tags, latest = service.profile(current_user.id, profile_id)
    except AssessmentNotFoundError as error:
        raise _not_found() from error
    except AssessmentProfileRequiredError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ErrorMessage.PROFILE_REQUIRED
        ) from error
    result = AssessmentProfileRead(
        student_profile_id=student_profile_id,
        tags=[ProfileTagRead.model_validate(tag) for tag in tags],
        latest_session_id=latest.id if latest is not None else None,
        latest_summary=latest.ai_summary if latest is not None else None,
        # 库上 `ck_assessment_sessions_summary_source` 只允许 `ai` / `fallback` 两个值，
        # 模型里再声明成 `Literal` 是为了让前端不必兜底第三个值；这里的窄化与
        # `create_summary()` 里 `SummaryRead(source=...)` 同一口径，依据是那条 CHECK。
        latest_summary_source=(
            latest.ai_summary_source  # type: ignore[arg-type]
            if latest is not None
            else None
        ),
    )
    return success_response(request, {"profile": result.model_dump(mode="json")})


@router.get("/sessions/{session_id}")
def read_session(
    session_id: str,
    request: Request,
    service: AssessmentServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """会话详情：题目 + 我的答案 + 状态。提交后 `is_correct` 才有值。"""
    try:
        session, questions, answers = service.get_session(current_user.id, session_id)
    except AssessmentNotFoundError as error:
        raise _not_found() from error
    return success_response(
        request,
        {
            "session": _session_json(session),
            "questions": _questions_json(questions),
            "answers": [
                AnswerRead.model_validate(answer).model_dump(mode="json") for answer in answers
            ],
        },
    )


@router.put("/sessions/{session_id}/answers")
def save_answers(
    session_id: str,
    payload: AnswerBatchUpsert,
    request: Request,
    service: AssessmentServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """批量覆盖答案（可多次调用，`in_progress` 才允许）。

    三处失败分开报，因为用户的下一步动作不同：会话不存在 → 返回列表；
    会话已提交 → 去看结果；选项不合法 → 重新选。
    """
    try:
        session, answers = service.save_answers(
            current_user.id,
            session_id,
            [(item.question_id, item.chosen_key) for item in payload.answers],
        )
    except AssessmentNotFoundError as error:
        raise _not_found() from error
    except AssessmentStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ErrorMessage.ASSESSMENT_STATE_CONFLICT
        ) from error
    except AssessmentAnswerInvalidError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ErrorMessage.ASSESSMENT_ANSWER_INVALID
        ) from error
    return success_response(
        request,
        {
            "session": _session_json(session),
            "answers": [
                AnswerRead.model_validate(answer).model_dump(mode="json") for answer in answers
            ],
        },
    )


@router.post("/sessions/{session_id}/submit")
def submit_session(
    session_id: str,
    request: Request,
    service: AssessmentServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """提交并判分：写标签 + 把已验证技能的来源升成 `assessment`（`ADR-028` §2.6）。

    重复提交是 **409 而不是重新判分**：提交会写画像标签，「悄悄成功」会让用户以为
    可以靠再点一次改分。
    """
    try:
        session, tags = service.submit(current_user.id, session_id)
    except AssessmentNotFoundError as error:
        raise _not_found() from error
    except AssessmentStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ErrorMessage.ASSESSMENT_STATE_CONFLICT
        ) from error
    return success_response(
        request,
        {
            "session": _session_json(session),
            "tags": [
                {
                    "tag": tag.skill_name,
                    "status": tag.status,
                    "score": tag.score,
                    "evidence_count": tag.question_count,
                }
                for tag in tags
            ],
        },
    )


@router.post("/sessions/{session_id}/abandon")
def abandon_session(
    session_id: str,
    request: Request,
    service: AssessmentServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """放弃这场测评：**不判分、不写标签**（`ADR-028` §2.2）。

    单独一个端点而不是「删会话」：会话是历史（重考要能排在它后面），
    `abandoned` 是它的一条终态，抹掉会让「我上次做了一半」这件事无法解释。
    """
    try:
        session = service.abandon(current_user.id, session_id)
    except AssessmentNotFoundError as error:
        raise _not_found() from error
    except AssessmentStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ErrorMessage.ASSESSMENT_STATE_CONFLICT
        ) from error
    return success_response(request, {"session": _session_json(session)})


@router.post("/sessions/{session_id}/summary")
def create_summary(
    session_id: str,
    request: Request,
    service: AssessmentServiceDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """生成画像概述（BYOK；AI 失败降级成 `source='fallback'`，仍返回 200）。

    为什么降级也给 200：画像是「标签 + 一段话」，AI 只负责措辞（`ADR-028` §1.1）。
    AI 挂了就说「这是自动整理的」，事实（标签、分数）一个字都不少——
    这与润色端点「没有模板兜底」是同一个判断的两面：那里丢了内容，这里只丢了措辞。
    """
    try:
        result = service.summarize(current_user.id, session_id)
    except AssessmentNotFoundError as error:
        raise _not_found() from error
    except AssessmentStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ErrorMessage.ASSESSMENT_STATE_CONFLICT
        ) from error
    payload = SummaryRead(
        session_id=session_id,
        summary=result.summary,
        source=result.source,  # type: ignore[arg-type]
        note=degrade_note(result.note),
    )
    return success_response(request, {"summary": payload.model_dump(mode="json")})
