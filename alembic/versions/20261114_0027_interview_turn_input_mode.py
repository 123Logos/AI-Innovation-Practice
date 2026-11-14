"""Add interview_turns.input_mode: text / voice (B11b · FR-07, ADR-030).

**只加一列，不建任何音频表**：这是 `ADR-030` §2.1 不变量 A 的直接结果——
音频一次都不经过我们的后端（ASR/TTS 由浏览器带用户自己的 Key 直连 DeepSeek），
所以「不入库、不存盘」不是纪律，而是网络路径上根本没有那条线。

这一列回答的问题只有一个：「这一轮是说的还是打的」。它**不是**音频的指针，
也不参与打分或统计。`server_default='text'` 是**真话**——B11b 之前所有轮次都是打字来的。

**宽度 8 不是 16**：取值只有 `text` / `voice`，最长 5 个字符。留到 8 是给将来可能的
`voice_retry` 留一格余量；`CHECK` 约束与 `app/core/interview_modes.py` 的枚举同源，
加一个取值要同时改两处（测试 `test_input_mode_rejects_unknown_value` 钉住写入侧）。

`downgrade` 只 drop 这一列：它不承载业务内容，丢的是「这一轮是语音」这个标记本身，
如实登记，不假装可回退。

Revision ID: 20261114_0027
Revises: 20261113_0026
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261114_0027"
down_revision: str | Sequence[str] | None = "20261113_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INPUT_MODE_CHECK = "input_mode IN ('text', 'voice')"


def upgrade() -> None:
    op.add_column(
        "interview_turns",
        sa.Column("input_mode", sa.String(8), nullable=False, server_default="text"),
    )
    op.create_check_constraint("ck_interview_turns_input_mode", "interview_turns", INPUT_MODE_CHECK)


def downgrade() -> None:
    # 先删约束再删列：MySQL 在列被 drop 之后再删它上面的 CHECK 会报 3940（约束找不到），
    # 与 `0026` 里「先删唯一键再 drop_table」同一个顺序问题。
    op.drop_constraint("ck_interview_turns_input_mode", "interview_turns", type_="check")
    op.drop_column("interview_turns", "input_mode")
