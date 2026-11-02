from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from html import escape

from app.schemas.profile import EducationInput
from app.schemas.resume import ExperienceInput, ProfileInput, SkillInput


def _format_month(value: date) -> str:
    """教育经历的时间只到月：`2021.09`（学信网备案的粒度就是月）。"""
    return f"{value.year}.{value.month:02d}"


def _format_day(value: date) -> str:
    """项目经历的时间精确到日：`2024.03.15`（项目周期往往不足一个月）。"""
    return f"{value.year}.{value.month:02d}.{value.day:02d}"


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
    """模板生成器。

    产出的 HTML **必须与前端 `ResumePreview.vue` 的 DOM 结构、CSS 类名一一对应**，
    这样「实时预览」和「已生成简历」才是同一版式（所见即所得）：
    没有写死的小标题、没有单独的「基本信息」区块，姓名大字直接打头。

    对应关系（generated → preview）：
        header.paper-header > h1.resume-name + p.contact > span.contact-*
        section.paper-section > h2 + article.paper-experience
        h3.education-school / p.education-meta > span.edu-*  （教育）
        div.skill-list > span.skill-item                      （技能）
        div.experience-heading > h3.experience-title + span.date（经历）

    文档标题仍放在 `<head><title>`，不进正文——正文第一行是姓名。
    """

    def __init__(self, template: str | None = None) -> None:
        self._template = template or (
            '<html><head><meta charset="utf-8"><title>{title}</title></head><body>'
            '<header class="paper-header">'
            '<h1 class="resume-name">{full_name}</h1>'
            '<p class="contact">{contact_items}</p>'
            "</header>"
            "{education_section}"
            "{skill_section}"
            "{experience_section}"
            "</body></html>"
        )

    def generate(
        self,
        profile: ProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[ExperienceInput],
        title: str,
        educations: Sequence[EducationInput] = (),
    ) -> str:
        # 联系方式：手机号 + 可选邮箱，结构与预览页的 `.contact` 内部完全一致。
        contact_items = f'<span class="contact-phone">{escape(profile.contact)}</span>'
        if profile.email:
            contact_items += (
                '<span class="contact-separator">·</span>'
                f'<span class="contact-email">{escape(profile.email)}</span>'
            )

        # 只渲染技能名：熟练度已从产品中移除，`self_rating` 仅作为入参保留校验，
        # 不得写入 HTML，否则会作为脏数据落进 `resumes.content_html`。
        skill_items = "".join(
            f'<span class="skill-item">{escape(skill.name)}</span>' for skill in skills
        )
        skill_section = (
            '<section class="paper-section"><h2>技能</h2>'
            f'<div class="skill-list">{skill_items}</div></section>'
            if skill_items
            else ""
        )

        experience_items = "".join(
            '<article class="paper-experience">'
            '<div class="experience-heading">'
            f'<h3 class="experience-title">{escape(experience.title)}</h3>'
            f'<span class="date">{_format_day(experience.start_date)} — '
            f"{_format_day(experience.end_date) if experience.end_date else '至今'}</span>"
            "</div>"
            f'<p class="experience-description">{escape(experience.description)}</p>'
            f'<p class="experience-responsibility">{escape(experience.responsibility)}</p>'
            "</article>"
            for experience in experiences
        )
        experience_section = (
            '<section class="paper-section"><h2>项目经历</h2>'
            f"{experience_items}</section>"
            if experience_items
            else ""
        )

        # 教育经历按入学时间倒序（最高/最近学历在前）；没有教育经历时整块不渲染。
        education_items = "".join(
            '<article class="paper-experience">'
            f'<h3 class="education-school">{escape(education.school)}</h3>'
            '<p class="education-meta">'
            f'<span class="edu-major">{escape(education.major)}</span>'
            '<span class="edu-divider"> ｜ </span>'
            f'<span class="edu-degree">{escape(education.degree)}</span>'
            '<span class="edu-divider"> ｜ </span>'
            f'<span class="edu-start">{_format_month(education.edu_start)}</span>'
            '<span class="edu-divider"> — </span>'
            "<span class=\"edu-end\">"
            f"{_format_month(education.edu_end) if education.edu_end else '至今'}"
            "</span>"
            "</p>"
            "</article>"
            for education in sorted(educations, key=lambda item: item.edu_start, reverse=True)
        )
        education_section = (
            '<section class="paper-section"><h2>教育经历</h2>'
            f"{education_items}</section>"
            if education_items
            else ""
        )

        return self._template.format(
            title=escape(title),
            full_name=escape(profile.full_name),
            contact_items=contact_items,
            education_section=education_section,
            skill_section=skill_section,
            experience_section=experience_section,
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
