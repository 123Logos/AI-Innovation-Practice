from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from html import escape

from app.schemas.profile import EducationInput
from app.schemas.resume import ExperienceInput, ProfileInput, SkillInput


def _format_month(value: date) -> str:
    """简历里的日期只到月：`2021.09`。"""
    return f"{value.year}.{value.month:02d}"


class ResumeGenerator(ABC):
    @abstractmethod
    def generate(
        self,
        profile: ProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[ExperienceInput],
        title: str,
        educations: Sequence[EducationInput] = (),
    ) -> str:
        """Generate HTML from structured student information."""


class TemplateResumeGenerator(ResumeGenerator):
    def __init__(self, template: str | None = None) -> None:
        self._template = template or (
            "<html><head><meta charset=\"utf-8\"><title>{title}</title></head>"
            "<body><h1>{title}</h1><section><h2>基本信息</h2>"
            # 专业不再进基本信息栏：它属于具体某段学历，由「教育经历」区块呈现。
            "<p>{full_name}｜{contact}</p></section>"
            "{education_section}"
            "<section><h2>技能</h2><ul>{skills}</ul></section>"
            "<section><h2>经历</h2>{experiences}</section></body></html>"
        )

    def generate(
        self,
        profile: ProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[ExperienceInput],
        title: str,
        educations: Sequence[EducationInput] = (),
    ) -> str:
        # 只渲染技能名：熟练度已从产品中移除，`self_rating` 仅作为入参保留校验，
        # 不得写入 HTML，否则会作为脏数据落进 `resumes.content_html`。
        skill_items = "".join(f"<li>{escape(skill.name)}</li>" for skill in skills)
        experience_items = "".join(
            "<article>"
            f"<h3>{escape(experience.title)}</h3>"
            f"<p>{experience.start_date.isoformat()} - "
            f"{experience.end_date.isoformat() if experience.end_date else '至今'}</p>"
            f"<p>{escape(experience.description)}</p>"
            f"<p>{escape(experience.responsibility)}</p>"
            "</article>"
            for experience in experiences
        )
        # 教育经历按入学时间倒序（最高/最近学历在前）；没有教育经历时整块不渲染。
        education_items = "".join(
            "<article>"
            f"<h3>{escape(education.school)}</h3>"
            f"<p>{escape(education.major)} ｜ {escape(education.degree)} ｜ "
            f"{_format_month(education.edu_start)} — "
            f"{_format_month(education.edu_end) if education.edu_end else '至今'}</p>"
            "</article>"
            for education in sorted(educations, key=lambda item: item.edu_start, reverse=True)
        )
        education_section = (
            f"<section><h2>教育经历</h2>{education_items}</section>" if education_items else ""
        )
        return self._template.format(
            title=escape(title),
            full_name=escape(profile.full_name),
            contact=escape(profile.contact),
            education_section=education_section,
            skills=skill_items,
            experiences=experience_items,
        )


class DeepSeekResumeGenerator(ResumeGenerator):
    # PHASE-2: not wired
    def generate(
        self,
        profile: ProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[ExperienceInput],
        title: str,
        educations: Sequence[EducationInput] = (),
    ) -> str:
        raise NotImplementedError("PHASE-2: not wired")
