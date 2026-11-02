"""Create the educations table (T7.5).

教育经历是一对多子表：一个学生可以有多段学历，字段对齐学信网备案表
与国家大学生就业服务平台的教育经历区块（学校 / 专业 / 学历 / 起止时间）。

Revision ID: 20261102_0002
Revises: 20261023_0001
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261102_0002"
down_revision: str | Sequence[str] | None = "20261023_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_args() -> dict[str, str]:
    return {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_0900_ai_ci",
    }


def upgrade() -> None:
    op.create_table(
        "educations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("student_profile_id", sa.String(36), nullable=False),
        sa.Column("school", sa.String(200), nullable=False),
        sa.Column("major", sa.String(100), nullable=False),
        sa.Column("degree", sa.String(50), nullable=False),
        sa.Column("edu_start", sa.Date(), nullable=False),
        sa.Column("edu_end", sa.Date(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        # 学历枚举与产品口径一致；字符串列 + CHECK，避免 MySQL ENUM 难以演进。
        sa.CheckConstraint(
            "degree IN ('专科', '本科', '硕士', '博士')",
            name="ck_educations_degree",
        ),
        sa.CheckConstraint(
            "edu_end IS NULL OR edu_end >= edu_start",
            name="ck_educations_date_range",
        ),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_educations_student_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        **_table_args(),
    )
    op.create_index("ix_educations_student_profile_id", "educations", ["student_profile_id"])


def downgrade() -> None:
    # MySQL 在删表时一并回收外键与其依赖的索引；显式 DROP INDEX 反而会报
    # 1553（index needed in a foreign key constraint），所以只删表。
    op.drop_table("educations")
