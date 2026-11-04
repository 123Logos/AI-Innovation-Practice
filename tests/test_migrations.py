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
        }
        assert foreign_keys.pop("fk_refresh_tokens_user_id") == "CASCADE"
        assert set(foreign_keys.values()) == {"RESTRICT"}

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

        created_at_indexes = connection.execute(
            text(
                """
                SELECT TABLE_NAME, INDEX_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND COLUMN_NAME = 'created_at'
                """
            )
        ).all()
        assert created_at_indexes == []

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
