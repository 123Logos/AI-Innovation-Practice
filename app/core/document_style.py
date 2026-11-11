"""简历文档样式：配色预设（D5.2，ADR-007 §5）与排版解析（D5.3，ADR-007 §6）。

放在 `app/core/` 而不是 services / integrations：它是**纯数据 + 纯函数**，
api / services / integrations 都能 import，不引入任何层间依赖
（ADR-007 §11.1 明确推荐这个落点）。

## 配色预设（D5.2）：为什么是「8 套枚举」而不是任意 HEX

主题色只染两处：`h2` 栏目标题、页眉下边框。

- `h2` 是 13px / 650 的**正文级**字号，WCAG 要求对比度 ≥ 4.5:1；
- 页眉线是 2px 的**非文本 UI 边界**，要求 ≥ 3:1。

下面 8 套的 `accent` 全部 ≥ 4.5、`rule` 全部 ≥ 3（实测值见注释），
所以「用户随便挑一个」都不可能挑出看不清的颜色 —— 这个保证是**离线**做完的，
而不是等运行时发现颜色太浅再报错或偷偷压暗（后者会让「用户选的颜色」
和他看到的颜色不是同一个，见 ADR-007 §5.2）。

若允许任意 HEX，就必须在运行时钳制到 4.5:1；而那套算法要在前端算一遍、
后端算一遍，两边必然漂移（浮点 / 色彩空间 / 步长任一不同就不同），
本仓前端又没有测试框架，漂移无法被自动化发现。预设把这个问题消灭在设计期。

## `rule` 怎么来的

页眉线要「同色系但更轻」，否则 2px 的线会和 `h2` 抢视线。
除 `forest` 外，7 套都按 `accent × 0.65 + 白 × 0.35` 取色，并逐档实测 ≥ 3:1；
`forest` 沿用 D5.1 之前就在用的 `#6b8f8a`，避免这一轮把默认主题的外观改掉
（它是 3.55，同样达标）。

## 加一套主题要动两处，且不会漏

1. 本文件的 `ThemeId` / `THEMES`；
2. 前端 `THEME_OPTIONS`（`frontend/src/stores/resume.ts`）。

两处的 hex 由 `tests/test_profile_contract.py::test_theme_palette_matches_frontend`
逐字比对，改了一边忘了另一边就会红。数据库列是 `VARCHAR(20)`，加主题不需要迁移。

## 只染两处，别扩

把主题色用到正文、背景或技能下划线会整体重算对比度，是另一个量级的工作
（ADR-007 §5.4 已明确排除）。`THEMES` 里刻意只给 `accent` / `rule` 两个色值，
**没有** `background` / `text` 字段，从数据结构上堵住这种误用。
"""

import logging
from dataclasses import dataclass
from typing import Literal, TypedDict, cast

#: 主题枚举。前端 `ThemeId`（`frontend/src/api/types.ts`）与此同源。
ThemeId = Literal["forest", "ink", "navy", "wine", "plum", "cocoa", "slate", "olive"]

#: 缺省主题：与迁移里的 `server_default`、前端 `createEmptyDraft()` 三处同源。
DEFAULT_THEME: ThemeId = "forest"

#: 展示顺序（面板上的圆点按这个顺序排），也是枚举的权威顺序。
THEME_IDS: tuple[ThemeId, ...] = (
    "forest",
    "ink",
    "navy",
    "wine",
    "plum",
    "cocoa",
    "slate",
    "olive",
)


@dataclass(frozen=True)
class DocumentTheme:
    """一套配色：`accent` 染 `h2`，`rule` 染页眉下边框。"""

    id: ThemeId
    #: 面板上展示的中文名（前端 `THEME_OPTIONS` 里有一份同源文案）。
    name: str
    #: `h2` 颜色。必须 ≥ 4.5:1（13px 正文级字号）。
    accent: str
    #: 页眉下边框颜色。必须 ≥ 3:1（非文本 UI 边界）。
    rule: str


#: 8 套预设。注释里的数字是「对白纸 `#FFFFFF` 实测的对比度」（WCAG 2.x 相对亮度）。
THEMES: dict[ThemeId, DocumentTheme] = {
    # 4.89 / 3.55（默认主题，rule 沿用 D5.1 之前的色值）
    "forest": DocumentTheme("forest", "青绿", "#527873", "#6B8F8A"),
    # 13.14 / 4.40
    "ink": DocumentTheme("ink", "墨黑", "#263330", "#727A78"),
    # 9.08 / 3.57
    "navy": DocumentTheme("navy", "藏青", "#2F4A6B", "#78899F"),
    # 8.35 / 3.45
    "wine": DocumentTheme("wine", "酒红", "#7A3B3B", "#A98080"),
    # 9.92 / 3.74
    "plum": DocumentTheme("plum", "深紫", "#4B3A6B", "#8A7F9F"),
    # 7.94 / 3.32
    "cocoa": DocumentTheme("cocoa", "深棕", "#6B4A2F", "#9F8978"),
    # 8.50 / 3.40
    "slate": DocumentTheme("slate", "石板灰蓝", "#3D4F5C", "#818D95"),
    # 7.50 / 3.19
    "olive": DocumentTheme("olive", "橄榄", "#4A5A32", "#89947A"),
}


def normalize_theme(value: object) -> ThemeId:
    """把任意值收敛到预设枚举，未知值一律回落 `forest`。

    读路径**永不因为装饰性字段失败**（ADR-007 §10.4）：手工改库、或将来
    支持过第 9 套主题又回滚，残留的 id 不该让整份简历 500。
    写路径仍由 pydantic 的 `Literal` 严格拦截（非法值 422），这个不对称是刻意的。
    """
    if isinstance(value, str):
        theme = THEMES.get(cast(ThemeId, value))
        if theme is not None:
            return theme.id
    return DEFAULT_THEME


def get_theme(value: object) -> DocumentTheme:
    """取预设实例；非法值回落默认，供生成器内联 CSS 变量时使用。"""
    return THEMES[normalize_theme(value)]


# --------------------------------------------------------------------------- #
# 排版（D5.3 · ADR-007 §6）：展示顺序与自定义标题
# --------------------------------------------------------------------------- #
#
# ## 为什么解析（`resolve_layout`）放在后端而不是前端
#
# 排序与标题在**两个地方**生效：生成器拼 HTML、预览渲染 `<section>`。
# 如果两边各写一套「脏数据怎么办」，同一个库里的坏配置会渲染出两种版式——
# 而前端没有测试框架，这种漂移在 CI 里发现不了（ADR-007 §7.4）。
# 所以降级矩阵**只在后端写一份**：读接口直接返回解析后的结果
# （`GET /profiles/{id}` 的 `layout_config` 恒为「5 段、合法 id、标题非空」），
# 前端拿到的永远是可以直接 `v-for` 的数据，不需要再判一次。
#
# 这与 `paper_size` / `theme` 的「读侧恒为具体值、三态只存在于写侧」是同一条口径。
#
# ## 降级矩阵（ADR-007 §6.8(3) 逐条落地）
#
# | 输入 | 处理 |
# |---|---|
# | `None` | 默认顺序 |
# | 不是 dict / JSON 非法（ORM 层已拦） | 默认顺序 + warning |
# | `version` 未知 | 默认顺序（向前兼容） |
# | `sections` 不是列表 / 空数组 | 默认顺序（**不是**「什么都不渲染」） |
# | 条目不是 dict、`id` 缺失 | 跳过，该模块在末尾按默认顺序补齐 |
# | `id` 未知 | 丢弃 + warning |
# | `id` 重复 | 保留首次出现，后面丢弃 + warning |
# | `title` 空 / 全空白 / 不是字符串 | 回落该模块默认标题 |
# | `title` 超过 12 字 | 截断（写入侧已 422，这条只应对手工改库） |
#
# 核心原则：排版是**装饰信息**，任何异常都退化为默认，绝不让它阻断主流程。
# 这与写路径的严格（未知 id / 重复 id / 超长标题 → 422）刻意不对称：
# 前者是数据状态（用户无从修复），后者是用户输入（可以报错让他改）。

#: 五个可排序模块的 id（ADR-015 §2.2：证书与作品链接进简历，各算一节）。
#: 前端 `SectionId`（`frontend/src/api/types.ts`）与此同源。
SectionId = Literal["educations", "skills", "experiences", "certificates", "portfolio"]

#: 配置版本。**只有等于这个值才认**，其余一律默认（ADR-007 §6.8(1)）。
#: ADR-015 §2.2 起是 2：3 段白名单扩成 5 段。三侧行为刻意不对称——
#: 读静默回落默认、写 422、**生成也 422**（`ResumeService.generate`），
#: 已存在简历的 `content_html` 是不可变快照，永不受升版影响。
LAYOUT_VERSION = 2

#: 标题长度上限（ADR-007 §6.8(4)）：`h2` 在 360px 视口下最多 15 字不折行，取 12 留余量。
LAYOUT_TITLE_MAX_LEN = 12


@dataclass(frozen=True)
class Section:
    """解析后的一个模块：按**渲染顺序**排好的 id + 标题。"""

    id: SectionId
    title: str


#: 默认顺序（ADR-015 §2.2）：教育 → 技能 → 项目 → 证书 → 作品链接。
#: 前三段与 D5.3 之前模板里的占位符顺序逐字一致，新两段追加在末尾。
#: 前端 `DEFAULT_LAYOUT_SECTIONS`（`frontend/src/stores/resume.ts`）另有一份，
#: 由 `tests/test_profile_contract.py::test_default_layout_matches_frontend` 比对。
DEFAULT_SECTIONS: tuple[Section, ...] = (
    Section("educations", "教育经历"),
    Section("skills", "技能"),
    Section("experiences", "项目经历"),
    Section("certificates", "证书"),
    Section("portfolio", "作品链接"),
)

#: 合法 id 的白名单，顺序同默认顺序。
SECTION_IDS: tuple[SectionId, ...] = tuple(section.id for section in DEFAULT_SECTIONS)

#: id → 默认标题。写入侧与降级路径都用它兜底。
DEFAULT_TITLES: dict[SectionId, str] = {section.id: section.title for section in DEFAULT_SECTIONS}


class LayoutSectionDict(TypedDict):
    """`layout_config.sections` 里的一项（只存元信息，不存内容）。"""

    id: str
    title: str


class LayoutConfig(TypedDict):
    """`student_profiles.layout_config` 这一列的 JSON 形状。

    刻意用 `TypedDict` 而不是 pydantic：本文件是「纯数据 + 纯函数」的 core 层，
    写路径的严格校验属于 schema 层（`app/schemas/profile.py` 的 `LayoutConfigInput`）。
    """

    version: int
    sections: list[LayoutSectionDict]


#: 排版降级时记 warning（不抛异常）：运维要能从日志看出「这份简历的配置坏了」。
_logger = logging.getLogger(__name__)


def _resolve_title(raw_title: object, section_id: SectionId) -> str:
    """标题归一：压平空白 → 空则回落默认 → 截断到上限。"""
    if isinstance(raw_title, str):
        # 压平所有空白（含换行）：`h2` 是单行标题，用户从别处粘贴带换行的文本时
        # 不能让纸面上出现一个两行的栏目标题。
        title = " ".join(raw_title.split())
        if title:
            return title[:LAYOUT_TITLE_MAX_LEN]
    return DEFAULT_TITLES[section_id]


def resolve_layout(layout_config: object) -> list[Section]:
    """把库里的 `layout_config` 解析成**可直接渲染**的模块顺序。

    参数类型标成 `object` 而不是 `dict | None`：调用方拿到的是 SQLAlchemy 的
    JSON 列，手工改库后它可能是字符串、数组甚至数字，签名里写 `dict` 只会
    让调用方误以为不必防御。真正的收窄在函数内部完成。
    """
    defaults = list(DEFAULT_SECTIONS)
    if layout_config is None:
        return defaults
    if not isinstance(layout_config, dict):
        _logger.warning("layout_config 不是 JSON 对象，已回落默认顺序：%r", layout_config)
        return defaults
    if layout_config.get("version") != LAYOUT_VERSION:
        _logger.warning(
            "layout_config.version 非当前版本（%r），已回落默认顺序", layout_config.get("version")
        )
        return defaults

    raw_sections = layout_config.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        # 空数组按「数据损坏」处理，**不是**「用户想隐藏所有模块」：
        # 后者需要 `visible` 字段，ADR-007 §6.8(6) 已明确本轮不做。
        _logger.warning("layout_config.sections 为空或不是数组，已回落默认顺序")
        return defaults

    resolved: list[Section] = []
    seen: set[str] = set()
    for entry in raw_sections:
        if not isinstance(entry, dict):
            _logger.warning("layout_config.sections 含非对象条目，已跳过：%r", entry)
            continue
        section_id = entry.get("id")
        if not isinstance(section_id, str) or section_id not in DEFAULT_TITLES:
            # 缺 id 与未知 id 在这里合并处理：都不加入 resolved，
            # 于是该模块会在下面「按默认顺序补齐」时回到末尾，未知 id 则彻底消失。
            if isinstance(section_id, str) and section_id not in DEFAULT_TITLES:
                _logger.warning("layout_config 含未知模块 id %r，已丢弃", section_id)
            continue
        if section_id in seen:
            _logger.warning("layout_config 含重复模块 id %r，保留首次出现的位置", section_id)
            continue
        seen.add(section_id)
        typed_id = cast(SectionId, section_id)
        resolved.append(Section(typed_id, _resolve_title(entry.get("title"), typed_id)))

    # 缺失的模块按默认顺序追加到末尾：老配置只见过旧白名单里的模块，
    # 加新模块时（ADR-015 把 3 段扩成 5 段就是这么发生的），老配置靠这一步自动带上它
    # （ADR-007 §6.8(3)）。注意这条只管「version 相符但 sections 缺项」；
    # version 不符的整份回落在上面已经返回默认顺序。
    for section in defaults:
        if section.id not in seen:
            resolved.append(section)
    return resolved
