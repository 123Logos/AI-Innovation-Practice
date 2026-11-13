"""标准化测评的请求/响应模型（批次 B9 · `ADR-028`）。

**一条贯穿全文件的约束：`answer_key` 不出现在任何响应模型里**（`ADR-028` §2.1 第 2 条）。
它不是「忘了加」，而是刻意不给——`AssessmentQuestionRead` 只有 `id / skill_name / prompt / options`。
把标准答案放进响应，测评当场变开卷，而这件事不会有任何报错提醒你。

第二个约束：`status` 与 `source` 都用 `Literal` 而不是 `str`。库里那两列是
`VARCHAR + CHECK`（与 `jobs.status` / `resumes.origin` 同一做法），响应模型再钉一次，
是为了让「多了一个状态」这件事在**接口层**也红，而不是等前端去猜怎么渲染。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.core.datetime_display import format_shanghai_datetime

#: 会话状态（`ADR-028` §2.2）。
SessionStatus = Literal["in_progress", "submitted", "abandoned"]
#: 标签三态（`ADR-028` §2.4）。
TagStatus = Literal["verified", "pending", "improving"]
#: 摘要来源（`ADR-028` §2.5）。`fallback` 必须能被前端识别，不许混进 `ai`。
SummarySource = Literal["ai", "fallback"]


class AssessmentOptionRead(BaseModel):
    """一个选项。**只有 key 与 label**：判分靠服务端的 `answer_key`。"""

    key: str
    label: str


class AssessmentQuestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    skill_name: str
    prompt: str
    options: list[AssessmentOptionRead]


class CatalogSkillRead(BaseModel):
    """题库目录的一行：某个技能有几道题。**不含题干**——目录只回答「能测什么」。"""

    skill_key: str
    skill_name: str
    question_count: int


class AssessmentCatalogRead(BaseModel):
    bank_version: str
    total_questions: int
    skills: list[CatalogSkillRead]


class AssessmentSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    student_profile_id: str
    status: SessionStatus
    bank_version: str
    question_count: int
    correct_count: int | None = None
    score: int | None = None
    started_at: datetime
    submitted_at: datetime | None = None
    abandoned_at: datetime | None = None
    ai_summary: str | None = None
    ai_summary_source: SummarySource | None = None
    ai_summary_at: datetime | None = None

    @field_serializer("started_at", "submitted_at", "abandoned_at", "ai_summary_at")
    def serialize_shanghai(self, value: datetime | None) -> str | None:
        """与档案 / 简历接口同口径：naive UTC → 东八区 ISO 串。"""
        return format_shanghai_datetime(value) if value is not None else None


class AssessmentSessionCreate(BaseModel):
    """开一场测评。`profile_id` 省略时用默认简历（与生成简历同一条解析）。"""

    profile_id: str | None = Field(default=None, max_length=36)


class AnswerSubmit(BaseModel):
    """一条作答：题目 id + 选中的 option key。"""

    question_id: str = Field(min_length=1, max_length=36)
    chosen_key: str = Field(min_length=1, max_length=4)


class AnswerBatchUpsert(BaseModel):
    """批量覆盖（`PUT`，可多次调用，`in_progress` 才允许）。

    上限 50 与题库规模同量级：一次提交几十道题已经够用，放开只会让
    「一次请求写满整张表」变成可能。
    """

    answers: list[AnswerSubmit] = Field(default_factory=list, max_length=50)

    @field_validator("answers")
    @classmethod
    def reject_duplicate_questions(cls, value: list[AnswerSubmit]) -> list[AnswerSubmit]:
        """同一题在一批里出现两次 → 422。

        不「后一条覆盖前一条」：那种静默选择会让用户永远不知道哪一条生效了，
        而同一批里出现两条本身就是客户端的 bug，应该在写库之前红。
        """
        seen: set[str] = set()
        for item in value:
            if item.question_id in seen:
                raise ValueError("answers must not contain the same question twice")
            seen.add(item.question_id)
        return value


class AnswerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    question_id: str
    chosen_key: str
    #: `None` = 还没判分（`in_progress`）。
    is_correct: bool | None = None


class ProfileTagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tag: str
    status: TagStatus
    score: int
    source: Literal["assessment"]
    evidence_count: int
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_shanghai(self, value: datetime) -> str:
        return format_shanghai_datetime(value)


class AssessmentProfileRead(BaseModel):
    """我的画像：标签列表 + 最近一次已提交会话的概述。

    `latest_summary_source` 与 `latest_summary` 一起返回，前端据此决定标题写
    「AI 分析」还是「自动概述」——降级不许伪装成 AI（`ADR-028` §2.5）。
    """

    student_profile_id: str
    tags: list[ProfileTagRead]
    latest_session_id: str | None = None
    latest_summary: str | None = None
    latest_summary_source: SummarySource | None = None


class SummaryRead(BaseModel):
    session_id: str
    summary: str
    source: SummarySource
    #: 降级原因（`key_missing` / `key_invalid` / `timeout` / `upstream` / `bad_output`）；
    #: `source='ai'` 时为 `None`。**不落库**：它描述的是这一次请求发生了什么，
    #: 落库反而会让「上次降级的原因」被当成本次的原因展示。
    note: str | None = None
