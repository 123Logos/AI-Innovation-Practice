"""Add `paper_size` to student_profiles (Phase 2 · D5.1).

ADR-007 决策二：纸张尺寸跟着**简历**走（不是跟用户），投国内用 A4、
投北美用 Letter 是同一时刻的两种需求，用户级配置表达不了。

只加一列，`VARCHAR(16) NOT NULL DEFAULT 'A4'`：

- 用 `VARCHAR` 而不是 MySQL `ENUM`：加一种纸只要改代码，不用改表；
  非法值的拦截放在 API 层（pydantic `Literal["A4", "Letter"]` → 422），
  与「写入严格、读取宽松」的既定口径一致（README 10.4）。
- `server_default='A4'` 让存量行在 `ADD COLUMN` 那一刻就有值，
  不需要额外的 `UPDATE`；同时保证「旧客户端不带这个字段」时库里有兜底。

**downgrade 是无损的**：只丢掉用户的纸张选择，内容本身不动。
（与 0007 的降级不同，那条是「多份简历 → 一份」，必然丢数据。）

Revision ID: 20261103_0008
Revises: 20261103_0007
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261103_0008"
down_revision: str | Sequence[str] | None = "20261103_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "student_profiles",
        sa.Column(
            "paper_size",
            sa.String(16),
            nullable=False,
            server_default="A4",
        ),
    )


def downgrade() -> None:
    op.drop_column("student_profiles", "paper_size")
