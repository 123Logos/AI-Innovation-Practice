"""G3 错误文案闸（守则 C3 / §9 G3，老板 2026-11-11 第 13 轮批准进 CI）。

**它守的不是「文案写得好不好」，而是「上屏的那句话从哪来」。** 这一条不看代码推不出来：
批次 F1a 建的 G1 只扫 `frontend/src`，而 `app/main.py` 那侧的 4xx handler 曾经是
`message = str(exc.detail)` —— 路由里任何一条英文 detail（`"Not Found"`、
`"invalid token"`、上游透传的一句英文）都会原样落到浏览器上，**在 G1 眼里完全隐形**
（`docs/ROADMAP.md` §3 X2 记的就是这个洞）。F1c 把这个出口收进
`app/core/error_messages.py` 那张表之后，本闸负责让「收口」这件事不会在下次改动里悄悄退化。

四条判据，全都能红：

1. **`HTTPException(detail=…)` 的每一条都必须是表里认得的句子**。认得的定义：
   等于 `ErrorMessage` 的某个成员、或在 `HTTP_DETAIL_ALIASES` 里（历史写法→标准句的桥）。
   模块级字符串常量会被解析（`UNAUTHORIZED_MESSAGE = "…"` 这种写法照样过）。
2. **动态拼出来的 detail 一律红**：`str(error)` / f-string / 函数调用都是「把异常文本
   当文案」的同一个毛病，只是换了个写法。修法只有两条——落进表里，或在表里新增一条。
3. **`app/main.py` 不许再出现 `str(exc.detail)`**，并且必须仍然调用
   `http_error_message()`。少了后者，这张表就是一份没人读的文档（本闸当时抓的正是这个：
   表建好了、一行都没接线）。
4. **豁免必须留痕**：`error-copy:allow <理由>` 写在同一行或上两行内，且要在下面的
   `EXEMPT` 名单里登记文件级理由。没有理由的豁免视同违规——「加个注释换绿」正是本闸要拦的动作。

为什么是脚本 + 一道 CI 步骤，而不是一条 pytest：与 `check_ui_copy.py` / `check_docs_citations.py`
同一个理由——纯文本检查不该为了跑起来去连真实 MySQL（`tests/conftest.py` 的会话夹具会建库），
而且放进 `scripts/` 顺带被 `ruff format --check` / `ruff check` / `mypy` 三道既有闸覆盖。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # 直接 `python scripts/…` 跑时，app 包不在搜索路径上
    sys.path.insert(0, str(ROOT))

from app.core.error_messages import (
    HTTP_DETAIL_ALIASES,
    ErrorMessage,
)

APP_DIR = ROOT / "app"
MAIN_FILE = APP_DIR / "main.py"

#: 豁免名单：文件 → 「为什么这个文件可以不遵守」。空字典是目标状态。
#: 加一项必须同时说清理由，并在交付说明里登记——名单本身就是账。
EXEMPT: dict[str, str] = {}

#: 豁免标记，与 G1 的 `ui-copy:allow` 同一个形态。
ALLOW_MARKER = "error-copy:allow"

#: `main.py` 里必须还能看到的接线证据：调了这张表，而不是自己拼句子。
MAIN_MUST_CALL = "http_error_message("


class Problem(Exception):
    """一条违例。`line` 为 0 表示整文件级问题。"""

    def __init__(self, path: Path, line: int, message: str) -> None:
        self.path = path
        self.line = line
        self.message = message

    def render(self) -> str:
        location = self.path.relative_to(ROOT).as_posix()
        if self.line:
            return f"{location}:{self.line}  {self.message}"
        return f"{location}  {self.message}"


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """收集模块级字符串常量，用于解析 `detail=SOME_MESSAGE` 这种写法。

    只认「赋值给名字的字面量」，不做跨模块求值：`detail` 出现在另一个模块的常量里时
    本闸解析不到，会按「无法静态判定」报红——那正是希望发生的，因为那种写法
    意味着文案绕过了本文件，得让人看一眼。
    """
    constants: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        if value is None or not isinstance(value, ast.Constant):
            continue
        if not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def _allowed_literals() -> set[str]:
    """表里所有的标准句 + 别名键：两者任一命中就算「认得」。"""
    return {member.value for member in ErrorMessage} | set(HTTP_DETAIL_ALIASES)


def _is_error_message_attr(node: ast.expr) -> bool:
    """`ErrorMessage.XXX` 形式：直接引用枚举，天然合规。"""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ErrorMessage"
    )


def _http_exception_details(tree: ast.Module) -> list[tuple[int, ast.expr | None]]:
    """所有 `HTTPException(...)` 调用的 `detail=` 实参（位置参数里的是 `status_code`）。"""
    found: list[tuple[int, ast.expr | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "HTTPException":
            continue
        detail = next((kw.value for kw in node.keywords if kw.arg == "detail"), None)
        found.append((node.lineno, detail))
    return found


def _has_allow_marker(lines: list[str], lineno: int) -> bool:
    """标记看本行与上两行（长模板串常见），与 G1 的规则一致。"""
    for offset in range(3):
        index = lineno - 1 - offset
        if 0 <= index < len(lines) and ALLOW_MARKER in lines[index]:
            return True
    return False


def check_file(path: Path, allowed: set[str]) -> list[Problem]:
    relative = path.relative_to(ROOT).as_posix()
    if relative in EXEMPT:
        return []
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    problems: list[Problem] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as error:  # 语法错由 ruff / mypy 负责，这里只说明为什么跳过
        return [Problem(path, error.lineno or 0, f"无法解析（{error.msg}），本闸跳过该文件")]

    constants = _module_string_constants(tree)
    for lineno, detail in _http_exception_details(tree):
        if detail is None or _has_allow_marker(lines, lineno):
            continue
        if _is_error_message_attr(detail):
            continue
        value: str | None = None
        if isinstance(detail, ast.Constant) and isinstance(detail.value, str):
            value = detail.value
        elif isinstance(detail, ast.Name):
            value = constants.get(detail.id)
            if value is None:
                problems.append(
                    Problem(
                        path,
                        lineno,
                        f"`detail={detail.id}` 无法静态判定：文案必须在同一文件的模块级"
                        "字符串常量里，或直接写进 app/core/error_messages.py",
                    )
                )
                continue
        else:
            problems.append(
                Problem(
                    path,
                    lineno,
                    "`detail` 是动态拼出来的（异常文本 / f-string / 函数调用）："
                    "不允许把异常内容当文案上屏，请改用 ErrorMessage 里的句子",
                )
            )
            continue
        if value not in allowed:
            problems.append(
                Problem(
                    path,
                    lineno,
                    f"`detail={value!r}` 不在文案表里：请在 app/core/error_messages.py 的"
                    "ErrorMessage 里加一条，或把它加进 HTTP_DETAIL_ALIASES 作为历史写法的桥",
                )
            )
    return problems


def check_main_wiring() -> list[Problem]:
    """判据 3：出口真的接在这张表上，而不是「表建好了没人用」。

    用 AST 而不是搜字符串：本文件自己的 docstring 里就抄着 `str(exc.detail)` 这行历史错误，
    搜字符串会把说明文字判成违例，最后只能靠改注释换绿——那是本闸最不该逼人做的事。
    """
    if not MAIN_FILE.exists():
        return [Problem(MAIN_FILE, 0, "app/main.py 不存在")]
    source = MAIN_FILE.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [Problem(MAIN_FILE, error.lineno or 0, f"无法解析（{error.msg}）")]

    problems: list[Problem] = []
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    calls_table = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "http_error_message":
            calls_table = True
            continue
        # `str(x.detail)` 只有在**要当消息用**时才是违例：slowapi 的
        # `details={"limit": str(exc.detail)}` 是限流窗口的诊断串（既有用例断言它含「10」），
        # 不是给用户读的句子，一刀切会把合法用法判红，最后只能靠豁免换绿。
        if not (
            isinstance(func, ast.Name)
            and func.id == "str"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Attribute)
            and node.args[0].attr == "detail"
        ):
            continue
        if _is_message_position(node, parents):
            problems.append(
                Problem(
                    MAIN_FILE,
                    node.lineno,
                    "出现 `str(<异常>.detail)` 当消息：异常文本被直接上屏，"
                    "请改用 http_error_message()",
                )
            )
    if not calls_table:
        problems.append(
            Problem(
                MAIN_FILE,
                0,
                f"没找到 `{MAIN_MUST_CALL}`：文案表没有被任何处理器使用，等于一份没人读的文档",
            )
        )
    return problems


def _is_message_position(node: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    """这个 `str(...)` 是「消息」吗：赋给 `message`，或作为 `error_response` 的第 3 个实参。"""
    parent = parents.get(node)
    if isinstance(parent, ast.Assign):
        return any(
            isinstance(target, ast.Name) and target.id == "message" for target in parent.targets
        )
    if isinstance(parent, ast.Call):
        func = parent.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name == "error_response":
            # `error_response(request, code, message, …)`
            return len(parent.args) >= 3 and parent.args[2] is node
    return False


def check_exempt_names() -> list[Problem]:
    """名单防腐：豁免项必须仍然存在，否则名单会变成一张过期名片的收集册。"""
    problems: list[Problem] = []
    for relative, reason in EXEMPT.items():
        if not (ROOT / relative).exists():
            problems.append(
                Problem(ROOT / relative, 0, f"豁免项在磁盘上已不存在（豁免理由：{reason}）")
            )
        if not reason.strip():
            problems.append(Problem(ROOT / relative, 0, "豁免项缺少理由"))
    return problems


def main() -> int:
    allowed = _allowed_literals()
    problems: list[Problem] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        problems.extend(check_file(path, allowed))
    problems.extend(check_main_wiring())
    problems.extend(check_exempt_names())

    if not problems:
        print(
            f"G3 错误文案：{APP_DIR.relative_to(ROOT).as_posix()}/ 下所有 HTTPException 的 detail 都在表内"
        )
        return 0
    print("G3 错误文案：发现以下违例", file=sys.stderr)
    for problem in problems:
        print(f"  {problem.render()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
