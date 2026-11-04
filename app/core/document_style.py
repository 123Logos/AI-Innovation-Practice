"""简历配色预设（Phase 2 · D5.2，ADR-007 §5）。

## 为什么是「8 套枚举」而不是任意 HEX

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

from dataclasses import dataclass
from typing import Literal, cast

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
