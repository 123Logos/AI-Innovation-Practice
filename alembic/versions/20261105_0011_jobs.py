"""Create the jobs table (Phase 4 · B1，见 `docs/adr/ADR-011-job-board-and-skill-extraction.md` 1.B).

岗位库的「非 AI」切片第一张表。列与约束按 ADR-011 1.B 逐字建立。

三处刻意的设计：

1. **`created_by_user_id` 走 `ON DELETE RESTRICT`**（与 `student_profiles.user_id` 同）：
   岗位是公开数据，删账号不该把别人能看到的历史岗位一起带走；真要清理时由
   开发者直接操作数据库（`MVP_SCOPE.md` §9「不做管理后台」）。
2. **状态只两态**（`draft` / `published`），`schema-draft.sql:203` 的七态被砍：
   `pending_review`/`approved`/`rejected` 需要审核角色（没有后台），
   `closed`/`expired` 需要定时任务（Redis/ARQ/Celery 仍冻结）。
   过期改为**读侧判定**（2.G：service 层用 `datetime.date.today()`），不落状态、不动数据，
   所以这里没有 `expired` 状态，SQL 也不需要数据库侧的当日函数。
3. **软删用 `deleted_at`**，语义与 0007 完全一致：`NULL` = 正常，非 `NULL` = 回收站。

`employment_type` / `status` 用 `VARCHAR` + `CHECK` 而不是 MySQL `ENUM`：
沿用 `educations.degree`（0002）的结论——加一个取值只改代码不改表，
而 `ENUM` 改取值是一次 `ALTER TABLE ... MODIFY COLUMN`。

索引 `ix_jobs_status_created (status, created_at)` 服务广场列表的
「按状态筛 + 按创建时间倒序」；`ix_jobs_creator (created_by_user_id, deleted_at)`
服务「只看我自己的、未删除的」这类查询，同时给外键提供落脚点。
0007 的教训（先建替换索引再 drop 旧索引）只适用于 `ALTER`，本迁移是新建表，
不涉及索引替换；但 `ix_jobs_creator` 以 `created_by_user_id` 为最左前缀，
MySQL 因此不会再为外键自动生成一条同列索引。

**downgrade 无损**：本轮刚建表，库里没有存量业务数据，直接删表。

Revision ID: 20261105_0011
Revises: 20261104_0010
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261105_0011"
down_revision: str | Sequence[str] | None = "20261104_0010"
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
        "jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("location", sa.String(160), nullable=True),
        sa.Column("employment_type", sa.String(20), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("salary_min", sa.DECIMAL(12, 2), nullable=True),
        sa.Column("salary_max", sa.DECIMAL(12, 2), nullable=True),
        sa.Column("experience_required", sa.String(100), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        # 以下三列是本轮**只建不写**的：PRD FR-03 L105 要求「保存模型 / 提示词版本 /
        # 时间」，写入方是 C 轮的 `POST /jobs/extract-skills`。B1 恒为 NULL。
        sa.Column("extract_model", sa.String(60), nullable=True),
        sa.Column("extract_prompt_version", sa.String(20), nullable=True),
        sa.Column("extracted_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "employment_type IS NULL OR employment_type IN "
            "('internship', 'full_time', 'part_time', 'contract', 'other')",
            name="ck_jobs_employment_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published')",
            name="ck_jobs_status",
        ),
        # 薪资区间：两端都选填，只在「都填了」时比较（ADR-011 1.B，先例 schema-draft.sql:216）。
        sa.CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max",
            name="ck_jobs_salary_range",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_jobs_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        **_table_args(),
    )
    op.create_index("ix_jobs_status_created", "jobs", ["status", "created_at"])
    op.create_index("ix_jobs_creator", "jobs", ["created_by_user_id", "deleted_at"])


def downgrade() -> None:
    # MySQL 在删表时一并回收外键与其依赖的索引；显式 DROP INDEX 反而会报
    # 1553（index needed in a foreign key constraint），所以只删表（与 0002 一致）。
    op.drop_table("jobs")
