"""合并 B5 与 B11a 两条并行迁移分支的 head（ADR-021 / ADR-022）。

`20261112_0018`（组织 / 角色 / 审计）与 `20261112_0019`（面试会话）各自以
`20261111_0017` 为父：它们是两条线在独立 worktree 里同时开发的产物
（`AGENTS.md` §5.4.1）。两条迁移互不依赖，谁先执行都不影响对方，所以本
merge revision 的 `upgrade` / `downgrade` 都是空操作，只负责把两个 head
收敛成一个——**不要**为了「让它做点什么」把任一侧的建表逻辑抄到这里。

`upgrade head` 按拓扑序执行 0019 → 0018 → 本条；**降级必须指定明确 revision**
（如 `downgrade 20261111_0017`）——merge point 上 `downgrade -1` 会报
`Ambiguous walk`，因为 Alembic 无法在两个父之间选。两条业务迁移的
`downgrade()` 各自负责删自己的表。

Revision ID: 20261112_0020
Revises: 20261112_0018, 20261112_0019
Create Date: 2026-11-12
"""

from collections.abc import Sequence

revision: str = "20261112_0020"
down_revision: str | Sequence[str] | None = ("20261112_0018", "20261112_0019")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
