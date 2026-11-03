"""Allow multiple profiles per user + recycle bin (Phase 2 · D4.1).

方案 C（见 `docs/adr/ADR-006-multiple-resumes-design.md`）：不新增表，把
`student_profiles` 从「用户的唯一档案」改成「用户的一份简历档案」——
放开 `user_id` 上的唯一约束，同一个人可以有多行，每行就是一份简历。

新增三列：

- `title`：这份简历的名字（列表里区分「投后端」「投算法」）；
- `is_default`：默认简历标记（`GET /profiles/me` 与生成简历不传 id 时用它）；
- `deleted_at`：软删除时间戳（回收站）。`NULL` = 正常，非 `NULL` = 在回收站里。

**为什么先建普通索引再删唯一索引**：`fk_student_profiles_user_id` 这个外键要求
`user_id` 左侧有可用索引，直接 `DROP INDEX user_id` 会报
`1553 Cannot drop index 'user_id': needed in a foreign key constraint`（已实测）。
先补一个非唯一索引 `ix_student_profiles_user_id`，外键就有了落脚点。

**downgrade 是有损的**：恢复 `UNIQUE(user_id)` 必须先删掉每用户多余的行，
连带删除这些行挂着的 `skills` / `experiences` / `educations` / `resumes`
（四张子表都是 `ON DELETE RESTRICT`，不先删子表会报 1451）。
保留哪一行：`is_default` 优先 → 未进回收站优先 → 创建时间最早 → id 最小。

Revision ID: 20261103_0007
Revises: 20261103_0006
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261103_0007"
down_revision: str | Sequence[str] | None = "20261103_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 挂在 `student_profiles` 下的子表；外键均为 RESTRICT，删除父行前必须先清这些。
_CHILD_TABLES = ("resumes", "educations", "experiences", "skills")


def upgrade() -> None:
    # 1) 外键需要 user_id 上有索引：先补普通索引，再摘掉唯一索引。
    op.create_index("ix_student_profiles_user_id", "student_profiles", ["user_id"])
    op.drop_index("user_id", table_name="student_profiles")

    # 2) 多份简历 + 回收站需要的三列。
    op.add_column(
        "student_profiles",
        sa.Column("title", sa.String(200), nullable=False, server_default="我的简历"),
    )
    op.add_column(
        "student_profiles",
        sa.Column("is_default", mysql.TINYINT(display_width=1), nullable=False, server_default="0"),
    )
    op.add_column(
        "student_profiles",
        sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True),
    )

    # 3) 存量：迁移前每人至多一行，一律标为默认简历。
    op.execute(text("UPDATE student_profiles SET is_default = 1"))


def downgrade() -> None:
    """恢复一对一结构（**有损**：每用户只保留一份简历）。

    多余的简历会被物理删除，连带其技能/经历/教育/生成物。降级前请自行备份。
    """
    connection = op.get_bind()

    # 每用户挑一行保留：默认优先 → 未删除优先 → 最早创建 → id 最小（保证确定性）。
    connection.execute(text("DROP TEMPORARY TABLE IF EXISTS keep_profile_ids"))
    connection.execute(
        text("CREATE TEMPORARY TABLE keep_profile_ids (id VARCHAR(36) NOT NULL PRIMARY KEY)")
    )
    connection.execute(
        text(
            "INSERT INTO keep_profile_ids (id) "
            "SELECT id FROM ("
            "  SELECT id, ROW_NUMBER() OVER ("
            "    PARTITION BY user_id "
            "    ORDER BY is_default DESC, (deleted_at IS NULL) DESC, created_at ASC, id ASC"
            "  ) AS row_number_in_user "
            "  FROM student_profiles"
            ") AS ranked WHERE row_number_in_user = 1"
        )
    )

    # 子表是 RESTRICT：先删要丢弃的简历的所有子行，再删父行。
    for table in _CHILD_TABLES:
        connection.execute(
            text(
                f"DELETE child FROM {table} AS child "
                "JOIN student_profiles AS profile ON child.student_profile_id = profile.id "
                "WHERE profile.id NOT IN (SELECT id FROM keep_profile_ids)"
            )
        )
    connection.execute(
        text(
            "DELETE profile FROM student_profiles AS profile "
            "WHERE profile.id NOT IN (SELECT id FROM keep_profile_ids)"
        )
    )
    connection.execute(text("DROP TEMPORARY TABLE keep_profile_ids"))

    op.drop_column("student_profiles", "deleted_at")
    op.drop_column("student_profiles", "is_default")
    op.drop_column("student_profiles", "title")

    # 恢复唯一约束，再摘掉当初为外键临时补的普通索引。
    op.create_index("user_id", "student_profiles", ["user_id"], unique=True)
    op.drop_index("ix_student_profiles_user_id", table_name="student_profiles")
