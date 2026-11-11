"""G4 令牌检查：组件样式里不许出现裸 hex 色值与裸 px 字号（守则 T1 / T2，§9 G4）。

判据一句话：`frontend/src/**` 的 `<style>` 块与 `styles.css` 里，
`font-size` 必须走 `--text-*`，颜色必须走 `--ui-*` / `--theme-*` / `--el-*`，
写死一个 `#527873` 或 `13px` 就是违例——暗色模式与换肤都靠令牌统一负责，
写死的值在深底上没人替它补对比度（StylePanel 曾经有 18 个硬编码 hex，其中三条只有 2.6:1）。

**豁免名单**（老板 2026-11-11 第 13 轮：「全部批准进 CI，但必须带豁免名单」）：

1. **纸面**：只在 `components/ResumePreview.vue` 里，且选择器**整条**都落在纸面文档内
   （首段是 `.paper` 本身，后面跟后代 / 子代组合子），才整块豁免。纸面要离线复现
   （打印 / 导出 PDF / 后端生成的 `content_html` 快照），字号与色值必须是固定值，
   不能跟着 Web UI 的令牌漂移。
2. **打印稿**：`@media print` 里的声明整块豁免，理由同上。判定要求媒体查询**整个**是
   print-only：`print` / `only print` / `PRINT and (color)` 算，`not print`、
   `screen, print`、`print, all`、`printscreen` 都不算——一半屏幕一半打印的查询
   豁免掉就等于放行了屏幕样式。
3. **令牌定义本身**：`styles.css` 里**选择器恰好是 `:root`** 的自定义属性定义
   （`--ui-*` / `--theme-*` / `--text-*` …）是令牌的出处，那里必须是裸值，
   否则就是循环定义。`--paper-*` 不在此列——纸面色板只许在纸面作用域里写死，
   在 `:root` 上定义一份等于给整个 UI 开了一个不受控的口子。

为什么是脚本而不是 vitest：与 G1 / G5 同一个理由——纯文本检查不该为了跑起来去开数据库
（`tests/conftest.py` 的会话夹具会连真实 MySQL），放在 `scripts/` 还顺带被
`ruff format --check` / `ruff check` / `mypy` 三道既有闸覆盖。

**解析为什么不引第三方 CSS 解析器**：本仓没有这个依赖，而判据只需要花括号层级、
选择器文本与「值与属性」两段。代价是这里必须自己处理字符串、注释、`url(...)`、
嵌套括号——**这几件恰恰是「闸看起来绿、其实什么都没扫」的常见来源**，
`_declarations()` 的注释里逐条写了为什么。三条 fail-closed：
解析不了就报「解析失败」，一个声明都没扫到就报「空扫描」，styles.css 缺失也报——
**宁可红着让人看，也不发一张「已经查过」的假证书**。

**间距不在本闸射程内**：T1 的规则文本提到间距，但机械判据只写了「裸 hex / 裸 px 字号」。
现有间距刻度（9/11/14/18/20/28/56…）与 `--space-*`（4/6/8/10/12/16/24/32/48）并不重合，
强行替换等于一次几百处的像素改动，那是视觉改版不是令牌化。要做它得先扩表并逐档实测，
是另一批的事，这里不假装已经管住。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"

#: `<style …>` 块（.vue 里可能有多块：scoped 的 UI chrome 与非 scoped 的纸面）。
STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)

#: 裸 hex 色值：#rgb / #rgba / #rrggbb / #rrggbbaa（大小写都算）。
HEX = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")

#: 裸 px 长度：支持小数、科学计数与大写单位（`.5px` / `1e2PX` 都是 px）。
BARE_PX = re.compile(r"(?<![\w-])(\d+(?:\.\d+)?(?:e[+-]?\d+)?)px", re.IGNORECASE)

#: 字号刻度：值 → 令牌名。与 `styles.css` 的 `--text-*` 一一对应，改表要两边一起改。
TEXT_TOKENS = {
    "10": "--text-2xs",
    "11": "--text-xs",
    "12": "--text-sm",
    "13": "--text-base",
    "14": "--text-md",
    "15": "--text-lg",
    "16": "--text-lg-plus",
    "17": "--text-xl",
    "18": "--text-2xl",
    "20": "--text-3xl",
    "22": "--text-4xl",
    "24": "--text-5xl",
    "30": "--text-6xl",
}

#: 纸面豁免只在这一个文件里成立：别的地方出现 `.paper` 是普通类名，不该蹭豁免。
PAPER_FILE = "frontend/src/components/ResumePreview.vue"

#: 纸面**内部**的预览 chrome：它们挂在 `.paper` 底下，但属于工具栏 / 提示条这类 UI，
#: 必须跟着令牌走。没有这一条，`.paper .toast` 会因为前缀是 `.paper` 而混进豁免。
PAPER_CHROME_CLASSES = frozenset(
    {"toast", "modal", "modal-title", "mode-badge", "preview-toolbar", "toolbar", "stage", "frame"}
)

#: `font` 简写的 line-height 位置：`font: italic 13px/1.4 serif` 里的 `1.4` 不是字号。
FONT_SHORTHAND = "font"

#: 只看尺寸位置的属性。`font` 简写要按 `/` 切开，`font-size` 整段都算。
SIZE_PROPERTIES = frozenset({"font-size", "font"})


class CssError(Exception):
    """CSS 结构不成立（括号 / 字符串 / 注释未闭合）。**fail closed**：宁可红，不可假绿。"""


def _blank(segment: str) -> str:
    """把一段不该被扫描的文本换成等长空白，**换行原样保留**：行号与偏移都不动。"""
    return "".join("\n" if char == "\n" else " " for char in segment)


def _sanitize(css: str) -> str:
    """去掉注释、抹掉字符串内容与 `url(...)` 的内容，保留结构与行号。

    三件事都必须做，而且都不只是「好看」：

    - **注释**：`/* font-size:99px */` 若原样留着，闸会因为一句注释变红；
      但注释也不能直接删掉（行号会漂），所以换成等长空白。
    - **字符串**：`content: "#abc 99px"` 里的 `#abc` 不是颜色。
    - **`url(...)`**：`url(#abc)` 是 SVG 片段引用、`url("data:…;#abc;99px")` 是内联图，
      它们既不是颜色也不是字号。整段内容抹掉、括号结构保留。
    """
    out: list[str] = []
    index = 0
    length = len(css)
    while index < length:
        char = css[index]
        if char == "/" and index + 1 < length and css[index + 1] == "*":
            end = css.find("*/", index + 2)
            if end == -1:
                raise CssError("注释未闭合")
            out.append(_blank(css[index : end + 2]))
            index = end + 2
            continue
        if char in {'"', "'"}:
            cursor = index + 1
            while cursor < length:
                if css[cursor] == "\\":
                    cursor += 2
                    continue
                if css[cursor] == char:
                    break
                cursor += 1
            if cursor >= length:
                raise CssError("字符串未闭合")
            out.append(char + _blank(css[index + 1 : cursor]) + char)
            index = cursor + 1
            continue
        if css[index : index + 4].lower() == "url(":
            depth = 0
            cursor = index + 3
            while cursor < length:
                if css[cursor] == "(":
                    depth += 1
                elif css[cursor] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1
            if cursor >= length:
                raise CssError("url( 未闭合")
            out.append("url(" + _blank(css[index + 4 : cursor]) + ")")
            index = cursor + 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _split_top_level(text: str, separator: str) -> list[str]:
    """按分隔符切分，但**不切**括号 / 中括号里的那些（`calc(a, b)`、`:is(.a, .b)`）。"""
    parts: list[str] = []
    buffer = ""
    depth = 0
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == separator and depth == 0:
            parts.append(buffer)
            buffer = ""
            continue
        buffer += char
    parts.append(buffer)
    return parts


class Declaration:
    """一条声明：够本闸判事的最小集合。"""

    __slots__ = ("at_rules", "line", "prop", "selectors", "value")

    def __init__(
        self,
        line: int,
        prop: str,
        value: str,
        selectors: list[str],
        at_rules: list[str],
    ) -> None:
        self.line = line
        self.prop = prop
        self.value = value
        self.selectors = selectors
        self.at_rules = at_rules


def _declarations(css: str, line_offset: int = 0) -> list[Declaration]:
    """扫出全部声明（含**块尾那条不带分号的**），带上选择器链与 at-rule 链。

    这个解析器要顶住四类真实的坑，每一类都能让闸悄悄失效：

    1. **块尾无分号**：`{a {color:#abc}` 这类规则的最后一条没有 `;`。只认 `;` 的实现
       对它们全盲——闸照样绿，问题一个拦不住，比没有闸更坏（它发的是假证书）。
    2. **字符串里的结构字符**：`content: "}; { font-size:88px"` 里的 `}` 不是块结束，
       字符串内部的 `#abc` 也不是颜色（`_sanitize()` 已经抹掉，这里只管结构）。
    3. **括号深度**：`calc(var(--x, max(2px, 3px)) + 1px)` 里的 `;` 与 `{}` 若在括号内，
       不是分隔符；反之括号在 `}` 前没闭合就是**结构不成立**，必须 fail closed。
    4. **嵌套 CSS**：`.paper { & + .toast { … } }` 的内层选择器要能拼回整条链，
       否则内层会白蹭外层的纸面豁免。
    """
    text = _sanitize(css)
    declarations: list[Declaration] = []
    stack: list[tuple[str, str]] = []  # (kind, text)；kind ∈ {"at", "rule"}
    buffer: list[str] = []
    buffer_line = 1
    buffer_started = False
    line = 1
    paren_depth = 0
    index = 0
    length = len(text)

    def buffer_text() -> str:
        return "".join(buffer)

    def reset(_: int) -> None:
        nonlocal buffer, buffer_started, paren_depth
        buffer = []
        buffer_started = False
        paren_depth = 0

    def append(value: str) -> None:
        """往缓冲区写一个字符；**第一个非空白字符所在的行**才是这条声明的行号。"""
        nonlocal buffer_started, buffer_line
        buffer.append(value)
        if not buffer_started and not value.isspace():
            buffer_line = line
            buffer_started = True

    def flush(at_line: int) -> None:
        """收一条声明：`prop: value`。冒号只认括号外的第一个。"""
        raw = buffer_text()
        stripped = raw.strip()
        if not stripped:
            return
        depth = 0
        for position, char in enumerate(raw):
            if char in "([":
                depth += 1
            elif char in ")]":
                depth -= 1
            elif char == ":" and depth == 0:
                prop = raw[:position].strip()
                value = raw[position + 1 :].strip()
                if prop and stack:
                    selectors = [entry for kind, entry in stack if kind == "rule"]
                    at_rules = [entry for kind, entry in stack if kind == "at"]
                    declarations.append(
                        Declaration(buffer_line + line_offset, prop, value, selectors, at_rules)
                    )
                return

    while index < length:
        char = text[index]
        if char == "\n":
            line += 1
        if char in "([":
            paren_depth += 1
            append(char)
        elif char in ")]":
            paren_depth -= 1
            if paren_depth < 0:
                raise CssError("括号不配对")
            append(char)
        elif char == "{":
            if paren_depth > 0:
                # `pair({a;b})` 这类写在括号里的花括号只是值的一部分。
                append(char)
                index += 1
                continue
            prelude = buffer_text().strip()
            stack.append(("at" if prelude.startswith("@") else "rule", prelude))
            # `:root { … }` / `@media … { … }` 这一层没有声明可收。
            buffer = []
            buffer_started = False
            paren_depth = 0
        elif char == "}":
            if paren_depth > 0:
                append(char)
                index += 1
                continue
            flush(line)
            if not stack:
                raise CssError("多出一个 `}`")
            stack.pop()
            reset(index)
        elif char == ";":
            if paren_depth > 0:
                append(char)
                index += 1
                continue
            flush(line)
            reset(index)
        else:
            append(char)
        index += 1

    if paren_depth > 0:
        raise CssError("括号未闭合")
    if stack:
        raise CssError("花括号未闭合")
    flush(line)
    return declarations


def _is_print_only(query: str) -> bool:
    """`@media` 前导是否**整条**只服务打印。

    允许：`print` / `only print` / `PRINT and (color)` / `print and (width > calc(...))`。
    拒绝：`not print`、`screen, print`、`print, all`、`printscreen`、以及任何把 print
    混进别的介质或塞进条件里的写法——**半条的豁免等于放行屏幕样式**。
    """
    for part in _split_top_level(query, ","):
        words = part.strip()
        if not words:
            return False
        segments = _split_top_level(words, " ")
        head = [segment for segment in segments if segment.strip()]
        if not head:
            return False
        offset = 1 if head[0].strip().lower() == "only" else 0
        if len(head) <= offset:
            return False
        if head[offset].strip().lower() != "print":
            return False
        rest = head[offset + 1 :]
        if rest and rest[0].strip().lower() != "and":
            return False
    return True


def _subject_classes(selector: str) -> set[str]:
    """一条选择器**末段**（真正命中的那个元素）上的类名。"""
    tail = ""
    depth = 0
    for char in selector:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char in " >+~" and depth == 0:
            tail = ""
            continue
        if depth == 0:
            tail += char
    return set(re.findall(r"\.([A-Za-z_][\w-]*)", tail))


def _selector_is_paper(selector: str) -> bool:
    """整条选择器是否落在纸面文档内（豁免判据，逐条都要成立）。"""
    text = selector.strip()
    if not text:
        return False
    # 首段只认 `.paper` 与 `.paper-watermark`：`.paperclip` / `.paper-stage` / `.paper-title`
    # 是预览界面自己的类名（前缀撞了而已），`:is(.paper…)` 也不是「整条都在纸面里」。
    first = re.match(r"\s*\.paper(?:-watermark)?(?![-\w])", text)
    if first is None:
        return False
    remainder = text[first.end() :]
    if not remainder:
        # 只有 `.paper` 自己：纸面根节点，豁免。
        return True
    # 后面必须是后代或子代：兄弟组合子（`+` / `~`）指向的是纸面**之外**的元素。
    if remainder[0] not in " >:":
        return False
    return not (_subject_classes(text) & PAPER_CHROME_CLASSES)


def _is_paper_scope(declaration: Declaration, relative: str) -> bool:
    if relative != PAPER_FILE:
        return False
    if not declaration.selectors:
        return False
    return all(
        _selector_is_paper(part) for selector in declaration.selectors for part in [selector]
    )


def _is_print_scope(declaration: Declaration) -> bool:
    return any(
        rule.lower().startswith("@media") and _is_print_only(rule[len("@media") :])
        for rule in declaration.at_rules
    )


def _is_root_token_definition(declaration: Declaration, relative: str) -> bool:
    """`styles.css` 里「选择器恰好是 `:root`」的自定义属性定义。**

    **不是**「`:root` 里的所有声明」：`color` / `font-size` 写在 `:root` 里照样违规，
    否则任何一个想绕闸的人都只要把样式塞进 `:root`。`--paper-*` 也不在此列——
    纸面色板只许在纸面作用域写死。
    """
    if relative != "frontend/src/styles.css":
        return False
    if [selector.strip() for selector in declaration.selectors] != [":root"]:
        return False
    return declaration.prop.startswith("--") and not declaration.prop.startswith("--paper-")


def _size_segment(prop: str, value: str) -> str | None:
    """取出该看尺寸的那一段：`font` 简写只看 `/` 左边的字号位。"""
    if prop == "font-size":
        return value
    if prop == FONT_SHORTHAND:
        return _split_top_level(value, "/")[0]
    return None


def _check_declaration(declaration: Declaration, relative: str) -> list[str]:
    if _is_print_scope(declaration) or _is_paper_scope(declaration, relative):
        return []
    if _is_root_token_definition(declaration, relative):
        return []
    where = f"{relative}:{declaration.line}"
    problems: list[str] = []
    size_value = _size_segment(declaration.prop.lower(), declaration.value)
    if size_value is not None and BARE_PX.search(size_value):
        match = BARE_PX.search(size_value)
        raw = match.group(0) if match else ""
        token = TEXT_TOKENS.get(raw.lower().removesuffix("px"))
        hint = f"改成 var({token})" if token else "刻度里没有这个值，先扩 --text-* 表"
        problems.append(f"{where} 裸 px 字号 `{declaration.prop}: {declaration.value}` —— {hint}")
    if HEX.search(declaration.value):
        problems.append(
            f"{where} 裸 hex 色值 `{declaration.prop}: {declaration.value}`"
            " —— 走 --ui-* / --theme-* 令牌"
        )
    return problems


def _style_blocks(path: Path, text: str) -> list[tuple[str, int]]:
    """把文件切成若干段 CSS，并给出每段在文件里的行偏移（行号要报得准才有人愿意修）。"""
    if path.suffix != ".vue":
        return [(text, 0)]
    return [
        (match.group(1), text.count("\n", 0, match.start(1)))
        for match in STYLE_BLOCK.finditer(text)
    ]


def check() -> list[str]:
    problems: list[str] = []
    stylesheet = SRC / "styles.css"
    files = sorted(SRC.rglob("*.vue"))
    if not stylesheet.is_file():
        problems.append("styles.css 不存在：令牌定义的本体缺失，本闸无法判断（fail closed）")

    scanned = 0
    for path in [*files, stylesheet]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        for block, offset in _style_blocks(path, text):
            try:
                declarations = _declarations(block, offset)
            except CssError as error:
                problems.append(f"{relative} 解析失败：{error}（本闸不会跳过它，请你把它写对）")
                continue
            scanned += len(declarations)
            for declaration in declarations:
                problems.extend(_check_declaration(declaration, relative))

    if scanned == 0:
        # 「一个声明都没扫到」永远不是好消息：要么路径配错了，要么解析器瞎了。
        problems.append("空扫描：一条 CSS 声明都没扫到，本闸等于没跑（fail closed）")
    return problems


def main() -> int:
    problems = check()
    print(f"设计令牌扫描：问题 {len(problems)} 处。")
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
