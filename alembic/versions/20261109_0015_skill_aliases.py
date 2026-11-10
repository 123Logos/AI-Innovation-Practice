"""Create the `skill_aliases` table (B1，契约见 `docs/adr/ADR-016-skill-alias-layer-and-matcher-versioning.md` §2.2).

FR-05 阶段 2 的 L1 别名层。它要修的是两件词面判不出来的事：

1. **技能域**：`JS` ↔ `JavaScript`、`K8s` ↔ `Kubernetes`、`golang` ↔ `Go`
   （`app/core/skill_names.py::_lexical_forms_match` 的四条规则在「词干不同」那一格
   立刻判否，缩写别名一律够不着）；
2. **角色域**：`React 工程师` ↔ `前端开发`。这一格是老板点名的「同方向不同词」，
   而它**不在技能表里**——两侧是 `student_profiles.target_role` 与 `jobs.title`
   两段自由文本，所以没有第二个域就修不到他点名的那一格（§2.2「必须有第二个域」）。

四件事这张表刻意不做，每一件都是 ADR-016 §2.1 的裁定而不是遗漏：

- **不参与判重**：`skill_name_key` 仍是唯一的身份键（5 个写入点一个没动）。
  用它当身份键会把「写法不同」升级成「身份相同」，于是 `Python` 与 `Python3`
  在同一个档案里存不进两条，并且撞 `uq_skills_profile_name` 报 500 而不是 422——
  那正是老板要的「都能写进去、匹配时认得出是一回事」的反面（§2.1 否掉的 (a)）。
- **不参与存储与回显**：这张表存的是**归一后的 key**，不是原始写法。
  原始写法永远只在 `skills.name` / `job_skills.name` 里，`ADR-013` 的
  `name` / `matched_form` 两个回显契约因此不受影响。
- **不在运行时被逐条查询**：一次匹配请求在 `_gather` 里整体载入一份 `AliasIndex`
  （§3「不许 N+1」）。热路径按 4 条岗位要求 × 3 条学生技能 × 3 个经历字段算，
  每条各查一次库的话就是一条没人复核过的隐式放大。
- **不写种子数据**：本迁移只建表。§3 要求每一条别名都人工过一遍版本尾巴约束，
  而那张约束（`app/core/skill_names.py::alias_version_tail_conflict`）落在唯一写入口
  `SkillAliasRepository.add()` 上——种子必须走同一个入口，不许有第二条入库路径。
  **空表 = B1 之前的纯词面行为**，这是 §2.5 版本轴成立的前提。

三处列设计的理由：

1. `UNIQUE (canonical_key, alias_key, scope)`（§2.2 点名要的那一条）：
   **单向存一行、判定双向查**。存两行的话漏一行就会出现「A 匹配 B、B 不匹配 A」
   这种无法解释的不对称；单向 + 内存里的对称闭包把这件事变成一次构造。
   约束名 `uq_skill_aliases_pair` 被 `tests/test_migrations.py` 钉进断言。
2. `scope` / `source` 是 **VARCHAR + CHECK** 而不是 ENUM——本仓对枚举列的一致做法
   （`jobs.status`、`job_skills.origin`/`type` 同）：加一个值不必改表类型，
   而值域仍由数据库挡。`scope` 只有 `skill`/`role`，`source` 只有 `seed`/`ai_reviewed`
   ——**运行时永远不查 AI**，`ai_reviewed` 记的是「阶段 3 的 AI 建议经人工评审后入库」，
   匹配链路的确定性承诺不因这张表松动。值域两份：这里 + `ALIAS_SCOPES`/`ALIAS_SOURCES`。
3. `note VARCHAR(200) NULL`：这一行为什么存在（「谁提的、哪次评审过的」）。
   §3 说每一行都会真实改变判定结果，那么「当时凭什么收它」必须能查到，
   否则下一次对账只剩一个数字而没有原因。

没有外键：别名对指向的是**归一后的技能键**，不是某张表的某一行
（`canonical_key` 可能既不在 `skills` 也不在 `job_skills` 里，它只是 `skill_name_key`
的输出空间中的一个值）。外键在这里会把它错绑成「某个岗位的技能条目」，
而别名是全库共享的词表。

**downgrade 是无损的吗**：不是，但它丢的是**词表内容而不是业务数据**——
`skill_aliases` 里没有一行来自用户，全部是人工评审的产物；而它的每一次变更都要求
`meta.alias_version` +1（§2.5），所以降级丢掉的词表可以由「哪一版词表」这个坐标复原。
这也是为什么这张表不进 `student_profiles` 的回收站体系：它不是用户的数据。

Revision ID: 20261109_0015
Revises: 20261107_0014
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "20261109_0015"
down_revision: str | Sequence[str] | None = "20261107_0014"
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
        "skill_aliases",
        sa.Column("id", sa.String(36), nullable=False),
        # 两个键都是 skill_name_key 的输出（NFKC + casefold），长度与技能名列同宽（200）。
        sa.Column("canonical_key", sa.String(200), nullable=False),
        sa.Column("alias_key", sa.String(200), nullable=False),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "scope IN ('skill', 'role')",
            name="ck_skill_aliases_scope",
        ),
        sa.CheckConstraint(
            "source IN ('seed', 'ai_reviewed')",
            name="ck_skill_aliases_source",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_key", "alias_key", "scope", name="uq_skill_aliases_pair"),
        **_table_args(),
    )
    # 刻意不建 `ix_skill_aliases_*`：这张表的唯一读法是「整体载入一次」（ADR-016 §3），
    # 全表扫 by design；单列索引在这里只有写放大没有读者，而 `tests/test_migrations.py`
    # 那条「不许给 created_at 单建索引」的判断同样适用于它。


def downgrade() -> None:
    op.drop_table("skill_aliases")
