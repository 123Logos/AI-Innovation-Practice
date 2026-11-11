"""Profile multi-value subtables + resume version pointer (B4 + B8, ADR-015).

契约见 `docs/adr/ADR-015-profile-multivalue-and-resume-version-pointer.md` §2.1 / §2.3 / §5。
一次迁移动 7 张表：

1. **三张新子表**（列名即契约，不留占位）：
   - `profile_certificates`：name VARCHAR(100) NOT NULL / issuer VARCHAR(100) NULL /
     issued_on DATE NULL；
   - `portfolio_links`：label VARCHAR(100) NOT NULL / url VARCHAR(500) NOT NULL；
   - `profile_city_preferences`：city VARCHAR(50) NOT NULL。**期望城市不是简历区块**，
     不进 `content_html`（ADR-015 §5 ③：它是投递元数据）。
   外键一律 `ON DELETE RESTRICT`，删父行靠应用层按 `_CHILD_TABLES` 显式清子行。
   上限（证书 5 / 链接 5 / 城市 5）在 API schema 层，与 educations 同档——一页 A4。

2. **现有三张子表转「全量替换 + 软删除」**（skills / experiences / educations）：
   加 `deleted_at DATETIME(6) NULL`；PUT 时活跃行盖戳再插新行，不再物理 DELETE。
   每张表加 STORED 生成列 `active_marker AS (IF(deleted_at IS NULL, 1, NULL))`，
   唯一键改成含 `active_marker` 的三元组。MySQL 唯一索引允许多个 NULL：
   历史行自由重复，活跃行仍逐字唯一——「删掉 Python 再加回 Python」不再撞 1062。
   experiences / educations 本来就没有唯一键（取证：`20261023_0001` 与
   `20261102_0002`），所以只有 `uq_skills_profile_name` 需要改造。

3. **`resumes` 版本指针**（ADR-015 §2.3）：
   - `version_no INT NOT NULL DEFAULT 1`；
   - `current_version_id VARCHAR(36) NULL`，FK → `resumes.id` **ON DELETE SET NULL**
     ——全仓唯一非 RESTRICT 的外键，偏离是刻意的：链头行本身可能被删，RESTRICT 会
     变成「有指针就删不掉行」的死锁；SET NULL 让其余行指针落空，而**指针落空时读侧
     必须显式报错**，不许回落到「挑一条看起来最新的」；
   - 生成列 `is_head AS (IF(id = current_version_id, 1, NULL))` +
     `UNIQUE(student_profile_id, is_head)`：每个档案至多一条链头。
   **一处对 ADR 字面的必要偏离（实测，MySQL 8.0.43）**：ADR 写的是 `STORED`，但
   MySQL 禁止「FK 的引用列被 STORED 生成列使用」时对该 FK 用 `ON DELETE SET NULL`
   （errno 1215，实测 RESTRICT 可以、SET NULL/CASCADE 不行）。SET NULL 是 ADR 的
   硬契约（链头可删 + 指针落空显式报错），生成列的存储形态不是——所以 `is_head`
   落为 **VIRTUAL**：表达式、唯一键、链头语义逐字不变，InnoDB 对虚拟列上的唯一
   索引同样强制（实测第二条链头撞 1062、删链头后其余行指针被置 NULL）。
   子表的 `active_marker` 仍是 STORED：它们的外键是 RESTRICT，不受这条限制。
   **存量行不回填指针**：按 `created_at` 挑链头正是 ADR 明令禁止的猜法，所以旧行
   指针保持 NULL，下一次生成时由 `create_resume` 在同一事务里把全链指向新行。
   已存在简历的 `content_html` 是不可变快照，不重渲染、不改写、不迁移。

**downgrade 是有损的**：软删除积累的历史行（`deleted_at IS NOT NULL`）会被物理清掉
（否则恢复 `UNIQUE(student_profile_id, name)` 时历史行与活跃行同名相撞）；三张新表
整表删除。降级前请自行备份。

Revision ID: 20261110_0016
Revises: 20261109_0015
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261110_0016"
down_revision: str | Sequence[str] | None = "20261109_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_args() -> dict[str, str]:
    return {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_0900_ai_ci",
    }


def _active_marker_column() -> sa.Column:
    """STORED 生成列：活跃行 = 1，历史行 = NULL（唯一键的第三个成员）。"""
    return sa.Column(
        "active_marker",
        sa.Integer(),
        sa.Computed("IF(deleted_at IS NULL, 1, NULL)", persisted=True),
        nullable=True,
    )


def _deleted_at_column() -> sa.Column:
    return sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True)


def upgrade() -> None:
    # 1) 老子表：软删除两列。skills 的唯一键改造成含 active_marker 的三元组。
    op.add_column("skills", _deleted_at_column())
    op.add_column("skills", _active_marker_column())
    op.drop_constraint("uq_skills_profile_name", "skills", type_="unique")
    op.create_unique_constraint(
        "uq_skills_profile_name", "skills", ["student_profile_id", "name", "active_marker"]
    )
    op.add_column("experiences", _deleted_at_column())
    op.add_column("experiences", _active_marker_column())
    op.add_column("educations", _deleted_at_column())
    op.add_column("educations", _active_marker_column())

    # 2) 三张新子表（ADR-015 §2.1 / §5 ③）。
    op.create_table(
        "profile_certificates",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("student_profile_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("issuer", sa.String(100), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=True),
        _deleted_at_column(),
        _active_marker_column(),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_profile_certificates_student_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "student_profile_id",
            "name",
            "active_marker",
            name="uq_profile_certificates_profile_name",
        ),
        **_table_args(),
    )
    op.create_index(
        "ix_profile_certificates_student_profile_id",
        "profile_certificates",
        ["student_profile_id"],
    )
    op.create_table(
        "portfolio_links",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("student_profile_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        _deleted_at_column(),
        _active_marker_column(),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_portfolio_links_student_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "student_profile_id", "url", "active_marker", name="uq_portfolio_links_profile_url"
        ),
        **_table_args(),
    )
    op.create_index(
        "ix_portfolio_links_student_profile_id", "portfolio_links", ["student_profile_id"]
    )
    op.create_table(
        "profile_city_preferences",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("student_profile_id", sa.String(36), nullable=False),
        sa.Column("city", sa.String(50), nullable=False),
        _deleted_at_column(),
        _active_marker_column(),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_profile_city_preferences_student_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "student_profile_id",
            "city",
            "active_marker",
            name="uq_profile_city_preferences_profile_city",
        ),
        **_table_args(),
    )
    op.create_index(
        "ix_profile_city_preferences_student_profile_id",
        "profile_city_preferences",
        ["student_profile_id"],
    )

    # 3) resumes 版本指针（ADR-015 §2.3）。存量行不回填：见模块 docstring。
    op.add_column(
        "resumes",
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("resumes", sa.Column("current_version_id", sa.String(36), nullable=True))
    op.add_column(
        "resumes",
        sa.Column(
            "is_head",
            sa.Integer(),
            # VIRTUAL 而非 ADR 字面的 STORED：MySQL 不允许 STORED 生成列的基列
            # 挂 ON DELETE SET NULL 外键（1215，实测见模块 docstring）。
            sa.Computed("IF(id = current_version_id, 1, NULL)", persisted=False),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_resumes_current_version_id",
        "resumes",
        "resumes",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_resumes_profile_head", "resumes", ["student_profile_id", "is_head"]
    )


def downgrade() -> None:
    # resumes：先摘唯一键与外键，再删列（生成列依赖 current_version_id，先删它）。
    op.drop_constraint("uq_resumes_profile_head", "resumes", type_="unique")
    op.drop_constraint("fk_resumes_current_version_id", "resumes", type_="foreignkey")
    op.drop_column("resumes", "is_head")
    op.drop_column("resumes", "current_version_id")
    op.drop_column("resumes", "version_no")

    op.drop_table("profile_city_preferences")
    op.drop_table("portfolio_links")
    op.drop_table("profile_certificates")

    # 老子表：历史行必须物理清掉，否则恢复旧唯一键时同名相撞（有损，见 docstring）。
    connection = op.get_bind()
    for table in ("skills", "experiences", "educations"):
        connection.execute(text(f"DELETE FROM {table} WHERE deleted_at IS NOT NULL"))
    op.drop_constraint("uq_skills_profile_name", "skills", type_="unique")
    op.create_unique_constraint("uq_skills_profile_name", "skills", ["student_profile_id", "name"])
    for table in ("skills", "experiences", "educations"):
        op.drop_column(table, "active_marker")
        op.drop_column(table, "deleted_at")
