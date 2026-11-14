"""教师端工具的纯口径（批次 B10a / B10b，`ADR-029`）。

分成两组，两组都**不碰数据库、不认识 ORM、不 import FastAPI**：

- 聚合口径：技能覆盖率、三档分桶、标签分布、ZIP 文件名消毒。看板上的每个数字
  都必须能在这里被追问「为什么」，而不是藏在 SQL 的 `CASE WHEN` 里；
- 打印口径：从 `ResumePreview.vue` **原样切出**纸面 CSS、拼出导出用的独立 HTML。
  这里刻意不在后端重写一份打印样式——`page-break-inside: avoid` 这类规则一旦有两份，
  表现就是「实时预览不腰斩、导出稿腰斩」，而且没有任何报错（见 `ADR-029` §2.5）。

读前端源码的理由与 `AGENTS.md` §4.5 的探测页一致：纸面 CSS 的真相只有一个，
手抄一份永远会自证正确。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from app.core.skill_names import skill_name_key

#: 项目根目录（`app/core/` 的上两级）。源码文件不随部署的 working directory 变化。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIEW_VUE = PROJECT_ROOT / "frontend" / "src" / "components" / "ResumePreview.vue"
GLOBAL_CSS = PROJECT_ROOT / "frontend" / "src" / "styles.css"

#: 覆盖率三档（`ADR-029` §2.2）。边界用「>= / <」写死，不留浮点比较的模糊地带。
BUCKET_BELOW_HALF = "below_half"
BUCKET_HALF_TO_EIGHTY = "half_to_eighty"
BUCKET_EIGHTY_PLUS = "eighty_plus"
BUCKETS = (BUCKET_BELOW_HALF, BUCKET_HALF_TO_EIGHTY, BUCKET_EIGHTY_PLUS)


class PaperStylesUnavailableError(Exception):
    """纸面 CSS 读不到：不许用一份后端自抄的样式偷偷顶上。"""


def coverage_percent(required: Iterable[str], possessed: Iterable[str]) -> int:
    """岗位技能覆盖率（0–100 整数，四舍五入 half-up）。

    用 `skill_name_key`（B7 同一份归一规则）判「是不是同一个技能」，**不接别名层**：
    别名是匹配打分的产品口径，看板要的是「纸面上写没写」这种可解释的事实（`ADR-029` §2.2）。
    岗位没写技能时返回 0，由调用方另打 `no_requirements` 标记——0 与「没要求」是两件事。
    """
    required_keys = {skill_name_key(name) for name in required}
    if not required_keys:
        return 0
    possessed_keys = {skill_name_key(name) for name in possessed}
    hit = len(required_keys & possessed_keys)
    # half-up，不用 round()（它是银行家舍入）。
    return (hit * 200 + len(required_keys)) // (2 * len(required_keys))


def coverage_bucket(percent: int) -> str:
    if percent >= 80:
        return BUCKET_EIGHTY_PLUS
    if percent >= 50:
        return BUCKET_HALF_TO_EIGHTY
    return BUCKET_BELOW_HALF


def empty_buckets() -> dict[str, int]:
    return {name: 0 for name in BUCKETS}


def tag_distribution(rows: Sequence[tuple[str, str, str]]) -> list[dict[str, object]]:
    """标签分布：`(tag, status, student_profile_id)` → 按 `(tag, status)` 数**学生**。

    用集合去重而不是 `len(rows)`：B9 的 `UNIQUE(student_profile_id, tag)` 今天保证
    一名学生一条标签，但将 `source` 扩到「项目 / 教师证据」时，同一个学生可能有多行，
    `COUNT(*)` 会把他算重——那时这条断言不会红，看板上的人数是静默错的。
    """
    grouped: dict[tuple[str, str], set[str]] = {}
    for tag, status, profile_id in rows:
        grouped.setdefault((tag, status), set()).add(profile_id)
    return [
        {"tag": tag, "status": status, "student_count": len(profile_ids)}
        for (tag, status), profile_ids in sorted(grouped.items())
    ]


#: 只留中英文、数字、空格、连字符与下划线；其余一律换掉。
_UNSAFE_NAME = re.compile(r"[^\w\u4e00-\u9fff\- ]+", re.UNICODE)


def teacher_audit_detail(
    resource_ids: list[str],
    item_count: int,
    bytes_count: int,
    *,
    reason: str | None = None,
) -> str:
    """`audit_logs.detail` 的 JSON 形状（`ADR-029` §2.4）：一处定义、两处调用。

    路由依赖（散人拒绝）与服务层（正常 / 部分跳过）都会写审计；让它们各拼一次
    dict，字段名迟早分叉，而 `detail` 是排障时唯一能回答「到底下了什么」的地方。
    """
    detail: dict[str, object] = {
        "resource_ids": resource_ids,
        "item_count": item_count,
        "format": "pdf",
        "bytes": bytes_count,
    }
    if reason is not None:
        detail["reason"] = reason
    return json.dumps(detail, ensure_ascii=False)


def skill_confirm_audit_detail(
    skill: str,
    *,
    previous_tag_source: str | None,
    previous_skill_source: str | None,
) -> str:
    """教师确认技能那条审计的 `detail`（B7 证据链）。

    **不复用 `teacher_audit_detail()`**：那个函数里 `format` 恒为 `"pdf"`、还有 `bytes`，
    它们描述的是「一次下载」；确认动作既不产出文件也没有字节数，套上去就成了
    一条自相矛盾的审计。两个形状各写一处，字段名反而不会分叉。

    为什么要记 `previous_*`：标签是**覆盖写**（`UNIQUE(student_profile_id, tag)`），
    写入之后那一行只剩 `teacher`，原来「测评 80 分」这件事在库里就查不到了。
    审计是唯一能回答「这条证据盖掉了什么」的地方。
    """
    detail: dict[str, object] = {
        "skill": skill,
        "previous_tag_source": previous_tag_source,
        "previous_skill_source": previous_skill_source,
    }
    return json.dumps(detail, ensure_ascii=False)


def safe_zip_filename(index: int, student_name: str, version_no: int) -> str:
    """ZIP 内条目名：`001_姓名_v3.pdf`。

    序号前缀不是装饰：一个班里重名的学生、同一学生同一版本号的多份简历都会撞名，
    没有前缀就会在压缩包里静默覆盖。原始姓名**只**用于拼这个字符串，绝不拿去拼路径。
    """
    cleaned = _UNSAFE_NAME.sub("", student_name or "未命名").strip().replace(" ", "_")
    cleaned = cleaned[:40] or "未命名"
    return f"{index:03d}_{cleaned}_v{version_no}.pdf"


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


def _style_blocks(text: str) -> list[str]:
    """按**行首**锚点切 SFC 的 `<style>` 块（与 `paperPaginationGuards.spec.ts` 同一判据）。

    不能用宽松正则扫 `<style...>`：脚本注释里出现过 `<style>` 字面量，从注释里切起会
    吐出「看起来还在、其实全废」的结果（本仓已经踩过两次）。
    """
    lines = text.splitlines(keepends=True)
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        if re.match(r"^<style\b[^\n>]*>[ \t]*\r?\n?$", lines[index]):
            index += 1
            collected: list[str] = []
            while index < len(lines) and not re.match(r"^</style>[ \t]*\r?\n?$", lines[index]):
                collected.append(lines[index])
                index += 1
            blocks.append(_strip_comments("".join(collected)))
        index += 1
    return blocks


def _root_block(text: str) -> str:
    """取 `styles.css` 的第一个 `:root { … }`（先去注释，否则注释里的字面量会先命中）。"""
    css = _strip_comments(text)
    start = css.find(":root")
    if start < 0:
        return ""
    open_at = css.find("{", start)
    if open_at < 0:
        return ""
    depth = 0
    for cursor in range(open_at, len(css)):
        if css[cursor] == "{":
            depth += 1
        elif css[cursor] == "}":
            depth -= 1
            if depth == 0:
                return css[start : cursor + 1]
    return ""


def load_paper_css() -> str:
    """导出用的纸面 CSS：**从前端源码原样切出**，不重抄。

    顺序有讲究：先全局令牌、再 scoped 纸面样式、最后非 scoped 的打印规则，
    这样 `@media print` 的覆盖声明排在最后。缺文件就直接抛，绝不用内置副本兜底。
    """
    if not PREVIEW_VUE.is_file() or not GLOBAL_CSS.is_file():
        raise PaperStylesUnavailableError("找不到前端纸面样式源文件")
    blocks = _style_blocks(PREVIEW_VUE.read_text(encoding="utf-8"))
    if len(blocks) < 2:
        raise PaperStylesUnavailableError("ResumePreview.vue 的 style 块数量异常")
    joined = "\n".join(blocks)
    # 反证点：非 scoped 的那块含段落级防腰斩；少了它导出稿会被腰斩而预览不会。
    if "page-break-inside: avoid" not in joined or "break-inside: avoid" not in joined:
        raise PaperStylesUnavailableError("打印分页规则不在源码里")
    root = _root_block(GLOBAL_CSS.read_text(encoding="utf-8"))
    return "\n".join(
        [
            root,
            *blocks,
            # 纸面尺寸由前端以 CSS 变量注入；独立导出文档没有那层 JS，这里给 A4 兜底。
            ":root { --paper-width: 210mm; --paper-height: 297mm; --paper-zoom: 1; }",
        ]
    )


def build_export_html(content_html: str, paper_size: str = "A4") -> str:
    """把 `resumes.content_html` 变成一份可打印的独立 HTML。

    三件必须做的事，少一件 PDF 就会「看着像简历、其实不是那一版」：

    1. 注入 `load_paper_css()` 的**同一份**样式（含非 scoped 的 `@media print`）；
    2. 把 body 内容包进 `.paper-stage > .paper`——生成物里的选择器全是 `.paper …`，
       少了这层外壳一份规则都命中不了；
    3. `@page` 用字面量而不是 `var()`（浏览器不接受），并让 `.paper` 打印时
       `min-height: 0`（这行在源码的 scoped 打印块里，§2.5 的自然分页靠它）。

    **不写页码进正文**：页码由导出侧（打印参数）给，写进 HTML 会在第二页重复。
    """
    css = load_paper_css()
    size = "letter" if paper_size.lower() == "letter" else "A4"
    style = f'<style data-export="print">\n@page {{ size: {size}; margin: 0; }}\n{css}\n</style>'
    document = content_html
    if "</head>" in document:
        document = document.replace("</head>", f"{style}\n</head>", 1)
    else:
        document = f"{style}\n{document}"

    body_open = re.search(r"<body\b[^>]*>", document, re.IGNORECASE)
    body_close = re.search(r"</body>", document, re.IGNORECASE)
    if body_open is None or body_close is None:
        raise PaperStylesUnavailableError("简历生成物不是完整 HTML 文档")
    inner = document[body_open.end() : body_close.start()]
    wrapped = '<div class="paper-stage"><div class="paper">' + inner + "</div></div>"
    return document[: body_open.end()] + wrapped + document[body_close.start() :]
