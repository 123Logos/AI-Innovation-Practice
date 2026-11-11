"""D5.3：`resolve_layout()` 的降级矩阵（ADR-007 §6.8(3)）逐条钉死。

这个文件是**纯函数单测**，不碰数据库、不起 FastAPI：`resolve_layout()` 是
「库里存什么 → 纸面渲染成什么」的唯一解释器，它错了就是「用户调好的顺序
保存后自己变回去」，而且不会有任何报错——所以每条降级分支都必须有一条断言。

为什么降级要这么认真：排版是**装饰信息**。坏掉的多半是数据（手工改库、
版本回滚、将来加第 4 个模块），用户无从修复，所以任何异常都退化为默认，
绝不能让整份简历打不开（ADR-007 §6.8(3) 的核心原则）。
"""

import logging

import pytest

from app.core.document_style import (
    DEFAULT_SECTIONS,
    LAYOUT_TITLE_MAX_LEN,
    SECTION_IDS,
    resolve_layout,
)


def _ids(layout: list) -> list[str]:  # type: ignore[type-arg]
    return [section.id for section in layout]


def _titles(layout: list) -> dict[str, str]:  # type: ignore[type-arg]
    return {section.id: section.title for section in layout}


def test_default_order_when_layout_is_null() -> None:
    """`layout_config IS NULL` = 从未自定义，用默认顺序。"""
    assert _ids(resolve_layout(None)) == [
        "educations",
        "skills",
        "experiences",
        "certificates",
        "portfolio",
    ]


def test_default_sections_constant_is_the_documented_order() -> None:
    """默认顺序（ADR-015 §2.2）：教育 → 技能 → 项目 → 证书 → 作品链接。"""
    assert _ids(list(DEFAULT_SECTIONS)) == [
        "educations",
        "skills",
        "experiences",
        "certificates",
        "portfolio",
    ]
    assert [section.title for section in DEFAULT_SECTIONS] == [
        "教育经历",
        "技能",
        "项目经历",
        "证书",
        "作品链接",
    ]


@pytest.mark.parametrize(
    "broken",
    [
        "junk",  # 手工改库塞了字符串
        [],  # 整体存成了数组
        42,
        {"version": 2},  # 缺 sections
        {"version": 2, "sections": "skills"},  # sections 不是数组
        {"version": 2, "sections": []},  # 空数组（数据损坏，不是「全隐藏」）
        {"version": 2, "sections": {}},  # dict 不是 list
    ],
)
def test_broken_config_falls_back_to_default(
    broken: object, caplog: pytest.LogCaptureFixture
) -> None:
    """任何形状不对的输入都退回默认顺序，且**不抛异常**（同时记 warning）。"""
    with caplog.at_level(logging.WARNING):
        layout = resolve_layout(broken)

    assert _ids(layout) == list(SECTION_IDS)
    assert caplog.records, "降级必须留下 warning，否则线上排查看不到「配置坏了」"


def test_unknown_version_falls_back_to_default() -> None:
    """版本不认识就当没配置：向前兼容「新版本写入、旧版本读取」。"""
    layout = resolve_layout(
        {
            "version": 99,
            "sections": [{"id": "skills", "title": "技术栈"}],
        }
    )

    assert _ids(layout) == list(SECTION_IDS)
    assert _titles(layout)["skills"] == "技能"


def test_missing_section_is_appended_in_default_order() -> None:
    """只给了两段时，缺的那段追加到**末尾**（不是插回原位）。"""
    layout = resolve_layout(
        {
            "version": 2,
            "sections": [
                {"id": "experiences", "title": "项目"},
                {"id": "skills", "title": "技能"},
            ],
        }
    )

    assert _ids(layout) == [
        "experiences",
        "skills",
        "educations",
        "certificates",
        "portfolio",
    ]


def test_unknown_section_id_is_dropped() -> None:
    """未知 id 丢弃（可能是新版本写入的模块），其余顺序不受影响。"""
    layout = resolve_layout(
        {
            "version": 2,
            "sections": [
                {"id": "awards", "title": "获奖"},
                {"id": "skills", "title": "技能"},
            ],
        }
    )

    assert _ids(layout) == [
        "skills",
        "educations",
        "experiences",
        "certificates",
        "portfolio",
    ]
    assert "awards" not in _titles(layout)


def test_duplicate_section_id_keeps_first_position_and_first_title() -> None:
    """重复 id 保留**首次出现**的位置与标题，后面的整条丢弃。"""
    layout = resolve_layout(
        {
            "version": 2,
            "sections": [
                {"id": "skills", "title": "技术栈"},
                {"id": "educations", "title": "学历"},
                {"id": "skills", "title": "重复的那条"},
            ],
        }
    )

    assert _ids(layout) == [
        "skills",
        "educations",
        "experiences",
        "certificates",
        "portfolio",
    ]
    assert _titles(layout)["skills"] == "技术栈"


@pytest.mark.parametrize("blank", ["", " ", "   ", "\n\t", None, 123])
def test_blank_or_non_string_title_falls_back_to_default(blank: object) -> None:
    """标题为空 / 全空白 / 不是字符串时回落默认标题（不能留一个空 `h2`）。"""
    layout = resolve_layout(
        {"version": 2, "sections": [{"id": "skills", "title": blank}]},
    )

    assert _titles(layout)["skills"] == "技能"


def test_title_whitespace_is_collapsed_and_length_is_capped() -> None:
    """标题压平空白并截断到 12 字：`h2` 是单行，折行会把版式撑开。"""
    layout = resolve_layout(
        {
            "version": 2,
            "sections": [
                {"id": "skills", "title": "  技术\n栈   与工具  "},
                {"id": "experiences", "title": "项目经历与开源贡献说明文档"},
            ],
        }
    )

    titles = _titles(layout)
    assert titles["skills"] == "技术 栈 与工具"
    assert titles["experiences"] == "项目经历与开源贡献说明文档"[:LAYOUT_TITLE_MAX_LEN]
    assert len(titles["experiences"]) == LAYOUT_TITLE_MAX_LEN


def test_valid_config_keeps_order_and_titles_verbatim() -> None:
    """正常配置原样生效——这是「没坏就别动」的那一半。"""
    layout = resolve_layout(
        {
            "version": 2,
            "sections": [
                {"id": "skills", "title": "技术栈"},
                {"id": "educations", "title": "教育背景"},
                {"id": "experiences", "title": "项目经验"},
            ],
        }
    )

    assert _ids(layout) == [
        "skills",
        "educations",
        "experiences",
        "certificates",
        "portfolio",
    ]
    assert _titles(layout) == {
        "skills": "技术栈",
        "educations": "教育背景",
        "experiences": "项目经验",
        "certificates": "证书",
        "portfolio": "作品链接",
    }


def test_resolution_always_yields_every_section_once() -> None:
    """不变量：不管输入多脏，输出恒为**五个模块各一次**（ADR-015 §2.2）。

    生成器靠这条保证「用户把某段拖到第一位但那段没内容」时，
    后面的内容不会因为那次丢弃而整体消失。
    """
    messy_inputs: list[object] = [
        None,
        "x",
        {"version": 2, "sections": []},
        {"version": 2, "sections": [{"id": "skills"}, {"id": "skills"}, {"id": "nope"}]},
        {"version": 2, "sections": [{"title": "没有 id"}]},
    ]

    for messy in messy_inputs:
        layout = resolve_layout(messy)
        assert len(layout) == len(SECTION_IDS)
        assert sorted(_ids(layout)) == sorted(SECTION_IDS)
        assert all(section.title.strip() for section in layout)
