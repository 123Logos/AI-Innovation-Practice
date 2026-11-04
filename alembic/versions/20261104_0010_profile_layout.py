"""Add `layout_config` to student_profiles (Phase 2 · D5.3).

ADR-007 决策四（方案 A：JSON 列）：展示顺序与自定义标题属于「整份读、整份写」
的装饰信息，跟简历走；用一条 `ALTER TABLE … ADD COLUMN` 就够，不需要侧表，
也就不会有「插错 `_CHILD_TABLES` 顺序报 1451」这类级联删除的坑。

存的是**元信息**（`{version, sections:[{id,title}]}`），不是内容本身：
内容永远在 `skills` / `experiences` / `educations` 三张子表里，
layout 只回答「先渲染谁、标题叫什么」。这样「改顺序」不会复制一份简历数据。

`NULL` 是**有语义的**，不是「空值」：表示「从未自定义」，将来默认顺序变了
（比如新增第 4 个模块）这份简历会自动跟上。这与「自定义成默认顺序」不同——
后者存了显式快照，会保留用户当时看到的顺序（ADR-007 §6.8(1)(2)）。

不加 `server_default`：`NULL` 就是缺省，存量行在 `ADD COLUMN` 那一刻
自然全部是 `NULL`，不需要回填。

**downgrade 是无损的**：只丢掉用户调过的顺序与标题，内容一行不动。

Revision ID: 20261104_0010
Revises: 20261103_0009
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261104_0010"
down_revision: str | Sequence[str] | None = "20261103_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 用 MySQL 方言的 JSON 而不是 TEXT：MySQL 会校验写入的是合法 JSON，
    # 且将来可以用 `->>` 做查询。注意 JSON 列**不能**带 server_default。
    op.add_column(
        "student_profiles",
        sa.Column("layout_config", mysql.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("student_profiles", "layout_config")
