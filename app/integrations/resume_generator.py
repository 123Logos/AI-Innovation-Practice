from abc import ABC, abstractmethod
from collections.abc import Sequence
from html import escape

from app.schemas.resume import ExperienceInput, ProfileInput, SkillInput


class ResumeGenerator(ABC):
    @abstractmethod
    def generate(
        self,
        profile: ProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[ExperienceInput],
        title: str,
    ) -> str:
        """Generate HTML from structured student information."""


class TemplateResumeGenerator(ResumeGenerator):
    def __init__(self, template: str | None = None) -> None:
        self._template = template or (
            "<html><head><meta charset=\"utf-8\"><title>{title}</title></head>"
            "<body><h1>{title}</h1><section><h2>基本信息</h2>"
            "<p>{full_name}｜{major}｜{grade}｜{contact}</p></section>"
            "<section><h2>技能</h2><ul>{skills}</ul></section>"
            "<section><h2>经历</h2>{experiences}</section></body></html>"
        )

    def generate(
        self,
        profile: ProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[ExperienceInput],
        title: str,
    ) -> str:
        skill_items = "".join(
            f"<li>{escape(skill.name)}（自评 {skill.self_rating}/5）</li>" for skill in skills
        )
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
        return self._template.format(
            title=escape(title),
            full_name=escape(profile.full_name),
            major=escape(profile.major),
            grade=escape(profile.grade),
            contact=escape(profile.contact),
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
    ) -> str:
        raise NotImplementedError("PHASE-2: not wired")
