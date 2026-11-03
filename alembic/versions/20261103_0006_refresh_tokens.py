"""Create the refresh_tokens table (Phase 2 · D2.1).

一轮「双 Token」里只有 refresh token 需要落库：access token 15 分钟过期、
无状态，靠签名自证；refresh token 要活 7 天，必须能**主动作废**，
否则改密码、异地登录、令牌泄露都没有收回手段。

三处刻意的设计：

1. `token_hash` 存 SHA-256 而非明文——JWT 等价于一张可直接使用的门票，
   库被读走就等于账号被接管（见 `app/core/security.token_hash`）。
2. `token_hash` 唯一索引：既是「按哈希查一行」的查询索引，
   也顺带保证同一个 token 不会被动写入两条记录（轮换写重时会直接报错，
   而不是留下两条都能用的记录）。
3. `user_id` 走 `ON DELETE CASCADE`：与 `student_profiles` 的 `RESTRICT`
   不同——令牌没有独立业务价值，用户被删时留着它只会变成孤儿行。

关于列类型：`id` / `user_id` 用 `VARCHAR(36)` 而不是设计稿写的 `CHAR(36)`。
本库所有主键（`users.id`、`student_profiles.id` …）都是 `sa.String(36)`
→ `VARCHAR(36)`，外键两侧必须同型同字符集才不会在不同 MySQL 版本上
出现「隐式转换导致索引失效」；`CHAR` 还会按定长补空格，JSON / 日志里
容易出现肉眼不可见的尾随空格。定长收益（36 字节）在这里没有意义。

Revision ID: 20261103_0006
Revises: 20261103_0005
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261103_0006"
down_revision: str | Sequence[str] | None = "20261103_0005"
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
        "refresh_tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        # SHA-256 十六进制恒为 64 字符；列宽按定长给，不做 TEXT。
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        # NULL = 仍然有效；非 NULL = 已被轮换 / 登出作废的时刻。
        sa.Column("revoked_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_refresh_tokens_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        **_table_args(),
    )
    # 按用户查（登出全部设备、排查异常登录）需要；token_hash 上面已有唯一索引。
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])


def downgrade() -> None:
    # 与 educations 同理：MySQL 删表时连带回收外键与索引，
    # 显式 DROP INDEX 反而会报 1553（index needed in a foreign key constraint）。
    op.drop_table("refresh_tokens")
