"""Add the job review workflow columns and widen the status CHECK (B6 · FR-03, ADR-024).

`ADR-011` 1.B 把七态砍成两态，理由是「没有审核角色、没有定时任务」。审核角色已由
B5 交付（`ADR-021`），第 18 轮裁定 ③ 要求审核动作过 `app/core/deadline.is_expired`，
于是本迁移把审核需要的三样东西补上：

1. `ck_jobs_status` 从两值扩成四值（`draft` / `pending_review` / `published` / `rejected`）。
   仍用 `VARCHAR + CHECK` 而不是 `ENUM`：加一个取值只改代码不改表（0002 的结论）。
2. `organization_id`（可空，FK → `organizations` RESTRICT）：**审核范围**靠它表达
   「同组织」。可空不是偷懒——第 18 轮裁定 ① 要求组织归属永久可空、不刷存量数据，
   存量已发布岗位这一列就是 `NULL`，不回溯、不重审。
3. `review_note`：驳回理由，只有 `rejected` 时有值。

索引顺序很关键：**先建 `ix_jobs_org_status (organization_id, status)` 再加外键**，
MySQL 会复用这条复合索引给外键落脚；反过来先加外键，MySQL 会为它单独建一条
`(organization_id)` 索引，最终留下两条几乎重复的索引。
`ix_jobs_status_created` / `ix_jobs_creator` 一律保留：广场与「我的草稿」还在用。

**downgrade 有损**：先把 `pending_review` / `rejected` 归成 `draft` 再加回两值 CHECK，
否则库里只要有一行新状态，加约束当场失败（1091 / 3819）。丢的是状态，不是内容。

Revision ID: 20261113_0022
Revises: 20261112_0021
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_0022"
down_revision: str | Sequence[str] | None = "20261112_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_CHECK_OLD = "status IN ('draft', 'published')"
STATUS_CHECK_NEW = "status IN ('draft', 'pending_review', 'published', 'rejected')"


def upgrade() -> None:
    op.add_column("jobs", sa.Column("organization_id", sa.String(36), nullable=True))
    op.add_column("jobs", sa.Column("review_note", sa.String(200), nullable=True))

    # 先索引、后外键：让外键复用这条复合索引，避免 MySQL 再自动建一条单列索引。
    op.create_index(
        "ix_jobs_org_status",
        "jobs",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_jobs_organization",
        "jobs",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_status", "jobs", STATUS_CHECK_NEW)


def downgrade() -> None:
    # 两值 CHECK 装不下新状态：先把它们归成 draft，再换约束。状态信息丢失，内容不动。
    op.execute("UPDATE jobs SET status = 'draft' WHERE status IN ('pending_review', 'rejected')")

    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_status", "jobs", STATUS_CHECK_OLD)

    op.drop_constraint("fk_jobs_organization", "jobs", type_="foreignkey")
    op.drop_index("ix_jobs_org_status", table_name="jobs")
    op.drop_column("jobs", "review_note")
    op.drop_column("jobs", "organization_id")
