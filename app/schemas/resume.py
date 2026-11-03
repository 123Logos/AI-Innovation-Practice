"""简历相关的 Pydantic 模型。

`ProfileInput` / `SkillInput` / `ExperienceInput` 是**生成器契约**：
`app/integrations/resume_generator.py` 直接消费它们，因此保留在原位置，
不再作为 API 请求体的一部分（T7.2 起请求体只收 title，数据一律来自数据库）。
"""

from datetime import date, datetime

from pydantic import BaseModel, Field, field_serializer, model_validator

from app.core.datetime_display import format_shanghai_datetime, shanghai_today


class ProfileInput(BaseModel):
    """生成器需要的基本信息。

    T7.5 起不再包含 `grade`（年级已从产品下架），避免调用方被一个
    「库里恒为空串」的字段卡在校验上。
    T7.6 起不再包含 `major`：专业由 `educations` 的每一段各自携带。
    用户反馈轮：新增 `email`（简历上展示的邮箱），可为空。
    """

    full_name: str = Field(min_length=1, max_length=100)
    contact: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=254)


class SkillInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    self_rating: int = Field(ge=1, le=5)


class ExperienceInput(BaseModel):
    """一段项目经历。

    「这段经历存在」的锚点是 `title`，必须非空；描述/职责允许留空——
    保存接口不再拦必填，只有「生成简历」时前端才做完整性校验。
    开始时间不得晚于今天（东八区），未来开始一律 422；
    结束时间允许未来（「预计结束」），只需不早于开始时间。
    """

    title: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date | None = None
    description: str = ""
    responsibility: str = ""

    @model_validator(mode="after")
    def validate_date_range(self) -> "ExperienceInput":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.start_date > shanghai_today():
            raise ValueError("start_date must not be in the future")
        return self


class ResumeGenerateRequest(BaseModel):
    """生成请求体只剩标题 + 可选的目标简历；档案内容由服务层按登录用户从库中读取。

    D4.1 起一个用户有多份简历，`profile_id` 指定用哪一份生成；
    不传则用默认简历 —— 旧前端（D4.2 之前）只发 `title`，必须继续可用。
    """

    title: str = Field(min_length=1, max_length=200)
    profile_id: str | None = Field(default=None, max_length=36)


class ResumeRead(BaseModel):
    resume_id: str
    title: str
    content_html: str
    created_at: datetime

    @field_serializer("created_at")
    def serialize_shanghai(self, value: datetime) -> str:
        """与档案接口同口径：naive UTC → 东八区 ISO 串。"""
        return format_shanghai_datetime(value)
