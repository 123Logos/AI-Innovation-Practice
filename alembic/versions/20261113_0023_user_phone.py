"""Add `users.phone` as a third login identifier (B6 · FR-01, ADR-025).

`ADR-025` §2.6 只做一件事：给账号一张手机号答卷。**不刷存量、不要求任何人补填**。

四条决定写在这里，因为它们是「表结构即契约」的一部分：

1. 列可空：存量账号全部 `NULL`，而唯一索引允许多个 `NULL`，所以升级不需要回填，
   也不会在升级那一刻撞 1062。手机号登录是**额外**入口，不是「必须绑定」。
2. 长度 20 而不是 11：校验仍由应用层按大陆 11 位卡死（`app/schemas/auth.py::PHONE_PATTERN`），
   但列宽给将来可能的国际号码留余量。列宽按「最小够用」定死会让加号前缀直接 1406。
3. 唯一键 `uq_users_phone`：与 `username` / `email` 同一档——三个标识都必须全局唯一，
   否则「用手机号登录」在库里就有两个候选行，而登录没有第二个参数可以消歧。
4. 不建 CHECK：手机号形状是**应用层**的规则（归一化在落库前完成，见 ADR-025 §2.2）。
   在库里再写一份正则，只会变成「两份规则慢慢漂移」的第三个来源，
   与 `test_migrations.py` 那条「email / contact 不许有 CHECK」的判据同一个口径。

Revision ID: 20261113_0023
Revises: 20261113_0022
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_0023"
down_revision: str | Sequence[str] | None = "20261113_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(20), nullable=True))
    op.create_unique_constraint("uq_users_phone", "users", ["phone"])


def downgrade() -> None:
    op.drop_constraint("uq_users_phone", "users", type_="unique")
    op.drop_column("users", "phone")
