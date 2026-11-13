"""Add skill metadata: source / last_used_on / first_added_at (B7 · FR-02, ADR-027).

三列解决两件事，理由都写在 ADR-027，这里只记**迁移层面**的口径：

1. `source`：来源（自评 / 项目 / 教师确认 / 测评）。`VARCHAR(24) + CHECK` 而不是 `ENUM`，
   与 `0002` / `0024` 同一结论：加一个取值只改代码不改表。
   `server_default='self_reported'` 是**真话**——存量行确实没有任何外部证据。

   **宽度是 24 不是 16**：四个取值里最长的是 `teacher_confirmed`，**17 个字符**。
   第一版按「看着差不多」写成 16，插入时直接 `1406 Data too long`——这不是理论风险，
   是 `tests/test_skill_metadata.py::test_writable_sources_round_trip` 第一次跑就撞上的。
   留到 24 是给「将来加一个 `employer_confirmed` 之类」留余量：`VARCHAR` 在 InnoDB 里
   按实际长度存，宽一点不占空间，窄一点就是一次线上写入失败加一次紧急迁移。
2. `last_used_on`：用户声明的「最近使用」。**不从 `created_at`/`updated_at` 推**，
   因为全量替换让那两列恒等于「上次保存时间」（ADR-027 §1.2）。
3. `first_added_at`：首次加入这份档案的时间。**这里必须回填** = `created_at`：
   存量行的 `created_at` 就是它们第一次被写进来的时刻（此后每次保存都换了新行，
   但那些新行的 `created_at` 也等于当时那次保存，所以取最早的一条即是首次加入）。
   不做回填会让所有存量技能显示「不知道什么时候加的」，而那个答案其实是有的。

**`first_added_at` 必须是 `DATETIME(6)`**：`skills.created_at` 是 `DATETIME(6)`
（`20261023_0001` 起全库时间列统一 fsp=6）。若写成 `DATETIME`（精度 0），
回填与后续写入都会被 MySQL **四舍五入**到整秒——`10:00:00.6` 变成 `10:00:01`，
即「首次加入」可能比真实时刻晚，也会与同一行的 `created_at` 对不上。
这不是美观问题：`first_added_at` 的全部价值就是「与历史行比早晚」。

**downgrade 有损**：三列直接 drop。用户填过的 `last_used_on` 与选择过的 `source` 会丢，
不允许写成「可回退」了事（ADR-027 §2.1 第 3 条）。

Revision ID: 20261113_0025
Revises: 20261113_0024
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261113_0025"
down_revision: str | Sequence[str] | None = "20261113_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_CHECK = "source IN ('self_reported', 'assessment', 'project', 'teacher_confirmed')"


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column("source", sa.String(24), nullable=False, server_default="self_reported"),
    )
    op.add_column("skills", sa.Column("last_used_on", sa.Date(), nullable=True))
    op.add_column("skills", sa.Column("first_added_at", mysql.DATETIME(fsp=6), nullable=True))

    op.create_check_constraint("ck_skills_source", "skills", SOURCE_CHECK)

    # 回填：存量行的 created_at 就是它们第一次被写入的时刻（见模块注释第 3 条）。
    op.execute("UPDATE skills SET first_added_at = created_at WHERE first_added_at IS NULL")


def downgrade() -> None:
    op.drop_constraint("ck_skills_source", "skills", type_="check")
    op.drop_column("skills", "first_added_at")
    op.drop_column("skills", "last_used_on")
    op.drop_column("skills", "source")
