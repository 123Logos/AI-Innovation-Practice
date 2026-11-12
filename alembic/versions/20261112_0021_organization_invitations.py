"""Create organization_invitations (B6 · FR-01, ADR-023).

组织邀请码与 `organizations.code` 是两样东西：后者长期、不限次、不可撤销，
前者带过期时间、可用次数、撤销标记与发起人（`ADR-023` §2.1）。

索引只建 `(organization_id, created_at)` 一条复合索引，对应「按组织倒序列邀请」
这一个真实查询；**刻意不建 `(created_at)` 单列索引**——本批没有跨组织的
「全平台邀请」查询，`tests/test_migrations.py` 那条判据也同时挡着它。

Revision ID: 20261112_0021
Revises: 20261112_0020
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261112_0021"
down_revision: str | Sequence[str] | None = "20261112_0020"
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
        "organization_invitations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="student"),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("revoked_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        # 邀请码刻意不能造出第二个校长：提权要经「改成员角色」端点并留审计。
        sa.CheckConstraint(
            "role IN ('student', 'teacher')", name="ck_organization_invitations_role"
        ),
        sa.CheckConstraint(
            "max_uses >= 1 AND max_uses <= 100",
            name="ck_organization_invitations_max_uses",
        ),
        sa.CheckConstraint("used_count >= 0", name="ck_organization_invitations_used_count"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_invitations_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_organization_invitations_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_organization_invitations_code"),
        **_table_args(),
    )
    op.create_index(
        "ix_organization_invitations_organization_created",
        "organization_invitations",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    # MySQL 在删表时一并回收索引与唯一键，显式 DROP INDEX 反而会报 1553。
    op.drop_table("organization_invitations")
