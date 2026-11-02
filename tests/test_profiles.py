"""T7.2 集成测试：学生档案读写，连接真实 MySQL（TEST_DATABASE_URL）。

覆盖：鉴权 → 未填写档案返回空结构 → PUT 落库（profiles/skills/experiences 三张表）
→ 二次 PUT 全量替换 → 跨用户隔离 → 请求校验边界。
建表走 Alembic 迁移（与生产同一条链路），测试前后 upgrade head / downgrade base。
"""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    # 必须早于任何 app.* 导入，否则 get_settings() 会缓存 .env 里的开发库连接串。
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.main import app
from app.repositories.user_repository import dispose_engine, get_engine

PROFILES_URL = "/api/v1/profiles/me"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"
# 外键为 RESTRICT，清理必须从子表到父表。
CLEAN_ORDER = ("resumes", "skills", "experiences", "student_profiles", "users")


def _delete_all_rows() -> None:
    with get_engine().begin() as connection:
        for table in CLEAN_ORDER:
            connection.execute(text(f"DELETE FROM {table}"))


def _register_and_login(client: TestClient) -> str:
    username = f"profile_{uuid4().hex[:10]}"
    registered = client.post(
        REGISTER_URL,
        json={"username": username, "password": PASSWORD, "email": f"{username}@example.com"},
    )
    assert registered.status_code == 201, registered.text
    logged_in = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    token: str = logged_in.json()["data"]["access_token"]
    return token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _skill(name: str, rating: int = 3) -> dict[str, object]:
    return {"name": name, "self_rating": rating}


def _experience(
    title: str = "校园二手交易平台",
    start_date: str = "2024-03-01",
    end_date: str | None = "2024-06-30",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "start_date": start_date,
        "description": "面向校内学生的二手交易 Web 应用",
        "responsibility": "负责后端 API 与数据库设计",
    }
    if end_date is not None:
        payload["end_date"] = end_date
    return payload


def _profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "full_name": "张三",
        "major": "计算机科学与技术",
        "grade": "大三",
        "contact": "13800000000",
        "skills": [_skill("Python", 4), _skill("SQL"), _skill("React")],
        "experiences": [_experience()],
    }
    payload.update(overrides)
    return payload


def _count(table: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


@pytest.fixture(scope="module", autouse=True)
def _schema() -> Iterator[None]:
    if not TEST_DATABASE_URL:
        raise RuntimeError("TEST_DATABASE_URL must be set for profile integration tests")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    try:
        yield
    finally:
        command.downgrade(config, "base")
        dispose_engine()


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    _delete_all_rows()
    yield
    _delete_all_rows()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_profiles_require_authentication(client: TestClient) -> None:
    assert client.get(PROFILES_URL).status_code == 401
    assert client.put(PROFILES_URL, json=_profile_payload()).status_code == 401


def test_get_profile_returns_null_before_creation(client: TestClient) -> None:
    token = _register_and_login(client)

    response = client.get(PROFILES_URL, headers=_headers(token))

    assert response.status_code == 200
    assert response.json()["data"]["profile"] is None
    assert _count("student_profiles") == 0


def test_put_profile_persists_profile_skills_and_experiences(client: TestClient) -> None:
    token = _register_and_login(client)

    response = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    assert response.status_code == 200, response.text
    profile = response.json()["data"]["profile"]
    assert profile["full_name"] == "张三"
    assert profile["major"] == "计算机科学与技术"
    assert profile["grade"] == "大三"
    assert profile["contact"] == "13800000000"
    assert {skill["name"] for skill in profile["skills"]} == {"Python", "SQL", "React"}
    assert len(profile["experiences"]) == 1
    assert profile["experiences"][0]["title"] == "校园二手交易平台"
    assert profile["created_at"] and profile["updated_at"]

    # 库内三张表都要有真实记录，且 profile.user_id 指向当前登录用户
    assert _count("student_profiles") == 1
    assert _count("skills") == 3
    assert _count("experiences") == 1
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT p.id, p.user_id, p.full_name, u.username "
                "FROM student_profiles AS p JOIN users AS u ON u.id = p.user_id"
            )
        ).one()
    assert row.id == profile["id"]
    assert row.user_id == profile["user_id"]
    assert row.full_name == "张三"

    # 再次 GET 必须读到同一份数据
    read_back = client.get(PROFILES_URL, headers=_headers(token))
    assert read_back.status_code == 200
    assert read_back.json()["data"]["profile"]["id"] == profile["id"]


def test_put_profile_replaces_children_on_second_call(client: TestClient) -> None:
    token = _register_and_login(client)
    first = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))
    assert first.status_code == 200
    first_profile_id = first.json()["data"]["profile"]["id"]

    second = client.put(
        PROFILES_URL,
        json=_profile_payload(
            full_name="李四",
            skills=[_skill("Go", 5), _skill("Kubernetes"), _skill("Linux"), _skill("MySQL")],
            experiences=[_experience("实习一"), _experience("实习二", "2023-01-01", None)],
        ),
        headers=_headers(token),
    )

    assert second.status_code == 200, second.text
    profile = second.json()["data"]["profile"]
    assert profile["id"] == first_profile_id
    assert profile["full_name"] == "李四"
    assert {skill["name"] for skill in profile["skills"]} == {
        "Go",
        "Kubernetes",
        "Linux",
        "MySQL",
    }
    assert len(profile["experiences"]) == 2
    # 全量替换：子表没有残留，父表只有一行
    assert _count("student_profiles") == 1
    assert _count("skills") == 4
    assert _count("experiences") == 2


def test_profiles_are_isolated_per_user(client: TestClient) -> None:
    first_token = _register_and_login(client)
    second_token = _register_and_login(client)

    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(first_token))
    second_profile = client.put(
        PROFILES_URL,
        json=_profile_payload(full_name="王五", contact="13900000000"),
        headers=_headers(second_token),
    )

    assert second_profile.status_code == 200
    first_read = client.get(PROFILES_URL, headers=_headers(first_token)).json()["data"]["profile"]
    second_read = client.get(PROFILES_URL, headers=_headers(second_token)).json()["data"][
        "profile"
    ]
    assert first_read["full_name"] == "张三"
    assert second_read["full_name"] == "王五"
    assert first_read["id"] != second_read["id"]
    assert _count("student_profiles") == 2


def test_put_profile_rejects_skill_count_outside_three_to_eight(client: TestClient) -> None:
    token = _register_and_login(client)
    too_few = _profile_payload(skills=[_skill("Python"), _skill("SQL")])
    too_many = _profile_payload(skills=[_skill(str(index)) for index in range(9)])

    for payload in (too_few, too_many):
        response = client.put(PROFILES_URL, json=payload, headers=_headers(token))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    assert _count("student_profiles") == 0


def test_put_profile_rejects_experience_count_outside_one_to_three(client: TestClient) -> None:
    token = _register_and_login(client)
    none = _profile_payload(experiences=[])
    too_many = _profile_payload(experiences=[_experience(f"经历{index}") for index in range(4)])

    for payload in (none, too_many):
        assert client.put(PROFILES_URL, json=payload, headers=_headers(token)).status_code == 422


def test_put_profile_rejects_invalid_rating(client: TestClient) -> None:
    token = _register_and_login(client)

    for rating in (0, 6):
        payload = _profile_payload(
            skills=[_skill("Python", rating), _skill("SQL"), _skill("React")]
        )
        assert client.put(PROFILES_URL, json=payload, headers=_headers(token)).status_code == 422


def test_put_profile_rejects_duplicate_skill_names(client: TestClient) -> None:
    token = _register_and_login(client)
    payload = _profile_payload(skills=[_skill("Python"), _skill("Python"), _skill("React")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_profile_rejects_invalid_date_range(client: TestClient) -> None:
    token = _register_and_login(client)
    payload = _profile_payload(experiences=[_experience(end_date="2024-02-28")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
