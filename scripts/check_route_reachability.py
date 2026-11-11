"""G5 路由可达性：`frontend/src/views/` 下不许有没登记、也没挂路由的视图文件（守则 L6）。

判据一句话：视图文件要么被 `frontend/src/router/index.ts` 引用（= 用户走得到），
要么出现在下面的豁免名单里（= 守则 §11 登记的**规划期资产**），否则按死代码报红。
两类都不占的，就是一份谁都不会渲染、却会跟着每次重构一起改的孤儿模板。

为什么是一条脚本 + 一道 CI 步骤，而不是一条 vitest：
与 `scripts/check_docs_citations.py` 同一个理由——纯文本检查不该为了跑起来去开数据库，
而 `tests/conftest.py` 的会话夹具会连真实 MySQL。放在 `scripts/` 还顺带被
`ruff format --check` / `ruff check` / `mypy` 三道既有闸覆盖。

为什么**必须**带豁免名单（守则 §9 G5 那行的原话：「没有名单它会第一天就误删规划资产」）：
`views/DashboardView.vue` 是老板 2026-11-09 点名「纳入路由，不要删」的双态首屏，
现在还没挂路由（归批次 F7）。没有这份名单，本脚本上线第一天就会把它判成死代码，
而「清一下死代码」恰恰是最没人会拦的动作。名单即判据：每一项都必须写清它归哪一批。

状态（2026-11-11）：**已接进 CI**（`.github/workflows/ci.yml` 的 `Route reachability check (G5)`）。
老板第 13 轮批准 G3/G4/G5 全部进 CI 且必须带豁免名单；G5 是其中立刻生效的一道。
上线当天用两个探针验过它会红：① 临时放一个无路由无引用的视图进 `views/`；
② 把视图目录换成一份缺 `DashboardView.vue` 的副本（名单防腐那条）。探针跑完即删。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEWS_DIR = ROOT / "frontend" / "src" / "views"
ROUTER_FILE = ROOT / "frontend" / "src" / "router" / "index.ts"
SRC_DIR = ROOT / "frontend" / "src"

#: 任意源文件里的视图导入（`@/views/Foo.vue`）。`from` 前面不区分，路由与组件同一套读法。
VIEW_IMPORT = re.compile(r"@/views/([A-Za-z0-9_-]+\.vue)")

#: 守则 §11 登记的规划期资产：文件名 → 「它是谁、为什么现在没挂路由、归哪一批」。
#: 删掉一项 = 断言该视图已挂路由或已删除；加一项 = 必须同时去守则 §11 登记，两边不能各说各话。
UNROUTED_PLANNED_ASSETS: dict[str, str] = {
    "DashboardView.vue": (
        "Dashboard 双态首屏（访客态 + 已登录态），老板 2026-11-09 定性「纳入路由，不要删」；"
        "尚未与项目视觉体系对齐，改造归批次 F7（登记见 docs/FRONTEND_DESIGN.md §11）"
    ),
}


def _imports_in(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return set(VIEW_IMPORT.findall(path.read_text(encoding="utf-8")))


def check() -> tuple[list[str], list[str]]:
    """返回 (问题, 说明)。说明是豁免项的去处，打出来让人知道名单在保谁。"""
    problems: list[str] = []
    notes: list[str] = []

    if not VIEWS_DIR.is_dir():
        return [f"视图目录不存在：{VIEWS_DIR.relative_to(ROOT)}"], notes
    if not ROUTER_FILE.is_file():
        return [f"路由文件不存在：{ROUTER_FILE.relative_to(ROOT)}"], notes

    on_disk = sorted(path.name for path in VIEWS_DIR.glob("*.vue"))
    if not on_disk:
        return [f"视图目录里一个 .vue 都没有：{VIEWS_DIR.relative_to(ROOT)}"], notes

    routed = _imports_in(ROUTER_FILE)

    # 除路由外还有谁引用了它：只用于把报错写得能行动（「被某组件引用但仍无路由」
    # 与「谁都不引用的孤儿」是两件事，处置方式不同）。
    referenced_elsewhere: dict[str, list[str]] = {}
    for source in sorted(SRC_DIR.rglob("*")):
        if not source.is_file() or source.suffix not in {".ts", ".vue"}:
            continue
        if source == ROUTER_FILE or VIEWS_DIR in source.parents:
            continue
        for name in _imports_in(source):
            referenced_elsewhere.setdefault(name, []).append(str(source.relative_to(ROOT)))

    for name in on_disk:
        if name in routed:
            continue
        if name in UNROUTED_PLANNED_ASSETS:
            notes.append(f"豁免（规划期资产）：views/{name} —— {UNROUTED_PLANNED_ASSETS[name]}")
            continue
        where = referenced_elsewhere.get(name, [])
        if where:
            problems.append(
                f"views/{name} 被 {'、'.join(where)} 引用，但没有任何路由指向它 —— "
                "要么挂上路由并在此处除名，要么连引用一起删掉"
            )
        else:
            problems.append(
                f"views/{name} 既无路由也无引用 —— 死代码：删掉它，"
                "或按守则 L6 去 docs/FRONTEND_DESIGN.md §11 登记再加进本文件的名单"
            )

    # 名单本身也要防腐：豁免一项却找不到文件，说明它已经改名/删除而名单没人回来清。
    for name in UNROUTED_PLANNED_ASSETS:
        if name not in on_disk:
            problems.append(f"豁免名单里的 views/{name} 已经不在磁盘上了 —— 请把这一项从名单里删掉")

    return problems, notes


def main() -> int:
    problems, notes = check()
    for note in notes:
        print(f"  {note}")
    print(f"视图可达性：{len(notes)} 项在册豁免，问题 {len(problems)} 处。")
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
