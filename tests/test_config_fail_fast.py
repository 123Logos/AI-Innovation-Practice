import asyncio
from collections.abc import Iterator
from typing import Self

import pytest

import app.main as main_module
from app.core.config import Settings
from app.main import create_app


def test_production_requires_database_url() -> None:
    with pytest.raises(ValueError, match="database_url is required"):
        Settings(environment="production", database_url=None)


def test_development_allows_missing_database_url() -> None:
    settings = Settings(environment="development", database_url=None)

    assert settings.database_url is None


def test_lifespan_probes_configured_database(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Connection:
        def __enter__(self) -> Self:
            calls.append("connect")
            return self

        def __exit__(self, *_: object) -> None:
            calls.append("close")

        def execute(self, query: object) -> None:
            calls.append(str(query))

    class Engine:
        def connect(self) -> Connection:
            return Connection()

        def dispose(self) -> None:
            calls.append("dispose")

    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(environment="test", database_url="mysql+pymysql://test"),
    )
    monkeypatch.setattr(main_module, "create_engine", lambda _: Engine())
    # 迁移版本校验有自己的用例（见文件末尾），这里只验证「启动会探一次库」。
    monkeypatch.setattr(
        main_module, "check_migration_state", lambda *_: calls.append("migration-check")
    )

    async def run_lifespan() -> None:
        async with main_module.lifespan(create_app()):
            pass

    asyncio.run(run_lifespan())

    assert calls == ["connect", "SELECT 1", "migration-check", "close", "dispose"]


class _FakeResult:
    def __init__(self, revisions: list[str]) -> None:
        self._revisions = revisions

    def scalars(self) -> Iterator[str]:
        return iter(self._revisions)


class _FakeConnection:
    """只实现 `check_migration_state` 真正用到的那一个方法。"""

    def __init__(self, revisions: list[str]) -> None:
        self._revisions = revisions

    def execute(self, *_: object) -> _FakeResult:
        return _FakeResult(self._revisions)


def test_migration_check_silent_when_database_matches_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    head = main_module.expected_migration_revisions()
    assert head, "alembic/versions 下应当至少有一个 head"

    main_module.check_migration_state(_FakeConnection(head), Settings(environment="development"))

    assert capsys.readouterr().err == ""


def test_migration_check_warns_in_development(capsys: pytest.CaptureFixture[str]) -> None:
    main_module.check_migration_state(
        _FakeConnection(["20200101_0000"]), Settings(environment="development")
    )

    stderr = capsys.readouterr().err
    assert "数据库结构与代码版本不一致" in stderr
    assert "uv run alembic upgrade head" in stderr


def test_migration_check_blocks_startup_in_production() -> None:
    with pytest.raises(main_module.DatabaseSchemaOutOfDateError, match="迁移|版本不一致"):
        main_module.check_migration_state(_FakeConnection([]), Settings(environment="production"))
