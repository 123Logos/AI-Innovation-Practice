"""Create the job_skills table (Phase 4 · B1，见 `docs/adr/ADR-011-job-board-and-skill-extraction.md` 1.D).

岗位要求的技能条目（「这个岗位要求什么」），与 `skills`（「我会什么 + 自评熟练度」）
语义相反、所有权也不同，因此**不合并、不加外键**（1.E）。

选子表而不是 `jobs.ai_skills` JSON 列 / 技能字典表（1.D 的已定结论）：
子表可 JOIN、可单条编辑、可由数据库去重——这三条正是将来匹配与人机共编的前提，
而 JSON 列的 `UNIQUE(job, skill)` 无法由数据库保证，字典表需要有人维护归一化（没有后台）。

三处刻意的设计：

1. **`job_id` 走 `ON DELETE CASCADE`**（与 `refresh_tokens` 同，与 `skills` 的 RESTRICT 不同）：
   技能条目是岗位的组成部分，不独立存在。本轮岗位只做软删（`deleted_at`），
   物理删除只发生在 `alembic downgrade`，届时 CASCADE 保证不留孤儿行。
2. **`origin` 只有 `jd_verifiable` / `unverified` 两个值，且禁止出现在请求体**：
   它是服务端派生的「能否回溯到 JD 原文」标记，客户端可伪造的溯源标记等于没有标记
   （1.D 已把 v1 的第三个值 `user_edited` 从枚举里删掉）。
   **值域命名（ADR-011 1.D v7，人类负责人裁定方案 a）**：这一列只能证明「这句话在 JD 原文里
   出现过」，证明不了「这句话是谁写的」，所以旧的「AI 提取 / 手动」两个值名会把逐字粘贴
   原文的手动条目误标成 AI 产出，已改为 `jd_verifiable`（可回溯原文）/ `unverified`（未标注出处）。
   **本轮写入的每条 `origin` 恒为 `unverified`**——AI 提取在 C 轮才落地。
   约束名 `ck_job_skills_origin` 与列名 `origin` 保持不变（`tests/test_migrations.py`
   把约束名钉进了断言；列名更名是已记录的遗留项，不属于本轮）。
3. **`type` / `confidence` / `evidence_text` 可空**：AI 的分级不可信，宁可为空让用户填。
   `confidence` / `evidence_text` 只有 C 轮的提取路径才写（1.F 的 v6 补条：
   `POST/PUT /jobs` 一律忽略客户端填值，仓储把这两列写成 NULL）；`type` 由用户填。

`UNIQUE (job_id, name)` 的去重语义依赖 `utf8mb4_0900_ai_ci`：按 MySQL 文档，
该排序规则大小写与重音不敏感、全角/半角亦不敏感，因此 `Python` / `python` /
`Ｐython`（首字母全角）**理论上**撞同一条唯一键。这是排序规则的实现细节而不是我们的契约，
所以它由 `tests/test_jobs_constraints.py` 用真实 MySQL 断言，而不是靠推断
（AGENTS.md §4.4）。断言变红即假设被证伪，届时改为应用层归一化的 `name_norm` 列，
而不是删掉断言（ADR-011 1.D 原文）。

索引 `ix_job_skills_name (name)`：详情页「你已具备」的只读比对（2.E）按技能名查，
将来「按技能找岗位」也需要它——这正是子表相对 JSON 列的实测收益。

Revision ID: 20261105_0012
Revises: 20261105_0011
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261105_0012"
down_revision: str | Sequence[str] | None = "20261105_0011"
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
        "job_skills",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(10), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("confidence", sa.DECIMAL(5, 4), nullable=True),
        sa.Column("evidence_text", sa.String(60), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "type IS NULL OR type IN ('hard', 'soft', 'domain')",
            name="ck_job_skills_type",
        ),
        # 只有两个值，且由服务端写死；请求体里出现 `origin` 一律忽略（1.D、1.F）。
        sa.CheckConstraint(
            "origin IN ('jd_verifiable', 'unverified')",
            name="ck_job_skills_origin",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_job_skills_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_skills_job_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "name", name="uq_job_skills_job_name"),
        **_table_args(),
    )
    op.create_index("ix_job_skills_name", "job_skills", ["name"])


def downgrade() -> None:
    # 子表先删（此时父表 0011 的 downgrade 才删得动）。
    op.drop_table("job_skills")
