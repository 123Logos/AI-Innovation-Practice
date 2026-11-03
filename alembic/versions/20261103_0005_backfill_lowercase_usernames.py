"""Backfill lowercase usernames (Phase 2 · D1).

用户名在 Phase 2 起由应用层归一为小写（`app/services/auth_service.py` 与
`app/schemas/auth.py`），本迁移把存量里含大写字母的记录一次性抹平。

为什么可以安全地 `LOWER()`：
MySQL 8.0 上所有表都由 `20261023_0001` 显式建为 `utf8mb4_0900_ai_ci`
（大小写不敏感 + 重音不敏感），唯一索引 `username` 早就不允许「仅大小写不同」的
两行共存。因此把现有值统一小写**不会产生新的唯一性冲突**——
冲突要能出现，库里必须已经存在 `Alice` 与 `alice` 两行，而这在当前索引下不可能。

不可逆：原大小写信息在 `UPDATE` 之后永久丢失，`downgrade()` 只能是 `pass`
（数据迁移与 DDL 不同，回滚没有「加回去」这一说，见 alembic 官方对数据迁移的建议）。

Revision ID: 20261103_0005
Revises: 20261102_0004
Create Date: 2026-11-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261103_0005"
down_revision: str | Sequence[str] | None = "20261102_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ⚠️ 这里**不能**照抄直觉写法 `WHERE username != LOWER(username)`：
    # `username` 列的排序规则是 `utf8mb4_0900_ai_ci`（大小写不敏感），
    # 于是 `'ZhangSan' <> LOWER('ZhangSan')` 在 MySQL 里求值为 **0**，
    # 这条 WHERE 会把所有行都判成「已经相等」，回填静默失效、一条都不更新。
    # 实测：`SELECT 'ZhangSan' <> LOWER('ZhangSan'),
    #               BINARY 'ZhangSan' <> BINARY LOWER('ZhangSan')` → `(0, 1)`。
    # 因此用 `BINARY` 强制按字节比较，只碰真的含大写/异体的行。
    op.execute(
        "UPDATE users SET username = LOWER(username) "
        "WHERE BINARY username <> BINARY LOWER(username)"
    )


def downgrade() -> None:
    # 数据回填不可逆：原大小写已被覆盖，无法从 LOWER 结果还原。
    pass
