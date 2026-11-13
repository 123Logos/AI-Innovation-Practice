"""Add `resumes.origin` and `resumes.target_job_id` (B8 · FR-06, ADR-026).

版本对比与按岗位定制只需要两格新信息；**版本本身早就存在**（ADR-015 §2.3 把每次
生成落成 `resumes` 的一行），所以这里不建从表、不改链头机制。

1. `origin`：`generated` / `customized`。**不许靠标题前缀或 HTML 内容推断**
   「这是不是定制版」——那是一种会在下一次改文案时静默失效的隐式契约。
   用 `VARCHAR(16) + CHECK` 而不是 `ENUM`：加一个取值只改代码不改表（0002 的结论）。
2. `target_job_id`：定制版面向哪条岗位。`ON DELETE SET NULL` 是**第二个**
   非 RESTRICT 外键（第一个是 `current_version_id`），偏离与理由写在 ADR-026 §2.7：
   岗位会被用户删，RESTRICT 会变成「有定制版就删不掉岗位」的死锁，而定制版必须活下来。
   指针落空时读侧照常展示（`target_job_id = NULL` 就是「原岗位已删除」），
   定制版的正文一个字都没丢。
3. `server_default='generated'`：存量行自动成为 `generated`，与它们的事实一致
   （它们确实都是生成出来的），因此升级**不需要回填**。

索引顺序：`target_job_id` 不单建索引，让 MySQL 为外键自动建一条——本仓没有任何
「按岗位查定制版」的查询，多一条显式索引只是写放大。

**downgrade 有损**：`customized` 行会退回 `generated`（列被删，信息无处可放），
`target_job_id` 一并丢弃。丢的是「这行怎么来的」这一格标签，版本内容与链头不动。

Revision ID: 20261113_0024
Revises: 20261113_0023
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_0024"
down_revision: str | Sequence[str] | None = "20261113_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGIN_CHECK = "origin IN ('generated', 'customized')"


def upgrade() -> None:
    op.add_column(
        "resumes",
        sa.Column("origin", sa.String(16), nullable=False, server_default="generated"),
    )
    op.add_column("resumes", sa.Column("target_job_id", sa.String(36), nullable=True))

    op.create_check_constraint("ck_resumes_origin", "resumes", ORIGIN_CHECK)
    op.create_foreign_key(
        "fk_resumes_target_job",
        "resumes",
        "jobs",
        ["target_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_resumes_target_job", "resumes", type_="foreignkey")
    op.drop_constraint("ck_resumes_origin", "resumes", type_="check")
    op.drop_column("resumes", "target_job_id")
    op.drop_column("resumes", "origin")
