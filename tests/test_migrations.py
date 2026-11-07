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
    "job_skills",
    "jobs",
    "refresh_tokens",
    "resumes",
    "skills",
    "student_profiles",
    "users",
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
        }
        # 「附属数据跟着删」的两张表；其余外键一律 RESTRICT（挡住删除）。
        for cascade_fk in ("fk_refresh_tokens_user_id", "fk_job_skills_job_id"):
            assert foreign_keys.pop(cascade_fk) == "CASCADE"
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
