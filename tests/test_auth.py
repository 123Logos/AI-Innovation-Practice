"""T7.1 集成测试：注册 / 登录 / JWT 鉴权，连接真实 MySQL（TEST_DATABASE_URL）。

铁律要求不得用 SQLite 顶替：测试库连接串、建表与用例间清理统一由
`tests/conftest.py` 负责（注入 `DATABASE_URL`、会话级 `upgrade head`、逐用例清表），
本文件只写用例，不再自己建表/删表。
"""

import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import create_access_token, create_refresh_token
from app.repositories.user_repository import get_engine

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
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


def _login_tokens(client: TestClient, username: str, password: str = PASSWORD) -> dict[str, object]:
    """登录并返回完整响应体（access + refresh），供双 Token 用例复用。"""
    response = client.post(LOGIN_URL, json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    data: dict[str, object] = response.json()["data"]
    return data


def _refresh_rows(user_id: str) -> list[dict[str, object]]:
    """按创建顺序取该用户的全部令牌记录（只取库里的**哈希**，取不到明文）。"""
    with get_engine().connect() as connection:
        rows = (
            connection.execute(
                text(
                    """
                    SELECT token_hash, expires_at, revoked_at, created_at
                    FROM refresh_tokens
                    WHERE user_id = :user_id
                    ORDER BY created_at, id
                    """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_engine_points_to_mysql_test_database(test_database_url: str) -> None:
    """证明测试跑在 TEST_DATABASE_URL 指向的真实 MySQL 上，不是 SQLite。"""
    engine = get_engine()
    expected_database = urlsplit(test_database_url.split("?")[0]).path.lstrip("/")

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


def test_login_issues_refresh_token_and_stores_only_hash(client: TestClient) -> None:
    """Phase 2 · D2.1：登录发双 Token，库里只留 refresh token 的 SHA-256 哈希。"""
    username = _unique_username("judy")
    user = _register(client, username)
    data = _login_tokens(client, username)

    assert data["access_token"].count(".") == 2
    assert data["refresh_token"].count(".") == 2
    assert data["expires_in"] == 900
    access_claims = jwt.decode(str(data["access_token"]), options={"verify_signature": False})
    refresh_claims = jwt.decode(str(data["refresh_token"]), options={"verify_signature": False})
    assert access_claims["type"] == "access"
    assert refresh_claims["type"] == "refresh"
    assert refresh_claims["sub"] == user["id"]

    rows = _refresh_rows(str(user["id"]))
    assert len(rows) == 1
    row = rows[0]
    plaintext = str(data["refresh_token"])
    assert row["token_hash"] == _sha256(plaintext)
    assert row["token_hash"] != plaintext
    assert len(str(row["token_hash"])) == 64
    assert row["revoked_at"] is None
    # 库里的任何一列都不该出现明文令牌。
    assert plaintext not in str(rows)

    expires_at = row["expires_at"]
    assert isinstance(expires_at, datetime)
    # 7 天有效期（给 1 小时容差，避免测试机时钟/取整干扰）。
    remaining = expires_at - datetime.now(UTC).replace(tzinfo=None)
    assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7, hours=1)


def test_login_twice_issues_distinct_refresh_tokens(client: TestClient) -> None:
    """回归：同一秒内为同一用户登录两次，两条 refresh token 也必须不同。

    JWT 的 `iat` / `exp` 精度只到秒，若载荷里没有随机 `jti`，这两条令牌会
    逐字节相同，`token_hash` 撞唯一索引 → 登录直接 500。
    这条用例就是那个 bug 的看门人（本轮实现时真的踩到了）。
    """
    username = _unique_username("peggy")
    user = _register(client, username)

    first = _login_tokens(client, username)
    second = _login_tokens(client, username)

    assert first["refresh_token"] != second["refresh_token"]
    rows = _refresh_rows(str(user["id"]))
    assert len(rows) == 2
    assert {str(row["token_hash"]) for row in rows} == {
        _sha256(str(first["refresh_token"])),
        _sha256(str(second["refresh_token"])),
    }


def test_refresh_rotates_token_pair(client: TestClient) -> None:
    """用 refresh token 换新：拿到的是一对全新令牌，旧记录被撤销。"""
    username = _unique_username("karl")
    user = _register(client, username)
    first = _login_tokens(client, username)

    response = client.post(REFRESH_URL, json={"refresh_token": first["refresh_token"]})

    assert response.status_code == 200, response.text
    second = response.json()["data"]
    assert second["access_token"] != first["access_token"]
    assert second["refresh_token"] != first["refresh_token"]
    assert second["token_type"] == "bearer"
    assert second["expires_in"] == 900
    claims = jwt.decode(second["access_token"], options={"verify_signature": False})
    assert claims["sub"] == user["id"]
    assert claims["type"] == "access"

    rows = _refresh_rows(str(user["id"]))
    assert len(rows) == 2
    old_row = next(row for row in rows if row["token_hash"] == _sha256(str(first["refresh_token"])))
    new_row = next(
        row for row in rows if row["token_hash"] == _sha256(str(second["refresh_token"]))
    )
    assert old_row["revoked_at"] is not None
    assert new_row["revoked_at"] is None

    # 新 access token 立刻可用于鉴权。
    me = client.get(ME_URL, headers={"Authorization": f"Bearer {second['access_token']}"})
    assert me.status_code == 200
    assert me.json()["data"]["user"]["id"] == user["id"]

    # 新 refresh token 还能继续换下一对（链式轮换不会把自己锁死）。
    third = client.post(REFRESH_URL, json={"refresh_token": second["refresh_token"]})
    assert third.status_code == 200, third.text
    assert third.json()["data"]["refresh_token"] != second["refresh_token"]


def test_refresh_rejects_reused_token(client: TestClient) -> None:
    """复用测试：同一条 refresh token 第二次使用必须 401（已被轮换撤销）。"""
    username = _unique_username("laura")
    user = _register(client, username)
    first = _login_tokens(client, username)

    assert (
        client.post(REFRESH_URL, json={"refresh_token": first["refresh_token"]}).status_code == 200
    )

    replay = client.post(REFRESH_URL, json={"refresh_token": first["refresh_token"]})

    assert replay.status_code == 401, replay.text
    body = replay.json()
    assert body["error"]["code"] == "CLIENT_ERROR"
    assert body["error"]["message"]
    assert body["meta"]["request_id"]
    assert "access_token" not in replay.text
    # 重放被拒后，库里不会多出记录（说明没有在拒绝前先发新令牌）。
    assert len(_refresh_rows(str(user["id"]))) == 2


def test_refresh_rejects_access_token(client: TestClient) -> None:
    """拿 access token 冒充 refresh token：类型不匹配直接 401。"""
    username = _unique_username("mallory")
    _register(client, username)
    data = _login_tokens(client, username)

    response = client.post(REFRESH_URL, json={"refresh_token": data["access_token"]})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "CLIENT_ERROR"


def test_refresh_rejects_unknown_refresh_token(client: TestClient) -> None:
    """签名合法但库里没有的 refresh token（用户注销、伪造主体）一律 401。"""
    response = client.post(REFRESH_URL, json={"refresh_token": create_refresh_token(str(uuid4()))})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "CLIENT_ERROR"


def test_refresh_rejects_malformed_token(client: TestClient) -> None:
    for token in ("not-a-jwt", "a.b.c", ""):
        response = client.post(REFRESH_URL, json={"refresh_token": token})
        assert response.status_code in {401, 422}, token
        assert "access_token" not in response.text


def test_logout_revokes_refresh_token(client: TestClient) -> None:
    """登出把 refresh token 置为已撤销；登出本身幂等，重复调用不报错。"""
    username = _unique_username("niaj")
    user = _register(client, username)
    data = _login_tokens(client, username)

    response = client.post(LOGOUT_URL, json={"refresh_token": data["refresh_token"]})

    assert response.status_code == 204
    assert response.content == b""
    rows = _refresh_rows(str(user["id"]))
    assert len(rows) == 1
    assert rows[0]["revoked_at"] is not None

    # 已撤销的令牌不能再换新。
    refresh = client.post(REFRESH_URL, json={"refresh_token": data["refresh_token"]})
    assert refresh.status_code == 401

    # 幂等：重复登出、或拿一个根本不存在的令牌登出，都返回 204。
    assert client.post(LOGOUT_URL, json={"refresh_token": data["refresh_token"]}).status_code == 204
    assert client.post(LOGOUT_URL, json={"refresh_token": "unknown"}).status_code == 204


def test_refresh_token_is_deleted_with_user(client: TestClient) -> None:
    """`ON DELETE CASCADE`：删用户时令牌随之清理，不留孤儿行。"""
    username = _unique_username("olivia")
    user = _register(client, username)
    _login_tokens(client, username)
    assert len(_refresh_rows(str(user["id"]))) == 1

    with get_engine().begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user["id"]})

    assert _refresh_rows(str(user["id"])) == []


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
