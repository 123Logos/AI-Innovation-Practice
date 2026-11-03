"""T7.1 集成测试：注册 / 登录 / JWT 鉴权，连接真实 MySQL（TEST_DATABASE_URL）。

铁律要求不得用 SQLite 顶替：本文件先把 TEST_DATABASE_URL 注入 DATABASE_URL，
再导入应用，因此应用内所有连接都指向测试库；测试前后自动建表与清理。
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    # 必须早于任何 app.* 导入，否则 get_settings() 会缓存 .env 里的开发库连接串。
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token
from app.main import app
from app.repositories.user_repository import Base, dispose_engine, get_engine

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"
PASSWORD = "Passw0rd!2026"


def _unique_username(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def _register(
    client: TestClient,
    username: str,
    password: str = PASSWORD,
    email: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"username": username, "password": password}
    if email is not None:
        payload["email"] = email
    response = client.post(REGISTER_URL, json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()["data"]["user"]
    return body


def _login(client: TestClient, username: str, password: str = PASSWORD) -> str:
    response = client.post(LOGIN_URL, json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    token: str = response.json()["data"]["access_token"]
    assert token
    return token


def _count_users(username: str) -> int:
    with get_engine().connect() as connection:
        value = connection.execute(
            text("SELECT COUNT(*) FROM users WHERE username = :username"),
            {"username": username},
        ).scalar_one()
    return int(value)


def _stored_username(user_id: str) -> str:
    """取回库里的用户名**原文**（按主键查）。

    刻意不写成 `WHERE username = :username`：该列是 `utf8mb4_0900_ai_ci`，
    `ZhangSan` 与 `zhangsan` 在它眼里相等，那样根本断言不了「存的是哪一种大小写」。
    """
    with get_engine().connect() as connection:
        value = connection.execute(
            text("SELECT username FROM users WHERE id = :id"),
            {"id": user_id},
        ).scalar_one()
    return str(value)


@pytest.fixture(scope="module", autouse=True)
def _schema() -> Iterator[None]:
    if not TEST_DATABASE_URL:
        raise RuntimeError("TEST_DATABASE_URL must be set for auth integration tests")
    engine = get_engine()
    Base.metadata.create_all(engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(engine)
        dispose_engine()


@pytest.fixture(autouse=True)
def _clean_users() -> Iterator[None]:
    with get_engine().begin() as connection:
        connection.execute(text("DELETE FROM users"))
    yield
    with get_engine().begin() as connection:
        connection.execute(text("DELETE FROM users"))


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_engine_points_to_mysql_test_database() -> None:
    """证明测试跑在 TEST_DATABASE_URL 指向的真实 MySQL 上，不是 SQLite。"""
    assert TEST_DATABASE_URL is not None
    engine = get_engine()
    expected_database = urlsplit(TEST_DATABASE_URL.split("?")[0]).path.lstrip("/")

    assert engine.dialect.name == "mysql"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
        assert connection.execute(text("SELECT DATABASE()")).scalar_one() == expected_database


def test_register_persists_user_in_database(client: TestClient) -> None:
    username = _unique_username("alice")
    email = f"{username}@example.com"

    user = _register(client, username, email=email)

    assert user["username"] == username
    assert user["email"] == email
    assert sorted(user) == ["created_at", "email", "id", "username"]
    assert _count_users(username) == 1

    with get_engine().connect() as connection:
        row = connection.execute(
            text("SELECT id, username, email, password_hash FROM users WHERE username = :username"),
            {"username": username},
        ).one()
    assert row.id == user["id"]
    assert row.email == email
    assert row.password_hash.startswith("$2b$")
    assert PASSWORD not in row.password_hash
    assert row.password_hash != PASSWORD


def test_register_lowercases_username(client: TestClient) -> None:
    """Phase 2 · D1：注册即归一为小写，库里存的就是小写。

    断言值用「按主键取原文」的方式读，不能靠 `WHERE username = ?` 判断——
    `utf8mb4_0900_ai_ci` 下大小写本来就相等，那样写等于什么都没验。
    """
    username = _unique_username("ZhangSan")
    assert username != username.lower()  # 前提：这个用例真的带了大写

    user = _register(client, username)

    assert user["username"] == username.lower()
    assert _stored_username(str(user["id"])) == username.lower()


def test_register_duplicate_username_returns_400(client: TestClient) -> None:
    username = _unique_username("bob")
    _register(client, username, email=f"{username}@example.com")

    response = client.post(
        REGISTER_URL,
        json={
            "username": username,
            "password": PASSWORD,
            "email": f"other_{username}@example.com",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "CLIENT_ERROR"
    assert body["error"]["message"]
    assert body["meta"]["request_id"]
    assert _count_users(username) == 1


def test_register_duplicate_email_returns_400(client: TestClient) -> None:
    email = f"{uuid4().hex[:10]}@example.com"
    _register(client, _unique_username("carol"), email=email)

    response = client.post(
        REGISTER_URL,
        json={
            "username": _unique_username("dave"),
            "password": PASSWORD,
            "email": email,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "CLIENT_ERROR"
    assert body["error"]["message"]


def test_register_rejects_short_password(client: TestClient) -> None:
    response = client.post(
        REGISTER_URL,
        json={"username": _unique_username("erin"), "password": "short"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_login_returns_bearer_token(client: TestClient) -> None:
    username = _unique_username("frank")
    user = _register(client, username)

    response = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 900
    assert data["access_token"].count(".") == 2
    claims = jwt.decode(data["access_token"], options={"verify_signature": False})
    assert claims["sub"] == user["id"]
    assert claims["type"] == "access"


def test_login_is_case_insensitive(client: TestClient) -> None:
    """Phase 2 · D1：注册时敲了什么大小写，登录时任意大小写都能进。

    这条测试是「守护」而不是「发现」：在 D1 之前，大小写不敏感是 MySQL 的
    `utf8mb4_0900_ai_ci` 隐式给的，代码里没有任何一行表达这个意图，
    也没有任何测试会因为排序规则变化而失败。现在归一是显式代码，测试锁住行为。
    """
    username = _unique_username("ZhangSan")
    user = _register(client, username)
    assert user["username"] == username.lower()

    for attempt in (username.lower(), username.upper(), username.swapcase()):
        response = client.post(LOGIN_URL, json={"username": attempt, "password": PASSWORD})
        assert response.status_code == 200, f"{attempt} -> {response.text}"
        token = response.json()["data"]["access_token"]
        assert token.count(".") == 2
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["sub"] == user["id"]


def test_login_with_wrong_password_returns_401(client: TestClient) -> None:
    username = _unique_username("grace")
    _register(client, username)

    response = client.post(LOGIN_URL, json={"username": username, "password": "WrongPass!2026"})

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "CLIENT_ERROR"
    assert body["error"]["message"]
    assert body["meta"]["request_id"]
    assert "access_token" not in response.text


def test_login_with_unknown_username_returns_401(client: TestClient) -> None:
    response = client.post(
        LOGIN_URL,
        json={"username": _unique_username("nobody"), "password": PASSWORD},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "CLIENT_ERROR"


def test_me_returns_user_with_valid_token(client: TestClient) -> None:
    username = _unique_username("heidi")
    user = _register(client, username, email=f"{username}@example.com")
    token = _login(client, username)

    response = client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    current_user = response.json()["data"]["user"]
    assert current_user["id"] == user["id"]
    assert current_user["username"] == username
    assert "password_hash" not in current_user


def test_me_without_token_returns_401(client: TestClient) -> None:
    response = client.get(ME_URL)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "CLIENT_ERROR"
    assert response.json()["error"]["message"]


def test_me_with_non_bearer_scheme_returns_401(client: TestClient) -> None:
    response = client.get(ME_URL, headers={"Authorization": "Token abc.def.ghi"})

    assert response.status_code == 401


def test_me_with_invalid_token_returns_401(client: TestClient) -> None:
    username = _unique_username("ivan")
    user = _register(client, username)
    valid_token = _login(client, username)

    now = datetime.now(UTC)
    foreign_secret_token = jwt.encode(
        {
            "sub": str(user["id"]),
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=15),
        },
        "attacker-secret-key-with-enough-length-32",
        algorithm="HS256",
    )
    expired_token = create_access_token(str(user["id"]), expires_minutes=-1)
    unknown_subject_token = create_access_token(str(uuid4()))
    tampered_token = f"{valid_token[:-4]}beef"

    for token in (
        "not-a-jwt",
        "a.b.c",
        foreign_secret_token,
        expired_token,
        unknown_subject_token,
        tampered_token,
    ):
        response = client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token
        assert response.json()["error"]["code"] == "CLIENT_ERROR"
        assert response.json()["meta"]["request_id"]
