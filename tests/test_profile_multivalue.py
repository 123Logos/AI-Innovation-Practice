"""B4（ADR-015 §2.1 / §5）：档案多值子表 + 「全量替换 + 软删除」的集成测试。

覆盖六条裁定落地后的可观察行为：

1. 三张新子表（证书 / 作品链接 / 期望城市）的写入与读回，上限各 5；
2. 软删除：PUT 替换给旧活跃行盖 `deleted_at`，**不再物理 DELETE**；
3. **三屏一致**：档案页 / 生成物 / 匹配都读不到已删行（读侧过滤只在仓储层，
   6 处调用点一律不自比——漏一处就是三屏读数互不相同）；
4. 删掉再加回同名条目不撞 1062（`active_marker` 三元组唯一键）；
5. 条数上限只数活跃行：历史行不占名额；
6. `duplicate_profile` 只复制活跃行；永久删除把三张新表（含历史行）物理清干净。

no-op PUT 的契约在 `tests/test_profiles.py::test_put_profile_replaces_children_on_second_call`
（X1：没有它，软删除会把每次无变更保存放大成历史爆炸）。
"""

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.repositories.user_repository import get_engine

PROFILES_URL = "/api/v1/profiles/me"
PROFILES_BASE = "/api/v1/profiles"
GENERATE_URL = "/api/v1/resumes/generate"
JOBS_URL = "/api/v1/jobs"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"

#: 「三屏一致」那条用例专用的经历：正文点名 Python 与 SQL。
#: 原因见用例内的注释——ADR-013 的「已具备」要求有出处，否则技能只会落到 `partial`。
EVIDENCED_EXPERIENCES: list[dict[str, Any]] = [
    {
        "title": "校园二手交易平台",
        "start_date": "2024-03-01",
        "end_date": "2024-06-30",
        "description": "用 Python 写的后端服务，SQL 负责建表与查询。",
        "responsibility": "负责后端 API 与数据库设计（Python / SQL）。",
    }
]


def _register_and_login(client: TestClient) -> str:
    username = f"multivalue_{uuid4().hex[:10]}"
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


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "full_name": "张三",
        "contact": "13800000000",
        "skills": [{"name": "Python"}, {"name": "SQL"}],
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
        "certificates": [{"name": "软件设计师", "issuer": "工信部", "issued_on": "2024-05-01"}],
        "portfolio_links": [{"label": "个人博客", "url": "https://example.com/blog"}],
        "city_preferences": [{"city": "深圳"}, {"city": "杭州"}],
    }
    payload.update(overrides)
    return payload


def _put(client: TestClient, token: str, **overrides: Any) -> dict[str, Any]:
    response = client.put(PROFILES_URL, json=_payload(**overrides), headers=_headers(token))
    assert response.status_code == 200, response.text
    profile: dict[str, Any] = response.json()["data"]["profile"]
    return profile


def _rows(sql: str, **params: Any) -> list[Any]:
    with get_engine().connect() as connection:
        return list(connection.execute(text(sql), params).all())


def _count(table: str, where: str = "1=1") -> int:
    return int(_rows(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}")[0].n)


# --------------------------------------------------------------------------- #
# 1. 三张新子表的写入与读回
# --------------------------------------------------------------------------- #


def test_multivalue_fields_round_trip(client: TestClient) -> None:
    """证书 / 作品 / 城市写进去要读得回来，顺序即保存顺序，选填格可为空。"""
    token = _register_and_login(client)

    profile = _put(
        client,
        token,
        certificates=[
            {"name": "软件设计师", "issuer": "工信部", "issued_on": "2024-05-01"},
            {"name": "CET-6"},
        ],
        portfolio_links=[
            {"label": "个人博客", "url": "https://example.com/blog"},
            {"label": "GitHub", "url": "https://github.com/example"},
        ],
        city_preferences=[{"city": "深圳"}, {"city": "杭州"}, {"city": "上海"}],
    )

    assert [item["name"] for item in profile["certificates"]] == ["软件设计师", "CET-6"]
    assert profile["certificates"][0]["issuer"] == "工信部"
    assert profile["certificates"][0]["issued_on"] == "2024-05-01"
    assert profile["certificates"][1]["issuer"] is None
    assert profile["certificates"][1]["issued_on"] is None
    assert [item["label"] for item in profile["portfolio_links"]] == ["个人博客", "GitHub"]
    assert [item["city"] for item in profile["city_preferences"]] == ["深圳", "杭州", "上海"]
    assert _count("profile_certificates") == 2
    assert _count("portfolio_links") == 2
    assert _count("profile_city_preferences") == 3

    read_back = client.get(PROFILES_URL, headers=_headers(token)).json()["data"]["profile"]
    assert read_back["certificates"] == profile["certificates"]
    assert read_back["portfolio_links"] == profile["portfolio_links"]
    assert read_back["city_preferences"] == profile["city_preferences"]


def test_multivalue_limits_are_five_each(client: TestClient) -> None:
    """上限各 5（与 educations 同档，理由只有一条：一页 A4）；第 6 条 422。"""
    token = _register_and_login(client)

    for field, items in (
        ("certificates", [{"name": f"证书{i}"} for i in range(6)]),
        (
            "portfolio_links",
            [{"label": f"作品{i}", "url": f"https://example.com/{i}"} for i in range(6)],
        ),
        ("city_preferences", [{"city": f"城市{i}"} for i in range(6)]),
    ):
        response = client.put(
            PROFILES_URL, json=_payload(**{field: items}), headers=_headers(token)
        )
        assert response.status_code == 422, f"{field} -> {response.text}"
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    # 恰好 5 条能存。
    profile = _put(
        client,
        token,
        certificates=[{"name": f"证书{i}"} for i in range(5)],
        portfolio_links=[
            {"label": f"作品{i}", "url": f"https://example.com/{i}"} for i in range(5)
        ],
        city_preferences=[{"city": f"城市{i}"} for i in range(5)],
    )
    assert len(profile["certificates"]) == 5
    assert len(profile["portfolio_links"]) == 5
    assert len(profile["city_preferences"]) == 5


def test_multivalue_validation_rejects_bad_rows(client: TestClient) -> None:
    """空锚点 / 重复条目 / 非 http(s) URL 都是可读 422，不是数据库 1062 变 500。"""
    token = _register_and_login(client)

    bad_payloads = [
        {"certificates": [{"name": "   "}]},
        {"certificates": [{"name": "CET-6"}, {"name": "cet-6"}]},
        {"portfolio_links": [{"label": "  ", "url": "https://example.com"}]},
        {"portfolio_links": [{"label": "a", "url": "javascript:alert(1)"}]},
        {"portfolio_links": [{"label": "a", "url": "ftp://example.com"}]},
        {
            "portfolio_links": [
                {"label": "a", "url": "https://example.com/x"},
                {"label": "b", "url": "https://example.com/x"},
            ]
        },
        {"city_preferences": [{"city": " "}]},
        {"city_preferences": [{"city": "深圳"}, {"city": "深圳"}]},
    ]
    for overrides in bad_payloads:
        response = client.put(PROFILES_URL, json=_payload(**overrides), headers=_headers(token))
        assert response.status_code == 422, f"{overrides} -> {response.text}"
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _count("profile_certificates") == 0
    assert _count("portfolio_links") == 0
    assert _count("profile_city_preferences") == 0


# --------------------------------------------------------------------------- #
# 2. 软删除 + 三屏一致 + 同名再加回
# --------------------------------------------------------------------------- #


def test_soft_deleted_rows_disappear_from_all_three_screens(client: TestClient) -> None:
    """档案页 / 生成物 / 匹配三屏都读不到已删行（ADR-015 §2.1 的推论）。

    读侧过滤只写在仓储层 `list_*` 里；这条用例同时盯住三个调用方
    （profile_service / resume_service / match_service），漏一处就红。
    """
    token = _register_and_login(client)
    _put(
        client,
        token,
        # 两段正文必须点名 Python 与 SQL：ADR-013 的「已具备」要求**有出处**，
        # 技能名进了档案但经历里找不到出处只会落到 `partial`（`matched_without_evidence`）。
        # 这条用例要验的是「三屏都读不到已删行」，不该被证据门槛挡成 partial。
        experiences=EVIDENCED_EXPERIENCES,
    )

    # 岗位侧：要求 Python + SQL，发布后任何人可匹配。
    job = client.post(
        JOBS_URL,
        json={
            "company_name": "示例科技",
            "title": "后端实习生",
            "description": "需要 Python 与 SQL。",
            "skills": [{"name": "Python", "type": "hard"}, {"name": "SQL", "type": "hard"}],
        },
        headers=_headers(token),
    )
    assert job.status_code == 201, job.text
    job_id = job.json()["data"]["job"]["id"]
    published = client.put(f"{JOBS_URL}/{job_id}/publish", headers=_headers(token))
    assert published.status_code == 200, published.text

    before = client.post(f"{JOBS_URL}/{job_id}/match", headers=_headers(token))
    assert before.status_code == 200, before.text
    assert {item["skill"] for item in before.json()["data"]["covered"]} == {"Python", "SQL"}
    generated_before = client.post(GENERATE_URL, json={"title": "删前"}, headers=_headers(token))
    assert generated_before.status_code == 200, generated_before.text
    assert (
        '<span class="skill-item">Python</span>' in generated_before.json()["data"]["content_html"]
    )

    # 删掉 Python（全量替换：skills 只提交 SQL）。
    _put(client, token, skills=[{"name": "SQL"}], experiences=EVIDENCED_EXPERIENCES)

    # 屏 1：档案页。
    profile = client.get(PROFILES_URL, headers=_headers(token)).json()["data"]["profile"]
    assert [item["name"] for item in profile["skills"]] == ["SQL"]
    # 屏 2：生成物。
    generated = client.post(GENERATE_URL, json={"title": "删后"}, headers=_headers(token))
    assert generated.status_code == 200, generated.text
    html = generated.json()["data"]["content_html"]
    assert '<span class="skill-item">Python</span>' not in html
    assert '<span class="skill-item">SQL</span>' in html
    # 屏 3：匹配（无 Key → 关键词模式，确定性）。
    after = client.post(f"{JOBS_URL}/{job_id}/match", headers=_headers(token))
    assert after.status_code == 200, after.text
    data = after.json()["data"]
    assert {item["skill"] for item in data["covered"]} == {"SQL"}
    assert {item["skill"] for item in data["gaps"]} == {"Python"}

    # 库里：Python 那行还在，但盖了戳（软删除，不是物理 DELETE）。
    assert _count("skills") == 3
    assert _count("skills", "deleted_at IS NULL") == 1
    deleted = _rows("SELECT name FROM skills WHERE deleted_at IS NOT NULL")
    assert {row.name for row in deleted} == {"Python", "SQL"}  # 第一轮两条都成了历史


def test_re_adding_same_name_does_not_hit_1062(client: TestClient) -> None:
    """删掉「Python」再加回「Python」：历史行还在，活跃行唯一键不撞（ADR-015 §2.1）。

    证书 / 链接 / 城市同理：唯一键是含 active_marker 的三元组，历史行 marker=NULL
    自由重复。这条在旧的两列唯一键下是 IntegrityError 1062 → 500。
    """
    token = _register_and_login(client)
    _put(client, token)

    # 删光再原样加回，来回两轮。
    for _ in range(2):
        _put(
            client,
            token,
            skills=[],
            certificates=[],
            portfolio_links=[],
            city_preferences=[],
        )
        _put(client, token)

    profile = client.get(PROFILES_URL, headers=_headers(token)).json()["data"]["profile"]
    assert [item["name"] for item in profile["skills"]] == ["Python", "SQL"]
    assert [item["name"] for item in profile["certificates"]] == ["软件设计师"]
    assert [item["city"] for item in profile["city_preferences"]] == ["深圳", "杭州"]
    # 历史行积累但活跃行唯一：技能 2 活跃 + 4 历史（两轮各删 2）。
    assert _count("skills", "deleted_at IS NULL") == 2
    assert _count("skills") == 6
    assert _count("profile_certificates", "deleted_at IS NULL") == 1
    assert _count("profile_city_preferences", "deleted_at IS NULL") == 2


def test_limits_count_active_rows_only(client: TestClient) -> None:
    """上限只数活跃行：存几次之后仍然加得进东西（历史行不占名额）。"""
    token = _register_and_login(client)

    # 技能上限 8：连续三轮各存 8 条不同的技能，每轮都必须 200。
    # 其余子表显式清空，免得每轮 `_payload` 的默认证书/链接混进下面的计数。
    empty = {"certificates": [], "portfolio_links": [], "city_preferences": []}
    for round_no in range(3):
        skills = [{"name": f"技能{round_no}-{i}"} for i in range(8)]
        response = client.put(
            PROFILES_URL, json=_payload(skills=skills, **empty), headers=_headers(token)
        )
        assert response.status_code == 200, response.text
    assert _count("skills") == 24
    assert _count("skills", "deleted_at IS NULL") == 8

    # 第 9 条仍然 422（上限本身没被放宽）。
    overflow = client.put(
        PROFILES_URL,
        json=_payload(skills=[{"name": f"技能{i}"} for i in range(9)], **empty),
        headers=_headers(token),
    )
    assert overflow.status_code == 422

    # 新表同理：三轮各 5 条证书后，仍然是 5 条活跃（历史行不占名额）。
    for round_no in range(3):
        certificates = [{"name": f"证书{round_no}-{i}"} for i in range(5)]
        response = client.put(
            PROFILES_URL,
            json=_payload(
                certificates=certificates, skills=[], portfolio_links=[], city_preferences=[]
            ),
            headers=_headers(token),
        )
        assert response.status_code == 200, response.text
    assert _count("profile_certificates") == 15
    assert _count("profile_certificates", "deleted_at IS NULL") == 5


# --------------------------------------------------------------------------- #
# 3. no-op / 深拷贝 / 永久删除
# --------------------------------------------------------------------------- #


def test_noop_put_with_multivalue_fields_keeps_ids_and_timestamp(client: TestClient) -> None:
    """含新子表的同体 PUT 也是 no-op：id 集合与 updated_at 不变，不盖 deleted_at。"""
    token = _register_and_login(client)
    first = _put(client, token)

    second = _put(client, token)

    assert second["updated_at"] == first["updated_at"]
    for field in ("skills", "experiences", "certificates", "portfolio_links", "city_preferences"):
        assert {item["id"] for item in second[field]} == {item["id"] for item in first[field]}, (
            field
        )
    for table in ("skills", "profile_certificates", "portfolio_links", "profile_city_preferences"):
        assert _count(table, "deleted_at IS NOT NULL") == 0, table


def test_duplicate_profile_copies_only_active_rows(client: TestClient) -> None:
    """深拷贝只复制活跃行（ADR-015 §2.1）：历史行不跟着副本再活一次。"""
    token = _register_and_login(client)
    source = _put(client, token)
    # 制造历史：替换一轮技能与证书。
    _put(
        client,
        token,
        skills=[{"name": "Go"}],
        certificates=[{"name": "CET-6"}],
        portfolio_links=[],
        city_preferences=[{"city": "北京"}],
    )
    assert _count("skills") == 3  # 2 历史 + 1 活跃
    assert _count("profile_certificates") == 2

    copied = client.post(
        PROFILES_BASE,
        json={"title": "副本", "copy_from_id": source["id"]},
        headers=_headers(token),
    )
    assert copied.status_code == 201, copied.text
    copy = copied.json()["data"]["profile"]
    assert [item["name"] for item in copy["skills"]] == ["Go"]
    assert [item["name"] for item in copy["certificates"]] == ["CET-6"]
    assert copy["portfolio_links"] == []
    assert [item["city"] for item in copy["city_preferences"]] == ["北京"]
    # 副本的行全是活跃行，且 id 全新。
    assert _count("skills", f"student_profile_id = '{copy['id']}' AND deleted_at IS NOT NULL") == 0
    assert {item["id"] for item in copy["skills"]}.isdisjoint(
        {item["id"] for item in source["skills"]}
    )


def test_permanent_delete_cleans_all_new_tables_including_history(client: TestClient) -> None:
    """永久删除把三张新表物理清干净（_CHILD_TABLES 在册），历史行一并清掉。"""
    token = _register_and_login(client)
    saved = _put(client, token)
    _put(client, token, skills=[{"name": "Go"}])  # 制造历史行
    assert _count("profile_certificates") == 2
    assert _count("portfolio_links") == 2
    assert _count("profile_city_preferences") == 4

    assert (
        client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token)).status_code == 200
    )
    purged = client.delete(f"{PROFILES_BASE}/{saved['id']}/permanent", headers=_headers(token))
    assert purged.status_code == 200, purged.text

    for table in (
        "skills",
        "experiences",
        "educations",
        "resumes",
        "profile_certificates",
        "portfolio_links",
        "profile_city_preferences",
    ):
        assert _count(table) == 0, f"{table} 没被清干净"
    assert _count("student_profiles") == 0
