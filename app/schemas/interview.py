"""文本模拟面试的 Pydantic 模型（FR-07 / B11a，ADR-022）。

读模型用 `ConfigDict(from_attributes=True)` 直接从 ORM 行构造；写模型（请求体）单独定义，
不让客户端触达 `user_id` / `status` / `sequence` 这类服务端派生的列。

`content` 的长度上限是应用层第一道闸：超长请求直接 422，不进库、不进 AI 调用。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.interview_modes import TurnInputMode

#: 候选人单次作答的应用层长度上限（字符）。足够写一段项目经历，又不至于撑爆单轮上下文。
ANSWER_MAX_LEN = 5000
#: 创建会话时填写的目标岗位名长度上限，与迁移列宽 `target_role VARCHAR(200)` 对齐。
TARGET_ROLE_MAX_LEN = 200


class InterviewSessionCreate(BaseModel):
    """创建会话的请求体：可选绑定岗位、可选填写目标岗位名。"""

    job_id: str | None = Field(
        default=None, description="可选绑定的目标岗位 id；不填则纯通用面试。"
    )
    target_role: str | None = Field(
        default=None, max_length=TARGET_ROLE_MAX_LEN, description="用户填写的目标岗位名，可选。"
    )


class InterviewMessageCreate(BaseModel):
    """发送一条候选人作答的请求体。"""

    content: str = Field(min_length=1, max_length=ANSWER_MAX_LEN, description="候选人的作答文本。")
    #: 这一轮是说的还是打的（B11b / `ADR-030`）。**音频不在请求里**：语音路径先在浏览器
    #: 直连 ASR 转写成文本，再把文本发到这里；默认 `text` 让 B11b 之前的客户端零改动。
    input_mode: TurnInputMode = Field(
        default=TurnInputMode.TEXT,
        description="作答方式：text（打字）或 voice（语音转写）。音频本身不上传。",
    )


class InterviewTurnRead(BaseModel):
    """一轮问答（按 `sequence` 顺序回放）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    sequence: int
    speaker: str
    content: str
    #: `text` 或 `voice`。前端据此给候选人那一轮标一个「语音作答」的角标。
    input_mode: str = TurnInputMode.TEXT.value
    #: AI 失败时这一轮内容由本地降级模板产出，此处非空即代表「此轮非真 AI」（可见降级）。
    ai_degrade_reason: str | None = None
    created_at: datetime


class InterviewSessionRead(BaseModel):
    """会话元信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    job_id: str | None = None
    status: str
    target_role: str | None = None
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None


class InterviewSessionDetailRead(BaseModel):
    """会话详情：元信息 + 全部轮次（带 turns）。"""

    session: InterviewSessionRead
    turns: list[InterviewTurnRead]


class InterviewMessageResult(BaseModel):
    """发送作答后的返回：候选人的轮次、面试官的新一轮、以及本轮是否降级。"""

    candidate_turn: InterviewTurnRead
    interviewer_turn: InterviewTurnRead
    #: 本轮面试官内容是否由本地降级模板产出（非空即代表 AI 不可用，需在前端可见提示）。
    ai_degrade_reason: str | None = None


class InterviewListResult(BaseModel):
    """会话列表（带分页 meta）。"""

    sessions: list[InterviewSessionRead]
    page: int
    page_size: int
    total: int
