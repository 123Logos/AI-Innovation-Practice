"""Drop the redundant `student_profiles.major` column (T7.6).

专业已由 `educations.major` 精确表达（一段学历一个专业）。主表再存一份会
导致用户重复填写、简历上信息重复，因此彻底移除主表列。
**本迁移不动 `educations.major`。**

Revision ID: 20261102_0003
Revises: 20261102_0002
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261102_0003"
down_revision: str | Sequence[str] | None = "20261102_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 该列没有索引、没有 CHECK、也没有外键依赖，MySQL 可直接 DROP。
    op.drop_column("student_profiles", "major")


def downgrade() -> None:
    # 为了可逆：旧列以可空形式加回。原值已随 DROP 丢失，
    # 且业务上不再写入该字段，故无需回填。
    op.add_column("student_profiles", sa.Column("major", sa.String(100), nullable=True))
