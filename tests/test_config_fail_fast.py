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


def test_production_requires_redis_url() -> None:
    """第 30 轮（`ADR-033`）：生产没有队列地址就**启动即失败**。

    理由与 `database_url` 同一条：`ADR-033` 之后导入与批量导出挂在队列上，
    生产跑起来却没有 Redis，表现是「用户点导入 → 503」或「任务永远 pending」，
    两者都不该等到上线才发现。开发/测试环境不受影响（没配就只是这两条功能 503）。

    **`redis_url=None` 必须显式传**：`pydantic-settings` 会把仓库根的 `.env` 填进没传的字段，
    本机 `.env` 里恰好有 `REDIS_URL`——不写这一格，用例会在有 `.env` 的机器上恒绿、
    在 CI 裸环境里才红（与 `test_production_requires_database_url` 显式传 `None` 同一个理由）。
    """
    with pytest.raises(ValueError, match="redis_url is required"):
        Settings(
            environment="production",
            database_url="mysql+pymysql://test",
            jwt_secret_key="fake-secret-for-testing-only-1234567890",
            redis_url=None,
        )


def test_development_allows_missing_redis_url() -> None:
    """开发环境没配 Redis 也要能起来：只剩两条任务端点 503，其它功能照常。"""
    settings = Settings(environment="development", redis_url=None)

    assert settings.redis_url is None


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
    """生产环境下迁移落后必须阻断启动。

    这里构造的是一个**合法**的 production `Settings`，因为断言针对的是
    `check_migration_state`，而不是配置校验本身。所以 production 的必填项
    都必须显式给齐，否则在没有 `.env` 的 CI 裸环境里会先在 `Settings()`
    抛 `jwt_secret_key is required`（注入了 DATABASE_URL 时）、
    `database_url is required`（连它也没有时）或 `redis_url is required`
    （第 30 轮新增，`ADR-033`），用例根本走不到断言。

    `database_url` / `redis_url` 都是占位值：本用例传入的是 `_FakeConnection`，不会真的连库或连队列。
    """
    settings = Settings(
        environment="production",
        database_url="mysql+pymysql://test",
        jwt_secret_key="fake-secret-for-testing-only-1234567890",
        redis_url="redis://127.0.0.1:6379/0",
    )

    with pytest.raises(main_module.DatabaseSchemaOutOfDateError, match="迁移|版本不一致"):
        main_module.check_migration_state(_FakeConnection([]), settings)
