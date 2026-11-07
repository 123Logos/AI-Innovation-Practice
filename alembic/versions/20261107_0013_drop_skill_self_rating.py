"""Drop `skills.self_rating` (ADR-013 §4).

删掉而不是留着不用，理由不在体积，在这个字段已经**不携带信息**：

- UI 从 2026-11 起不再采集熟练度（`frontend/src/stores/resume.ts` 的文件头写明
  「技能熟练度（self_rating）与年级已从 UI 移除」），提交时恒发常量 3；
- 渲染也不用它（`app/integrations/resume_generator.py` 只渲染技能名）；
- dev 库实测分布 `{2: 1, 3: 23, 4: 1}`——25 行里 23 行是那个常量，
  剩下 2 行是熟练度下线之前写的。

于是「技能上有熟练度」变成一个**只有 schema 还相信的事实**。一个 UI 不采集、
渲染不使用、却仍被 API 要求必填 1–5 的字段，比没有字段更坏：它强迫每个客户端编一个数，
而将来做证据强度（ADR-013 §7.2：项目经验关键词命中 + AI 打分）的人很可能顺手拿它当输入。

`downgrade` 把列加回来并用 `server_default='3'` 填充：这与删除时库里几乎全是 3 的事实一致，
所以回退在数据层面是无损的（唯一的损失是那 2 条历史值 2 和 4，它们是产品已废弃的采集结果）。

Revision ID: 20261107_0013
Revises: 20261105_0012
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261107_0013"
down_revision: str | Sequence[str] | None = "20261105_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # MySQL 8.0 在 DROP COLUMN 时会连带删除引用该列的 CHECK 约束
    # （`ck_skills_self_rating`，见 20261023_0001:68），所以这里不单独 drop constraint；
    # 测试 `tests/test_migrations.py` 没有钉这个约束名，钉的是 experiences/
    # educations/jobs 三组，删掉它不会让任何断言静默失配。
    op.drop_column("skills", "self_rating")


def downgrade() -> None:
    op.add_column(
        "skills",
        sa.Column(
            "self_rating",
            mysql.TINYINT(unsigned=True),
            nullable=False,
            server_default="3",
        ),
    )
    op.create_check_constraint("ck_skills_self_rating", "skills", "self_rating BETWEEN 1 AND 5")
