import os
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

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


def _alembic_config(database_url: str) -> Config:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    os.environ["DATABASE_URL"] = database_url
    return config


def test_upgrade_schema_constraints_and_downgrade() -> None:
    database_url = _database_url()
    config = _alembic_config(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)

    command.downgrade(config, "base")
    command.upgrade(config, "head")

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

    command.downgrade(config, "base")

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
