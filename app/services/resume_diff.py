"""简历版本的**结构级**差异（批次 B8 · `ADR-026` §2.4）。

输入是两段 `content_html` 快照（`ADR-015` §2.3 决定的「整份 HTML 一版」），
输出是「区块 → 条目 → 字段」三级差异。三条设计约束：

1. **不做字符级 diff，也不让 AI 说差异**。前者把 `class="skill-item"` 与实体转义
   抖到用户眼前，后者不可复现且可能编出不存在的变化（ADR-026 §1.1 的表）。
2. **区块按结构识别，不按标题识别**。用户把「技能」改名成「技术栈」时，结果应当
   是一条 `title_before/title_after`，而不是「删掉一个区块 + 新增一个区块」。
   识别键是区块内出现的**条目类名**（`skill-item` / `experience-title` / …），
   这套类名是 `TemplateResumeGenerator` 与 `ResumePreview.vue` 之间的既有契约。
3. **不认识的快照不许假装能比**。解析不出结构时返回「不可比」，
   绝不返回一份「什么都没变」的空 diff —— 后者会让用户以为两版一样，
   而那正是最坏的一种错误（与 `ADR-015` §2.3 对「猜链头」的态度一致）。

本模块是纯函数：不碰数据库、不认识 ORM、不抛业务异常。服务层负责取两行、
把结果装进响应模型。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

#: 区块开标签。`TemplateResumeGenerator` 的固定产物（README/ADR-007 §7.3 的顺序拼接）。
_SECTION_OPEN = '<section class="paper-section">'

#: `<article class="paper-experience">…</article>`：项目 / 教育 / 证书 / 作品四种条目共用这个外壳。
_ARTICLE = re.compile(r'<article class="paper-experience">(.*?)</article>', re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_BODY = re.compile(r"<body[^>]*>(.*?)</body>", re.DOTALL | re.IGNORECASE)
_HEAD = re.compile(r"<head[^>]*>.*?</head>", re.DOTALL | re.IGNORECASE)

#: 条目类名 → 区块 key。**顺序无关**，但每个 key 只能出现一次。
#: 技能是唯一「条目不是 article」的区块（`<span class="skill-item">`），单独处理。
SECTION_KEY_BY_ITEM_CLASS: dict[str, str] = {
    "education-school": "educations",
    "skill-item": "skills",
    "experience-title": "experiences",
    "certificate-name": "certificates",
    "portfolio-label": "portfolio",
}

#: 区块 key → 条目锚点类名（技能没有锚点类名：技能名本身就是锚点）。
ANCHOR_CLASS_BY_SECTION: dict[str, str] = {
    "educations": "education-school",
    "experiences": "experience-title",
    "certificates": "certificate-name",
    "portfolio": "portfolio-label",
}

#: 区块 key → 「认得的字段」。只列这几格：段落里的自由文本（描述/职责）就是值本身，
#: 而 `pending-field` 的「待补充：X」是**纸面上可见的内容**，照常参与比较。
FIELD_CLASS_BY_SECTION: dict[str, dict[str, str]] = {
    "experiences": {
        "date": "时间",
        "experience-description": "项目描述",
        "experience-responsibility": "个人职责",
    },
    "educations": {
        "edu-major": "专业",
        "edu-degree": "学历",
        "edu-start": "入学",
        "edu-end": "毕业",
    },
    "certificates": {
        "certificate-issuer": "颁发机构",
        "certificate-date": "颁发日期",
    },
    "portfolio": {
        "portfolio-url": "链接",
    },
}

#: 区块的中文兜底名：快照里连 `<h2>` 都没有时用它，保证 `label` 永不为空串。
SECTION_LABEL_FALLBACK: dict[str, str] = {
    "educations": "教育经历",
    "skills": "技能",
    "experiences": "项目经历",
    "certificates": "证书",
    "portfolio": "作品链接",
}

#: 区块在界面上的固定顺序（与 `DEFAULT_SECTIONS` 同序）。差异列表按它排序，
#: 不按「哪个版本在前」——两版排版顺序不同时，差异列表的顺序不该跟着跳。
SECTION_ORDER: tuple[str, ...] = (
    "educations",
    "skills",
    "experiences",
    "certificates",
    "portfolio",
)


@dataclass
class ParsedItem:
    """一个条目：锚点文本 + 认得的字段（字段名用中文，直接上屏）。"""

    label: str
    fields: dict[str, str] = field(default_factory=dict)

    def plain_text(self) -> str:
        """整条的一段文字，用于「新增 / 删除」时的 before/after 展示。"""
        if not self.fields:
            return self.label
        parts = [self.label]
        parts.extend(f"{name}：{value}" for name, value in self.fields.items() if value)
        return " ｜ ".join(parts)


@dataclass
class ParsedSection:
    key: str
    title: str
    items: list[ParsedItem] = field(default_factory=list)


@dataclass
class ParsedDocument:
    sections: dict[str, ParsedSection]
    #: 区块在**这一版**里的实际顺序（差异据此判 `section_order_changed`）。
    order: list[str]

    def item_labels(self, key: str) -> list[str]:
        section = self.sections.get(key)
        return [item.label for item in section.items] if section else []


@dataclass
class FieldDiff:
    field: str
    before: str
    after: str


@dataclass
class ItemDiff:
    kind: str  # added / removed / changed
    label: str
    before: str | None
    after: str | None
    fields: list[FieldDiff] = field(default_factory=list)


@dataclass
class SectionDiff:
    key: str
    label: str
    change: str  # added / removed / changed / unchanged
    title_before: str | None
    title_after: str | None
    order_changed: bool
    items: list[ItemDiff] = field(default_factory=list)


@dataclass
class DocumentDiff:
    comparable: bool
    note: str | None
    sections: list[SectionDiff]
    added: int
    removed: int
    changed: int
    section_order_changed: bool
    item_order_changed: bool


def _text(fragment: str) -> str:
    """去标签 + 解实体 + 折叠空白。"""
    without_tags = _TAG.sub(" ", fragment)
    return " ".join(unescape(without_tags).split())


def _element_text(fragment: str, css_class: str) -> str:
    """取**第一个**带这个类名的元素的内文；没有则空串。

    用「类名出现在 `class` 属性里、后面紧跟结束引号或空白」的边界判定，
    而不是裸 `in`：`.skill-item` 的裸子串也会命中 `skill-item-x`，
    那类实现写坏了也照样「看起来对」（G1 的同类教训，见 `scripts/check_ui_copy.py`）。
    """
    pattern = re.compile(
        r'<(\w+)[^>]*\bclass="(?:[^"]*\s)?'
        + re.escape(css_class)
        + r'(?:\s[^"]*)?"[^>]*>(.*?)</\1>',
        re.DOTALL,
    )
    match = pattern.search(fragment)
    return _text(match.group(2)) if match else ""


def _all_elements_text(fragment: str, css_class: str) -> list[str]:
    pattern = re.compile(
        r'<(\w+)[^>]*\bclass="(?:[^"]*\s)?'
        + re.escape(css_class)
        + r'(?:\s[^"]*)?"[^>]*>(.*?)</\1>',
        re.DOTALL,
    )
    return [_text(match.group(2)) for match in pattern.finditer(fragment)]


def parse_snapshot(content_html: str) -> ParsedDocument | None:
    """把一份生成物拆成区块与条目；结构不认识时返回 `None`（**不回落到空文档**）。"""
    body_match = _BODY.search(content_html)
    body = body_match.group(1) if body_match else _HEAD.sub("", content_html)
    if _SECTION_OPEN not in body:
        return None

    chunks = body.split(_SECTION_OPEN)[1:]
    sections: dict[str, ParsedSection] = {}
    order: list[str] = []
    for chunk in chunks:
        closing = chunk.find("</section>")
        inner = chunk[:closing] if closing >= 0 else chunk
        heading_match = re.search(r"<h2[^>]*>(.*?)</h2>", inner, re.DOTALL)
        title = _text(heading_match.group(1)) if heading_match else ""

        key = _section_key(inner)
        if key is None:
            # 认不出条目类名的区块（空区块 / 老模板）：跳过。
            # 一个文档里**全部**区块都认不出时，整体判不可比（见 parse 后的检查）。
            continue
        if key in sections:  # 同一区块出现两次：以后一次为准，避免 items 被覆盖成一半
            sections[key] = _parse_section(key, title, inner)
        else:
            sections[key] = _parse_section(key, title, inner)
            order.append(key)

    if not sections:
        return None
    return ParsedDocument(sections=sections, order=order)


def _section_key(inner: str) -> str | None:
    """按「区块里出现了哪些条目类名」定 key（`ADR-026` §2.4 规则 1）。

    技能必须最先判：它的条目是 `span`，与其余四种的 `article` 外壳不同，
    而 `<div class="skill-list">` 里不会有别的类名混进来。
    """
    for css_class, key in SECTION_KEY_BY_ITEM_CLASS.items():
        if _element_text(inner, css_class) or _all_elements_text(inner, css_class):
            return key
    return None


def _parse_section(key: str, title: str, inner: str) -> ParsedSection:
    if key == "skills":
        skill_items = [ParsedItem(label=name) for name in _all_elements_text(inner, "skill-item")]
        return ParsedSection(key=key, title=title, items=skill_items)

    anchor_class = ANCHOR_CLASS_BY_SECTION[key]
    field_classes = FIELD_CLASS_BY_SECTION.get(key, {})
    items: list[ParsedItem] = []
    for article in _ARTICLE.findall(inner):
        label = _element_text(article, anchor_class)
        if not label:
            # 锚点缺失的条目无法配对，直接跳过：给它编一个「第 N 条」的名字，
            # 下次顺序一变就会变成「一条删除 + 一条新增」，那是假的差异。
            continue
        fields: dict[str, str] = {}
        for css_class, display_name in field_classes.items():
            value = _element_text(article, css_class)
            if value:
                fields[display_name] = value
        items.append(ParsedItem(label=label, fields=fields))
    return ParsedSection(key=key, title=title or SECTION_LABEL_FALLBACK[key], items=items)


def _pair_items(
    before: list[ParsedItem], after: list[ParsedItem]
) -> list[tuple[ParsedItem | None, ParsedItem | None]]:
    """按锚点文本配对，同名按出现次序（`ADR-026` §2.4 规则 2）。

    不用「集合差」：`["实习", "实习"]` 这种同名两条的项目经历在集合里会塌成一条，
    于是「删掉其中一段」会被读成「什么都没变」。
    """
    buckets: dict[str, list[ParsedItem]] = {}
    for item in after:
        buckets.setdefault(item.label, []).append(item)

    pairs: list[tuple[ParsedItem | None, ParsedItem | None]] = []
    matched: list[ParsedItem] = []
    for item in before:
        bucket = buckets.get(item.label)
        if bucket:
            matched.append(bucket.pop(0))
            pairs.append((item, matched[-1]))
        else:
            pairs.append((item, None))
    # `after` 里没被配上的行，按它们自己的顺序追加为「新增」。
    leftovers = [item for bucket in buckets.values() for item in bucket]
    pairs.extend((None, item) for item in leftovers)
    return pairs


def _diff_items(key: str, before: list[ParsedItem], after: list[ParsedItem]) -> list[ItemDiff]:
    diffs: list[ItemDiff] = []
    for old, new in _pair_items(before, after):
        if old is None and new is not None:
            diffs.append(
                ItemDiff(kind="added", label=new.label, before=None, after=new.plain_text())
            )
            continue
        if new is None and old is not None:
            diffs.append(
                ItemDiff(kind="removed", label=old.label, before=old.plain_text(), after=None)
            )
            continue
        if old is None or new is None:  # pragma: no cover - 上面的两支已经覆盖全部组合
            continue
        field_diffs = [
            FieldDiff(field=name, before=old.fields.get(name, ""), after=value)
            for name, value in new.fields.items()
            if old.fields.get(name, "") != value
        ]
        # 旧版有、新版整格消失的字段也要报（否则「删掉一句职责」会被读成没变）。
        field_diffs.extend(
            FieldDiff(field=name, before=value, after="")
            for name, value in old.fields.items()
            if name not in new.fields
        )
        if field_diffs:
            diffs.append(
                ItemDiff(
                    kind="changed",
                    label=new.label,
                    before=old.plain_text(),
                    after=new.plain_text(),
                    fields=field_diffs,
                )
            )
    return diffs


def _section_order_changed(old: ParsedDocument, new: ParsedDocument) -> bool:
    """两版都有的区块，相对顺序是否变了（`ADR-026` §2.4 规则 4）。"""
    kept = [key for key in new.order if key in old.sections]
    old_kept = [key for key in old.order if key in new.sections]
    return kept != old_kept


def diff_documents(old_doc: ParsedDocument | None, new_doc: ParsedDocument | None) -> DocumentDiff:
    """两版文档的差异。任一侧不可解析 → `comparable=False` + 一句说明。"""
    if old_doc is None or new_doc is None:
        return DocumentDiff(
            comparable=False,
            note="其中一版的排版结构无法识别（可能是更早版本生成的），本次只能看两版的正文，不能逐条对比。",
            sections=[],
            added=0,
            removed=0,
            changed=0,
            section_order_changed=False,
            item_order_changed=False,
        )

    sections: list[SectionDiff] = []
    added = removed = changed = 0
    item_order_changed = False

    keys = [key for key in SECTION_ORDER if key in old_doc.sections or key in new_doc.sections]
    for key in keys:
        old_section = old_doc.sections.get(key)
        new_section = new_doc.sections.get(key)
        if old_section is None and new_section is not None:
            items = [
                ItemDiff(kind="added", label=item.label, before=None, after=item.plain_text())
                for item in new_section.items
            ]
            added += len(items)
            sections.append(
                SectionDiff(
                    key=key,
                    label=new_section.title or SECTION_LABEL_FALLBACK[key],
                    change="added",
                    title_before=None,
                    title_after=new_section.title,
                    order_changed=False,
                    items=items,
                )
            )
            continue
        if new_section is None and old_section is not None:
            items = [
                ItemDiff(kind="removed", label=item.label, before=item.plain_text(), after=None)
                for item in old_section.items
            ]
            removed += len(items)
            sections.append(
                SectionDiff(
                    key=key,
                    label=old_section.title or SECTION_LABEL_FALLBACK[key],
                    change="removed",
                    title_before=old_section.title,
                    title_after=None,
                    order_changed=False,
                    items=items,
                )
            )
            continue
        if old_section is None or new_section is None:  # pragma: no cover
            continue

        order_changed = _sequence_reordered(
            [item.label for item in old_section.items],
            [item.label for item in new_section.items],
        )
        item_order_changed = item_order_changed or order_changed
        items = _diff_items(key, old_section.items, new_section.items)
        added += sum(1 for item in items if item.kind == "added")
        removed += sum(1 for item in items if item.kind == "removed")
        changed += sum(1 for item in items if item.kind == "changed")
        title_changed = old_section.title != new_section.title
        sections.append(
            SectionDiff(
                key=key,
                label=new_section.title or SECTION_LABEL_FALLBACK[key],
                change="changed" if (items or title_changed or order_changed) else "unchanged",
                title_before=old_section.title,
                title_after=new_section.title,
                order_changed=order_changed,
                items=items,
            )
        )

    return DocumentDiff(
        comparable=True,
        note=None,
        sections=sections,
        added=added,
        removed=removed,
        changed=changed,
        section_order_changed=_section_order_changed(old_doc, new_doc),
        item_order_changed=item_order_changed,
    )


def _sequence_reordered(before: list[str], after: list[str]) -> bool:
    """只比较**两版都出现过的条目**的先后：新增/删除本身不算顺序变化。

    同名条目按「过滤后仍出现」计数：`[A, B, A]` → `[A, A, B]` 是顺序变化，
    而 `[A, B]` → `[C, A, B]` 不是（C 是新增，A/B 的相对次序没动）。
    """
    shared_before = [label for label in before if label in after]
    shared_after = [label for label in after if label in before]
    return shared_before != shared_after
