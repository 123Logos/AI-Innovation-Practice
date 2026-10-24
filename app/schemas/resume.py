from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator


class ProfileInput(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    major: str = Field(min_length=1, max_length=100)
    grade: str = Field(min_length=1, max_length=50)
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
    title: str = Field(min_length=1, max_length=200)
    profile: ProfileInput
    skills: list[SkillInput] = Field(min_length=3, max_length=8)
    experiences: list[ExperienceInput] = Field(min_length=1, max_length=3)

    @field_validator("skills")
    @classmethod
    def validate_unique_skill_names(cls, skills: list[SkillInput]) -> list[SkillInput]:
        names = [skill.name for skill in skills]
        if len(names) != len(set(names)):
            raise ValueError("skill names must be unique")
        return skills
