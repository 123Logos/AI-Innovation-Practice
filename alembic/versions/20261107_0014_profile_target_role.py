"""Add `target_role` to student_profiles (ADR-013 §5).

匹配五项权重里唯一「现在算不出来」的那一项是 0.10 的目标偏好：全库没有任何字段能表达
「这个人想做什么岗」（grep `target_role|preferred|意向` 在 `app/` 与 `alembic/` 0 命中）。
人类负责人指令 6.3 要求补一个用户填写的偏好岗位，填了才谈得上计算。

为什么挂在 `student_profiles` 而不是 `users`：现有模型是「一人多档案，每份档案一个 `title`」
（ADR-006），「投后端」「投算法」两版本就是两份档案，偏好跟着档案走才自洽；挂用户级会强迫
一个人同时只能有一个目标。

为什么 `NULL` 可空且不给默认值：**没填的人不该被系统性扣分**。匹配那边对应 ADR-013 §5.1 的
权重重新归一——缺项不参与加权，而不是记 0 分。

为什么是自由文本而不是枚举：枚举会把「我想做 AI 产品」这类真实表达强行折进我们的词表，
而这一项只需要它和岗位 `title` 做字符串级比较。

**downgrade 是无损的**：只丢掉用户填的偏好文本，不动其它列。

Revision ID: 20261107_0014
Revises: 20261107_0013
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261107_0014"
down_revision: str | Sequence[str] | None = "20261107_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("student_profiles", sa.Column("target_role", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("student_profiles", "target_role")
