"""Add `theme` to student_profiles (Phase 2 · D5.2).

ADR-007 决策三（方案 A：固定预设）：主题**跟着简历走**，与 `paper_size` 同一层
——「同一份内容投 A 岗用青绿、投 B 岗用藏青」是同一时刻的两种需求，
放在用户级配置里表达不了。

只加一列，`VARCHAR(20) NOT NULL DEFAULT 'forest'`：

- 存的是预设 id（`forest` / `ink` / …），不是 hex：8 套配色在
  `app/core/document_style.py` 里静态定义，对比度是一次性离线保证的；
  存 hex 就等于允许任意颜色，浅色在白纸上看不清（ADR-007 §5.2）。
- `VARCHAR(20)` 而不是 `ENUM`：加一套主题只改代码不改表；
  非法值由 API 层 `Literal` 拦成 422，读路径再宽松兜底回 `forest`。
- `server_default='forest'` 让存量行在 `ADD COLUMN` 那一刻就有值，
  不需要额外 `UPDATE`；旧客户端不带这个字段时库里也有兜底。

**downgrade 是无损的**：只丢掉用户的配色选择，内容本身不动。

Revision ID: 20261103_0009
Revises: 20261103_0008
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261103_0009"
down_revision: str | Sequence[str] | None = "20261103_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "student_profiles",
        sa.Column(
            "theme",
            sa.String(20),
            nullable=False,
            server_default="forest",
        ),
    )


def downgrade() -> None:
    op.drop_column("student_profiles", "theme")
