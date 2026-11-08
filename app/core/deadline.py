"""`jobs.deadline` 的唯一过期判据（ADR-011 2.G / ADR-013 §7.5）。

为什么住在 `app/core`：岗位读侧（`JobService.is_expired`）与人岗匹配的纯打分层
（`app/services/match_scoring`）要的是**同一个**比较。判据写在 service 里，另一条路就只能
再抄一遍 `deadline < today`——而 ADR-013 §7.5 明确禁止两处各写一份。
`core` 零依赖、谁都能 import，反向（core import services）才被契约挡住；
同一个位置先例是 `app/core/skill_names.py`（为完全相同的理由搬进来）。
"""

from __future__ import annotations

from datetime import date


def is_expired(deadline: date | None, today: date) -> bool:
    """2.G 的唯一判据：`deadline` 非空且**严格早于**今天。

    `deadline = 今天` 还没过期（「今天截止」仍可投递）；`deadline IS NULL` 长期有效，
    因此**不是**过期——匹配侧要区分「过期」与「没有截止时间」，见 ADR-013 §7.5。
    """
    return deadline is not None and deadline < today
