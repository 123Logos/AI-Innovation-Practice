"""Add `student_profiles.email` (user feedback round).

`users.email` 是**登录邮箱**；档案上的 email 是**简历上展示的邮箱**，
两者可能不同（比如简历上写常用邮箱、登录用另一个）。因此独立成列，可空。

Revision ID: 20261102_0004
Revises: 20261102_0003
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261102_0004"
down_revision: str | Sequence[str] | None = "20261102_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 254 = RFC 5321 对邮箱地址长度的上限，与 `users.email` 保持一致。
    op.add_column("student_profiles", sa.Column("email", sa.String(254), nullable=True))


def downgrade() -> None:
    op.drop_column("student_profiles", "email")
