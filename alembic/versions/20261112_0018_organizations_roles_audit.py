"""Organizations, membership roles and append-only audit log (B5, ADR-021).

契约见 `docs/adr/ADR-021-organizations-roles-and-audit-log.md` §2.1。三张表：

1. `organizations`：`code` 唯一且大小写不敏感（collation 是 `utf8mb4_0900_ai_ci`，
   落库前另外 `strip().upper()` 一次），所以它可以直接当邀请码用——不必再发一套 token。
2. `organization_members`：**角色挂成员关系，不挂 `users`**。`user_id` 唯一是本次的
   业务边界（一个账号当前只属于一个组织），放宽成 `(user_id, organization_id)` 即可支持
   双身份，表结构不用重做。
3. `audit_logs`：只追加，**没有 `updated_at`**——可改的审计日志不是审计日志。
   `result` 区分 ok / denied，被拒绝的越权尝试也要留痕。

**downgrade 是有损的**：三张表整表删除，审计记录不可恢复。降级前请自行备份。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261112_0018"
down_revision: str | Sequence[str] | None = "20261111_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_args() -> dict[str, str]:
    return {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_0900_ai_ci",
    }


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        # 唯一键建在 code 上：它同时是邀请码，重复就等于两个组织抢同一个入伙口令。
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="school"),
        *_timestamps(),
        sa.UniqueConstraint("code", name="uq_organizations_code"),
        **_table_args(),
    )

    op.create_table(
        "organization_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="student"),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_org_members_organization",
            ondelete="RESTRICT",
        ),
        # 用户行被删时成员关系随之消失；RESTRICT 会留下永远删不掉的孤儿外键。
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_org_members_user",
            ondelete="CASCADE",
        ),
        # ADR-021 §2.1 的业务边界：一个账号当前只属于一个组织。
        sa.UniqueConstraint("user_id", name="uq_org_members_user"),
        **_table_args(),
    )
    op.create_index(
        "ix_org_members_organization_role",
        "organization_members",
        ["organization_id", "role"],
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True),
        # 系统动作（迁移回填、定时任务）没有操作者：留 NULL 而不是假造一个用户 id。
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("organization_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.String(36), nullable=True),
        sa.Column("result", sa.String(10), nullable=False, server_default="ok"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        # 只追加表：只有 created_at，没有 updated_at。
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_logs_actor",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_audit_logs_organization",
            ondelete="SET NULL",
        ),
        **_table_args(),
    )
    # 管理页的两个真查询：按组织倒序翻页、按操作者追溯。两条都是复合索引，
    # `created_at` 在第二列——这正是 `tests/test_migrations.py` 那条
    # 「不许有单列 created_at 索引」判据要的形状（单列索引服务不了「筛完再排序」）。
    # **刻意不建 `(created_at)` 单列索引**：本批没有任何跨组织的「全平台最近操作」查询
    # （ADR-021 §3.3 明确 platform_admin 不给跨组织视图），一个没人用的索引
    # 只是每次写入多一次维护成本。
    op.create_index(
        "ix_audit_logs_organization_created", "audit_logs", ["organization_id", "created_at"]
    )
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_user_id", "created_at"])


def downgrade() -> None:
    # **不逐个 drop_index**：`ix_audit_logs_actor_created` 同时被外键
    # `fk_audit_logs_actor` 借用来满足约束，先删它 MySQL 会报 1553
    # （Cannot drop index … needed in a foreign key constraint，实测）。
    # `DROP TABLE` 会连带清掉它的索引与外键，顺序按外键依赖反向：
    # audit_logs 依赖 organizations，所以先删它。
    op.drop_table("audit_logs")
    op.drop_table("organization_members")
    op.drop_table("organizations")
