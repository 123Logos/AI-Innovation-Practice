"""文档里的代码引用必须**还能指到它说的那件事**（`ADR-013` §10 第 10 步的教训，v2）。

v1 管三条硬判据：文件存在、行号不越界、区间首末行非空。它漏掉了这一批真正烧到我们的那一类
——「行号还在、那一行已经不是文档说的事」：批次 B1/B2/B3/F1a/F1b/F2 合并后代码整体位移，
CI 全绿，而 `docs/` 里几十条指针指到了别的函数身上。v1 看不见，因为它压根不知道文档在说哪个符号。

v2 按老板 2026-11-10 第 11 轮裁定加两条判据，并明确**不选**「逐条重指行号」这条路：

1. **符号锚点**：写成 `` `path::symbol` `` 的引用，符号必须能在被引文件里找到**定义**
   （`def` / `class` / `function` / `const` / `let` / `export` / 模块级赋值）。找不到就红。
2. **同行点名的符号必须落在区间里**：写成 `` `path:12-34` 的 `foo()` `` 这种，文档在同一点名了
   `foo`，那 `foo` 的定义就必须出现在 12-34 之间；若它在同一文件的别处，报「漂到第几行」。
   只有「该符号在这个文件里根本没有定义」才放过——那说明点名的不是符号（是 CSS 类名、
   SQL 片段、枚举字面量之类），不是本判据要管的东西。这一条是防止「拿假符号糊过去」的：
   它只在文件里确实有这个定义时才生效，所以闸不会因为文档写了个生造的名字而变红，
   也不会因为文档写了个真符号而放掉漂移。
3. **易变文件不许留行号**：`VOLATILE_PATHS` 里的文件（被多个批次反复改过的那批），
   引用一律必须写成 `path::symbol`；写成 `path:NN` 直接红。

为什么不从 git 历史里算「易变性」：CI 的 checkout 是浅克隆（`actions/checkout` 默认 depth=1），
`git log` 在那儿拿不到历史，任何依赖历史的判据在 CI 上会**恒真**——那正是本项目明令禁止的假闸。
所以名单显式维护在下面，加谁、什么时候加，写在注释里。

历史段里的引用（已删除的符号、已搬家的函数）应当去掉行号只留文件与符号：
行号是一种会过期的指针，历史结论不该依赖它。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 反引号里的 `路径:行号` 或 `路径:起-止`。前缀白名单即「本仓库的代码与文档」。
CITATION = re.compile(
    r"`((?:app|alembic|scripts|tests|docs|frontend/src|frontend/tests)/"
    r"[A-Za-z0-9_./-]+?\.(?:py|ts|vue|js|mjs|sql|md|toml|ini|ya?ml))"
    r":(\d+)(?:-(\d+))?`"
)

#: 新形式：`路径::符号`。符号段允许 `.` 连接（`Foo.bar` 取末段查定义）。
SYMBOL_CITATION = re.compile(
    r"`((?:app|alembic|scripts|tests|docs|frontend/src|frontend/tests)/"
    r"[A-Za-z0-9_./-]+?\.(?:py|ts|vue|js|mjs|sql|md|toml|ini|ya?ml))"
    r"::([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)`"
)

#: 只有这三种形态算「点名了代码符号」，其余反引号内容是文案、类名、枚举值。
_SYMBOL_HINT = re.compile(r"(?:的|由|在|进|走|读|写)\s*`([A-Za-z_][A-Za-z0-9_]{2,})(?:\(\))?`")

#: 定义位置（用于「符号是否存在」与「它在哪一行」）。多语言并集，宁可少判不可误判。
DEF_PATTERNS = (
    re.compile(r"^\s*(?:async\s+)?def\s+(%s)\b", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(%s)\b", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(%s)\b", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(%s)\s*[:=]", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+(%s)\b", re.MULTILINE),
    re.compile(r"^(%s)\s*=\s*[^=]", re.MULTILINE),  # Python 模块级常量：顶格的 `NAME = ...`
)

#: 易变文件：被两个及以上批次反复改过，行号在这些文件上就是会过期的指针。
#: 维护规则：谁在批次里第二次改到某个文件、且 `docs/` 有引用它，就把它的**完整路径**加进来；
#: 加的时候顺手把该文件的引用改成 `path::symbol`，别让闸红着过夜。
VOLATILE_PATHS = frozenset(
    {
        # 这一批（F1a 文案清扫 / F1b 弹层 / F2 布局收敛）连续三轮改到的文件。
        "frontend/src/views/JobBoardView.vue",
        "frontend/src/views/JobCreateView.vue",
        "frontend/src/views/JobDetailView.vue",
        "frontend/src/views/JobTrashView.vue",
        "frontend/src/views/RecycleBinView.vue",
        "frontend/src/views/ResumeEditorView.vue",
        "frontend/src/components/AppTopBar.vue",
        "frontend/src/components/ResumePreview.vue",
        "frontend/src/components/StylePanel.vue",
        "frontend/src/stores/resume.ts",
        "frontend/src/stores/job.ts",
        "frontend/src/api/client.ts",
        # 后端这一批改到的匹配链。
        "app/services/match_service.py",
        "app/services/match_scoring.py",
        "app/schemas/match.py",
        "app/services/resume_service.py",
        "app/integrations/resume_generator.py",
    }
)

DOC_FILES = [
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    *sorted((ROOT / "docs").rglob("*.md")),
]


def definition_line(body: list[str], name: str) -> int | None:
    """符号在该文件里第一次出现「定义」的行号（1-based），找不到返回 None。"""
    for pattern in DEF_PATTERNS:
        rx = re.compile(pattern.pattern.replace("%s", re.escape(name)))
        for idx, line in enumerate(body, start=1):
            if rx.search(line):
                return idx
    return None


#: 同一行里点名的裸文件名引用：`` `AppTopBar.vue:265-284` ``。
#: v1 看不见这一类，而 `docs/FRONTEND_DESIGN.md` 的证据列恰恰就是这么写的——
#: 不补上，(b)+(c) 等于留了一条「少写目录就绕过」的旁路。
BARE_CITATION = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:py|ts|vue|js|mjs|sql)):(\d+)(?:-(\d+))?`"
)

#: 同一行点名的符号：`` `foo()` `` 或 `` `foo` ``。
_ANCHOR_HINT = re.compile(r"(?:的|由|在|进|走|读|写)\s*`([A-Za-z_][A-Za-z0-9_]{2,})(?:\(\))?`")

#: 裸文件名的解析缓存（同名文件在两个目录都出现时不猜，直接放过并计入歧义数）。
_index: dict[str, str | None] = {}


def resolve_bare(name: str) -> str | None:
    """把 `AppTopBar.vue` 解析成仓库内的唯一路径；有歧义或找不到返回 None。"""
    if name in _index:
        return _index[name]
    hits = [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob(name)
        if path.is_file()
        and "node_modules" not in path.parts
        and ".qoder" not in path.parts
        and "dist" not in path.parts
    ]
    _index[name] = hits[0] if len(hits) == 1 else None
    return _index[name]


def check() -> tuple[list[str], dict[str, int]]:
    problems: list[str] = []
    cache: dict[str, list[str]] = {}
    counts = {"line": 0, "symbol": 0, "anchored": 0, "bare": 0}

    def body_of(rel: str) -> list[str] | None:
        if rel not in cache:
            target = ROOT / rel
            cache[rel] = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
        return cache[rel] or None

    for doc in DOC_FILES:
        if not doc.is_file():
            continue
        where = f"{doc.relative_to(ROOT)}"
        lines = doc.read_text(encoding="utf-8").splitlines()
        for line_no, text in enumerate(lines, start=1):
            at = f"{where}:{line_no}"

            # --- 裸文件名形式（`AppTopBar.vue:265`）：先解析成真实路径，再走同一套判据 ---
            for bare in BARE_CITATION.finditer(text):
                counts["bare"] += 1
                rel = resolve_bare(bare.group(1))
                if rel is None:
                    problems.append(
                        f"{at}  裸文件名解析不了：{bare.group(1)}（不存在或有歧义，请写完整路径）"
                    )
                    continue
                body = body_of(rel)
                if body is None:
                    problems.append(f"{at}  文件不存在：{rel}")
                    continue
                start, end = int(bare.group(2)), int(bare.group(3) or bare.group(2))
                if end > len(body) or start < 1:
                    problems.append(f"{at}  行号越界：{rel}:{start}-{end}（全文 {len(body)} 行）")
                    continue
                if not body[start - 1].strip() or not body[end - 1].strip():
                    problems.append(f"{at}  区间首末行是空行：{rel}:{start}-{end}")
                    continue
                if rel in VOLATILE_PATHS:
                    problems.append(
                        f"{at}  易变文件不许留行号：{rel}:{start}-{end} → 改写成 `{bare.group(1)}::符号`"
                    )

            # --- 形式一：path::symbol —— 符号必须有定义 ---
            for match in SYMBOL_CITATION.finditer(text):
                counts["symbol"] += 1
                rel, symbol = match.group(1), match.group(2).split(".")[-1]
                body = body_of(rel)
                if body is None:
                    problems.append(f"{at}  文件不存在：{rel}")
                    continue
                if definition_line(body, symbol) is None:
                    problems.append(f"{at}  符号没有定义：{rel}::{symbol}")

            # --- 形式二：path:NN —— 三条老判据 + 锚点 + 易变名单 ---
            for match in CITATION.finditer(text):
                counts["line"] += 1
                rel, start = match.group(1), int(match.group(2))
                end = int(match.group(3) or start)
                body = body_of(rel)
                if body is None:
                    problems.append(f"{at}  文件不存在：{rel}")
                    continue
                if end > len(body) or start > end or start < 1:
                    problems.append(f"{at}  行号越界：{rel}:{start}-{end}（全文 {len(body)} 行）")
                    continue
                if not body[start - 1].strip():
                    problems.append(f"{at}  首行是空行：{rel}:{start}-{end}")
                    continue
                if not body[end - 1].strip():
                    problems.append(f"{at}  末行是空行：{rel}:{start}-{end}")
                    continue

                # 同行点名的符号：只认「动词 + `foo`」这种明确指向代码的写法，
                # 且该符号在文件里确有定义，才要求它落在区间内。
                # 注意这一段**必须走在易变名单之前**：名单会把行号式引用判掉，
                # 若在这里先 continue，锚点判据就永远扫不到东西——一条恒为 0 的检查就是假闸。
                for hint in _SYMBOL_HINT.finditer(text[match.end() : match.end() + 260]):
                    name = hint.group(1)
                    if name in ("null", "true", "false", "None"):
                        continue
                    located = definition_line(body, name)
                    if located is None:
                        continue
                    counts["anchored"] += 1
                    if not start <= located <= end:
                        problems.append(
                            f"{at}  锚点漂移：{rel}:{start}-{end} 说它是 {name}()"
                            f"，而 {name} 的定义在第 {located} 行"
                        )

                if rel in VOLATILE_PATHS:
                    problems.append(
                        f"{at}  易变文件不许留行号：{rel}:{start}-{end} → 改写成 `{rel}::符号`"
                    )
    return problems, counts


def main() -> int:
    problems, counts = check()
    print(
        f"文档代码引用检查：显式路径行号式 {counts['line']} 条、符号式 {counts['symbol']} 条、"
        f"裸文件名式 {counts['bare']} 条、锚点核过 {counts['anchored']} 处，"
        f"问题 {len(problems)} 处。"
    )
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
