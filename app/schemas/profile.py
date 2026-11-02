"""学生档案的请求/响应模型。

写入沿用 T5 已定的边界：3–8 条技能、1–3 条经历；
技能名在同一份档案内唯一，评分 1–5，经历结束日期不得早于开始日期。
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.resume import ExperienceInput, SkillInput


class SkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    self_rating: int


class ExperienceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    start_date: date
    end_date: date | None = None
    description: str
    responsibility: str


class ProfileUpsertRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    major: str = Field(min_length=1, max_length=100)
    grade: str = Field(min_length=1, max_length=50)
    contact: str = Field(min_length=1, max_length=255)
    skills: list[SkillInput] = Field(min_length=3, max_length=8)
    experiences: list[ExperienceInput] = Field(min_length=1, max_length=3)

    @field_validator("skills")
    @classmethod
    def validate_unique_skill_names(cls, skills: list[SkillInput]) -> list[SkillInput]:
        names = [skill.name for skill in skills]
        if len(names) != len(set(names)):
            raise ValueError("skill names must be unique")
        return skills


class ProfileRead(BaseModel):
    id: str
    user_id: str
    full_name: str
    major: str
    grade: str
    contact: str
    created_at: datetime
    updated_at: datetime
    skills: list[SkillRead]
    experiences: list[ExperienceRead]
