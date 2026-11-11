"""批次 B2（PRD FR-06）：导出前隐私检查。链路 = 注册 → 登录 → PUT 档案 → `POST /resumes/privacy-check`。

这个端点是**纯计算**：读一份已保存的档案，把「会被带出去什么」组装成四类清单返回，
不落库、不加列、不建表（形态与 `POST /jobs/extract-skills` 同一条路子，ADR-011 议题 2.A）。
所以本文件的第一条判据不是清单内容，而是「调用之后库里一行都没多」。

四类里，**批次 B4 之后**只剩一类核不了：授权范围（没有任何 consent/授权相关的列或表，
要等 B5 的组织与角色）。它必须如实返回 `not_available` 并带一句理由——把它伪装成
「用户什么都没填」是假话，把它省掉则是把 FR-06 点名的那一格悄悄删了。
作品链接在 B4 建出 `portfolio_links` 之后**改为真核**（`test_privacy_check_reports_real_portfolio_links`）。

错误口径与 `/generate` 逐字对齐（同一份简历不能两边各说一套）：
指定不存在的 / 别人的 / 在回收站的 → 404；一份档案都没有 → 400。
"""

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.repositories.user_repository import get_engine

PRIVACY_URL = "/api/v1/resumes/privacy-check"
GENERATE_URL = "/api/v1/resumes/generate"
PROFILES_URL = "/api/v1/profiles/me"
PROFILES_BASE = "/api/v1/profiles"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"

CATEGORY_KEYS = ["contact", "portfolio", "personal", "authorization"]


def _register_and_login(client: TestClient) -> str:
    username = f"privacy_{uuid4().hex[:10]}"
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


def _profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "full_name": "张三",
        "email": "zhangsan@example.com",
        "contact": "13800000000",
        "target_role": "后端开发",
        "skills": [{"name": "Python"}, {"name": "SQL"}],
        "educations": [
            {
                "school": "示例大学",
                "major": "软件工程",
                "degree": "本科",
                "edu_start": "2021-09-01",
                "edu_end": "2025-06-30",
            }
        ],
        "experiences": [
            {
                "title": "校园二手交易平台",
                "start_date": "2024-03-01",
                "end_date": "2024-06-30",
                "description": "面向校内学生的二手交易 Web 应用",
                "responsibility": "负责后端 API 与数据库设计",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _categories(response: TestClient) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = response.json()["data"]["categories"]
    return data


def _category(response: TestClient, key: str) -> dict[str, Any]:
    found = [item for item in _categories(response) if item["key"] == key]
    assert len(found) == 1, f"类别 {key} 应当恰好一条，实际 {len(found)}"
    return found[0]


def _item(response: TestClient, category_key: str, item_key: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = _category(response, category_key)["items"]
    found = [item for item in items if item["key"] == item_key]
    assert len(found) == 1, f"{category_key} 里应当恰好一条 {item_key}"
    return found[0]


def _count(table: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def _saved(client: TestClient, token: str, **overrides: object) -> dict[str, Any]:
    response = client.put(PROFILES_URL, json=_profile_payload(**overrides), headers=_headers(token))
    assert response.status_code == 200, response.text
    profile: dict[str, Any] = response.json()["data"]["profile"]
    return profile


def test_privacy_check_requires_authentication(client: TestClient) -> None:
    response = client.post(PRIVACY_URL, json={})

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "CLIENT_ERROR"


def test_privacy_check_is_pure_computation(client: TestClient) -> None:
    """这条是本端点的第一判据：检查一次，库里不许多出一行。"""
    token = _register_and_login(client)
    _saved(client, token)
    before = _count("resumes")

    checked = client.post(PRIVACY_URL, json={}, headers=_headers(token))

    assert checked.status_code == 200, checked.text
    assert _count("resumes") == before


def test_privacy_check_reports_the_four_categories_in_prd_order(client: TestClient) -> None:
    token = _register_and_login(client)
    saved = _saved(client, token)

    response = client.post(PRIVACY_URL, json={}, headers=_headers(token))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["profile_id"] == saved["id"]
    assert [item["key"] for item in _categories(response)] == CATEGORY_KEYS
    assert [item["label"] for item in _categories(response)] == [
        "联系方式",
        "作品链接",
        "个人信息",
        "授权范围",
    ]


def test_privacy_check_marks_filled_contact_as_included(client: TestClient) -> None:
    token = _register_and_login(client)
    _saved(client, token)

    response = client.post(PRIVACY_URL, json={}, headers=_headers(token))

    contact = _category(response, "contact")
    assert contact["status"] == "present"
    assert contact["note"] is None
    phone = _item(response, "contact", "phone")
    assert phone["present"] is True and phone["included"] is True
    email = _item(response, "contact", "email")
    assert email["present"] is True and email["included"] is True
    assert email["note"] is None


def test_privacy_check_says_so_when_email_is_missing(client: TestClient) -> None:
    """没填邮箱时纸面上不留「待补充」标记（那是可选项），所以**这里必须说**——
    两边各沉默一次，用户就永远不知道 HR 少了一种联系他的方式。"""
    token = _register_and_login(client)
    _saved(client, token, email=None)

    response = client.post(PRIVACY_URL, json={}, headers=_headers(token))

    email = _item(response, "contact", "email")
    assert email["present"] is False
    assert email["included"] is False
    assert email["note"]
    assert "邮箱" in email["note"]


def test_privacy_check_separates_present_from_included(client: TestClient) -> None:
    """`present` 与 `included` 是两个问题：目标岗位偏好确实存在，但不写进简历。"""
    token = _register_and_login(client)
    _saved(client, token)

    response = client.post(PRIVACY_URL, json={}, headers=_headers(token))

    target_role = _item(response, "personal", "target_role")
    assert target_role["present"] is True
    assert target_role["included"] is False
    assert target_role["note"]


def test_privacy_check_labels_counts_of_content(client: TestClient) -> None:
    token = _register_and_login(client)
    _saved(client, token)

    response = client.post(PRIVACY_URL, json={}, headers=_headers(token))

    assert _item(response, "personal", "skills")["label"] == "技能（2 条）"
    assert _item(response, "personal", "experiences")["label"] == "项目经历（1 段）"
    assert _item(response, "personal", "educations")["label"] == "教育经历（1 段）"


def test_privacy_check_announces_missing_education_section(client: TestClient) -> None:
    token = _register_and_login(client)
    _saved(client, token, educations=[])

    response = client.post(PRIVACY_URL, json={}, headers=_headers(token))

    educations = _item(response, "personal", "educations")
    assert educations["present"] is False
    assert educations["included"] is False
    assert educations["note"]


def test_privacy_check_is_honest_about_the_unbuildable_category(
    client: TestClient,
) -> None:
    """授权范围：数据模型里没有这一层，就说没有，不伪装成「用户没填」。

    `status="not_available"` 时 `note` 是**必给**的：不给，用户看见的就是一个没有来由的空壳。

    **这一格从「两格」变成「一格」是批次 B4 的直接后果**：`portfolio_links` 表建起来之后，
    作品链接第一次真的可核（见下一条用例），再写 `not_available` 就是假话。
    只有「授权给谁看」还缺数据模型——它等 B5 的组织与角色。
    """
    token = _register_and_login(client)
    _saved(client, token)

    response = client.post(PRIVACY_URL, json={}, headers=_headers(token))

    authorization = _category(response, "authorization")
    assert authorization["status"] == "not_available"
    assert authorization["items"] == []
    assert authorization["note"]


def test_privacy_check_reports_real_portfolio_links(client: TestClient) -> None:
    """作品链接这一格：B4 之后它真的在核对，而不是一句「今天没有这一栏」。

    三件事必须同时成立，缺一条这格就会退回成一句安慰话：
    ① 没填时是 `missing`（诚实说没填，不是 `not_available`）；
    ② 填了就逐条列出来，`included=True`——简历里它确实是可点的链接；
    ③ 每条都带上那条 URL 本身，用户才知道「带出去的到底是哪一个」。
    """
    token = _register_and_login(client)
    _saved(client, token)

    empty = client.post(PRIVACY_URL, json={}, headers=_headers(token))
    empty_portfolio = _category(empty, "portfolio")
    assert empty_portfolio["status"] == "missing"
    assert empty_portfolio["items"] == []

    _saved(
        client,
        token,
        portfolio_links=[
            {"label": "个人博客", "url": "https://example.com/blog"},
            {"label": "开源项目", "url": "https://example.com/repo"},
        ],
    )
    filled = client.post(PRIVACY_URL, json={}, headers=_headers(token))
    portfolio = _category(filled, "portfolio")
    assert portfolio["status"] == "present"
    labels = [item["label"] for item in portfolio["items"]]
    assert labels == ["个人博客", "开源项目"]
    assert all(item["present"] and item["included"] for item in portfolio["items"])
    notes = " ".join(str(item["note"]) for item in portfolio["items"])
    assert "https://example.com/blog" in notes
    assert "https://example.com/repo" in notes


def test_privacy_check_and_generate_point_at_the_same_resume(client: TestClient) -> None:
    """跨端点一致性（与 ADR-017 同一手法）：同一份简历，两边的读数不能各说一套。

    钉的是「项目经历这一格」：`generate` 因为它是空的而 400，隐私检查就必须说它没填；
    反过来填上之后，两边同时转正。只读一边永远发现不了「两处各自判了一次」。
    """
    token = _register_and_login(client)
    _saved(client, token)

    filled = client.post(PRIVACY_URL, json={}, headers=_headers(token))
    assert _item(filled, "personal", "experiences")["present"] is True
    assert (
        client.post(GENERATE_URL, json={"title": "有经历"}, headers=_headers(token)).status_code
        == 200
    )

    # 另开一份空经历的简历：两边都必须同时把它判成「没填」。
    second = client.post(
        PROFILES_BASE,
        json={"title": "投算法"},
        headers=_headers(token),
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["data"]["profile"]["id"]
    saved_second = client.put(
        f"{PROFILES_BASE}/{second_id}",
        json=_profile_payload(experiences=[]),
        headers=_headers(token),
    )
    assert saved_second.status_code == 200, saved_second.text

    checked = client.post(PRIVACY_URL, json={"profile_id": second_id}, headers=_headers(token))
    assert checked.status_code == 200, checked.text
    assert checked.json()["data"]["profile_id"] == second_id
    assert _item(checked, "personal", "experiences")["present"] is False

    generated = client.post(
        GENERATE_URL,
        json={"title": "投算法的简历", "profile_id": second_id},
        headers=_headers(token),
    )
    assert generated.status_code == 400, generated.text


def test_privacy_check_rejects_profile_of_another_user(client: TestClient) -> None:
    token_a = _register_and_login(client)
    token_b = _register_and_login(client)
    profile_a = _saved(client, token_a)
    _saved(client, token_b, full_name="王五", contact="13900000000")

    response = client.post(
        PRIVACY_URL,
        json={"profile_id": profile_a["id"]},
        headers=_headers(token_b),
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_privacy_check_rejects_profile_in_trash(client: TestClient) -> None:
    token = _register_and_login(client)
    saved = _saved(client, token)
    client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token))

    response = client.post(PRIVACY_URL, json={"profile_id": saved["id"]}, headers=_headers(token))

    assert response.status_code == 404, response.text


def test_privacy_check_returns_400_when_user_has_no_profile(client: TestClient) -> None:
    token = _register_and_login(client)

    response = client.post(PRIVACY_URL, json={}, headers=_headers(token))

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "CLIENT_ERROR"


def test_privacy_check_rejects_a_non_uuid_profile_id_shape(client: TestClient) -> None:
    """`profile_id` 有长度上限（36）：超出的必须是 422 校验错，而不是被静默截断后查不到。"""
    token = _register_and_login(client)
    _saved(client, token)

    response = client.post(
        PRIVACY_URL,
        json={"profile_id": "x" * 200},
        headers=_headers(token),
    )

    assert response.status_code == 422, response.text
