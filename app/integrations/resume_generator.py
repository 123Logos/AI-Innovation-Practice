from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from html import escape

from app.core.document_style import DEFAULT_SECTIONS, DEFAULT_THEME, Section, ThemeId, get_theme
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
        theme: ThemeId = DEFAULT_THEME,
        layout: Sequence[Section] | None = None,
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

    **配色（D5.2）**：正文外面包一层 `<div class="paper-doc" style="--paper-accent:
    …; --paper-rule: …">`，把主题写成**内联 CSS 变量**。为什么不用 `<style>` 也不写
    在 `body` 上：前端只取 `body.innerHTML` 塞进 `.paper`，写在 `head` 的样式会丢；
    写在 `body` 上则会被前端那层 `.paper` 覆盖。包一层 div 的好处是——导出 / 分享
    这份 HTML 时它自带配色，不依赖页面样式表；同时前端「实时预览」用同一对变量名，
    于是「改主题 → 预览立刻变」和「生成物自带主题」是同一套机制。

    **顺序与标题（D5.3）**：模板里不再有 `{education_section}` 这类「位置即顺序」的
    占位符，只剩一个 `{sections}`。三个模块先各自渲染成片段放进 `blocks`，再按
    `layout` 给出的顺序拼接（ADR-007 §7.3）。这样「改顺序」只是换一个列表，
    不需要同时改三处硬编码（模板 / 生成器 / 预览）。

    三个必须守住的细节：
    1. **「有没有内容」在生成片段时判断**，不在拼接时判断——否则用户删光技能后，
       纸面上会留一个空的「技能」标题；
    2. **缺模块自动跳过**：`if section.id in blocks` 就是降级。用户把「教育」放在
       第一位但没填教育，第一位就直接是「技能」；
    3. **标题来自 layout 且必须 `escape()`**：标题是用户输入，可以包含 `<script>`。
    """

    def __init__(self, template: str | None = None) -> None:
        self._template = template or (
            '<html><head><meta charset="utf-8"><title>{title}</title></head><body>'
            '<div class="paper-doc"'
            ' style="--paper-accent: {accent}; --paper-rule: {rule};">'
            '<header class="paper-header">'
            '<h1 class="resume-name">{full_name}</h1>'
            '<p class="contact">{contact_items}</p>'
            "</header>"
            "{sections}"
            "</div>"
            "</body></html>"
        )

    def generate(
        self,
        profile: ProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[ExperienceInput],
        title: str,
        educations: Sequence[EducationInput] = (),
        theme: ThemeId = DEFAULT_THEME,
        layout: Sequence[Section] | None = None,
    ) -> str:
        # 联系方式：手机号 + 可选邮箱，结构与预览页的 `.contact` 内部完全一致。
        contact_items = f'<span class="contact-phone">{escape(profile.contact)}</span>'
        if profile.email:
            contact_items += (
                '<span class="contact-separator">·</span>'
                f'<span class="contact-email">{escape(profile.email)}</span>'
            )

        # 只渲染技能名：熟练度已从产品中移除（`self_rating` 于 2026-11-07 随迁移
        # 20261107_0013 从库里删掉），写进 HTML 只会成为 `content_html` 里的脏数据。
        skill_items = "".join(
            f'<span class="skill-item">{escape(skill.name)}</span>' for skill in skills
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
            '<span class="edu-end">'
            f"{_format_month(education.edu_end) if education.edu_end else '至今'}"
            "</span>"
            "</p>"
            "</article>"
            for education in sorted(educations, key=lambda item: item.edu_start, reverse=True)
        )

        # 片段缓存：key 是模块 id，value 是**已经渲染好**的「栏目标题以下的正文」。
        # 有内容才进 dict —— 空模块从这里就消失了，拼接阶段不必再判一次。
        # 技能要多包一层 `.skill-list`（`ResumePreview.vue` 的 flex 换行靠它），
        # 教育 / 项目的主体直接是 `<article>`，由外层 `<section>` 承载。
        blocks: dict[str, str] = {}
        if education_items:
            blocks["educations"] = education_items
        if skill_items:
            blocks["skills"] = f'<div class="skill-list">{skill_items}</div>'
        if experience_items:
            blocks["experiences"] = experience_items

        # 按 layout 的顺序拼接。`layout=None` 时用默认顺序，
        # 所以老调用点（不传 layout）的行为与 D5.2 完全一致。
        # 标题取自 layout（用户可自定义），`escape()` 防 XSS 不能省。
        sections_html = "".join(
            '<section class="paper-section"><h2>'
            f"{escape(section.title)}</h2>{blocks[section.id]}</section>"
            for section in (layout if layout is not None else DEFAULT_SECTIONS)
            if section.id in blocks
        )

        palette = get_theme(theme)
        return self._template.format(
            title=escape(title),
            full_name=escape(profile.full_name),
            contact_items=contact_items,
            sections=sections_html,
            accent=palette.accent,
            rule=palette.rule,
        )
