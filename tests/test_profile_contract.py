"""前后端档案契约测试（保存 422 修复轮）。

背景：点「保存修改」返回 422，根因是前端 `toUpsertPayload()` 的假定与后端
`ProfileUpsertRequest` 的校验不一致。这个文件的职责只有一个：**把前端真实会发出的
JSON 形状原样喂给后端模型**，让契约断裂在 CI 里就炸，而不是等用户点保存。

三种验证层次：
1. 纯 Pydantic（不需要数据库）：`ProfileUpsertRequest.model_validate(...)` 直接校验
   前端 payload 的字段名、类型与取值；
2. 走 HTTP（需要 TEST_DATABASE_URL）：同样的 payload `PUT /profiles/me` 必须 200；
3. 负向用例：后端仍然拒绝的输入（非法手机号、空入学日期），确保前端
   `validateForSave()` 拦的是同一批规则，而不是各说各话。
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
from app.schemas.profile import ProfileUpsertRequest

PROFILES_URL = "/api/v1/profiles/me"
GENERATE_URL = "/api/v1/resumes/generate"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"
CLEAN_ORDER = ("resumes", "educations", "skills", "experiences", "student_profiles", "users")

#: 前端 `ProfileUpsertRequest` 接口（`frontend/src/api/types.ts`）的字段全集。
FRONTEND_PAYLOAD_KEYS = {
    "full_name",
    "email",
    "contact",
    "skills",
    "experiences",
    "educations",
}


def _frontend_payload() -> dict[str, object]:
    """`toUpsertPayload()` 对一个「正常填过」的草稿实际会发出的 JSON。

    逐字段对应 `frontend/src/stores/resume.ts`：
    - `email` 空串 → `null`；
    - `self_rating` 固定 3（UI 不采集，但后端 `SkillInput` 必填 1–5）；
    - `end_date` 空串 → `null`（「至今」）；
    - `edu_start` / `edu_end` 由 `YYYY-MM` 补成当月 1 号，空 → `null`；
    - 不再有顶层 `major` / `grade`。
    """
    return {
        "full_name": "张三",
        "email": "zhangsan@example.com",
        "contact": "13800000000",
        "skills": [
            {"name": "Python", "self_rating": 3},
            {"name": "SQL", "self_rating": 3},
        ],
        "experiences": [
            {
                "title": "校园二手交易平台",
                "start_date": "2024-03-01",
                "end_date": None,
                "description": "面向校内学生的二手交易 Web 应用",
                "responsibility": "负责后端 API 与数据库设计",
            }
        ],
        "educations": [
            {
                "school": "示例大学",
                "major": "计算机科学与技术",
                "degree": "本科",
                "edu_start": "2021-09-01",
                "edu_end": None,
            }
        ],
    }


def _empty_draft_payload() -> dict[str, object]:
    """空白骨架点保存时的 payload：空行被 `meaningful*()` 过滤，全空即空档案。"""
    return {
        "full_name": "",
        "email": None,
        "contact": "",
        "skills": [],
        "experiences": [],
        "educations": [],
    }


def test_frontend_payload_is_accepted_by_backend_model() -> None:
    """前端 payload 的字段名/类型必须被后端模型原样接受（契约的最小断言）。"""
    payload = _frontend_payload()

    parsed = ProfileUpsertRequest.model_validate(payload)

    assert set(payload) == FRONTEND_PAYLOAD_KEYS
    assert parsed.full_name == "张三"
    assert parsed.contact == "13800000000"
    assert [skill.self_rating for skill in parsed.skills] == [3, 3]
    assert parsed.experiences[0].end_date is None
    assert parsed.educations[0].edu_start.isoformat() == "2021-09-01"
    assert parsed.educations[0].edu_end is None


def test_empty_draft_payload_is_accepted() -> None:
    """「保存不拦必填」：全空的草稿必须能通过模型校验（原先卡在 full_name）。"""
    parsed = ProfileUpsertRequest.model_validate(_empty_draft_payload())

    assert parsed.full_name == ""
    assert parsed.contact == ""
    assert parsed.skills == []
    assert parsed.experiences == []
    assert parsed.educations == []


def test_legacy_grade_key_is_ignored() -> None:
    """旧客户端可能还带着 `grade`：允许传，但不参与保存。"""
    payload = {**_frontend_payload(), "grade": "大四"}

    parsed = ProfileUpsertRequest.model_validate(payload)

    assert parsed.grade == "大四"
    assert parsed.educations[0].school == "示例大学"


def test_backend_still_rejects_inputs_frontend_precheck_blocks() -> None:
    """后端仍会 422 的输入 —— 前端 `validateForSave()` 必须拦同一批规则。

    这里断言的是「规则还在」：如果哪天后端放宽了手机号或日期，这条用例会红，
    提醒同步删掉前端那条 precheck，避免两边各说各话。
    """
    bad_phone = {**_frontend_payload(), "contact": "123331"}
    with pytest.raises(ValueError, match="contact must be a mainland China mobile number"):
        ProfileUpsertRequest.model_validate(bad_phone)

    empty_edu_start = _frontend_payload()
    empty_edu_start["educations"] = [
        {
            "school": "示例大学",
            "major": "计算机科学与技术",
            "degree": "本科",
            "edu_start": "",
            "edu_end": None,
        }
    ]
    with pytest.raises(ValueError, match="valid date"):
        ProfileUpsertRequest.model_validate(empty_edu_start)


def _delete_all_rows() -> None:
    with get_engine().begin() as connection:
        for table in CLEAN_ORDER:
            connection.execute(text(f"DELETE FROM {table}"))


def _register_and_login(client: TestClient) -> str:
    username = f"contract_{uuid4().hex[:10]}"
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


@pytest.fixture(scope="module", autouse=True)
def _schema() -> Iterator[None]:
    if not TEST_DATABASE_URL:
        raise RuntimeError("TEST_DATABASE_URL must be set for profile contract tests")
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


def test_frontend_payload_saves_over_http(client: TestClient) -> None:
    """同一份前端 payload 走真实 HTTP 必须 200（保存 422 的回归防线）。"""
    token = _register_and_login(client)

    response = client.put(PROFILES_URL, json=_frontend_payload(), headers=_headers(token))

    assert response.status_code == 200, response.text
    profile = response.json()["data"]["profile"]
    assert profile["full_name"] == "张三"
    assert profile["email"] == "zhangsan@example.com"
    assert [skill["name"] for skill in profile["skills"]] == ["Python", "SQL"]
    assert profile["experiences"][0]["end_date"] is None
    assert profile["educations"][0]["edu_end"] is None


def test_empty_draft_saves_and_generate_returns_400(client: TestClient) -> None:
    """空草稿能存；随后生成简历必须是 400（档案不完整），而不是 500。"""
    token = _register_and_login(client)

    saved = client.put(PROFILES_URL, json=_empty_draft_payload(), headers=_headers(token))
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["profile"]["full_name"] == ""

    generated = client.post(GENERATE_URL, json={"title": "我的简历"}, headers=_headers(token))
    assert generated.status_code == 400, generated.text
    assert generated.json()["error"]["code"] == "CLIENT_ERROR"
