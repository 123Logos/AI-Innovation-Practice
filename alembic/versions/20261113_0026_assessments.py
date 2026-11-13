"""Standardized assessment bank, sessions, answers and profile tags (B9 · FR-04, ADR-028).

四张表 + 12 题种子数据，一次建完。理由都写在 ADR-028，这里只记**迁移层面**的口径：

1. **题库**（`assessment_questions`）：选项存 JSON、`answer_key` 单独一列且**不进任何读响应**。
   `bank_version` 是字符串（`v1`）——它要被人读懂，不是自增号。
2. **会话**（`assessment_sessions`）：`question_ids` 存**这一场实际出过的题**。
   ADR-028 §2.2 原本只写了 `question_count`，实现时补了这一列：只记数量的话，
   判分得「按当前题库重新取一遍题」，而题库随时可能改——同一份答案的分数会取决于
   什么时候点的提交。数量仍可读（`len`）。
3. **答案**（`assessment_answers`）：`UNIQUE(session_id, question_id)` 让「改答案」
   是覆盖而不是追加；`is_correct` 可空（`in_progress` 时未判分）。
4. **画像标签**（`profile_tags`）：挂**档案**不挂用户，`UNIQUE(student_profile_id, tag)`
   让重考覆盖同一标签，而不是让画像越长越胖。不汇总成一段 JSON 的理由见 ADR-028 §2.4。

状态与来源都是 `VARCHAR + CHECK`（与 `jobs.status` / `resumes.origin` 同一做法）：
多一个取值只改代码不改表结构，但「多一个值」仍然必须经过一次明确的迁移。

`downgrade` 是**干净**的：四张表连种子一起 drop，不残留「有表没数据」或「有数据没表」。
本条与 `0025` 的有损降级不同——那里丢的是用户填过的数据，这里丢的是可重放的种子。

Revision ID: 20261113_0026
Revises: 20261113_0025
Create Date: 2026-11-13
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261113_0026"
down_revision: str | Sequence[str] | None = "20261113_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 种子题库版本。扩库是**数据**操作（插新版本的行），不需要再写迁移。
SEED_BANK_VERSION = "v1"

#: 种子写入时间：所有行同一个值，版本排序才有意义（`latest_bank_version` 按它倒序）。
SEED_CREATED_AT = datetime(2026, 11, 13, 0, 0, 0)


def _seed_questions() -> list[dict[str, object]]:
    """12 道种子题（4 项技能 × 3 题）。

    题目内容是**通用且可核对**的基础知识，不是「AI 生成的能力题」：
    ADR-028 §5 明确「不许让 AI 生成题目直接进库」。正确答案的位置刻意打散，
    免得「蒙 C」也能过（虽然 12 题的量本来也挡不住蒙，但别把口径写反）。
    """
    python_key = "python"
    sql_key = "sql"
    react_key = "react"
    git_key = "git"

    rows: list[tuple[str, str, str, str, list[dict[str, str]], str]] = [
        (
            "b9000000-0000-4000-8000-000000000001",
            python_key,
            "Python",
            "Python 里 list 与 tuple 的关键差别是什么？",
            [
                {"key": "A", "label": "list 能存放任意类型，tuple 只能存放数字"},
                {"key": "B", "label": "tuple 创建后不能再增删改元素"},
                {"key": "C", "label": "list 不能被 for 循环遍历"},
                {"key": "D", "label": "tuple 不能作为函数的参数"},
            ],
            "B",
        ),
        (
            "b9000000-0000-4000-8000-000000000002",
            python_key,
            "Python",
            "表达式 [x * 2 for x in range(3)] 的结果是？",
            [
                {"key": "A", "label": "[0, 2, 4]"},
                {"key": "B", "label": "[2, 4, 6]"},
                {"key": "C", "label": "[0, 1, 2]"},
                {"key": "D", "label": "[1, 2, 3]"},
            ],
            "A",
        ),
        (
            "b9000000-0000-4000-8000-000000000003",
            python_key,
            "Python",
            "d = {'a': 1}；执行 d.get('b', 0) 的结果是？",
            [
                {"key": "A", "label": "抛出 KeyError"},
                {"key": "B", "label": "返回 None"},
                {"key": "C", "label": "返回 0"},
                {"key": "D", "label": "返回空字典"},
            ],
            "C",
        ),
        (
            "b9000000-0000-4000-8000-000000000004",
            sql_key,
            "SQL",
            "SQL 中 WHERE 与 HAVING 的主要区别是什么？",
            [
                {"key": "A", "label": "两者完全等价，只是写法不同"},
                {"key": "B", "label": "WHERE 只能用于数字列"},
                {"key": "C", "label": "HAVING 用于过滤分组之后的聚合结果"},
                {"key": "D", "label": "HAVING 在 FROM 之前执行"},
            ],
            "C",
        ),
        (
            "b9000000-0000-4000-8000-000000000005",
            sql_key,
            "SQL",
            "LEFT JOIN 会保留哪一侧的全部行？",
            [
                {"key": "A", "label": "右表的全部行"},
                {"key": "B", "label": "左表的全部行"},
                {"key": "C", "label": "只保留两表匹配上的行"},
                {"key": "D", "label": "两表行数相加"},
            ],
            "B",
        ),
        (
            "b9000000-0000-4000-8000-000000000006",
            sql_key,
            "SQL",
            "给 users.email 建唯一索引（UNIQUE）的作用是什么？",
            [
                {"key": "A", "label": "加快删除操作"},
                {"key": "B", "label": "保证这一列不会出现重复值"},
                {"key": "C", "label": "自动对邮箱加密存储"},
                {"key": "D", "label": "自动去掉首尾空格"},
            ],
            "B",
        ),
        (
            "b9000000-0000-4000-8000-000000000007",
            react_key,
            "React",
            "React 函数组件里 useState 返回什么？",
            [
                {"key": "A", "label": "只返回当前状态值"},
                {"key": "B", "label": "当前状态值和一个更新它的函数"},
                {"key": "C", "label": "组件的 DOM 节点"},
                {"key": "D", "label": "父组件传进来的 props"},
            ],
            "B",
        ),
        (
            "b9000000-0000-4000-8000-000000000008",
            react_key,
            "React",
            "渲染列表时给每一项加 key 的主要作用是什么？",
            [
                {"key": "A", "label": "决定列表的显示顺序"},
                {"key": "B", "label": "作为这一项的 CSS 类名"},
                {"key": "C", "label": "帮助 React 识别同层子元素的身份，正确复用节点"},
                {"key": "D", "label": "这是浏览器的必填属性"},
            ],
            "C",
        ),
        (
            "b9000000-0000-4000-8000-000000000009",
            react_key,
            "React",
            "组件收到的 props 可以直接修改吗？",
            [
                {"key": "A", "label": "不可以，props 是只读的"},
                {"key": "B", "label": "可以，用赋值语句直接改"},
                {"key": "C", "label": "只有在类组件里可以改"},
                {"key": "D", "label": "改了之后会自动同步回父组件"},
            ],
            "A",
        ),
        (
            "b9000000-0000-4000-8000-000000000010",
            git_key,
            "Git",
            "git commit 做了什么？",
            [
                {"key": "A", "label": "把本地提交上传到远端仓库"},
                {"key": "B", "label": "把暂存区的内容记录成一次提交"},
                {"key": "C", "label": "撤销上一次提交"},
                {"key": "D", "label": "把两个分支合并成一个"},
            ],
            "B",
        ),
        (
            "b9000000-0000-4000-8000-000000000011",
            git_key,
            "Git",
            "在默认配置下，git pull 大致等价于哪两步？",
            [
                {"key": "A", "label": "git push 之后再 git merge"},
                {"key": "B", "label": "git fetch 之后再 git merge"},
                {"key": "C", "label": "git clone 之后再 git checkout"},
                {"key": "D", "label": "git rebase 之后再 git push"},
            ],
            "B",
        ),
        (
            "b9000000-0000-4000-8000-000000000012",
            git_key,
            "Git",
            "合并冲突通常出现在什么情况下？",
            [
                {"key": "A", "label": "只要执行 commit 就会出现"},
                {"key": "B", "label": "只在 push 到远端时出现"},
                {"key": "C", "label": "两个分支修改了同一文件的同一区域并尝试合并时"},
                {"key": "D", "label": "只在克隆仓库时出现"},
            ],
            "C",
        ),
    ]

    return [
        {
            "id": question_id,
            "skill_key": skill_key,
            "skill_name": skill_name,
            "prompt": prompt,
            "options": options,
            "answer_key": answer_key,
            "bank_version": SEED_BANK_VERSION,
            "is_active": True,
            "created_at": SEED_CREATED_AT,
        }
        for question_id, skill_key, skill_name, prompt, options, answer_key in rows
    ]


def upgrade() -> None:
    op.create_table(
        "assessment_questions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("skill_key", sa.String(100), nullable=False),
        sa.Column("skill_name", sa.String(100), nullable=False),
        sa.Column("prompt", sa.String(500), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("answer_key", sa.String(4), nullable=False),
        sa.Column("bank_version", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "ix_assessment_questions_bank",
        "assessment_questions",
        ["bank_version", "is_active"],
    )
    op.create_index("ix_assessment_questions_skill", "assessment_questions", ["skill_key"])

    op.create_table(
        "assessment_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("student_profile_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("bank_version", sa.String(16), nullable=False),
        # 这一场**实际出过**的题（顺序即出题顺序）。见模块注释第 2 条。
        sa.Column("question_ids", sa.JSON(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("submitted_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("abandoned_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("ai_summary_source", sa.String(16), nullable=True),
        sa.Column("ai_summary_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.CheckConstraint(
            "status IN ('in_progress', 'submitted', 'abandoned')",
            name="ck_assessment_sessions_status",
        ),
        sa.CheckConstraint(
            "ai_summary_source IS NULL OR ai_summary_source IN ('ai', 'fallback')",
            name="ck_assessment_sessions_summary_source",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_assessment_sessions_user", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_assessment_sessions_profile",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    # 「我的测评列表」按人 + 时间倒序；「这份档案最近一次已提交」按档案 + 状态。
    op.create_index("ix_assessment_sessions_user", "assessment_sessions", ["user_id", "started_at"])
    op.create_index(
        "ix_assessment_sessions_profile", "assessment_sessions", ["student_profile_id", "status"]
    )

    op.create_table(
        "assessment_answers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("question_id", sa.String(36), nullable=False),
        sa.Column("chosen_key", sa.String(4), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("answered_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["assessment_sessions.id"],
            name="fk_assessment_answers_session",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["assessment_questions.id"],
            name="fk_assessment_answers_question",
            ondelete="RESTRICT",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    # 一场会话里一道题只有一行：改答案走覆盖，不追加（ADR-028 §2.3）。
    op.create_index(
        "uq_assessment_answers_session_question",
        "assessment_answers",
        ["session_id", "question_id"],
        unique=True,
    )

    op.create_table(
        "profile_tags",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("student_profile_id", sa.String(36), nullable=False),
        sa.Column("tag", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "status IN ('verified', 'pending', 'improving')", name="ck_profile_tags_status"
        ),
        sa.CheckConstraint(
            "source IN ('assessment', 'project', 'teacher')", name="ck_profile_tags_source"
        ),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_profile_tags_profile",
            ondelete="RESTRICT",
        ),
        # 会话被清掉时标签留着（它已经写进画像），只让指针落空：
        # 与 `resumes.target_job_id` 同一取舍（B8 剩余 / ADR-026 §2.7）。
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["assessment_sessions.id"],
            name="fk_profile_tags_session",
            ondelete="SET NULL",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    # 重考覆盖同一标签，而不是让画像越长越胖（ADR-028 §2.4）。
    op.create_index(
        "uq_profile_tags_profile_tag",
        "profile_tags",
        ["student_profile_id", "tag"],
        unique=True,
    )

    questions = sa.table(
        "assessment_questions",
        sa.column("id", sa.String),
        sa.column("skill_key", sa.String),
        sa.column("skill_name", sa.String),
        sa.column("prompt", sa.String),
        sa.column("options", sa.JSON),
        sa.column("answer_key", sa.String),
        sa.column("bank_version", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", mysql.DATETIME(fsp=6)),
    )
    op.bulk_insert(questions, _seed_questions())


def downgrade() -> None:
    # 只 drop 表、不逐个 drop 索引：`profile_tags` 的唯一键
    # （`student_profile_id`, `tag`）同时是外键 `fk_profile_tags_profile` 的支撑索引，
    # 先 `DROP INDEX` 会撞 MySQL 1553（"needed in a foreign key constraint"）。
    # `drop_table` 会把索引与约束一起带走，顺序按子表 → 父表。
    op.drop_table("profile_tags")
    op.drop_table("assessment_answers")
    op.drop_table("assessment_sessions")
    op.drop_table("assessment_questions")
