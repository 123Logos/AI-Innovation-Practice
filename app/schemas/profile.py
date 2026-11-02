"""学生档案的请求/响应模型。

写入沿用 T5 已定的边界：3–8 条技能、1–3 条经历；
技能名在同一份档案内唯一，评分 1–5，经历结束日期不得早于开始日期。

T7.5 起教育经历是一对多子表（`educations`）：
学校 / 专业 / 学历 / 起止时间，字段对齐学信网备案表。
`grade`（年级）在库中保留，但已从产品表单下架，请求体里可选、后端忽略。
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.resume import ExperienceInput, SkillInput

#: 学历枚举，与 `ck_educations_degree` 约束保持一致。
Degree = Literal["专科", "本科", "硕士", "博士"]


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


class EducationInput(BaseModel):
    """一段教育经历；`edu_end` 为 None 表示「至今」（在读）。"""

    school: str = Field(min_length=1, max_length=200)
    major: str = Field(min_length=1, max_length=100)
    degree: Degree
    edu_start: date
    edu_end: date | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> "EducationInput":
        if self.edu_end is not None and self.edu_end < self.edu_start:
            raise ValueError("edu_end must be on or after edu_start")
        return self


class EducationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    school: str
    major: str
    degree: str
    edu_start: date
    edu_end: date | None = None


class ProfileUpsertRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    major: str = Field(min_length=1, max_length=100)
    #: 年级已下架：允许缺省，服务层不再把它写进库（列保留）。
    grade: str | None = Field(default=None, max_length=50)
    contact: str = Field(min_length=1, max_length=255)
    skills: list[SkillInput] = Field(min_length=3, max_length=8)
    experiences: list[ExperienceInput] = Field(min_length=1, max_length=3)
    #: 教育经历 0–5 段；为空时不渲染教育区块，缺省值兼容尚未升级的旧客户端。
    educations: list[EducationInput] = Field(default_factory=list, max_length=5)

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
    educations: list[EducationRead] = Field(default_factory=list)
