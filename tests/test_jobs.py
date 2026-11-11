"""岗位库的端到端集成测试（Phase 4 · B1），连接真实 MySQL（`TEST_DATABASE_URL`）。

覆盖 ADR-011 的**行为**面：1.F 权限矩阵八行、议题 3.C 的 `keyword`/分页、
2.G 的过期三态、软删与回收站、写侧的「身份只从 JWT 取」，以及 404/422/500 的统一错误模型。
建表与用例间清理由 `tests/conftest.py` 负责（会话级 `upgrade head` + 逐用例清表）。

数据库层的约束（CHECK / 外键 / 唯一键的真实行为）在 `tests/test_jobs_constraints.py`，
本文件不重复那部分。

八行矩阵的断言口径：**非创建者一律 404，不是 403**（1.F 与 `profiles.py:10` 的先例）。
每条都同时断言「原数据没被动过」，否则「404 但其实改了」这种实现能骗过只看状态码的测试。
"""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.error_messages import ErrorMessage
from app.integrations.skill_extractor import (
    DEEPSEEK_MODEL,
    EXTRACT_PROMPT_VERSION,
)
from app.main import app
from app.repositories.job_repository import JobRepository
from app.repositories.user_repository import get_engine, session_scope
from app.services.dependencies import get_job_service
from app.services.job_service import JobNotFoundError, JobService

JOBS_BASE = "/api/v1/jobs"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"
PASSWORD = "Passw0rd!2026"


# --------------------------------------------------------------------------- #
# 夹具式辅助
# --------------------------------------------------------------------------- #


def _register_and_login(client: TestClient) -> str:
    username = f"job_{uuid4().hex[:10]}"
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


def _user_id(client: TestClient, token: str) -> str:
    response = client.get(ME_URL, headers=_headers(token))
    assert response.status_code == 200, response.text
    user_id: str = response.json()["data"]["user"]["id"]
    return user_id


def _skill(name: str = "Python", **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {"name": name}
    payload.update(extra)
    return payload


def _job_payload(**overrides: object) -> dict[str, object]:
    """一份最小可用的岗位请求体（三个必填 + 两条技能）。"""
    payload: dict[str, object] = {
        "company_name": "示例科技",
        "title": "后端实习（AI 方向）",
        "description": "负责后端接口开发，熟练掌握 Python 与 FastAPI。",
        "skills": [_skill("Python", type="hard"), _skill("沟通能力强", type="soft")],
    }
    payload.update(overrides)
    return payload


def _create(
    client: TestClient,
    token: str,
    **overrides: object,
) -> dict[str, object]:
    """录入一个岗位并断言 201；`overrides` 是**覆盖**而非替换（其余字段用默认值）。"""
    response = client.post(JOBS_BASE, json=_job_payload(**overrides), headers=_headers(token))
    assert response.status_code == 201, response.text
    job: dict[str, object] = response.json()["data"]["job"]
    return job


def _publish(client: TestClient, token: str, job_id: object) -> dict[str, object]:
    response = client.put(f"{JOBS_BASE}/{job_id}/publish", headers=_headers(token))
    assert response.status_code == 200, response.text
    job: dict[str, object] = response.json()["data"]["job"]
    return job


def _list(
    client: TestClient,
    token: str,
    **params: object,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    response = client.get(JOBS_BASE, params=params, headers=_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    jobs: list[dict[str, object]] = body["data"]["jobs"]
    meta: dict[str, object] = body["meta"]
    return jobs, meta


def _list_ids(client: TestClient, token: str, **params: object) -> list[str]:
    jobs, _ = _list(client, token, **params)
    return [str(job["id"]) for job in jobs]


def _detail(client: TestClient, token: str, job_id: object) -> dict[str, object]:
    response = client.get(f"{JOBS_BASE}/{job_id}", headers=_headers(token))
    assert response.status_code == 200, response.text
    job: dict[str, object] = response.json()["data"]["job"]
    return job


def _trash(client: TestClient, token: str) -> list[dict[str, object]]:
    response = client.get(f"{JOBS_BASE}/trash", headers=_headers(token))
    assert response.status_code == 200, response.text
    jobs: list[dict[str, object]] = response.json()["data"]["jobs"]
    return jobs


def _count(table: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def _scalar(sql: str, **params: object) -> object:
    with get_engine().connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


# --------------------------------------------------------------------------- #
# 鉴权（矩阵第 1/2/3/8 行的前置：所有端点都要登录）
# --------------------------------------------------------------------------- #


def test_all_job_endpoints_require_authentication(client: TestClient) -> None:
    """未登录时列表/详情/录入/回收站/恢复全部 401，岗位库不对匿名访客开放。"""
    assert client.get(JOBS_BASE).status_code == 401
    assert client.post(JOBS_BASE, json=_job_payload()).status_code == 401
    assert client.get(f"{JOBS_BASE}/some-id").status_code == 401
    assert client.put(f"{JOBS_BASE}/some-id", json=_job_payload()).status_code == 401
    assert client.delete(f"{JOBS_BASE}/some-id").status_code == 401
    assert client.put(f"{JOBS_BASE}/some-id/publish").status_code == 401
    assert client.put(f"{JOBS_BASE}/some-id/unpublish").status_code == 401
    assert client.put(f"{JOBS_BASE}/some-id/restore").status_code == 401
    assert client.get(f"{JOBS_BASE}/trash").status_code == 401


# --------------------------------------------------------------------------- #
# 矩阵第 3 行：POST /jobs —— 任何登录用户，201，恒为 draft
# --------------------------------------------------------------------------- #


def test_create_job_returns_201_and_persists_every_column(client: TestClient) -> None:
    """录入成功后响应与库里都有完整数据（不是只回显请求体）。"""
    token = _register_and_login(client)
    today = date.today()

    created = _create(
        client,
        token,
        company_name="示例科技",
        title="后端实习",
        location="上海",
        employment_type="internship",
        description="熟练掌握 Python，熟悉 FastAPI。",
        salary_min=3000,
        salary_max=6000,
        experience_required="3 年以上后端经验",
        deadline=(today + timedelta(days=30)).isoformat(),
        skills=[_skill("Python", type="hard", confidence=0.9)],
    )

    assert created["status"] == "draft"
    assert created["title"] == "后端实习"
    assert created["location"] == "上海"
    assert created["employment_type"] == "internship"
    assert created["experience_required"] == "3 年以上后端经验"
    assert created["deadline"] == (today + timedelta(days=30)).isoformat()
    assert created["is_expired"] is False
    # DECIMAL 列经 Pydantic 输出为字符串：不会有二进制浮点的尾数噪声。
    assert float(created["salary_min"]) == 3000
    assert float(created["salary_max"]) == 6000
    assert created["skills"] == [
        {
            "id": created["skills"][0]["id"],
            "name": "Python",
            "type": "hard",
            # 这一条**没有** `evidence_text`，所以 1.D 的派生只能给「未标注出处」；
            # `confidence` 自 ADR-011 v9 起真的落库（列 `DECIMAL(5,4)` → 读回 `"0.9000"`），
            # 但「有置信度」≠「可回溯原文」：置信度是模型自报的数字，徽标只看子串核验。
            "origin": "unverified",
            "confidence": "0.9000",
            "evidence_text": None,
        }
    ]
    # 时间字段与档案接口同口径：带 +08:00 偏移返回。
    assert str(created["created_at"]).endswith("+08:00")

    assert _count("jobs") == 1
    assert _count("job_skills") == 1
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT company_name, title, location, employment_type, description, "
                "       salary_min, salary_max, experience_required, deadline, status, "
                "       extract_model, extract_prompt_version, extracted_at, deleted_at "
                "FROM jobs"
            )
        ).one()
    assert row.company_name == "示例科技"
    assert row.description == "熟练掌握 Python，熟悉 FastAPI。"
    assert float(row.salary_min) == 3000
    assert str(row.deadline) == (today + timedelta(days=30)).isoformat()
    assert row.status == "draft"
    # 三列溯源戳仍为 NULL，但**理由换了**（依据 ADR-011 v9 放开）：
    # 不是「没有代码能写它们」，而是本次保存没有任何一条技能被判为 `jd_verifiable`
    # （这条只带了 `confidence`，没有 `evidence_text`，无从回溯原文），
    # 按 v9 第 ② 条，一条都没有时三列保持 NULL。盖戳的正向用例见下面的写回路径用例。
    assert row.extract_model is None
    assert row.extract_prompt_version is None
    assert row.extracted_at is None
    assert row.deleted_at is None


def test_create_job_is_open_to_every_logged_in_user(client: TestClient) -> None:
    """矩阵 #3：不区分角色，任何登录用户都能录（1.A：`users` 没有 `role`）。"""
    first = _register_and_login(client)
    second = _register_and_login(client)

    assert client.post(JOBS_BASE, json=_job_payload(), headers=_headers(first)).status_code == 201
    assert client.post(JOBS_BASE, json=_job_payload(), headers=_headers(second)).status_code == 201
    assert _count("jobs") == 2


def test_create_job_ignores_status_creator_and_origin_from_request_body(
    client: TestClient,
) -> None:
    """写侧的三条「不采信」（1.F）：`status` / `created_by_user_id` / `origin`。

    先例是 `test_generate_ignores_profile_fields_sent_in_request_body`。
    断言看的是**库里的值**而不是响应体：响应可以临时改写，落库才是既成事实。
    """
    token = _register_and_login(client)
    other_user_id = _user_id(client, _register_and_login(client))

    response = client.post(
        JOBS_BASE,
        json=_job_payload(
            # 伪装成「已发布」「别人创建的」「可回溯原文的」
            status="published",
            created_by_user_id=other_user_id,
            origin="jd_verifiable",
            skills=[_skill("Python", origin="jd_verifiable", type="hard")],
        ),
        headers=_headers(token),
    )
    assert response.status_code == 201, response.text
    job = response.json()["data"]["job"]

    assert job["status"] == "draft", "发布是显式动作（矩阵 #6），不是创建的副作用"
    assert job["created_by_user_id"] == _user_id(client, token)
    assert job["skills"][0]["origin"] == "unverified"

    assert _scalar("SELECT status FROM jobs WHERE id = :id", id=job["id"]) == "draft"
    assert _scalar("SELECT created_by_user_id FROM jobs WHERE id = :id", id=job["id"]) == _user_id(
        client, token
    )
    assert _scalar("SELECT origin FROM job_skills WHERE job_id = :id", id=job["id"]) == "unverified"


def test_create_job_rejects_employment_type_and_salary_range(client: TestClient) -> None:
    """422 + 统一错误模型：枚举外的 `employment_type`、填反了的薪资区间、空的 JD 原文。"""
    token = _register_and_login(client)

    for payload in (
        _job_payload(employment_type="远程"),
        _job_payload(salary_min=6000, salary_max=3000),
        _job_payload(description=""),
        _job_payload(company_name=""),
        _job_payload(title=""),
        _job_payload(skills=[_skill("Python"), _skill("Python")]),
        _job_payload(skills=[_skill(str(index)) for index in range(16)]),
        _job_payload(description="x" * 8001),
    ):
        response = client.post(JOBS_BASE, json=payload, headers=_headers(token))
        assert response.status_code == 422, str(payload)[:120] + response.text
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["message"]
        assert body["meta"]["request_id"]

    assert _count("jobs") == 0, "校验失败的请求不能留下一行"


def test_post_and_put_write_back_evidence_confidence_and_stamp_trace(client: TestClient) -> None:
    """写回路径的三条 v9 规则（依据 ADR-011 v9 放开 `evidence_text` / `confidence`）。

    本用例取代 B1 的 `test_post_and_put_ignore_evidence_text_and_confidence`——
    那条钉的是 v6 补条「写侧一律忽略这两格、库里恒为 NULL」；C1 的提取链路接通后
    该前提不再成立，所以**逐条改成 v9 的新口径**（断言一个没删，只是方向反过来）：

    1. `skills[]` 接受 `name`/`type`/`evidence_text`/`confidence` 四格，**库里就是这四格**；
    2. `origin` 仍由服务端按 1.D 派生（`evidence_text` 归一化后是否是 JD 原文的子串），
       客户端提交的 `origin` 忽略；
    3. 只要有任何一条被判 `jd_verifiable`，服务端盖三列溯源戳；一条都没有则三列 `NULL`；
       客户端提交的 `extract_model` / `extract_prompt_version` / `extracted_at` /
       `status` / `created_by_user_id` 一律忽略（断言看的是**库里的值**）；
    4. `confidence` 越界与 `evidence_text` 超 60 字 → **422 走统一错误模型**
       （v9 第 ③ 条，不再是 v6 的静默丢弃），且校验失败的请求不留一行。

    断言全部读原始表而不是响应体：响应可以临时改写，落库才是既成事实。
    """
    token = _register_and_login(client)
    other_user_id = _user_id(client, _register_and_login(client))

    def stored_skills(job_id: object) -> list[tuple[object, object, object, object]]:
        with get_engine().connect() as connection:
            return list(
                connection.execute(
                    text(
                        "SELECT name, origin, confidence, evidence_text FROM job_skills "
                        "WHERE job_id = :id ORDER BY created_at"
                    ),
                    {"id": str(job_id)},
                ).all()
            )

    def stored_stamp(job_id: object) -> tuple[object, object, object]:
        with get_engine().connect() as connection:
            row = connection.execute(
                text(
                    "SELECT extract_model, extract_prompt_version, extracted_at "
                    "FROM jobs WHERE id = :id"
                ),
                {"id": str(job_id)},
            ).one()
        return (row.extract_model, row.extract_prompt_version, row.extracted_at)

    # ---- 1/2/3：两条带原文依据、一条不带；默认 JD 原文是
    #      「负责后端接口开发，熟练掌握 Python 与 FastAPI。」
    created = _create(
        client,
        token,
        # 客户端能伪造的都伪造一遍：这三列与 status / 归属 / origin 都不该被采信。
        status="published",
        created_by_user_id=other_user_id,
        extract_model="some-other-model",
        extract_prompt_version="v99",
        extracted_at="2020-01-01T00:00:00",
        skills=[
            _skill("Python", type="hard", evidence_text="熟练掌握 Python", confidence=0.99),
            # 全角逗号 vs 半角逗号：§2.D.1 的「标点不参与比较」口径下这条同样可回溯。
            _skill("FastAPI", evidence_text="熟练掌握 Python, 与 FastAPI", confidence=0.5),
            _skill("SQL", evidence_text="熟悉 SQL", confidence=0.25, origin="jd_verifiable"),
            _skill("Docker"),
        ],
    )
    rows = stored_skills(created["id"])
    assert [row.name for row in rows] == ["Python", "FastAPI", "SQL", "Docker"]
    # `origin` 由服务端派生：前两条的锚点能在 JD 原文里逐字找到，
    # 「熟悉 SQL」找不到 → 未标注出处；客户端给 SQL 那条伪装的 `jd_verifiable` 被丢掉。
    assert [row.origin for row in rows] == [
        "jd_verifiable",
        "jd_verifiable",
        "unverified",
        "unverified",
    ]
    # v9 放开：库里就是提交的那两格（`DECIMAL(5,4)` 补零到四位）。
    assert [str(row.confidence) for row in rows] == ["0.9900", "0.5000", "0.2500", "None"]
    assert [row.evidence_text for row in rows] == [
        "熟练掌握 Python",
        "熟练掌握 Python, 与 FastAPI",
        "熟悉 SQL",
        None,
    ]
    # 响应体与库里同一口径（读路径先给出来，前端不需要为「字段将来才有值」写两套代码）。
    assert [skill["evidence_text"] for skill in created["skills"]] == [
        "熟练掌握 Python",
        "熟练掌握 Python, 与 FastAPI",
        "熟悉 SQL",
        None,
    ]
    returned_confidence = [
        None if skill["confidence"] is None else float(skill["confidence"])
        for skill in created["skills"]
    ]
    assert returned_confidence == [0.99, 0.5, 0.25, None]

    model, prompt_version, extracted_at = stored_stamp(created["id"])
    assert model == DEEPSEEK_MODEL, "提交的 extract_model 被忽略，落库的是服务端自己那一个"
    assert prompt_version == EXTRACT_PROMPT_VERSION
    assert extracted_at is not None, "有可回溯条目时必须盖时间戳，且不是提交里那个 2020 年"
    # 归属与状态同样不采信（1.F 的写侧规则，与这两格无关，留在本用例里一起钉住）。
    assert _scalar("SELECT status FROM jobs WHERE id = :id", id=created["id"]) == "draft"
    assert _scalar(
        "SELECT created_by_user_id FROM jobs WHERE id = :id", id=created["id"]
    ) == _user_id(client, token)

    # ---- 3 的反向：PUT 换成「一条都不可回溯」的技能后，三列必须回到 NULL。
    #      技能子表是整批换掉的，留着旧戳就是在为一批已经不在库里的技能声称「出自 JD 原文」。
    edited = client.put(
        f"{JOBS_BASE}/{created['id']}",
        json=_job_payload(skills=[_skill("Go", type="hard", confidence=0.5)]),
        headers=_headers(token),
    )
    assert edited.status_code == 200, edited.text
    rows = stored_skills(created["id"])
    assert [row.name for row in rows] == ["Go"]
    assert rows[0].origin == "unverified", "锚点不在 JD 里，置信度再高也不能算可回溯"
    assert str(rows[0].confidence) == "0.5000"
    assert rows[0].evidence_text is None
    assert stored_stamp(created["id"]) == (None, None, None)

    # ---- 4：越界值现在是 422（v6 是「静默丢掉、照样 201」）。
    before = _count("jobs")
    for payload in (
        _job_payload(skills=[_skill("Python", confidence=1.5)]),
        _job_payload(skills=[_skill("Python", confidence=-0.0001)]),
        _job_payload(skills=[_skill("Python", evidence_text="D" * 61)]),
    ):
        response = client.post(JOBS_BASE, json=payload, headers=_headers(token))
        assert response.status_code == 422, str(payload)[:120] + response.text
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["message"]
        assert body["meta"]["request_id"]
    assert _count("jobs") == before, "越界的技能不能留下一行"


# --------------------------------------------------------------------------- #
# 矩阵第 1 行：GET /jobs —— 默认只有 published，自己的草稿要显式 ?status=draft
# --------------------------------------------------------------------------- #


def test_list_defaults_to_published_only_and_own_drafts_on_request(client: TestClient) -> None:
    """矩阵 #1：默认列表只出 published；`status=draft` 出**自己的**草稿，绝不出别人的。"""
    owner = _register_and_login(client)
    stranger = _register_and_login(client)

    draft = _create(client, owner, title="还没确认的草稿")
    published = _create(client, owner, title="已发布的岗位")
    _publish(client, owner, published["id"])

    # 默认：只看到 published（自己的草稿也不出现，草稿要显式查）
    assert _list_ids(client, owner) == [str(published["id"])]
    # 别人的视角：只看得到 published
    assert _list_ids(client, stranger) == [str(published["id"])]
    # 自己的草稿：显式传 status=draft 才出现
    assert _list_ids(client, owner, status="draft") == [str(draft["id"])]
    # 别人的草稿永远不出现在任何列表里
    assert _list_ids(client, stranger, status="draft") == []

    # 白名单外的 status 直接 422（不拿用户输入拼 SQL）
    response = client.get(JOBS_BASE, params={"status": "pending_review"}, headers=_headers(owner))
    assert response.status_code == 422, response.text


def test_list_newest_first_and_carries_pagination_meta(client: TestClient) -> None:
    """议题 3.C：`page`/`page_size`/`total` 一次做对，默认「最近录入的在前」。"""
    token = _register_and_login(client)
    first = _create(client, token, title="岗位一")
    second = _create(client, token, title="岗位二")
    third = _create(client, token, title="岗位三")
    for job_id in (first["id"], second["id"], third["id"]):
        _publish(client, token, job_id)

    jobs, meta = _list(client, token)
    assert [job["id"] for job in jobs] == [third["id"], second["id"], first["id"]]
    assert meta["page"] == 1
    assert meta["page_size"] == 20
    assert meta["total"] == 3

    page_one, meta_one = _list(client, token, page=1, page_size=2)
    assert [job["id"] for job in page_one] == [third["id"], second["id"]]
    assert meta_one["total"] == 3, "total 是符合筛选的总数，不是当页条数"

    page_two, meta_two = _list(client, token, page=2, page_size=2)
    assert [job["id"] for job in page_two] == [first["id"]]
    assert meta_two["page"] == 2

    # 越界页：空列表但 total 如实（前端据此判断「没有下一页」）
    page_nine, _ = _list(client, token, page=9, page_size=2)
    assert page_nine == []

    # page_size 上限 100、page 下限 1：非法值 422 而不是全表扫描
    assert (
        client.get(JOBS_BASE, params={"page_size": 101}, headers=_headers(token)).status_code == 422
    )
    assert client.get(JOBS_BASE, params={"page": 0}, headers=_headers(token)).status_code == 422


def test_list_keyword_matches_company_and_title_only(client: TestClient) -> None:
    """本轮筛选只有 `keyword`（企业名/岗位名）+ `status`（议题 3.C、1.G）。"""
    token = _register_and_login(client)
    by_company = _create(client, token, company_name="字节跳动", title="算法实习")
    by_title = _create(client, token, company_name="某某科技", title="字节方向后端")
    other = _create(client, token, company_name="某某科技", title="前端实习")
    for job_id in (by_company["id"], by_title["id"], other["id"]):
        _publish(client, token, job_id)

    matched = _list_ids(client, token, keyword="字节")
    assert sorted(matched) == sorted([str(by_company["id"]), str(by_title["id"])]), (
        "企业名或岗位名命中都算"
    )

    # keyword 不匹配 description：JD 正文最长 8000 字，广场搜索的是「哪个岗位」
    assert _list_ids(client, token, keyword="FastAPI") == []

    # LIKE 通配符按字面量处理（`autoescape`）：搜 `%` 不该把所有岗位捞出来
    percent = _create(client, token, company_name="100% 远程", title=" percent 岗位")
    _publish(client, token, percent["id"])
    assert _list_ids(client, token, keyword="%") == [str(percent["id"])]


def test_list_summary_omits_description_and_caps_skills_at_six(client: TestClient) -> None:
    """列表项是卡片形状：不带 JD 原文（最长 8000 字），技能只给前 6 条（议题 3.B）。"""
    token = _register_and_login(client)
    created = _create(
        client,
        token,
        description="很长的 JD 原文" * 100,
        skills=[_skill(f"技能{index}") for index in range(6)],
    )
    _publish(client, token, created["id"])

    jobs, _ = _list(client, token)
    assert len(jobs) == 1
    assert "description" not in jobs[0]
    assert len(jobs[0]["skills"]) == 6

    # 第 7 条技能在详情里完整可见，只是列表不搬
    trimmed = _create(
        client,
        token,
        skills=[_skill(f"技能{index}") for index in range(7)],
    )
    _publish(client, token, trimmed["id"])
    cards = {job["id"]: job for job in _list(client, token)[0]}
    assert len(cards[trimmed["id"]]["skills"]) == 6
    assert len(_detail(client, token, trimmed["id"])["skills"]) == 7
    # 顺序 = 录入顺序（子行 created_at 由仓储错开 1 微秒保证）
    assert [skill["name"] for skill in _detail(client, token, trimmed["id"])["skills"]] == [
        f"技能{index}" for index in range(7)
    ]


# --------------------------------------------------------------------------- #
# 矩阵第 2 行：GET /jobs/{id} —— published 人人可读，他人 draft 一律 404
# --------------------------------------------------------------------------- #


def test_detail_published_is_readable_by_anyone_but_draft_only_by_owner(
    client: TestClient,
) -> None:
    """矩阵 #2：published 任何登录用户 200；他人 draft 404；不存在的 id 也是 404。"""
    owner = _register_and_login(client)
    stranger = _register_and_login(client)
    draft = _create(client, owner, title="别人的草稿")
    published = _create(client, owner, title="已发布")
    _publish(client, owner, published["id"])

    assert _detail(client, stranger, published["id"])["status"] == "published"

    forbidden = client.get(f"{JOBS_BASE}/{draft['id']}", headers=_headers(stranger))
    assert forbidden.status_code == 404, forbidden.text
    # 关键：不是 403。403 会暴露「这个 id 真的存在，只是你没权限」。
    assert forbidden.json()["error"]["code"] == "NOT_FOUND"
    assert forbidden.json()["meta"]["request_id"]

    assert client.get(f"{JOBS_BASE}/{uuid4()}", headers=_headers(owner)).status_code == 404, (
        "「不存在」与「无权」必须是同一个答案"
    )

    # 自己的草稿当然可读
    assert _detail(client, owner, draft["id"])["status"] == "draft"


# --------------------------------------------------------------------------- #
# 矩阵第 4 行：PUT /jobs/{id} —— 仅创建者，status 保持不变
# --------------------------------------------------------------------------- #


def test_update_is_owner_only_and_keeps_status(client: TestClient) -> None:
    """矩阵 #4：非创建者 404 且数据一字未改；创建者 PUT 不会顺带发布或下架。"""
    owner = _register_and_login(client)
    stranger = _register_and_login(client)
    created = _create(client, owner, title="原标题", skills=[_skill("Python")])
    _publish(client, owner, created["id"])

    forbidden = client.put(
        f"{JOBS_BASE}/{created['id']}",
        json=_job_payload(title="被改掉了", skills=[_skill("伪造")]),
        headers=_headers(stranger),
    )
    assert forbidden.status_code == 404, forbidden.text
    untouched = _detail(client, owner, created["id"])
    assert untouched["title"] == "原标题"
    assert [skill["name"] for skill in untouched["skills"]] == ["Python"]

    updated = client.put(
        f"{JOBS_BASE}/{created['id']}",
        json=_job_payload(title="改对了", skills=[_skill("Go"), _skill("SQL")]),
        headers=_headers(owner),
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()["data"]["job"]
    assert body["title"] == "改对了"
    assert body["status"] == "published", "PUT 不改变发布状态（发布是独立动作）"
    assert [skill["name"] for skill in body["skills"]] == ["Go", "SQL"]
    # 子表全量替换：旧行必须物理消失，不能靠过滤遮住
    assert _count("job_skills") == 2

    # 草稿改完还是草稿
    draft = _create(client, owner, title="草稿")
    edited = client.put(
        f"{JOBS_BASE}/{draft['id']}",
        json=_job_payload(title="草稿改名了"),
        headers=_headers(owner),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["job"]["status"] == "draft"

    assert (
        client.put(
            f"{JOBS_BASE}/{uuid4()}", json=_job_payload(), headers=_headers(owner)
        ).status_code
        == 404
    )


# --------------------------------------------------------------------------- #
# 矩阵第 6 行：publish / unpublish —— 仅创建者，广场可见性立刻跟着变
# --------------------------------------------------------------------------- #


def test_publish_and_unpublish_are_owner_only_and_take_effect_immediately(
    client: TestClient,
) -> None:
    """矩阵 #6：非创建者两个动作都 404；unpublish 后别人立刻读不到、自己也回不到广场。"""
    owner = _register_and_login(client)
    stranger = _register_and_login(client)
    created = _create(client, owner)

    # 草稿期：陌生人四处都找不到它
    assert _list_ids(client, stranger) == []
    assert client.get(f"{JOBS_BASE}/{created['id']}", headers=_headers(stranger)).status_code == 404

    # 陌生人既不能发布也不能下架
    for verb, action in (("put", "publish"), ("put", "unpublish")):
        response = getattr(client, verb)(
            f"{JOBS_BASE}/{created['id']}/{action}", headers=_headers(stranger)
        )
        assert response.status_code == 404, response.text
    assert _scalar("SELECT status FROM jobs WHERE id = :id", id=created["id"]) == "draft"

    published = _publish(client, owner, created["id"])
    assert published["status"] == "published"
    assert _list_ids(client, stranger) == [str(created["id"])]
    assert _detail(client, stranger, created["id"])["status"] == "published"

    unpublished = client.put(f"{JOBS_BASE}/{created['id']}/unpublish", headers=_headers(owner))
    assert unpublished.status_code == 200, unpublished.text
    assert unpublished.json()["data"]["job"]["status"] == "draft"
    # 下架后广场立刻没有，陌生人详情也 404（读的是 status，不是缓存）
    assert _list_ids(client, stranger) == []
    assert client.get(f"{JOBS_BASE}/{created['id']}", headers=_headers(stranger)).status_code == 404
    # 创建者自己的列表也回到草稿侧
    assert _list_ids(client, owner) == []
    assert _list_ids(client, owner, status="draft") == [str(created["id"])]

    assert client.put(f"{JOBS_BASE}/{uuid4()}/publish", headers=_headers(owner)).status_code == 404
    assert (
        client.put(f"{JOBS_BASE}/{uuid4()}/unpublish", headers=_headers(owner)).status_code == 404
    )


# --------------------------------------------------------------------------- #
# 矩阵第 5 行 + 第 8 行：软删（回收站），只返回自己的
# --------------------------------------------------------------------------- #


def test_soft_delete_hides_everything_but_keeps_children(client: TestClient) -> None:
    """矩阵 #5：软删后列表/详情都读不到，但 `job_skills` 一行不少（回收站必须能还原）。"""
    token = _register_and_login(client)
    created = _create(client, token, skills=[_skill("Python"), _skill("SQL")])
    _publish(client, token, created["id"])
    assert _list_ids(client, token) == [str(created["id"])]

    stranger = _register_and_login(client)
    assert (
        client.delete(f"{JOBS_BASE}/{created['id']}", headers=_headers(stranger)).status_code == 404
    ), "矩阵 #5：非创建者不能删别人的岗位"
    assert _count("jobs") == 1

    deleted = client.delete(f"{JOBS_BASE}/{created['id']}", headers=_headers(token))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {"job_id": created["id"], "deleted": True}

    # 广场与草稿列表都没了，详情 404，回收站里有
    assert _list_ids(client, token) == []
    assert _list_ids(client, token, status="draft") == []
    assert client.get(f"{JOBS_BASE}/{created['id']}", headers=_headers(token)).status_code == 404
    # 软删的岗位连发布都不能做（已回收的行不参与任何写路径）
    assert (
        client.put(f"{JOBS_BASE}/{created['id']}/publish", headers=_headers(token)).status_code
        == 404
    )

    trash = _trash(client, token)
    assert [job["id"] for job in trash] == [str(created["id"])]
    assert str(trash[0]["deleted_at"]).endswith("+08:00")
    assert trash[0]["status"] == "published"

    # 库里父行还在（只打了 deleted_at），子行一条没动
    assert _count("jobs") == 1
    assert _count("job_skills") == 2
    assert _scalar("SELECT deleted_at IS NOT NULL FROM jobs WHERE id = :id", id=created["id"]) == 1

    # 重复删除已经回收的行：404（`get_owned` 默认排除回收站）
    assert client.delete(f"{JOBS_BASE}/{created['id']}", headers=_headers(token)).status_code == 404


def test_trash_contains_only_the_callers_own_deleted_jobs(client: TestClient) -> None:
    """矩阵 #8：回收站只返回**自己的**软删岗位，过滤在 SQL 里做而不是前端筛。"""
    first = _register_and_login(client)
    second = _register_and_login(client)
    first_job = _create(client, first, title="甲的岗位")
    second_job = _create(client, second, title="乙的岗位")
    _publish(client, first, first_job["id"])
    _publish(client, second, second_job["id"])
    client.delete(f"{JOBS_BASE}/{first_job['id']}", headers=_headers(first))
    client.delete(f"{JOBS_BASE}/{second_job['id']}", headers=_headers(second))

    assert [job["id"] for job in _trash(client, first)] == [str(first_job["id"])]
    assert [job["id"] for job in _trash(client, second)] == [str(second_job["id"])]
    # 未删除时回收站是空的（不是 404，是空集合）
    third = _register_and_login(client)
    assert _trash(client, third) == []


# --------------------------------------------------------------------------- #
# 矩阵第 9 行：软删的逆操作 —— 恢复（0007 的回收站语义必须可逆）
# --------------------------------------------------------------------------- #


def test_soft_deleted_job_can_be_restored_with_its_content(client: TestClient) -> None:
    """软删 → 恢复 的完整往返，恢复后内容/状态原样回来（service + repository 层）。

    `PUT /jobs/{job_id}/restore` 接通后（ADR-011 1.F #9），HTTP 侧的三条行为由下面
    `test_restore_*` 三条用例覆盖；这条留在 service 层，是因为它验证的是另外两件事：
    领域异常 `JobNotFoundError` 本身（越权与「不在回收站」收敛成同一个异常，1.F 第三条），
    以及恢复动作不碰 `job_skills`（子行必须原样还在）。
    """
    token = _register_and_login(client)
    user_id = _user_id(client, token)
    created = _create(client, token, skills=[_skill("Python"), _skill("SQL")])
    _publish(client, token, created["id"])
    client.delete(f"{JOBS_BASE}/{created['id']}", headers=_headers(token))
    assert _trash(client, token) != []

    with session_scope() as session:
        restored = JobService(JobRepository(session)).restore_job(user_id, str(created["id"]))

    assert restored.deleted_at is None
    assert restored.status == "published", "恢复不该顺手把岗位退回草稿"
    assert [skill.name for skill in restored.skills] == ["Python", "SQL"]
    assert _scalar("SELECT deleted_at FROM jobs WHERE id = :id", id=created["id"]) is None
    # 回到广场与详情
    assert str(created["id"]) in _list_ids(client, token)
    assert _detail(client, token, created["id"])["status"] == "published"
    assert _trash(client, token) == []

    # 没在回收站里的岗位不能「恢复」（与「不存在」同一个 404 语义）
    with session_scope() as session, pytest.raises(JobNotFoundError):
        JobService(JobRepository(session)).restore_job(user_id, str(created["id"]))

    # 越权：别人的回收站条目恢复不了（矩阵 #5/#8 的同一口径）
    other_token = _register_and_login(client)
    other_job = _create(client, other_token, title="乙的岗位")
    client.delete(f"{JOBS_BASE}/{other_job['id']}", headers=_headers(other_token))
    with session_scope() as session, pytest.raises(JobNotFoundError):
        JobService(JobRepository(session)).restore_job(user_id, str(other_job["id"]))
    # 乙的那份必须还在回收站里，没被甲动过
    assert [job["id"] for job in _trash(client, other_token)] == [str(other_job["id"])]


def test_restore_endpoint_returns_the_job_and_keeps_the_pre_delete_status(
    client: TestClient,
) -> None:
    """矩阵 #9（创建者）：恢复 → 200，岗位重新出现在默认列表，`status` 不被恢复动作改写。

    `status` 用两个岗位分别验证：删掉的是 `published` 就恢复成 `published`（回广场），
    删掉的是 `draft` 就恢复成 `draft`（回自己的草稿列表）——
    恢复只是把 `deleted_at` 清回 `NULL`，不是「重新发布」，也不是「退回草稿」。
    响应形状对齐 `app/api/v1/profiles.py:221` 的先例：`data.job` 是完整岗位对象。
    """
    owner = _register_and_login(client)
    published = _create(client, owner, title="被误删的岗位", skills=[_skill("Python")])
    draft = _create(client, owner, title="被误删的草稿")
    _publish(client, owner, published["id"])
    for job_id in (published["id"], draft["id"]):
        assert client.delete(f"{JOBS_BASE}/{job_id}", headers=_headers(owner)).status_code == 200

    restored = client.put(f"{JOBS_BASE}/{published['id']}/restore", headers=_headers(owner))
    assert restored.status_code == 200, restored.text
    body = restored.json()["data"]["job"]
    assert body["id"] == published["id"]
    assert body["deleted_at"] is None
    assert body["status"] == "published"
    assert [skill["origin"] for skill in body["skills"]] == ["unverified"]
    # 默认列表（广场）回来了，回收站清空了，库里 `deleted_at` 也真的回 NULL
    assert str(published["id"]) in _list_ids(client, owner)
    assert [job["id"] for job in _trash(client, owner)] == [str(draft["id"])]
    assert _scalar("SELECT deleted_at FROM jobs WHERE id = :id", id=published["id"]) is None

    restored_draft = client.put(f"{JOBS_BASE}/{draft['id']}/restore", headers=_headers(owner))
    assert restored_draft.status_code == 200, restored_draft.text
    assert restored_draft.json()["data"]["job"]["status"] == "draft"
    assert _list_ids(client, owner, status="draft") == [str(draft["id"])]
    assert _list_ids(client, owner) == [str(published["id"])], "恢复草稿不该把草稿推进广场"
    assert _trash(client, owner) == []


def test_restore_by_a_non_creator_is_404_and_leaves_the_trash_entry_alone(
    client: TestClient,
) -> None:
    """矩阵 #9（非创建者）：**404 而不是 403**，且回收站里那条一点没动。

    与 1.F 同口径：403 会暴露「该 id 存在」，而非创建者本来就不该知道有没有这一行。
    """
    owner = _register_and_login(client)
    stranger = _register_and_login(client)
    created = _create(client, owner, title="甲的岗位")
    _publish(client, owner, created["id"])
    assert client.delete(f"{JOBS_BASE}/{created['id']}", headers=_headers(owner)).status_code == 200

    forbidden = client.put(f"{JOBS_BASE}/{created['id']}/restore", headers=_headers(stranger))
    assert forbidden.status_code == 404, forbidden.text
    assert forbidden.json()["error"]["code"] == "NOT_FOUND"
    assert forbidden.json()["meta"]["request_id"]

    # 陌生人「恢复」失败后：仍软删、仍是 published、仍在甲的回收站里
    assert _scalar("SELECT deleted_at IS NOT NULL FROM jobs WHERE id = :id", id=created["id"]) == 1
    assert _scalar("SELECT status FROM jobs WHERE id = :id", id=created["id"]) == "published"
    assert [job["id"] for job in _trash(client, owner)] == [str(created["id"])]
    assert _list_ids(client, stranger) == []

    # 不存在的 id 给出同一个答案：越权与不存在不可分辨
    assert (
        client.put(f"{JOBS_BASE}/{uuid4()}/restore", headers=_headers(stranger)).status_code == 404
    )


def test_restore_of_a_job_that_is_not_in_the_trash_is_404(client: TestClient) -> None:
    """矩阵 #9 的第二道门：岗位存在但 `deleted_at IS NULL` → 同样 404。

    「存在但没被删」和「存在但不是我删的」都是存在性信息，所以都不给通道
    （`app/api/v1/profiles.py:10` 的理由原样适用）。
    """
    token = _register_and_login(client)
    draft = _create(client, token, title="没删过的草稿")
    published = _create(client, token, title="没删过的岗位")
    _publish(client, token, published["id"])

    for job_id in (draft["id"], published["id"], uuid4()):
        response = client.put(f"{JOBS_BASE}/{job_id}/restore", headers=_headers(token))
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "NOT_FOUND"

    # 失败的恢复一个字都没改：状态没被动过，也没被写入任何 deleted_at
    assert _scalar("SELECT deleted_at FROM jobs WHERE id = :id", id=draft["id"]) is None
    assert _scalar("SELECT status FROM jobs WHERE id = :id", id=draft["id"]) == "draft"
    assert _scalar("SELECT status FROM jobs WHERE id = :id", id=published["id"]) == "published"
    assert _list_ids(client, token) == [str(published["id"])]
    assert _list_ids(client, token, status="draft") == [str(draft["id"])]


# --------------------------------------------------------------------------- #
# 2.G：过期三态（昨天隐藏 / 今天出现 / include_expired 出现）+ NULL 长期有效
# --------------------------------------------------------------------------- #


def test_expired_jobs_are_hidden_by_default_on_the_app_clock(client: TestClient) -> None:
    """矩阵外的 2.G：过期是**读侧**判定，不写库、不改状态。"""
    token = _register_and_login(client)
    today = date.today()

    expired = _create(
        client, token, title="昨天截止", deadline=(today - timedelta(days=1)).isoformat()
    )
    due_today = _create(client, token, title="今天截止", deadline=today.isoformat())
    future = _create(
        client, token, title="下周截止", deadline=(today + timedelta(days=7)).isoformat()
    )
    forever = _create(client, token, title="没有截止时间")
    for job_id in (expired["id"], due_today["id"], future["id"], forever["id"]):
        _publish(client, token, job_id)

    default_ids = _list_ids(client, token)
    assert str(expired["id"]) not in default_ids, "deadline < 今天 默认不出现"
    assert str(due_today["id"]) in default_ids, "边界：今天截止还没过期"
    assert str(future["id"]) in default_ids
    assert str(forever["id"]) in default_ids, "deadline IS NULL 视为长期有效"

    with_expired = _list_ids(client, token, include_expired="true")
    assert str(expired["id"]) in with_expired, "include_expired=true 才连过期一起看到"

    # 过期没有改写任何状态：库里仍是 published，也没有 expired 这个值（1.B）
    assert _scalar("SELECT status FROM jobs WHERE id = :id", id=expired["id"]) == "published"
    assert _scalar(
        "SELECT deadline FROM jobs WHERE id = :id", id=expired["id"]
    ) == today - timedelta(days=1)

    # 过期位随响应给出，前端不需要再实现一遍判据
    cards = {job["id"]: job for job in _list(client, token, include_expired="true")[0]}
    assert cards[str(expired["id"])]["is_expired"] is True
    assert cards[str(due_today["id"])]["is_expired"] is False

    # 详情页与列表同一判据、不绑定列表参数、不区分查看者（议题 3.B）
    stranger = _register_and_login(client)
    assert _detail(client, stranger, expired["id"])["is_expired"] is True
    assert _detail(client, stranger, due_today["id"])["is_expired"] is False


# --------------------------------------------------------------------------- #
# 矩阵第 7 行：提取端点已在 C1 接通（B1 那轮的「本轮不存在」守卫随之改写）
# --------------------------------------------------------------------------- #


def test_extract_skills_endpoint_is_wired_as_a_static_path(client: TestClient) -> None:
    """`POST /jobs/extract-skills` 存在，且**没有**被 `/jobs/{job_id}` 当成岗位 id 吃掉。

    B1 那一轮这条用例钉的是反面（「本轮没有任何外部调用，端点不存在 → 404/405」）；
    C1 按 ADR-011 2.A 把它接上，所以**依据 v9 改成新口径**：不带 Key 时必须是
    `AI_KEY_MISSING`（400）——这条只有真的落到提取处理函数里才可能出现，
    若路径被当成 `job_id` 就会是 405/404。路由顺序与失败语义的完整覆盖在
    `tests/test_jobs_extract_skills.py`，这里只留「端点存在」这一格守卫。

    另外保留 B1 的原断言：提取是纯计算，一次失败的提取也不能留下任何一行。
    """
    token = _register_and_login(client)

    response = client.post(
        f"{JOBS_BASE}/extract-skills",
        json={"description": "熟练掌握 Python"},
        headers=_headers(token),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "AI_KEY_MISSING"
    # 没有任何地方把提取结果落成技能：库里一行都没有
    assert _count("jobs") == 0
    assert _count("job_skills") == 0


# --------------------------------------------------------------------------- #
# 422 / 404 / 500 的统一错误模型（AGENTS.md §4.3）
# --------------------------------------------------------------------------- #


def test_job_validation_error_uses_unified_error_model(client: TestClient) -> None:
    token = _register_and_login(client)

    response = client.post(JOBS_BASE, json={"title": "缺企业名和 JD"}, headers=_headers(token))

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"]
    assert body["error"]["details"]
    assert body["meta"]["request_id"]


def test_job_not_found_uses_unified_error_model(client: TestClient) -> None:
    token = _register_and_login(client)

    response = client.get(f"{JOBS_BASE}/{uuid4()}", headers=_headers(token))

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    # 文案的唯一来源是 G3 那张表（批次 F1c）：断言枚举而不是抄一句中文，
    # 否则表里改一次措辞就要来这里追一遍，而「改文案」本来是允许的动作。
    # 断言的是**岗位专属**那一格：`HTTP_DETAIL_ALIASES` 把 `"岗位不存在。"` 映到
    # `JOB_NOT_FOUND` 而不是通用的 `NOT_FOUND`，因为守则 C3 要求一句话说清「是哪一件事」
    # （用户此刻在岗位列表，不在简历列表）。
    assert body["error"]["message"] == ErrorMessage.JOB_NOT_FOUND
    assert body["meta"]["request_id"]


def test_job_internal_error_uses_unified_error_model(client: TestClient) -> None:
    """500 也必须走统一模型：依赖装配阶段抛异常时，响应体仍有 code/message/request_id。"""

    def raise_error() -> None:
        raise RuntimeError("test failure")

    app.dependency_overrides[get_job_service] = raise_error
    try:
        response = TestClient(app, raise_server_exceptions=False).get(
            JOBS_BASE, headers=_headers(_register_and_login(client))
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"]
    assert body["meta"]["request_id"]
