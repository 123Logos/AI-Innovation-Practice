"""结构级差异的**规则**（批次 B8 剩余，`docs/adr/ADR-026` §2.4）。

`app/services/resume_diff.py` 是纯函数：不碰数据库、不认识 ORM。所以这一组用例
不启 FastAPI，只喂两段 HTML——每条规则都能单独钉住，失败时也不用先怀疑夹具。

守的是 ADR 里四条**会被静默违反**的规则：

1. 区块按**条目类名**识别，不按标题识别——用户把「技能」改名成「技术栈」，
   结果应当是一条标题变化，而不是「删掉一块 + 新增一块」；
2. 同名条目按**出现次序**配对——`["实习", "实习"]` 在集合里会塌成一条，
   于是「删掉其中一段」被读成「什么都没变」；
3. 新增/删除本身不算顺序变化（`[A,B]` → `[C,A,B]` 不是重排）；
4. 解析不出结构时返回「不可比」，**绝不**返回一份空的「什么都没变」。
"""

from app.services.resume_diff import (
    DocumentDiff,
    diff_documents,
    parse_snapshot,
)

SKILL_ITEM = '<span class="skill-item">{name}</span>'


def _skills(*names: str, title: str = "技能") -> str:
    items = "".join(SKILL_ITEM.format(name=name) for name in names)
    return (
        '<section class="paper-section">'
        f"<h2>{title}</h2>"
        f'<div class="skill-list">{items}</div>'
        "</section>"
    )


def _article(
    title: str,
    *,
    description: str = "",
    responsibility: str = "",
    date: str = "",
) -> str:
    parts = [f'<h3 class="experience-title">{title}</h3>']
    if date:
        parts.append(f'<p class="experience-date">{date}</p>')
    if description:
        parts.append(f'<p class="experience-description">{description}</p>')
    if responsibility:
        parts.append(f'<p class="experience-responsibility">{responsibility}</p>')
    return '<article class="paper-experience">' + "".join(parts) + "</article>"


def _experiences(*articles: str, title: str = "项目经历") -> str:
    return f'<section class="paper-section"><h2>{title}</h2>{"".join(articles)}</section>'


def _document(*sections: str) -> str:
    return (
        "<!DOCTYPE html><html><head><title>简历</title></head><body>"
        + "".join(sections)
        + "</body></html>"
    )


def _diff(before: str, after: str) -> DocumentDiff:
    return diff_documents(parse_snapshot(before), parse_snapshot(after))


def test_a_renamed_section_is_a_title_change_not_remove_plus_add() -> None:
    """规则 1 的全部意义：识别靠 `skill-item` 类名，不靠 `<h2>技能</h2>` 那几个字。"""
    result = _diff(_document(_skills("Python")), _document(_skills("Python", title="技术栈")))

    assert result.comparable
    (section,) = result.sections
    assert section.key == "skills"
    assert section.change == "changed"
    assert section.title_before == "技能"
    assert section.title_after == "技术栈"
    # 「改名」不该伴随任何条目差异：标题不是条目。
    assert section.items == []
    assert (result.added, result.removed, result.changed) == (0, 0, 0)


def test_reordering_is_reported_as_order_change_without_item_changes() -> None:
    """按岗位定制**只该**产生这种差异（`ADR-026` §2.5）。"""
    before = _document(_skills("React", "Python", "SQL"))
    after = _document(_skills("Python", "React", "SQL"))

    result = _diff(before, after)

    (section,) = result.sections
    assert section.order_changed is True
    assert result.item_order_changed is True
    assert section.items == []
    assert (result.added, result.removed, result.changed) == (0, 0, 0)


def test_a_changed_field_reports_before_and_after() -> None:
    """规则 3：字段级差异只报**变了的**那几格。"""
    before = _document(_experiences(_article("校园二手平台", responsibility="负责后端 API")))
    after = _document(_experiences(_article("校园二手平台", responsibility="负责后端与前端")))

    result = _diff(before, after)

    (section,) = result.sections
    (item,) = section.items
    assert item.kind == "changed"
    (field,) = item.fields
    assert field.field == "个人职责"
    assert field.before == "负责后端 API"
    assert field.after == "负责后端与前端"


def test_a_field_that_disappeared_is_reported_as_removed_value() -> None:
    """「删掉一句职责」必须是差异，不能被读成没变。"""
    before = _document(_experiences(_article("校园二手平台", responsibility="负责后端 API")))
    after = _document(_experiences(_article("校园二手平台")))

    result = _diff(before, after)

    (section,) = result.sections
    (item,) = section.items
    (field,) = item.fields
    assert (field.field, field.before, field.after) == ("个人职责", "负责后端 API", "")
    assert result.changed == 1


def test_duplicate_labels_are_paired_by_occurrence() -> None:
    """规则 2：两段同名经历删掉一段 → 一条 `removed`，而不是「集合相等」的 0 差异。"""
    before = _document(_experiences(_article("实习"), _article("实习")))
    after = _document(_experiences(_article("实习")))

    result = _diff(before, after)

    (section,) = result.sections
    assert [item.kind for item in section.items] == ["removed"]
    assert result.removed == 1


def test_adding_an_item_is_not_a_reorder() -> None:
    """规则 4：新增插在中间，原有条目的相对次序没动。"""
    before = _document(_experiences(_article("甲"), _article("乙")))
    after = _document(_experiences(_article("丙"), _article("甲"), _article("乙")))

    result = _diff(before, after)

    (section,) = result.sections
    assert section.order_changed is False
    assert result.item_order_changed is False
    assert result.added == 1


def test_an_unrecognized_snapshot_is_not_comparable_instead_of_empty_diff() -> None:
    """规则 5（也是本模块最要紧的一条）：不认识就说「不可比」。

    返回一份空差异会让用户以为两版一样——而「看起来一样、其实没比」是
    最坏的一种错误（与 ADR-015 §2.3 对「猜链头」的态度一致）。
    """
    legacy = "<!DOCTYPE html><html><body><p>早期纯文本简历</p></body></html>"

    result = _diff(legacy, _document(_skills("Python")))

    assert result.comparable is False
    assert result.sections == []
    assert result.note  # 必须给一句能上屏的说明
    assert (result.added, result.removed, result.changed) == (0, 0, 0)


def test_identical_documents_are_comparable_and_empty() -> None:
    """反向控制：真的没变时必须说「可比且全空」，否则上面的「不可比」无法区分。"""
    document = _document(_skills("Python"), _experiences(_article("甲")))

    result = _diff(document, document)

    assert result.comparable is True
    assert result.note is None
    assert {section.change for section in result.sections} == {"unchanged"}
    assert (result.added, result.removed, result.changed) == (0, 0, 0)
