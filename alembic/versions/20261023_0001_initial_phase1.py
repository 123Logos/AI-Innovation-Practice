"""Create the Phase 1 resume schema.

Revision ID: 20261023_0001
Revises:
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261023_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_args() -> dict[str, str]:
    return {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
        **_table_args(),
    )
    op.create_table(
        "student_profiles",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("full_name", sa.String(100), nullable=False),
        sa.Column("major", sa.String(100), nullable=False),
        sa.Column("grade", sa.String(50), nullable=False),
        sa.Column("contact", sa.String(255), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_student_profiles_user_id", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
        **_table_args(),
    )
    op.create_table(
        "skills",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("student_profile_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("self_rating", mysql.TINYINT(unsigned=True), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint("self_rating BETWEEN 1 AND 5", name="ck_skills_self_rating"),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_skills_student_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_profile_id", "name", name="uq_skills_profile_name"),
        **_table_args(),
    )
    op.create_index("ix_skills_student_profile_id", "skills", ["student_profile_id"])
    op.create_table(
        "experiences",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("student_profile_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("responsibility", sa.Text(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_experiences_date_range",
        ),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_experiences_student_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        **_table_args(),
    )
    op.create_index("ix_experiences_student_profile_id", "experiences", ["student_profile_id"])
    op.create_table(
        "resumes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("student_profile_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content_html", mysql.LONGTEXT(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_resumes_student_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        **_table_args(),
    )
    op.create_index("ix_resumes_student_profile_id", "resumes", ["student_profile_id"])


def downgrade() -> None:
    op.drop_table("resumes")
    op.drop_table("experiences")
    op.drop_table("skills")
    op.drop_table("student_profiles")
    op.drop_table("users")
