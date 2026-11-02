"""简历相关的 Pydantic 模型。

`ProfileInput` / `SkillInput` / `ExperienceInput` 是**生成器契约**：
`app/integrations/resume_generator.py` 直接消费它们，因此保留在原位置，
不再作为 API 请求体的一部分（T7.2 起请求体只收 title，数据一律来自数据库）。
"""

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator


class ProfileInput(BaseModel):
    """生成器需要的基本信息。

    T7.5 起不再包含 `grade`（年级已从产品下架），避免调用方被一个
    「库里恒为空串」的字段卡在校验上。
    """

    full_name: str = Field(min_length=1, max_length=100)
    major: str = Field(min_length=1, max_length=100)
    contact: str = Field(min_length=1, max_length=255)


class SkillInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    self_rating: int = Field(ge=1, le=5)


class ExperienceInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date | None = None
    description: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_date_range(self) -> "ExperienceInput":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class ResumeGenerateRequest(BaseModel):
    """生成请求体只剩标题；档案、技能、经历由服务层按登录用户从库中读取。"""

    title: str = Field(min_length=1, max_length=200)


class ResumeRead(BaseModel):
    resume_id: str
    title: str
    content_html: str
    created_at: datetime
