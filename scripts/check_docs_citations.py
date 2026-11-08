"""文档里的 `路径:行号` 引用必须还能指到真实代码（ADR-013 §10 第 10 步的教训）。

为什么是一条脚本 + CI 步骤，而不是一条 pytest：
`tests/conftest.py` 的 session autouse 夹具会连真实 MySQL 建表，而本文件一个字都不读代码运行时——
为一个纯文本检查去开数据库，是把检查的成本做成了它想保护的东西的成本。放在 `scripts/` 还顺带
被现有三道闸覆盖（`ruff format --check` / `ruff check` / `mypy`，见 `ci.yml`）。

管三条硬判据，全部可机械判定：

1. 被引的文件存在；
2. 行号落在文件行数范围内；
3. 区间**首行与末行**都不是空行。

刻意**不管**的一条：区间是否恰好等于被引那个函数。difflib 式的机械平移能保证 1 与 2，
但保证不了语义——第 10 步就是这么写出 12 处「首行是空行」的。语义落点靠人按代码读，
本脚本只在事后把「空行 / 越界 / 文件没了」这类一眼可判的腐坏钉住。

历史段里的引用（已删除的符号、已搬家的函数）应当**去掉行号**只留文件与符号：
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

DOC_FILES = [
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    *sorted((ROOT / "docs").rglob("*.md")),
]


def check() -> list[str]:
    problems: list[str] = []
    cache: dict[str, list[str]] = {}
    total = 0
    for doc in DOC_FILES:
        if not doc.is_file():
            continue
        lines = doc.read_text(encoding="utf-8").splitlines()
        for line_no, text in enumerate(lines, start=1):
            for match in CITATION.finditer(text):
                total += 1
                rel, start = match.group(1), int(match.group(2))
                end = int(match.group(3) or start)
                where = f"{doc.relative_to(ROOT)}:{line_no}"
                if rel not in cache:
                    target = ROOT / rel
                    cache[rel] = (
                        target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
                    )
                body = cache[rel]
                if not body:
                    problems.append(f"{where}  文件不存在：{rel}")
                elif end > len(body) or start > end or start < 1:
                    problems.append(
                        f"{where}  行号越界：{rel}:{start}-{end}（全文 {len(body)} 行）"
                    )
                elif not body[start - 1].strip():
                    problems.append(f"{where}  首行是空行：{rel}:{start}-{end}")
                elif not body[end - 1].strip():
                    problems.append(f"{where}  末行是空行：{rel}:{start}-{end}")
    print(f"文档行号引用：{total} 条，问题 {len(problems)} 处。")
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
