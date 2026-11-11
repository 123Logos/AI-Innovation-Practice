"""B8（ADR-015 §2.3）：简历版本指针与 `resumes` 的第一条读路径。

此前 `resumes` 是一张只写不读的表（全仓 `select(Resume)` 零命中）。本文件钉住：

1. `create_resume` 在同一事务里插新行 + 旧链头指针指向新行 + 新行自指；
   一个档案的所有行指向同一条链头，`version_no` 递增；
2. 「每个档案至多一条链头」由 `UNIQUE(student_profile_id, is_head)` 在库里强制；
3. `GET /resumes/current` **只读指针**：链头在 → 返回它；没生成过 → 404；
   **指针落空 → 显式报错（400），绝不回落到 `ORDER BY created_at DESC LIMIT 1`**；
4. 链头被物理删除时 `ON DELETE SET NULL` 让其余行指针落空（全仓唯一非 RESTRICT 外键）；
5. 越权 / 回收站 / 无档案的错误口径与 `/generate` 一致。
"""

from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.error_messages import ErrorMessage
from app.repositories.user_repository import get_engine

GENERATE_URL = "/api/v1/resumes/generate"
CURRENT_URL = "/api/v1/resumes/current"
PROFILES_URL = "/api/v1/profiles/me"
PROFILES_BASE = "/api/v1/profiles"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"


def _register_and_login(client: TestClient) -> str:
    username = f"versions_{uuid4().hex[:10]}"
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


def _complete_profile(client: TestClient, token: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "full_name": "张三",
        "contact": "13800000000",
        "skills": [{"name": "Python"}],
        "experiences": [
            {
                "title": "校园二手交易平台",
                "start_date": "2024-03-01",
                "end_date": "2024-06-30",
                "description": "面向校内学生的二手交易 Web 应用",
                "responsibility": "负责后端 API 与数据库设计",
            }
        ],
        "educations": [],
    }
    payload.update(overrides)
    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))
    assert response.status_code == 200, response.text
    profile: dict[str, Any] = response.json()["data"]["profile"]
    return profile


def _generate(client: TestClient, token: str, title: str, **extra: Any) -> dict[str, Any]:
    response = client.post(GENERATE_URL, json={"title": title, **extra}, headers=_headers(token))
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _resume_rows() -> list[Any]:
    with get_engine().connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT id, student_profile_id, title, version_no, current_version_id, is_head "
                    "FROM resumes ORDER BY version_no"
                )
            ).all()
        )


def test_generate_builds_pointer_chain_in_one_transaction(client: TestClient) -> None:
    """两次生成：版本号 1→2，全链指向新行，新行自指，旧链头让位。"""
    token = _register_and_login(client)
    profile = _complete_profile(client, token)

    first = _generate(client, token, "第一版")
    assert first["version_no"] == 1
    rows = _resume_rows()
    assert len(rows) == 1
    assert rows[0].current_version_id == rows[0].id, "链头必须自指"
    assert rows[0].is_head == 1

    second = _generate(client, token, "第二版")
    assert second["version_no"] == 2
    assert second["resume_id"] != first["resume_id"]

    rows = _resume_rows()
    assert len(rows) == 2
    # 语义写死：所有行的 current_version_id 都指向同一条（新链头），且该行自指。
    assert {row.current_version_id for row in rows} == {second["resume_id"]}
    head = [row for row in rows if row.id == second["resume_id"]]
    assert len(head) == 1 and head[0].is_head == 1
    old = [row for row in rows if row.id == first["resume_id"]]
    assert old[0].is_head is None
    assert old[0].version_no == 1
    # 归属不变：两条都挂同一份档案。
    assert {row.student_profile_id for row in rows} == {profile["id"]}


def test_head_unique_constraint_blocks_second_head(client: TestClient) -> None:
    """库里强制「至多一条链头」：手工让第二行自指必须撞唯一键（1062）。"""
    token = _register_and_login(client)
    _complete_profile(client, token)
    _generate(client, token, "第一版")
    second = _generate(client, token, "第二版")

    rows = _resume_rows()
    old_id = next(row.id for row in rows if row.id != second["resume_id"])
    with pytest.raises(IntegrityError) as excinfo, get_engine().begin() as connection:
        connection.execute(
            text("UPDATE resumes SET current_version_id = id WHERE id = :id"),
            {"id": old_id},
        )
    assert "1062" in str(excinfo.value) or "Duplicate entry" in str(excinfo.value)


def test_current_endpoint_reads_the_pointer(client: TestClient) -> None:
    """GET /resumes/current 返回链头那一版（不是「看起来最新」的猜法）。"""
    token = _register_and_login(client)
    _complete_profile(client, token)
    first = _generate(client, token, "第一版")
    second = _generate(client, token, "第二版")

    response = client.get(CURRENT_URL, headers=_headers(token))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["resume_id"] == second["resume_id"]
    assert data["title"] == "第二版"
    assert data["version_no"] == 2
    assert data["content_html"] == second["content_html"]
    assert data["created_at"].endswith("+08:00")
    assert data["resume_id"] != first["resume_id"]


def test_current_endpoint_respects_profile_id(client: TestClient) -> None:
    """指定 profile_id 时读那一份的链头；两份档案的链互不串。"""
    token = _register_and_login(client)
    _complete_profile(client, token)
    _generate(client, token, "默认那份")

    other = client.post(PROFILES_BASE, json={"title": "投算法"}, headers=_headers(token))
    assert other.status_code == 201, other.text
    other_id = other.json()["data"]["profile"]["id"]
    filled = client.put(
        f"{PROFILES_BASE}/{other_id}",
        json={
            "full_name": "李四",
            "contact": "13900000000",
            "skills": [{"name": "PyTorch"}],
            "experiences": [
                {
                    "title": "算法项目",
                    "start_date": "2024-03-01",
                    "description": "做了检索",
                    "responsibility": "负责召回",
                }
            ],
        },
        headers=_headers(token),
    )
    assert filled.status_code == 200, filled.text
    other_resume = _generate(client, token, "算法版", profile_id=other_id)

    mine = client.get(CURRENT_URL, headers=_headers(token)).json()["data"]
    assert mine["title"] == "默认那份"
    theirs = client.get(
        CURRENT_URL, params={"profile_id": other_id}, headers=_headers(token)
    ).json()["data"]
    assert theirs["resume_id"] == other_resume["resume_id"]
    assert theirs["title"] == "算法版"
    assert theirs["version_no"] == 1, "版本号按档案各自递增"


def test_current_endpoint_error_contract(client: TestClient) -> None:
    """401 未登录；400 无档案；404 没生成过；404 越权 / 回收站。"""
    assert client.get(CURRENT_URL).status_code == 401

    token = _register_and_login(client)
    no_profile = client.get(CURRENT_URL, headers=_headers(token))
    assert no_profile.status_code == 400, no_profile.text
    assert no_profile.json()["error"]["code"] == "CLIENT_ERROR"

    profile = _complete_profile(client, token)
    never_generated = client.get(CURRENT_URL, headers=_headers(token))
    assert never_generated.status_code == 404, never_generated.text
    assert never_generated.json()["error"]["code"] == "NOT_FOUND"

    intruder = _register_and_login(client)
    assert (
        client.get(
            CURRENT_URL, params={"profile_id": profile["id"]}, headers=_headers(intruder)
        ).status_code
        == 404
    )

    client.delete(f"{PROFILES_BASE}/{profile['id']}", headers=_headers(token))
    assert (
        client.get(
            CURRENT_URL, params={"profile_id": profile["id"]}, headers=_headers(token)
        ).status_code
        == 404
    )


def test_broken_pointer_reports_explicitly_and_never_falls_back(client: TestClient) -> None:
    """指针落空必须显式报错（ADR-015 §2.3）：不许回落到 created_at 最新的一条。

    构造方式就是 ADR 写明的场景：链头行被物理删除，`ON DELETE SET NULL` 把其余行
    的指针置空。此时库里明明还有一条旧简历，读侧也必须报错而不是把它当「当前」。
    """
    token = _register_and_login(client)
    _complete_profile(client, token)
    _generate(client, token, "第一版")
    second = _generate(client, token, "第二版")

    with get_engine().begin() as connection:
        connection.execute(text("DELETE FROM resumes WHERE id = :id"), {"id": second["resume_id"]})

    # SET NULL 生效：剩下的行指针落空（而不是悬空指向已删行）。
    rows = _resume_rows()
    assert len(rows) == 1
    assert rows[0].current_version_id is None
    assert rows[0].is_head is None

    response = client.get(CURRENT_URL, headers=_headers(token))
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["code"] == "CLIENT_ERROR"
    # 文案唯一来源是 G3 那张表（批次 F1c）：断言枚举 + 用户动作，不抄整句。
    assert body["error"]["message"] == ErrorMessage.RESUME_POINTER_BROKEN
    assert "重新生成" in body["error"]["message"]

    # 修得动：重新生成一份，链头恢复，版本号接着序列走（不回退到 1）。
    third = _generate(client, token, "第三版")
    assert third["version_no"] == 3
    repaired = client.get(CURRENT_URL, headers=_headers(token))
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["data"]["resume_id"] == third["resume_id"]
    rows = _resume_rows()
    assert {row.current_version_id for row in rows} == {third["resume_id"]}


def test_existing_resume_snapshot_is_immutable_across_layout_upgrade(client: TestClient) -> None:
    """已存在的简历永不受升版影响（ADR-015 §2.2）：旧布局拦的是新产出，不改旧快照。"""
    token = _register_and_login(client)
    profile = _complete_profile(client, token)
    generated = _generate(client, token, "升版前")

    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE student_profiles SET layout_config = CAST(:raw AS JSON) WHERE id = :id"),
            {
                "raw": '{"version": 1, "sections": [{"id": "skills", "title": "技能"}]}',
                "id": profile["id"],
            },
        )

    blocked = client.post(GENERATE_URL, json={"title": "升版后"}, headers=_headers(token))
    assert blocked.status_code == 422, blocked.text

    # 旧快照一字未动，读路径照常返回它。
    current = client.get(CURRENT_URL, headers=_headers(token))
    assert current.status_code == 200, current.text
    assert current.json()["data"]["content_html"] == generated["content_html"]
    with get_engine().connect() as connection:
        stored = connection.execute(text("SELECT content_html FROM resumes")).scalar_one()
    assert stored == generated["content_html"]
