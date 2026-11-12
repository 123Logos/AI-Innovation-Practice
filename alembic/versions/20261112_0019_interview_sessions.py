"""Create interview_sessions and interview_turns tables (B11a · FR-07).

纯文本模拟面试（FR-07 本期）只需两张关系表，不建媒体房间、不建评估报告表（留待 B11b）。

设计要点（详见 `docs/adr/ADR-022-text-interview-sessions.md`）：

1. **两张表而非单 JSON 字段**：sessions 存元信息，turns 存逐轮问答（有序事件流），
   可按 `sequence` 回放、可审计、可增量写入。
2. **降级可追踪**：`interview_turns.ai_degrade_reason` 列为非空时代表该轮 interviewer
   内容由本地降级模板产出而非真 AI——这是「可见降级、绝不静默」红线的数据层证据。
3. **外键 RESTRICT**：会话归属用户、可选绑定岗位，删账号/删岗位都不该带走面试历史。
4. **状态/发言方用 VARCHAR + CHECK** 而非 MySQL ENUM：加一个取值只改代码不改表。
5. **时间用 `DATETIME(fsp=6)`**（与 jobs/student_profiles 一致，UTC 由应用层写入）。

downgrade 无损：本批刚建表，库内无存量业务数据，先删子表 turns 再删父表 sessions。

Revision ID: 20261112_0019
Revises: 20261111_0017
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261112_0019"
down_revision: str | Sequence[str] | None = "20261111_0017"
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
        "interview_sessions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("target_role", sa.String(200), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("ended_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'ended')",
            name="ck_interview_sessions_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_interview_sessions_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_interview_sessions_job_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        **_table_args(),
    )
    op.create_table(
        "interview_turns",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("ai_degrade_reason", sa.Text(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "speaker IN ('interviewer', 'candidate')",
            name="ck_interview_turns_speaker",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["interview_sessions.id"],
            name="fk_interview_turns_session_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        **_table_args(),
    )
    op.create_index(
        "ix_interview_sessions_user_status",
        "interview_sessions",
        ["user_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_interview_turns_session_sequence",
        "interview_turns",
        ["session_id", "sequence"],
    )


def downgrade() -> None:
    # 先删子表 turns（FK 指向 sessions），再删父表 sessions。
    # MySQL 在删表时一并回收外键与其依赖的索引，显式 DROP INDEX 反而会报 1553。
    op.drop_table("interview_turns")
    op.drop_table("interview_sessions")
