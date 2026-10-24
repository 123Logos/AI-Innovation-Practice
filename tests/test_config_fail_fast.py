import asyncio
from typing import Self

import pytest

from app.core.config import Settings
from app.main import create_app


def test_production_requires_database_url() -> None:
    with pytest.raises(ValueError, match="database_url is required"):
        Settings(environment="production", database_url=None)


def test_development_allows_missing_database_url() -> None:
    settings = Settings(environment="development", database_url=None)

    assert settings.database_url is None


def test_lifespan_probes_configured_database(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main_module

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

    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(environment="test", database_url="mysql+pymysql://test"),
    )
    monkeypatch.setattr(main_module, "create_engine", lambda _: Engine())

    async def run_lifespan() -> None:
        async with main_module.lifespan(create_app()):
            pass

    asyncio.run(run_lifespan())

    assert calls == ["connect", "SELECT 1", "close"]
