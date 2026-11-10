"""G1 文案闸（`docs/FRONTEND_DESIGN.md` 守则 C1 / C2 + §9 G1，老板 2026-11-09 批准进 CI）。

为什么是一条脚本 + CI 步骤，而不是一条 pytest：与 `scripts/check_docs_citations.py` 同一个理由
——`tests/conftest.py` 的夹具会连真实 MySQL 建表，为一个纯文本检查去开数据库，是把检查的成本
做成了它想保护的东西的成本。放在 `scripts/` 还顺带被现有三道闸覆盖（ruff / mypy / CI）。

它管三件事：

1. **emoji 与被当图标用的符号**（C1）：`✨ 💡 ✓ ✗ ★ ⚠ ← ↑ ↓ ↺ ✦` 之类一律不许上屏。
   要图标请上 SVG（本闸只拦裸符号，不检查「有没有配套 SVG」）。
2. **开发者黑话**（C2）：服务商名、`API Key`/`token`/`JSON`/`JWT`、「模型」「提示词」「非流式」
   「上游」「后端」「服务端」「数据库」「落库」「额度」这类词，以及裸状态码数字。
3. **豁免必须留痕**：`ui-copy:allow <理由>` 写在同一行，或写在**上两行内**（长模板串常见）。
   **没有理由的豁免视同违规**——「加个注释换绿」正是本闸要拦的动作，不是要放行的动作。

**只检查会上屏的东西**，这是本闸的全部精度所在：`.vue` 的模板文本节点、`placeholder`/`title`/
`aria-label` 这类属性值、以及字符串字面量；注释行、`<style>` 块、`console.*`、标识符与存储键
（`'ai-career.access-token'`、`'X-DeepSeek-Key'`）**都不算文案**。两条收窄规则：

- 「黑话」只在**含中文的**字面量里判——`token` 出现在 `const token = …` 里是代码，不是界面。
- 「符号」在任何字面量与模板文本里都判，因为 `✨` 不需要中文上下文就已经是界面的一部分。

它刻意**不管**的两条（写明白，免得把「闸门绿了」当成「文案对了」）：

- 只看像不像人话，不看句子读不读得通。措辞质量归评审与真实用户，本闸拦的是符号与词表。
- **本批只扫前端**。后端上屏字符串（`app/main.py` 的 `message = str(exc.detail)` 那类，未来任何
  一条英文 detail 会裸上屏）**不在覆盖内**，属批次 **F1c**（`docs/ROADMAP.md` §3 X2）。
  把 `app/` 加进 `TARGETS` 之前，「全站禁黑话」这条守则**只对前端成立**。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 扫什么。加一行就扩一块覆盖面——F1c 的落点就是把 `ROOT / "app"` 加进来。
TARGETS: list[tuple[Path, tuple[str, ...]]] = [
    (ROOT / "frontend" / "src", (".vue", ".ts")),
]

#: 不是用户可见物的目录/文件。测试标题里出现旧按钮名不算违例（但应当跟着改准，见交付说明）。
SKIP_DIR_NAMES = {"__tests__", "node_modules", ".git"}
SKIP_FILE_SUFFIXES = (".spec.ts", ".test.ts")

#: C1：emoji 区段 + 被当图标用的符号。区间宁可宽——漏报没人会发现，误报有豁免留痕可解释。
ICON_RANGES = (
    (0x2190, 0x21FF),  # 箭头 ← ↑ ↓ →
    (0x2600, 0x27BF),  # 杂项符号与装饰 ✨ ★ ✓ ✗ ⚠
    (0x2B00, 0x2BFF),  # ⬅ ➡ ⬆ ⬇
    (0xFE0F, 0xFE0F),  # 变体选择符（emoji 上色）
    (0x1F000, 0x1FAFF),  # 成串 emoji
)
ICON_ALIASES = "↺↻✎✏✂⌛⏳❄☀☆✓✔✗✘⚠⚡"

#: C2 词表。**刻意收窄**：只拦「系统在说它自己」的那几种说法，不拦行业词。
#: 理由很具体——这是一个简历/岗位产品，「后端开发工程师」「数据库」是用户会写进简历的**岗位词汇**
#: （`placeholder="如：后端开发工程师"` 不是黑话），把它们一律判红只会逼人给正常文案加豁免，
#: 闸门就废了。所以 `后端`/`服务端`/`数据库` 必须配上系统动词才算违例。
JARGON_TERMS = (
    "DeepSeek",
    "API Key",
    "apikey",
    "access_token",
    "refresh_token",
    "JWT",
    "JSON",
    "token",
    "sessionStorage",
    "localStorage",
    "v-html",
    "落库",
    "模型",
    "提示词",
    "非流式",
    "流式",
    "上游",
    "额度",
    "内存",
    "序列化",
    "兜底",
    "降级",
    "接口返回",
    "缓存",
)
SYSTEM_VOICE_PATTERNS = (
    r"后端(?:在跑|响应|返回|报错|拒绝|拒掉|处理|端)",
    r"由后端",
    r"服务端(?:拿|上限|自动|返回|响应|处理|决定|报错)",
    r"由服务端",
    r"(?:保存到|写入|存进|存到|落进)数据库",
    r"数据库(?:里|中的|自动)",
)
JARGON_RE = re.compile(
    "|".join(
        [
            rf"(?<![\w.]){re.escape(t)}(?![\w.])" if t.isascii() else re.escape(t)
            for t in JARGON_TERMS
        ]
        + list(SYSTEM_VOICE_PATTERNS)
    ),
    re.IGNORECASE,
)
#: 裸状态码上屏（C2）。只在候选文案里判，代码里的 `status === 0` 与 `401` 分支不算。
STATUS_CODE_RE = re.compile(r"(?<![\w.])(?:4\d{2}|5\d{2})(?![\w.])")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
ALLOW_MARKER = "ui-copy:allow"
COMMENT_LINE = re.compile(r"^\s*(?://|/\*|\*+\s|\*/|#|<!--)")
NON_DISPLAY_LINE = re.compile(r"console\.|throw new Error|window\.(?:local|session)Storage")
#: 模板里这些属性的值是给用户看的（占位符、悬浮提示、读屏文本）。
USER_ATTR_RE = re.compile(
    r"""(?::?)\b(?:placeholder|title|aria-label)\s*=\s*"([^"]*)"|"""
    r"""(?::?)\b(?:placeholder|title|aria-label)\s*=\s*'([^']*)'"""
)
TAG_RE = re.compile(r"<[^>]+>")
STRING_LITERAL_RE = re.compile(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`")


#: 守则 §11 登记的**规划期资产**：老板 2026-11-09 明令「纳入路由、不要删、当前不动这个文件」，
#: 所以本闸对它跳过。跳过是**有账的**——批次 F7 改造它那天必须把这一行删空，留着就是欠账。
PLANNING_ASSETS = ("frontend/src/views/DashboardView.vue",)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for base, suffixes in TARGETS:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in suffixes:
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.name.endswith(SKIP_FILE_SUFFIXES):
                continue
            if path.relative_to(ROOT).as_posix() in PLANNING_ASSETS:
                continue
            files.append(path)
    return files


def strip_comments(lines: list[str]) -> list[str]:
    """去掉 `<!-- … -->` 与 `/* … */` 的内容，返回每行「可能上屏」的那部分。

    注释里出现 `降级`、`✨`、旧按钮名都**不上屏**，判红只会逼人给注释加豁免。
    但注释仍然是豁免标记的合法载体（`ui-copy:allow <理由>`），所以标记检测看原始整行。
    """
    out: list[str] = []
    block: str | None = None
    for text in lines:
        code: list[str] = []
        rest = text
        while rest:
            if block:
                close = "-->" if block == "<!--" else "*/"
                index = rest.find(close)
                if index < 0:
                    rest = ""
                    continue
                rest = rest[index + len(close) :]
                block = None
                continue
            opens = [(rest.find(token), token) for token in ("<!--", "/*") if rest.find(token) >= 0]
            if not opens:
                code.append(rest)
                break
            position, token = min(opens)
            code.append(rest[:position])
            rest = rest[position + len(token) :]
            block = token
        out.append("".join(code))
    return out


def display_chunks(text: str, in_template: bool) -> list[str]:
    """把一行里「会上屏」的部分挑出来。挑不出东西的行返回空表，不参与判定。"""
    if COMMENT_LINE.match(text) or NON_DISPLAY_LINE.search(text):
        return []
    if in_template:
        attrs = next((m.groups() for m in USER_ATTR_RE.finditer(text) if any(m.groups())), ())
        stripped = TAG_RE.sub(" ", text)
        chunks = [value for value in attrs if value]
        chunks.append(stripped)
        kept: list[str] = []
        for chunk in chunks:
            if not chunk.strip():
                continue
            # 跨行标签会让属性值同时留在 `stripped` 里；被包含的那块不重复报。
            if any(chunk is not other and chunk in other for other in chunks):
                continue
            kept.append(chunk)
        return kept
    literals = [next((p for p in parts if p), "") for parts in STRING_LITERAL_RE.findall(text)]
    return [literal for literal in literals if literal]


def judge(chunk: str) -> list[str]:
    hits: list[str] = []
    for char in chunk:
        point = ord(char)
        if char in ICON_ALIASES or any(lo <= point <= hi for lo, hi in ICON_RANGES):
            hits.append(f"图标符号 `{char}`（C1）")
            break
    if CJK_RE.search(chunk):  # 黑话只在含中文的候选里判：`const token = …` 是代码不是界面
        jargon = JARGON_RE.search(chunk)
        if jargon:
            hits.append(f"黑话 `{jargon.group(0)}`（C2）")
        status = STATUS_CODE_RE.search(chunk)
        if status:
            hits.append(f"裸状态码 `{status.group(0)}`（C2）")
    return hits


def collect(lines: list[str], is_vue: bool) -> tuple[list[str], int]:
    problems: list[str] = []
    exempted = 0
    code_lines = strip_comments(lines)
    in_script = in_style = False
    for index, (text, code_text) in enumerate(zip(lines, code_lines), start=1):
        if "<script" in text:
            in_script = True
        if "</script>" in text:
            in_script = False
        if "<style" in text:
            in_style = True
        if "</style>" in text:
            in_style = False
            continue
        if in_style or not code_text.strip():
            continue
        # .ts 文件没有模板区；.vue 的 `<script>` 里只认字符串字面量，其余按模板文本处理。
        in_template = is_vue and not in_script
        hits = [hit for chunk in display_chunks(code_text, in_template) for hit in judge(chunk)]
        if not hits:
            continue
        window = lines[max(0, index - 3) : index]
        if any(ALLOW_MARKER in row for row in window):
            marker_row = next(row for row in reversed(window) if ALLOW_MARKER in row)
            reason = marker_row.split(ALLOW_MARKER, 1)[1].strip(" :：-*—>").rstrip("-").strip()
            if len(reason) >= 6:
                exempted += 1
                continue
            problems.append(f"第 {index} 行的豁免没有理由（{marker_row.strip()[:24]}……）")
            continue
        for hit in hits:
            problems.append(f"第 {index} 行 {hit} → {text.strip()[:76]}")
    return problems, exempted


def check() -> list[str]:
    problems: list[str] = []
    exempted_total = 0
    files = iter_files()
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        found, exempted = collect(lines, path.suffix == ".vue")
        exempted_total += exempted
        problems.extend(f"{path.relative_to(ROOT)}:{item}" for item in found)
    print(f"界面文案扫描：{len(files)} 个文件，豁免 {exempted_total} 处，问题 {len(problems)} 处。")
    for line in problems:
        print("  ", line)
    if problems:
        print(
            "   ※ 覆盖范围仅前端（见 TARGETS）；后端上屏文案属批次 F1c，见 docs/ROADMAP.md §3 X2。"
        )
    return problems


if __name__ == "__main__":
    sys.exit(1 if check() else 0)
