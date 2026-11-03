"""T7.2 集成测试：学生档案读写，连接真实 MySQL（TEST_DATABASE_URL）。

覆盖：鉴权 → 未填写档案返回空结构 → PUT 落库（profiles/skills/experiences/educations）
→ 二次 PUT 全量替换 → 跨用户隔离 → 请求校验边界。
建表走 Alembic 迁移（与生产同一条链路），测试前后 upgrade head / downgrade base。

D4.1 起补充「多份简历 + 回收站」的用例（文件末尾）：越权、默认简历规则、
软删除/恢复/彻底删除、过期惰性清理。
"""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
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
PROFILES_BASE = "/api/v1/profiles"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"
# 外键为 RESTRICT，清理必须从子表到父表。
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


def _education(
    school: str = "示例大学",
    major: str = "计算机科学与技术",
    degree: str = "本科",
    edu_start: str = "2021-09-01",
    edu_end: str | None = "2025-06-30",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "school": school,
        "major": major,
        "degree": degree,
        "edu_start": edu_start,
    }
    if edu_end is not None:
        payload["edu_end"] = edu_end
    return payload


def _profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "full_name": "张三",
        "email": "zhangsan@example.com",
        "contact": "13800000000",
        "skills": [_skill("Python", 4), _skill("SQL"), _skill("React")],
        "experiences": [_experience()],
        "educations": [_education()],
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


def test_schema_dropped_profile_major_but_keeps_education_major() -> None:
    """T7.6：主表 `major` 必须被迁移删掉，`educations.major` 必须原样保留。

    用户反馈轮：主表新增可空的 `email`（简历邮箱），登录邮箱仍在 `users.email`。
    """
    with get_engine().connect() as connection:
        columns = {
            (row.TABLE_NAME, row.COLUMN_NAME)
            for row in connection.execute(
                text(
                    "SELECT TABLE_NAME, COLUMN_NAME "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME IN ('student_profiles', 'educations', 'users')"
                )
            ).all()
        }

    assert ("student_profiles", "major") not in columns
    assert ("educations", "major") in columns
    assert ("student_profiles", "email") in columns
    assert ("users", "email") in columns


def test_put_profile_ignores_legacy_major_field(client: TestClient) -> None:
    """旧客户端还会传顶层 `major`：不能报 422，也不能写到任何地方。"""
    token = _register_and_login(client)

    response = client.put(
        PROFILES_URL,
        json=_profile_payload(major="计算机科学与技术"),
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    assert "major" not in response.json()["data"]["profile"]
    # 专业只落在教育经历子表里
    assert _count("educations") == 1
    with get_engine().connect() as connection:
        majors = connection.execute(text("SELECT major FROM educations")).scalars().all()
    assert majors == ["计算机科学与技术"]


def test_put_profile_persists_profile_skills_experiences_and_educations(
    client: TestClient,
) -> None:
    token = _register_and_login(client)

    response = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    assert response.status_code == 200, response.text
    profile = response.json()["data"]["profile"]
    assert profile["full_name"] == "张三"
    # T7.6：主表 `major` 已彻底移除，专业只存在于每段教育经历。
    assert "major" not in profile
    # 年级已下架：列还在，但不再写业务值。
    assert profile["grade"] == ""
    assert profile["contact"] == "13800000000"
    # 简历邮箱落在档案表（与 users.email 登录邮箱互不影响）。
    assert profile["email"] == "zhangsan@example.com"
    assert {skill["name"] for skill in profile["skills"]} == {"Python", "SQL", "React"}
    assert len(profile["experiences"]) == 1
    assert profile["experiences"][0]["title"] == "校园二手交易平台"
    assert len(profile["educations"]) == 1
    assert profile["educations"][0]["school"] == "示例大学"
    assert profile["educations"][0]["degree"] == "本科"
    # 时间字段统一按东八区返回：带 +08:00 偏移，前端解析不会再差 8 小时。
    assert profile["created_at"].endswith("+08:00"), profile["created_at"]
    assert profile["updated_at"].endswith("+08:00"), profile["updated_at"]

    # 库内四张子/父表都要有真实记录，且 profile.user_id 指向当前登录用户
    assert _count("student_profiles") == 1
    assert _count("skills") == 3
    assert _count("experiences") == 1
    assert _count("educations") == 1
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT p.id, p.user_id, p.full_name, p.email, u.username "
                "FROM student_profiles AS p JOIN users AS u ON u.id = p.user_id"
            )
        ).one()
    assert row.id == profile["id"]
    assert row.user_id == profile["user_id"]
    assert row.full_name == "张三"
    assert row.email == "zhangsan@example.com"

    # 再次 GET 必须读到同一份数据
    read_back = client.get(PROFILES_URL, headers=_headers(token))
    assert read_back.status_code == 200
    assert read_back.json()["data"]["profile"]["id"] == profile["id"]


def test_put_profile_keeps_multiple_educations_then_replaces_them(client: TestClient) -> None:
    """T7.5：教育经历是一对多子表，PUT 语义为全量替换（先删后插）。"""
    token = _register_and_login(client)
    two = _profile_payload(
        educations=[
            _education(
                school="清华大学",
                major="软件工程",
                degree="硕士",
                edu_start="2025-09-01",
                edu_end=None,
            ),
            _education(school="示例大学", major="计算机科学与技术", degree="本科"),
        ]
    )

    first = client.put(PROFILES_URL, json=two, headers=_headers(token))

    assert first.status_code == 200, first.text
    educations = first.json()["data"]["profile"]["educations"]
    assert len(educations) == 2
    assert _count("educations") == 2
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT school, major, degree, edu_start, edu_end FROM educations ORDER BY edu_start"
            )
        ).all()
    assert [row.school for row in rows] == ["示例大学", "清华大学"]
    assert rows[1].degree == "硕士"
    assert rows[1].edu_end is None  # 在读：edu_end 为 NULL
    assert str(rows[0].edu_end) == "2025-06-30"

    second = client.put(
        PROFILES_URL,
        json=_profile_payload(educations=[_education(school="示例大学", degree="专科")]),
        headers=_headers(token),
    )

    assert second.status_code == 200, second.text
    replaced = second.json()["data"]["profile"]["educations"]
    assert len(replaced) == 1
    assert replaced[0]["degree"] == "专科"
    # 旧的两行必须被物理删除，不能残留
    assert _count("educations") == 1
    with get_engine().connect() as connection:
        remaining = connection.execute(text("SELECT school, degree FROM educations")).all()
    assert [(row.school, row.degree) for row in remaining] == [("示例大学", "专科")]


def test_put_profile_rejects_unknown_degree(client: TestClient) -> None:
    token = _register_and_login(client)
    payload = _profile_payload(educations=[_education(degree="博士后")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _count("educations") == 0


def test_put_profile_rejects_education_date_range_violation(client: TestClient) -> None:
    token = _register_and_login(client)
    payload = _profile_payload(
        educations=[_education(edu_start="2025-06-01", edu_end="2021-09-01")]
    )

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_profile_allows_empty_educations(client: TestClient) -> None:
    """未填写教育经历时不该被后端拦住（生成器负责跳过整块）。"""
    token = _register_and_login(client)

    response = client.put(
        PROFILES_URL, json=_profile_payload(educations=[]), headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["profile"]["educations"] == []
    assert _count("educations") == 0


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
    assert _count("educations") == 1


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
    second_read = client.get(PROFILES_URL, headers=_headers(second_token)).json()["data"]["profile"]
    assert first_read["full_name"] == "张三"
    assert second_read["full_name"] == "王五"
    assert first_read["id"] != second_read["id"]
    assert _count("student_profiles") == 2


def test_put_profile_allows_empty_children_but_rejects_overflow(client: TestClient) -> None:
    """保存不再拦必填（用户反馈）：技能/经历/教育都可为空，只拦超出上限。"""
    token = _register_and_login(client)
    empty = _profile_payload(skills=[], experiences=[], educations=[])

    saved = client.put(PROFILES_URL, json=empty, headers=_headers(token))

    assert saved.status_code == 200, saved.text
    profile = saved.json()["data"]["profile"]
    assert profile["skills"] == []
    assert profile["experiences"] == []
    assert profile["educations"] == []
    assert _count("student_profiles") == 1

    overflow = (
        _profile_payload(skills=[_skill(str(index)) for index in range(9)]),
        _profile_payload(experiences=[_experience(f"经历{index}") for index in range(4)]),
        _profile_payload(educations=[_education(school=f"学校{index}") for index in range(6)]),
    )
    for payload in overflow:
        response = client.put(PROFILES_URL, json=payload, headers=_headers(token))
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_profile_accepts_partially_filled_rows(client: TestClient) -> None:
    """只填了锚点字段（技能名 / 项目名 / 学校）也必须能保存，不因缺描述被拒。"""
    token = _register_and_login(client)
    payload = _profile_payload(
        skills=[_skill("Python")],
        experiences=[
            {"title": "还没写描述", "start_date": "2024-03-01"},
        ],
        educations=[
            {
                "school": "示例大学",
                "degree": "本科",
                "edu_start": "2021-09-01",
            }
        ],
    )

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 200, response.text
    profile = response.json()["data"]["profile"]
    assert profile["experiences"][0]["description"] == ""
    assert profile["experiences"][0]["responsibility"] == ""
    assert profile["educations"][0]["major"] == ""


def test_put_profile_stores_resume_email_without_touching_login_email(
    client: TestClient,
) -> None:
    """档案 email 是「简历上的邮箱」，与 users.email（登录邮箱）互不影响。"""
    token = _register_and_login(client)

    response = client.put(
        PROFILES_URL,
        json=_profile_payload(email="resume-only@example.com"),
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["profile"]["email"] == "resume-only@example.com"
    with get_engine().connect() as connection:
        stored = connection.execute(text("SELECT email FROM student_profiles")).scalar_one()
    assert stored == "resume-only@example.com"

    me = client.get("/api/v1/auth/me", headers=_headers(token))
    assert me.status_code == 200
    assert me.json()["data"]["user"]["email"] != "resume-only@example.com"

    # 空串等同「没填」，库里存 NULL 而不是空字符串
    cleared = client.put(PROFILES_URL, json=_profile_payload(email=""), headers=_headers(token))
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["profile"]["email"] is None
    with get_engine().connect() as connection:
        assert connection.execute(text("SELECT email FROM student_profiles")).scalar_one() is None


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


def test_put_profile_rejects_future_start_dates(client: TestClient) -> None:
    """**开始**时间不能是未来：入学 / 项目开始晚于今天一律 422。

    结束时间不在此列——应届生要填「预计毕业时间」（见下一个用例）。
    """
    token = _register_and_login(client)
    future = (date.today() + timedelta(days=400)).isoformat()

    for payload in (
        _profile_payload(educations=[_education(edu_start=future, edu_end=None)]),
        _profile_payload(experiences=[_experience("未来开始", future, None)]),
    ):
        response = client.put(PROFILES_URL, json=payload, headers=_headers(token))
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    assert _count("student_profiles") == 0


def test_put_profile_accepts_future_end_dates(client: TestClient) -> None:
    """**结束**时间允许未来：应届生的「预计毕业」必须能存。

    体验反馈修复：之前把 `edu_end` / `end_date` 也卡在「过去」，
    导致应届生无法填写毕业时间、在做的项目无法写预计结束时间。
    """
    token = _register_and_login(client)
    future = (date.today() + timedelta(days=400)).isoformat()

    response = client.put(
        PROFILES_URL,
        json=_profile_payload(
            educations=[_education(edu_start="2023-09-01", edu_end=future)],
            experiences=[_experience("预计结束的项目", "2024-03-01", future)],
        ),
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    profile = response.json()["data"]["profile"]
    assert profile["educations"][0]["edu_end"] == future
    assert profile["experiences"][0]["end_date"] == future

    # 真实落库，不只是回显：未来的结束时间必须原样写进 DATE 列。
    with get_engine().connect() as connection:
        edu_row = connection.execute(text("SELECT edu_end FROM educations")).one()
        exp_row = connection.execute(text("SELECT end_date FROM experiences")).one()
    assert str(edu_row.edu_end) == future
    assert str(exp_row.end_date) == future


def test_put_profile_accepts_today_as_date(client: TestClient) -> None:
    """边界：允许「今天」，只有严格晚于今天才拦。"""
    token = _register_and_login(client)
    today = date.today().isoformat()

    response = client.put(
        PROFILES_URL,
        json=_profile_payload(
            educations=[_education(edu_start="2021-09-01", edu_end=today)],
            experiences=[_experience("到今天为止", "2024-03-01", today)],
        ),
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text


def test_put_profile_rejects_non_phone_contact(client: TestClient) -> None:
    """手机号必须是大陆 11 位号码（`^1[3-9]\\d{9}$`）。"""
    token = _register_and_login(client)

    for contact in ("12345", "1380000000", "12345678901", "021-88886666", "手机13800000000"):
        response = client.put(
            PROFILES_URL, json=_profile_payload(contact=contact), headers=_headers(token)
        )
        assert response.status_code == 422, f"{contact} -> {response.text}"
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    assert _count("student_profiles") == 0


def test_put_profile_allows_empty_contact_as_draft(client: TestClient) -> None:
    """保存不拦必填：手机号留空也能存草稿，只在生成简历时强校验。"""
    token = _register_and_login(client)

    response = client.put(PROFILES_URL, json=_profile_payload(contact=""), headers=_headers(token))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["profile"]["contact"] == ""


# --------------------------------------------------------------------------- #
# D4.1：多份简历 + 回收站
# --------------------------------------------------------------------------- #


def _create(client: TestClient, token: str, **payload: object) -> dict[str, object]:
    response = client.post(PROFILES_BASE, json=payload, headers=_headers(token))
    assert response.status_code == 201, response.text
    profile: dict[str, object] = response.json()["data"]["profile"]
    return profile


def _list_profiles(client: TestClient, token: str) -> list[dict[str, object]]:
    response = client.get(PROFILES_BASE, headers=_headers(token))
    assert response.status_code == 200, response.text
    profiles: list[dict[str, object]] = response.json()["data"]["profiles"]
    return profiles


def _trash(client: TestClient, token: str) -> list[dict[str, object]]:
    response = client.get(f"{PROFILES_BASE}/trash", headers=_headers(token))
    assert response.status_code == 200, response.text
    profiles: list[dict[str, object]] = response.json()["data"]["profiles"]
    return profiles


def _user_id(client: TestClient, token: str) -> str:
    response = client.get("/api/v1/auth/me", headers=_headers(token))
    assert response.status_code == 200, response.text
    user_id: str = response.json()["data"]["user"]["id"]
    return user_id


def _naive_utc_now() -> datetime:
    """库里存的是 naive UTC，测试回填时间要同口径（不能塞带时区的 datetime）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def test_schema_allows_multiple_profiles_per_user() -> None:
    """D4.1：`user_id` 唯一约束必须消失，改为普通索引；三列必须存在。

    直接 `DROP INDEX user_id` 会被外键挡住（实测 1553），所以迁移里是先补
    `ix_student_profiles_user_id` 再摘唯一索引 —— 这里一并断言那个普通索引在。
    """
    with get_engine().connect() as connection:
        indexes = connection.execute(
            text(
                "SELECT INDEX_NAME, NON_UNIQUE, COLUMN_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'student_profiles'"
            )
        ).all()
        columns = {
            row.COLUMN_NAME
            for row in connection.execute(
                text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'student_profiles'"
                )
            ).all()
        }

    unique_on_user_id = [
        row.INDEX_NAME for row in indexes if row.COLUMN_NAME == "user_id" and row.NON_UNIQUE == 0
    ]
    assert unique_on_user_id == [], "user_id 上不应再有唯一索引（否则一人只能一份简历）"
    assert any(
        row.INDEX_NAME == "ix_student_profiles_user_id" and row.NON_UNIQUE == 1 for row in indexes
    ), "外键需要 user_id 上的普通索引"
    assert {"title", "is_default", "deleted_at"} <= columns


def test_create_profile_autoincrements_default_title(client: TestClient) -> None:
    """新建不传标题时递增命名：我的简历 → 我的简历 2 → …；显式标题原样保留。"""
    token = _register_and_login(client)

    first = _create(client, token)
    assert first["title"] == "我的简历"
    # 第一份自动成为默认简历
    assert first["is_default"] is True

    second = _create(client, token)
    assert second["title"] == "我的简历 2"
    assert second["is_default"] is False

    named = _create(client, token, title="投算法")
    assert named["title"] == "投算法"

    third = _create(client, token)
    assert third["title"] == "我的简历 3", "跳过已占用的编号，取最大值 + 1"

    assert _count("student_profiles") == 4


def test_list_and_detail_are_scoped_to_owner(client: TestClient) -> None:
    """用户 A 只能看到/改动自己的简历；B 猜 id 也一律 404（不是 403）。"""
    token_a = _register_and_login(client)
    token_b = _register_and_login(client)
    a_first = _create(client, token_a)
    a_second = _create(client, token_a)
    b_first = _create(client, token_b)

    assert sorted(profile["id"] for profile in _list_profiles(client, token_a)) == sorted(
        [a_first["id"], a_second["id"]]
    )
    assert [profile["id"] for profile in _list_profiles(client, token_b)] == [b_first["id"]]

    profile_url = f"{PROFILES_BASE}/{a_first['id']}"
    assert client.get(profile_url, headers=_headers(token_b)).status_code == 404
    assert (
        client.put(profile_url, json=_profile_payload(full_name="越权"), headers=_headers(token_b))
    ).status_code == 404
    assert client.delete(profile_url, headers=_headers(token_b)).status_code == 404
    assert (client.put(f"{profile_url}/restore", headers=_headers(token_b))).status_code == 404
    assert (client.delete(f"{profile_url}/permanent", headers=_headers(token_b))).status_code == 404

    # A 的数据必须完全没被 B 的尝试影响
    still_there = client.get(profile_url, headers=_headers(token_a))
    assert still_there.status_code == 200
    assert still_there.json()["data"]["profile"]["full_name"] == ""
    assert len(_list_profiles(client, token_a)) == 2


def test_create_profile_copy_from_deep_copies_content(client: TestClient) -> None:
    """`copy_from_id` 是服务端深拷贝：内容一致、子行 id 全新、改副本不影响源。"""
    token = _register_and_login(client)
    source = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]

    copied = _create(client, token, copy_from_id=source["id"], title="投后端")

    assert copied["id"] != source["id"]
    assert copied["title"] == "投后端"
    assert copied["full_name"] == "张三"
    assert copied["contact"] == "13800000000"
    assert {skill["name"] for skill in copied["skills"]} == {"Python", "SQL", "React"}
    assert len(copied["experiences"]) == 1
    assert copied["educations"][0]["school"] == "示例大学"
    assert copied["is_default"] is False
    assert {skill["id"] for skill in copied["skills"]}.isdisjoint(
        {skill["id"] for skill in source["skills"]}
    ), "子行必须重新分配 id，否则改副本会回写源简历"

    updated = client.put(
        f"{PROFILES_BASE}/{copied['id']}",
        json=_profile_payload(full_name="李四", skills=[_skill("Go", 5)]),
        headers=_headers(token),
    )
    assert updated.status_code == 200, updated.text

    reread = client.get(f"{PROFILES_BASE}/{source['id']}", headers=_headers(token))
    source_after = reread.json()["data"]["profile"]
    assert source_after["full_name"] == "张三"
    assert {skill["name"] for skill in source_after["skills"]} == {"Python", "SQL", "React"}


def test_create_profile_as_default_clears_previous(client: TestClient) -> None:
    """新建时 `is_default=true` 要把旧的默认置为 0，且全局只有一个默认。"""
    token = _register_and_login(client)
    first = _create(client, token)
    second = _create(client, token, is_default=True)

    profiles = {profile["id"]: profile for profile in _list_profiles(client, token)}
    assert profiles[second["id"]]["is_default"] is True
    assert profiles[first["id"]]["is_default"] is False
    assert sum(1 for profile in profiles.values() if profile["is_default"]) == 1

    me = client.get(PROFILES_URL, headers=_headers(token))
    assert me.json()["data"]["profile"]["id"] == second["id"]

    # 显式切换默认：旧的自动取消
    switched = client.put(f"{PROFILES_BASE}/{first['id']}/default", headers=_headers(token))
    assert switched.status_code == 200
    assert switched.json()["data"]["profile"]["is_default"] is True
    profiles = {profile["id"]: profile for profile in _list_profiles(client, token)}
    assert profiles[second["id"]]["is_default"] is False


def test_put_by_id_only_touches_that_profile(client: TestClient) -> None:
    token = _register_and_login(client)
    first = _create(client, token)
    second = _create(client, token)

    client.put(
        f"{PROFILES_BASE}/{second['id']}",
        json=_profile_payload(full_name="李四", skills=[_skill("Go")]),
        headers=_headers(token),
    )

    untouched = client.get(f"{PROFILES_BASE}/{first['id']}", headers=_headers(token))
    assert untouched.json()["data"]["profile"]["full_name"] == ""
    assert untouched.json()["data"]["profile"]["skills"] == []


def test_soft_delete_moves_profile_to_trash(client: TestClient) -> None:
    """软删除：列表消失、详情 404、回收站可见；子表一行都不能少。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]

    deleted = client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {"profile_id": saved["id"], "deleted": True}

    assert _list_profiles(client, token) == []
    assert client.get(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token)).status_code == 404
    # 软删除只是打标记，子表不能动（恢复后内容必须还在）
    assert _count("student_profiles") == 1
    assert _count("skills") == 3
    assert _count("experiences") == 1
    assert _count("educations") == 1

    trash = _trash(client, token)
    assert [profile["id"] for profile in trash] == [saved["id"]]
    assert trash[0]["deleted_at"].endswith("+08:00"), trash[0]["deleted_at"]

    restored = client.put(f"{PROFILES_BASE}/{saved['id']}/restore", headers=_headers(token))
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["profile"]["deleted_at"] is None
    assert [profile["id"] for profile in _list_profiles(client, token)] == [saved["id"]]
    assert _trash(client, token) == []


def test_deleting_default_promotes_newest_remaining(client: TestClient) -> None:
    """删掉默认简历后，自动把**最新**的一份未删除简历设为默认。"""
    token = _register_and_login(client)
    oldest = _create(client, token)
    _create(client, token)
    newest = _create(client, token)
    assert oldest["is_default"] is True

    client.delete(f"{PROFILES_BASE}/{oldest['id']}", headers=_headers(token))

    remaining = _list_profiles(client, token)
    assert len(remaining) == 2
    defaults = [profile for profile in remaining if profile["is_default"]]
    assert [profile["id"] for profile in defaults] == [newest["id"]]

    me = client.get(PROFILES_URL, headers=_headers(token))
    assert me.json()["data"]["profile"]["id"] == newest["id"]


def test_permanent_delete_removes_profile_and_children(client: TestClient) -> None:
    """彻底删除只能对回收站里的简历执行，且要连带清掉四张子表。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]

    refused = client.delete(f"{PROFILES_BASE}/{saved['id']}/permanent", headers=_headers(token))
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "CLIENT_ERROR"
    assert _count("student_profiles") == 1, "未进回收站不能物理删除"

    client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token))
    purged = client.delete(f"{PROFILES_BASE}/{saved['id']}/permanent", headers=_headers(token))
    assert purged.status_code == 200, purged.text
    assert purged.json()["data"] == {"profile_id": saved["id"], "purged": True}

    assert _count("student_profiles") == 0
    assert _count("skills") == 0
    assert _count("experiences") == 0
    assert _count("educations") == 0
    # 物删之后连恢复都该 404
    assert (
        client.put(f"{PROFILES_BASE}/{saved['id']}/restore", headers=_headers(token)).status_code
        == 404
    )


def test_expired_trash_is_purged_lazily_on_list(client: TestClient) -> None:
    """回收站保留 30 天：过期记录在下一次读取列表时被惰性物理清理。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]
    client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token))

    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE student_profiles SET deleted_at = :at WHERE id = :id"),
            {"at": _naive_utc_now() - timedelta(days=31), "id": saved["id"]},
        )

    assert _trash(client, token) == []
    assert _count("student_profiles") == 0
    assert _count("skills") == 0
    assert _count("educations") == 0


def test_me_rebuilds_default_profile_after_everything_is_deleted(client: TestClient) -> None:
    """旧客户端（只认 `/profiles/me`）在全部删光后再点保存，要能重建一份默认简历。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]
    client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token))

    me = client.get(PROFILES_URL, headers=_headers(token))
    assert me.status_code == 200
    assert me.json()["data"]["profile"] is None

    again = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))
    assert again.status_code == 200, again.text
    rebuilt = again.json()["data"]["profile"]
    assert rebuilt["is_default"] is True
    assert rebuilt["title"] == "我的简历"
    assert rebuilt["id"] != saved["id"]
    # 回收站里那份仍在（软删除不参与重名判断，所以标题可以重复）
    assert _count("student_profiles") == 2
    assert [profile["id"] for profile in _trash(client, token)] == [saved["id"]]


def test_profile_count_is_capped(client: TestClient) -> None:
    """每人最多 20 份（ADR-006 §4.4）：超出返回 400，不再新增行。"""
    token = _register_and_login(client)
    user_id = _user_id(client, token)
    now = _naive_utc_now()
    with get_engine().begin() as connection:
        for index in range(20):
            connection.execute(
                text(
                    "INSERT INTO student_profiles "
                    "(id, user_id, title, is_default, full_name, grade, contact, "
                    " created_at, updated_at) "
                    "VALUES (:id, :user_id, :title, :is_default, '', '', '', :now, :now)"
                ),
                {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "title": f"批量 {index}",
                    "is_default": 1 if index == 0 else 0,
                    "now": now,
                },
            )

    response = client.post(PROFILES_BASE, json={}, headers=_headers(token))

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "CLIENT_ERROR"
    assert "20" in response.json()["error"]["message"]
    assert _count("student_profiles") == 20
