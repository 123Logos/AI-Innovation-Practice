"""T7.2 集成测试：简历生成走真实 MySQL，覆盖完整生命周期。

链路：注册 → 登录 → PUT 档案 → POST 生成 → 校验 `resumes` 表内的 HTML 与归属。
同时验证：未登录 401、无档案 400、请求体不能覆盖库内数据、标题缺失 422。
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

GENERATE_URL = "/api/v1/resumes/generate"
PROFILES_URL = "/api/v1/profiles/me"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"
CLEAN_ORDER = (
    "resumes",
    "educations",
    "skills",
    "experiences",
    "student_profiles",
    "users",
)


def _delete_all_rows() -> None:
    with get_engine().begin() as connection:
        for table in CLEAN_ORDER:
            connection.execute(text(f"DELETE FROM {table}"))


def _register_and_login(client: TestClient) -> str:
    username = f"resume_{uuid4().hex[:10]}"
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


def _educations() -> list[dict[str, object]]:
    """两段学历：硕士在读（较新）+ 本科（较早），用于验证倒序渲染。"""
    return [
        {
            "school": "清华大学",
            "major": "软件工程",
            "degree": "硕士",
            "edu_start": "2025-09-01",
        },
        {
            "school": "示例大学",
            "major": "计算机科学与技术",
            "degree": "本科",
            "edu_start": "2021-09-01",
            "edu_end": "2025-06-30",
        },
    ]


def _profile_payload(**overrides: object) -> dict[str, object]:
    experience: dict[str, object] = {
        "title": "校园二手交易平台",
        "start_date": "2024-03-01",
        "description": "面向校内学生的二手交易 Web 应用",
        "responsibility": "负责后端 API 与数据库设计",
    }
    payload: dict[str, object] = {
        "full_name": "张三",
        "major": "计算机科学与技术",
        "contact": "13800000000",
        "skills": [
            {"name": "Python", "self_rating": 4},
            {"name": "SQL", "self_rating": 3},
            {"name": "React", "self_rating": 3},
        ],
        "educations": _educations(),
    }
    end_date = overrides.pop("end_date", "2024-06-30")
    if end_date is not None:
        experience["end_date"] = end_date
    payload["experiences"] = [experience]
    payload.update(overrides)
    return payload


def _current_profile(client: TestClient, token: str) -> dict[str, object]:
    response = client.get(PROFILES_URL, headers=_headers(token))
    assert response.status_code == 200, response.text
    profile: dict[str, object] = response.json()["data"]["profile"]
    return profile


def _count(table: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


@pytest.fixture(scope="module", autouse=True)
def _schema() -> Iterator[None]:
    if not TEST_DATABASE_URL:
        raise RuntimeError("TEST_DATABASE_URL must be set for resume integration tests")
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


def test_generate_requires_authentication(client: TestClient) -> None:
    response = client.post(GENERATE_URL, json={"title": "我的简历"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "CLIENT_ERROR"
    assert _count("resumes") == 0


def test_generate_returns_400_when_profile_is_missing(client: TestClient) -> None:
    token = _register_and_login(client)

    response = client.post(GENERATE_URL, json={"title": "我的简历"}, headers=_headers(token))

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "CLIENT_ERROR"
    assert "档案" in body["error"]["message"]
    assert body["meta"]["request_id"]
    assert _count("resumes") == 0


def test_generate_persists_resume_for_current_user(client: TestClient) -> None:
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))
    assert saved.status_code == 200, saved.text
    profile_id = saved.json()["data"]["profile"]["id"]

    response = client.post(GENERATE_URL, json={"title": "我的第一份简历"}, headers=_headers(token))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["resume_id"]
    assert data["title"] == "我的第一份简历"
    assert data["created_at"]
    assert "张三" in data["content_html"]
    assert "计算机科学与技术" in data["content_html"]
    assert "Python" in data["content_html"]
    assert "校园二手交易平台" in data["content_html"]
    # 熟练度已下架：生成器只渲染技能名，HTML 里不得残留「自评 x/5」。
    assert "自评" not in data["content_html"]
    assert "<li>Python</li>" in data["content_html"]
    # 年级已下架：基本信息行不能留下空的 `｜｜` 分隔。
    assert "｜｜" not in data["content_html"]

    # 教育经历区块：学校全称 / 专业 / 学历 / 起止时间，且按入学时间倒序。
    html = data["content_html"]
    assert "教育经历" in html
    for fragment in ("清华大学", "软件工程", "硕士", "2025.09", "至今"):
        assert fragment in html, fragment
    for fragment in ("示例大学", "计算机科学与技术", "本科", "2021.09", "2025.06"):
        assert fragment in html, fragment
    assert html.index("清华大学") < html.index("示例大学"), "教育经历必须按入学时间倒序"
    assert html.index("硕士") < html.index("本科")

    # 落库校验：id / 归属 / 标题 / HTML 与响应一致
    with get_engine().connect() as connection:
        row = connection.execute(
            text("SELECT id, student_profile_id, title, content_html FROM resumes")
        ).one()
    assert row.id == data["resume_id"]
    assert row.student_profile_id == profile_id
    assert row.title == "我的第一份简历"
    assert row.content_html == data["content_html"]
    # 落库的 HTML 必须本身干净，不能靠前端剥离掩盖脏数据。
    assert "自评" not in row.content_html
    assert "教育经历" in row.content_html
    assert _count("resumes") == 1


def test_generate_skips_education_section_when_profile_has_none(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(
        PROFILES_URL,
        json=_profile_payload(educations=[]),
        headers=_headers(token),
    )

    response = client.post(GENERATE_URL, json={"title": "无学历简历"}, headers=_headers(token))

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    # 区块整体不渲染，不能留一个空标题
    assert "教育经历" not in content_html
    assert "张三" in content_html


def test_generate_ignores_profile_fields_sent_in_request_body(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    response = client.post(
        GENERATE_URL,
        json={
            "title": "被污染？",
            "full_name": "李四",
            "skills": [{"name": "伪造技能", "self_rating": 5}],
            "experiences": [{"title": "伪造经历"}],
        },
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    assert "张三" in content_html
    assert "Python" in content_html
    assert "李四" not in content_html
    assert "伪造技能" not in content_html
    assert "伪造经历" not in content_html


def test_generate_renders_ongoing_experience(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(end_date=None), headers=_headers(token))

    response = client.post(GENERATE_URL, json={"title": "在读经历"}, headers=_headers(token))

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    assert "至今" in content_html
    assert "2024-03-01" in content_html


def test_generate_rejects_missing_title(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    response = client.post(GENERATE_URL, json={}, headers=_headers(token))

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "title" in body["error"]["details"][0]["loc"]
    assert _count("resumes") == 0


def test_each_generate_creates_a_new_resume_row(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    first = client.post(GENERATE_URL, json={"title": "第一版"}, headers=_headers(token))
    second = client.post(GENERATE_URL, json={"title": "第二版"}, headers=_headers(token))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["resume_id"] != second.json()["data"]["resume_id"]
    assert _count("resumes") == 2
