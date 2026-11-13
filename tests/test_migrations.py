"""迁移可逆性与库结构契约。

**这是唯一允许 `downgrade base` 的地方**：降级本身就是被测对象（验证每一条
`downgrade()` 真的把表/列/约束删干净），不是给别的用例做清理的清理手段。
用例间的清理由 `tests/conftest.py` 统一负责，见那里的说明。

降级会把库清空，所以用例用 `finally` 把库恢复到 head：否则后续用例会跑在
一个「只有 alembic_version」的空库上，只能靠调整文件执行顺序来掩盖。
"""

import os
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from alembic import command

EXPECTED_TABLES = {
    "educations",
    "experiences",
    "interview_sessions",
    "interview_turns",
    "job_skills",
    "jobs",
    "portfolio_links",
    "profile_certificates",
    "profile_city_preferences",
    "refresh_tokens",
    "resumes",
    "skill_aliases",
    "skills",
    "student_profiles",
    "users",
    # 批次 B5（ADR-021）：组织、成员关系、审计日志。三张表一起进清单，
    # 漏一张就会在下一次「新表建了但没人知道」时才发现。
    "organizations",
    "organization_members",
    "audit_logs",
    # B6（ADR-023）：组织邀请码。
    "organization_invitations",
}


def _database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        raise RuntimeError("TEST_DATABASE_URL must be set for migration tests")
    return value


def _alembic_config() -> Config:
    """`alembic/env.py` 从 `DATABASE_URL` 读连接串；conftest 已把它指向测试库。"""
    return Config(str(Path(__file__).parents[1] / "alembic.ini"))


def _assert_schema_after_upgrade(engine: Engine) -> None:
    with engine.connect() as connection:
        tables = set(
            connection.execute(
                text(
                    """
                    SELECT TABLE_NAME
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_TYPE = 'BASE TABLE'
                    """
                )
            ).scalars()
        )
        assert tables == EXPECTED_TABLES | {"alembic_version"}

        # ADR-013 §4：`self_rating` 已随迁移 20261107_0013 从库里删除。
        # 显式钉「列不存在」而不是靠「NOT NULL 写不进去」间接暴露——后者要等某次
        # INSERT 才失效，而 upgrade/downgrade 链路本身会一路绿灯。
        skill_columns = set(
            connection.execute(
                text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'skills'"
                )
            ).scalars()
        )
        assert "self_rating" not in skill_columns
        assert skill_columns == {
            "id",
            "student_profile_id",
            "name",
            # B4（ADR-015 §2.1）：全量替换 + 软删除的两列。
            "deleted_at",
            "active_marker",
            "created_at",
            "updated_at",
        }

        foreign_keys = dict(
            connection.execute(
                text(
                    """
                    SELECT CONSTRAINT_NAME, DELETE_RULE
                    FROM information_schema.REFERENTIAL_CONSTRAINTS
                    WHERE CONSTRAINT_SCHEMA = DATABASE()
                    """
                )
            ).all()
        )
        assert set(foreign_keys) == {
            "fk_student_profiles_user_id",
            "fk_skills_student_profile_id",
            "fk_experiences_student_profile_id",
            "fk_educations_student_profile_id",
            "fk_resumes_student_profile_id",
            # Phase 2 · D2.1：令牌是附属数据，用户被删时必须跟着删（CASCADE），
            # 而不是像档案那样挡住删除（RESTRICT）。
            "fk_refresh_tokens_user_id",
            # Phase 4 · B1（ADR-011 1.B / 1.D）：
            # 岗位是公开数据，删账号不该把别人看过的岗位一起带走 → RESTRICT；
            # 技能条目是岗位的组成部分，不独立存在 → CASCADE。
            "fk_jobs_created_by_user_id",
            "fk_job_skills_job_id",
            # B4（ADR-015 §2.1）：三张多值子表，一律 RESTRICT。
            "fk_profile_certificates_student_profile_id",
            "fk_portfolio_links_student_profile_id",
            "fk_profile_city_preferences_student_profile_id",
            # FR-07 / B11a：会话归属用户、可选绑定岗位，删账号/删岗位都不该带走面试历史 → RESTRICT；
            # turns 是会话的组成部分，不独立存在 → RESTRICT。
            "fk_interview_sessions_user_id",
            "fk_interview_sessions_job_id",
            "fk_interview_turns_session_id",
            # B8（ADR-015 §2.3）：全仓唯一非 RESTRICT 的外键——链头行本身可能被删，
            # RESTRICT 会变成「有指针就删不掉行」的死锁；SET NULL 让指针落空，
            # 读侧必须显式报错而不是回落猜一条。
            "fk_resumes_current_version_id",
            # B5（ADR-021 §2.1）：组织与成员关系。
            # 组织被删时成员关系挡住（RESTRICT）；用户被删时成员关系跟着走（CASCADE）——
            # 留一条指向不存在用户的成员行，会让「这个组织有几人」永远算不准。
            "fk_org_members_organization",
            "fk_org_members_user",
            # B5（ADR-021 §2.5）：审计表两条都是 SET NULL——记录本身要活下来，
            # 但操作者/组织被删后不该连带删掉「他做过什么」的证据。
            "fk_audit_logs_actor",
            "fk_audit_logs_organization",
            # B6（ADR-023）：邀请归属组织、发起人可追溯，删任一方都不该带走邀请记录。
            "fk_organization_invitations_organization",
            "fk_organization_invitations_creator",
            # B6 另一半（ADR-024 §2.8）：岗位的审核范围。可空（存量岗位不回溯，
            # 第 18 轮裁定 ①），但一旦指向某个组织就挡住删组织——审核记录不能
            # 指向一个已经不存在的组织。
            "fk_jobs_organization",
        }
        # 「附属数据跟着删」的外键；其余外键一律 RESTRICT 或 SET NULL（挡住删除）。
        for cascade_fk in (
            "fk_refresh_tokens_user_id",
            "fk_job_skills_job_id",
            "fk_org_members_user",
        ):
            assert foreign_keys.pop(cascade_fk) == "CASCADE"
        for set_null_fk in (
            "fk_resumes_current_version_id",
            "fk_audit_logs_actor",
            "fk_audit_logs_organization",
        ):
            assert foreign_keys.pop(set_null_fk) == "SET NULL"
        assert set(foreign_keys.values()) == {"RESTRICT"}
        assert foreign_keys["fk_jobs_created_by_user_id"] == "RESTRICT"

        checks = set(
            connection.execute(
                text(
                    """
                    SELECT cc.CONSTRAINT_NAME
                    FROM information_schema.CHECK_CONSTRAINTS AS cc
                    JOIN information_schema.TABLE_CONSTRAINTS AS tc
                      ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
                     AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
                    WHERE cc.CONSTRAINT_SCHEMA = DATABASE()
                      AND tc.TABLE_NAME = 'experiences'
                      AND tc.CONSTRAINT_TYPE = 'CHECK'
                    """
                )
            ).scalars()
        )
        assert "ck_experiences_date_range" in checks

        education_checks = set(
            connection.execute(
                text(
                    """
                    SELECT cc.CONSTRAINT_NAME
                    FROM information_schema.CHECK_CONSTRAINTS AS cc
                    JOIN information_schema.TABLE_CONSTRAINTS AS tc
                      ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
                     AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
                    WHERE cc.CONSTRAINT_SCHEMA = DATABASE()
                      AND tc.TABLE_NAME = 'educations'
                      AND tc.CONSTRAINT_TYPE = 'CHECK'
                    """
                )
            ).scalars()
        )
        assert "ck_educations_degree" in education_checks
        assert "ck_educations_date_range" in education_checks

        # Phase 4 · B1（ADR-011 1.B / 1.D）：状态、工作方式、薪资区间、技能分类与来源
        # 全部是「VARCHAR + CHECK」而不是 ENUM，四个约束名必须在库里。
        job_checks = set(
            connection.execute(
                text(
                    "SELECT cc.CONSTRAINT_NAME "
                    "FROM information_schema.CHECK_CONSTRAINTS AS cc "
                    "JOIN information_schema.TABLE_CONSTRAINTS AS tc "
                    "  ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA "
                    " AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME "
                    "WHERE cc.CONSTRAINT_SCHEMA = DATABASE() "
                    "  AND tc.TABLE_NAME = 'jobs' "
                    "  AND tc.CONSTRAINT_TYPE = 'CHECK'"
                )
            ).scalars()
        )
        assert job_checks == {"ck_jobs_status", "ck_jobs_employment_type", "ck_jobs_salary_range"}

        job_skill_checks = set(
            connection.execute(
                text(
                    "SELECT cc.CONSTRAINT_NAME "
                    "FROM information_schema.CHECK_CONSTRAINTS AS cc "
                    "JOIN information_schema.TABLE_CONSTRAINTS AS tc "
                    "  ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA "
                    " AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME "
                    "WHERE cc.CONSTRAINT_SCHEMA = DATABASE() "
                    "  AND tc.TABLE_NAME = 'job_skills' "
                    "  AND tc.CONSTRAINT_TYPE = 'CHECK'"
                )
            ).scalars()
        )
        assert job_skill_checks == {
            "ck_job_skills_type",
            # 只有 jd_verifiable / unverified 两个值（1.D v7 改的名）：`user_edited` 早已
            # 从枚举删除，断言用 == 而不是 in，多一个值就红。约束名本轮不改。
            "ck_job_skills_origin",
            "ck_job_skills_confidence",
        }

        # 「谁都不该给 created_at 单建一条索引」——这条断言的本意是挡**单列**时间索引
        # （单列 `created_at` 索引在本项目没有任何查询会用，纯写放大）。
        # Phase 4 · B1 引入了 ADR-011 1.B 要求的**复合**索引
        # `ix_jobs_status_created (status, created_at)`：`created_at` 是第二列，
        # 服务的是「按状态筛完再按时间倒序」这条真实查询。所以判据收紧为
        # 「索引只有 created_at 一列」，复合索引不再命中——不是把断言删掉，
        # 而是让它继续挡住原来要挡的东西。
        single_column_created_at_indexes = connection.execute(
            text(
                """
                SELECT s.TABLE_NAME, s.INDEX_NAME
                FROM information_schema.STATISTICS AS s
                WHERE s.TABLE_SCHEMA = DATABASE()
                  AND s.COLUMN_NAME = 'created_at'
                  AND (
                    SELECT COUNT(*)
                    FROM information_schema.STATISTICS AS t
                    WHERE t.TABLE_SCHEMA = s.TABLE_SCHEMA
                      AND t.TABLE_NAME = s.TABLE_NAME
                      AND t.INDEX_NAME = s.INDEX_NAME
                  ) = 1
                """
            )
        ).all()
        assert single_column_created_at_indexes == []

        # 岗位列表的驱动索引必须存在且列序为 (status, created_at)：
        # 反过来 (created_at, status) 无法满足「先按状态筛」，索引就白建了。
        job_index_columns = connection.execute(
            text(
                """
                SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'jobs'
                  AND INDEX_NAME IN ('ix_jobs_status_created', 'ix_jobs_creator')
                ORDER BY INDEX_NAME, SEQ_IN_INDEX
                """
            )
        ).all()
        assert [
            (row.INDEX_NAME, row.SEQ_IN_INDEX, row.COLUMN_NAME) for row in job_index_columns
        ] == [
            ("ix_jobs_creator", 1, "created_by_user_id"),
            ("ix_jobs_creator", 2, "deleted_at"),
            ("ix_jobs_status_created", 1, "status"),
            ("ix_jobs_status_created", 2, "created_at"),
        ]

        # `job_skills` 的两个数据库级契约（ADR-011 1.D）：
        # - `uq_job_skills_job_name` 唯一：去重由数据库保证，不是应用层的自觉；
        #   三种写法（大小写/全半角）是否真的撞同一键，见 `test_jobs_constraints.py`
        #   —— 那是排序规则的行为，本文件只断言「这条唯一约束在」。
        # - `ix_job_skills_name`：详情页「你已具备」按技能名比对（2.E）。
        job_skill_indexes = {
            row.INDEX_NAME: int(row.NON_UNIQUE)
            for row in connection.execute(
                text(
                    """
                    SELECT INDEX_NAME, NON_UNIQUE
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'job_skills'
                    """
                )
            ).all()
        }
        assert int(job_skill_indexes["uq_job_skills_job_name"]) == 0
        assert "ix_job_skills_name" in job_skill_indexes

        # refresh_tokens.token_hash 必须唯一：既是查询索引，也保证同一令牌
        # 不会在轮换时被写成两条都能用的记录。
        refresh_token_indexes = dict(
            connection.execute(
                text(
                    """
                    SELECT INDEX_NAME, NON_UNIQUE
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'refresh_tokens'
                    """
                )
            ).all()
        )
        assert int(refresh_token_indexes["uq_refresh_tokens_token_hash"]) == 0
        assert "ix_refresh_tokens_user_id" in refresh_token_indexes

        # B1 / ADR-016 §2.2：`skill_aliases` 的列名即契约，所以逐列钉死。
        # 「列名即契约」不是修辞——别名对存在 `skill_name_key` 的输出空间里这件事，
        # 只有靠列名才能在下一次读代码时仍然看得出来（叫 `name` 就会有人往里写原始写法）。
        alias_columns = set(
            connection.execute(
                text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'skill_aliases'"
                )
            ).scalars()
        )
        assert alias_columns == {
            "id",
            "canonical_key",
            "alias_key",
            "scope",
            "source",
            "note",
            "created_at",
            "updated_at",
        }

        # 唯一键必须是 **(canonical_key, alias_key, scope) 三列、且真的唯一**：
        # 少了 `scope` 就会让「技能域的同义」与「角色域的同义」互相顶掉——
        # 同一对 key 在两个域里各有含义，这是 §2.2 那一格写 `scope` 的全部理由。
        # 不唯一则「单向存一行、判定双向查」失去依据，重复行会静默改变词表语义。
        alias_unique_columns = connection.execute(
            text(
                """
                SELECT s.SEQ_IN_INDEX, s.COLUMN_NAME, s.NON_UNIQUE
                FROM information_schema.STATISTICS AS s
                WHERE s.TABLE_SCHEMA = DATABASE()
                  AND s.TABLE_NAME = 'skill_aliases'
                  AND s.INDEX_NAME = 'uq_skill_aliases_pair'
                ORDER BY s.SEQ_IN_INDEX
                """
            )
        ).all()
        assert [(int(row.SEQ_IN_INDEX), row.COLUMN_NAME) for row in alias_unique_columns] == [
            (1, "canonical_key"),
            (2, "alias_key"),
            (3, "scope"),
        ]
        assert all(int(row.NON_UNIQUE) == 0 for row in alias_unique_columns), (
            "uq_skill_aliases_pair 不是唯一索引：双向查的语义就没有依据了"
        )

        # `scope` / `source` 两个值域由数据库挡（与 `jobs.status`、`job_skills.origin` 同一做法：
        # VARCHAR + CHECK 而不是 ENUM，加一个值不必改表类型）。
        alias_checks = set(
            connection.execute(
                text(
                    "SELECT cc.CONSTRAINT_NAME "
                    "FROM information_schema.CHECK_CONSTRAINTS AS cc "
                    "JOIN information_schema.TABLE_CONSTRAINTS AS tc "
                    "  ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA "
                    " AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME "
                    "WHERE cc.CONSTRAINT_SCHEMA = DATABASE() "
                    "  AND tc.TABLE_NAME = 'skill_aliases' "
                    "  AND tc.CONSTRAINT_TYPE = 'CHECK'"
                )
            ).scalars()
        )
        assert alias_checks == {"ck_skill_aliases_scope", "ck_skill_aliases_source"}

        # 这张表**没有外键**，而且是决定而不是遗漏（迁移 docstring 第「没有外键」那段）：
        # `canonical_key` 指向的是归一后的键，不是某张表的某一行。有了外键就等于把全库共享的
        # 词表错绑成「某个岗位的技能条目」，那是 §2.1 否掉 (a) 时同一种病的另一个形状。
        alias_foreign_keys = connection.execute(
            text(
                """
                SELECT CONSTRAINT_NAME
                FROM information_schema.REFERENTIAL_CONSTRAINTS
                WHERE CONSTRAINT_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'skill_aliases'
                """
            )
        ).all()
        assert alias_foreign_keys == []

        # ------------------------------------------------------------------ #
        # B4 + B8（ADR-015 §2.1 / §2.3 / §5）：软删除两列、active_marker 唯一键、
        # 三张新子表的列名即契约、resumes 版本指针与链头唯一。
        # ------------------------------------------------------------------ #
        for table in ("skills", "experiences", "educations"):
            columns = {
                row.COLUMN_NAME: row
                for row in connection.execute(
                    text(
                        "SELECT COLUMN_NAME, EXTRA, GENERATION_EXPRESSION "
                        "FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
                    ),
                    {"table": table},
                ).all()
            }
            assert "deleted_at" in columns, f"{table} 缺软删除列"
            marker = columns["active_marker"]
            assert "STORED GENERATED" in str(marker.EXTRA), (
                f"{table}.active_marker 必须是 STORED 生成列"
            )
            assert "if" in str(marker.GENERATION_EXPRESSION).lower()

        # 唯一键必须是含 active_marker 的三元组：少了它，「删掉再加回同名」撞 1062；
        # 历史行（marker=NULL）靠 MySQL「唯一索引允许多个 NULL」自由重复。
        for table, index, expected in (
            ("skills", "uq_skills_profile_name", ["student_profile_id", "name", "active_marker"]),
            (
                "profile_certificates",
                "uq_profile_certificates_profile_name",
                ["student_profile_id", "name", "active_marker"],
            ),
            (
                "portfolio_links",
                "uq_portfolio_links_profile_url",
                ["student_profile_id", "url", "active_marker"],
            ),
            (
                "profile_city_preferences",
                "uq_profile_city_preferences_profile_city",
                ["student_profile_id", "city", "active_marker"],
            ),
            ("resumes", "uq_resumes_profile_head", ["student_profile_id", "is_head"]),
        ):
            rows = connection.execute(
                text(
                    """
                    SELECT SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = :table AND INDEX_NAME = :index
                    ORDER BY SEQ_IN_INDEX
                    """
                ),
                {"table": table, "index": index},
            ).all()
            assert [row.COLUMN_NAME for row in rows] == expected, f"{table}.{index} 列序不对"
            assert all(int(row.NON_UNIQUE) == 0 for row in rows), f"{table}.{index} 不是唯一索引"

        # 三张新子表的列名即契约（ADR-015 §2.1 / §5 ③ 逐字）。
        for table, expected_columns in (
            (
                "profile_certificates",
                {
                    "id",
                    "student_profile_id",
                    "name",
                    "issuer",
                    "issued_on",
                    "deleted_at",
                    "active_marker",
                    "created_at",
                    "updated_at",
                },
            ),
            (
                "portfolio_links",
                {
                    "id",
                    "student_profile_id",
                    "label",
                    "url",
                    "deleted_at",
                    "active_marker",
                    "created_at",
                    "updated_at",
                },
            ),
            (
                "profile_city_preferences",
                {
                    "id",
                    "student_profile_id",
                    "city",
                    "deleted_at",
                    "active_marker",
                    "created_at",
                    "updated_at",
                },
            ),
        ):
            columns = set(
                connection.execute(
                    text(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
                    ),
                    {"table": table},
                ).scalars()
            )
            assert columns == expected_columns, f"{table} 列集合与 ADR-015 不一致"

        resume_columns = {
            row.COLUMN_NAME: row
            for row in connection.execute(
                text(
                    "SELECT COLUMN_NAME, EXTRA, COLUMN_DEFAULT, IS_NULLABLE "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'resumes'"
                )
            ).all()
        }
        assert {"version_no", "current_version_id", "is_head"} <= set(resume_columns)
        assert resume_columns["version_no"].IS_NULLABLE == "NO"
        assert str(resume_columns["version_no"].COLUMN_DEFAULT) == "1"
        assert resume_columns["current_version_id"].IS_NULLABLE == "YES"
        # is_head 是 VIRTUAL 生成列（不是 ADR 字面的 STORED）：MySQL 禁止 STORED
        # 生成列的基列挂 ON DELETE SET NULL 外键（1215，实测），而 SET NULL 是
        # ADR-015 §2.3 的硬契约。偏离理由见迁移 20261110_0016 的 docstring。
        assert "VIRTUAL GENERATED" in str(resume_columns["is_head"].EXTRA)

        triggers = connection.execute(
            text(
                """
                SELECT TRIGGER_NAME
                FROM information_schema.TRIGGERS
                WHERE TRIGGER_SCHEMA = DATABASE()
                """
            )
        ).all()
        assert triggers == []

        relevant_checks = connection.execute(
            text(
                """
                SELECT CONSTRAINT_NAME, CHECK_CLAUSE
                FROM information_schema.CHECK_CONSTRAINTS
                WHERE CONSTRAINT_SCHEMA = DATABASE()
                  AND (
                    CHECK_CLAUSE LIKE '%email%'
                    OR CHECK_CLAUSE LIKE '%contact%'
                  )
                """
            )
        ).all()
        assert relevant_checks == []


def _assert_schema_removed(engine: Engine) -> None:
    with engine.connect() as connection:
        remaining_tables = set(
            connection.execute(
                text(
                    """
                    SELECT TABLE_NAME
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_TYPE = 'BASE TABLE'
                    """
                )
            ).scalars()
        )
    assert remaining_tables == {"alembic_version"}
    # T7.5 的 downgrade 必须真的把 educations 删掉，而不是留在库里。
    assert "educations" not in remaining_tables
    # B1 的别名表同样必须真的被 `20261109_0015` 的 downgrade 删掉：
    # 残留一张「有结构但没有词表」的表会让下一次 upgrade 之前的任何写入口撞 1146，
    # 而 `alias_version` 那一格仍然照发 v1——屏幕上一切正常，库里已经不对。
    assert "skill_aliases" not in remaining_tables
    # B4 的三张多值子表（ADR-015）同理必须真的被 `20261110_0016` 的 downgrade 删掉。
    for table in ("profile_certificates", "portfolio_links", "profile_city_preferences"):
        assert table not in remaining_tables


def test_upgrade_schema_constraints_and_downgrade() -> None:
    config = _alembic_config()
    engine = create_engine(_database_url(), pool_pre_ping=True)

    try:
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        _assert_schema_after_upgrade(engine)

        command.downgrade(config, "base")
        _assert_schema_removed(engine)
    finally:
        # 无论断言是否失败，都把库交回 head（见模块 docstring）。
        command.upgrade(config, "head")
        engine.dispose()
