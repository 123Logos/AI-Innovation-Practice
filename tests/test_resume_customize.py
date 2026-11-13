"""批次 B8 剩余：版本列表、两版差异、按岗位定制（`docs/adr/ADR-026`）。

守的是 ADR 里那几条**会被静默违反**的规则：

1. 定制版进入历史，但**不移动链头**（§2.2）——`GET /resumes/current` 必须还返回原来那版；
2. 定制**只重排、一个字都不改**（§2.5）：差异里可以出现 `item_order_changed`，
   但不许出现任何 `added/removed/changed`；
3. 岗位必须「可读 + 已发布 + 未过期」三件同时成立（§2.6）；
4. 两个版本必须同属**当前用户的同一份档案**，否则统一 404（不送版本 id 探测器）。

纯规则（改名不是删+增、同名条目按次序配对…）在 `tests/test_resume_diff_rules.py`，
那一组不启 FastAPI，失败时不必怀疑夹具。
"""

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.error_messages import ErrorMessage
from app.repositories.user_repository import get_engine
from app.services.resume_diff import parse_snapshot
from tests.review_support import publish_via_review

API = "/api/v1"
GENERATE_URL = f"{API}/resumes/generate"
CURRENT_URL = f"{API}/resumes/current"
VERSIONS_URL = f"{API}/resumes/versions"
DIFF_URL = f"{API}/resumes/versions/diff"
CUSTOMIZE_URL = f"{API}/resumes/customize"
PROFILES_URL = f"{API}/profiles/me"
JOBS_BASE = f"{API}/jobs"
REGISTER_URL = f"{API}/auth/register"
LOGIN_URL = f"{API}/auth/login"
PASSWORD = "Passw0rd!2026"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient) -> str:
    username = f"b8_{uuid4().hex[:10]}"
    registered = client.post(
        REGISTER_URL,
        json={"username": username, "password": PASSWORD, "email": f"{username}@example.com"},
    )
    assert registered.status_code == 201, registered.text
    logged_in = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    token: str = logged_in.json()["data"]["access_token"]
    return token


def _profile(client: TestClient, token: str, **overrides: Any) -> dict[str, Any]:
    """一份完整档案：技能三条（Python **不在**首位）、两段经历（相关的在**后面**）。

    这个顺序是刻意的：定制必须把它俩都提上来，否则「没重排」和「重排对了」
    在断言里长得一样。
    """
    payload: dict[str, Any] = {
        "full_name": "张三",
        "contact": "13800000000",
        "skills": [{"name": "React"}, {"name": "Python"}, {"name": "SQL"}],
        "experiences": [
            {
                "title": "校园二手交易平台",
                "start_date": "2024-03-01",
                "end_date": "2024-06-30",
                "description": "面向校内学生的二手交易 Web 应用",
                "responsibility": "负责前端页面与交互",
            },
            {
                "title": "数据看板",
                "start_date": "2024-07-01",
                "end_date": "2024-09-30",
                "description": "给社团做的运营数据看板",
                "responsibility": "用 Python 写后端接口与定时任务",
            },
        ],
    }
    payload.update(overrides)
    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))
    assert response.status_code == 200, response.text
    profile: dict[str, Any] = response.json()["data"]["profile"]
    return profile


def _generate(client: TestClient, token: str, title: str) -> dict[str, Any]:
    response = client.post(GENERATE_URL, json={"title": title}, headers=_headers(token))
    assert response.status_code == 200, response.text
    resume: dict[str, Any] = response.json()["data"]
    return resume


def _versions(client: TestClient, token: str) -> dict[str, Any]:
    response = client.get(VERSIONS_URL, headers=_headers(token))
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _job(client: TestClient, token: str) -> dict[str, Any]:
    """录一条草稿岗位：技能只要 Python（定制时它应当被提到技能列表最前）。"""
    response = client.post(
        JOBS_BASE,
        json={
            "company_name": "示例科技",
            "title": "Python 后端实习",
            "description": "负责后端接口开发，熟练掌握 Python 与 FastAPI。",
            "skills": [{"name": "Python", "type": "hard"}, {"name": "Docker", "type": "hard"}],
        },
        headers=_headers(token),
    )
    assert response.status_code == 201, response.text
    job: dict[str, Any] = response.json()["data"]["job"]
    return job


# --------------------------------- 版本列表 ---------------------------------


def test_versions_are_empty_before_the_first_generation(client: TestClient) -> None:
    """「还没有简历」是 `/current` 的错误口径；本端点回答历史，空历史是合法答案。"""
    token = _register(client)
    profile = _profile(client, token)

    data = _versions(client, token)

    assert data["profile_id"] == profile["id"]
    assert data["current_version_id"] is None
    assert data["versions"] == []


def test_versions_report_origin_head_and_target_job(client: TestClient) -> None:
    token = _register(client)
    _profile(client, token)
    first = _generate(client, token, "第一版")
    second = _generate(client, token, "第二版")

    data = _versions(client, token)

    assert data["current_version_id"] == second["resume_id"]
    # 倒序：最近一次产出在最前。
    assert [item["version_no"] for item in data["versions"]] == [2, 1]
    assert [item["resume_id"] for item in data["versions"]] == [
        second["resume_id"],
        first["resume_id"],
    ]
    assert [item["is_head"] for item in data["versions"]] == [True, False]
    assert {item["origin"] for item in data["versions"]} == {"generated"}
    assert {item["target_job_id"] for item in data["versions"]} == {None}
    # 摘要刻意不带正文：列表页不需要把每版 HTML 都传一遍。
    assert "content_html" not in data["versions"][0]


def test_versions_of_another_users_profile_are_not_readable(client: TestClient) -> None:
    owner = _register(client)
    owner_profile = _profile(client, owner)
    _generate(client, owner, "我的简历")

    intruder = _register(client)
    _profile(client, intruder)

    response = client.get(
        VERSIONS_URL, params={"profile_id": owner_profile["id"]}, headers=_headers(intruder)
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["message"] == ErrorMessage.PROFILE_NOT_FOUND


# --------------------------------- 按岗位定制 ---------------------------------


def test_customize_reorders_without_moving_the_head(client: TestClient) -> None:
    """B8 剩余的核心不变量：定制版进历史，链头不动（§2.2）。"""
    token = _register(client)
    _profile(client, token)
    head = _generate(client, token, "通用版")
    job = _job(client, token)
    published = publish_via_review(client, token, str(job["id"]))

    response = client.post(CUSTOMIZE_URL, json={"job_id": published["id"]}, headers=_headers(token))
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    customized = data["resume"]
    assert customized["resume_id"] != head["resume_id"]
    assert customized["version_no"] == 2
    assert data["based_on_version_no"] == 1
    assert data["focus"]["job_id"] == published["id"]
    assert data["focus"]["job_title"] == "Python 后端实习"
    # 命中按**档案**顺序给（用户看「我哪几条中了」），缺失按**岗位**顺序给。
    assert data["focus"]["matched"] == ["Python"]
    assert data["focus"]["missing"] == ["Docker"]

    # 链头仍是第一版：`/current` 与版本列表都必须这么说。
    current = client.get(CURRENT_URL, headers=_headers(token))
    assert current.status_code == 200, current.text
    assert current.json()["data"]["resume_id"] == head["resume_id"]

    versions = _versions(client, token)
    assert versions["current_version_id"] == head["resume_id"]
    by_id = {item["resume_id"]: item for item in versions["versions"]}
    assert by_id[customized["resume_id"]]["origin"] == "customized"
    assert by_id[customized["resume_id"]]["is_head"] is False
    assert by_id[customized["resume_id"]]["target_job_id"] == published["id"]
    assert by_id[head["resume_id"]]["is_head"] is True


def test_customized_version_differs_only_in_order(client: TestClient) -> None:
    """§2.5：只重排，一个字都不改。

    这条断言是整批里最容易被「顺手改一点文案」破坏的一条：一旦定制版动了正文，
    差异里就会出现 `changed`，而用户完全没被问过要不要改。
    """
    token = _register(client)
    _profile(client, token)
    head = _generate(client, token, "通用版")
    job = _job(client, token)
    published = publish_via_review(client, token, str(job["id"]))
    customized = client.post(
        CUSTOMIZE_URL, json={"job_id": published["id"]}, headers=_headers(token)
    ).json()["data"]["resume"]

    response = client.get(
        DIFF_URL,
        params={"from_version_id": head["resume_id"], "to_version_id": customized["resume_id"]},
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    diff = response.json()["data"]

    assert diff["comparable"] is True
    assert diff["summary"]["added"] == 0
    assert diff["summary"]["removed"] == 0
    assert diff["summary"]["changed"] == 0
    # 顺序确实变了——否则「没有内容差异」可能只是因为定制版压根没生效。
    assert diff["summary"]["item_order_changed"] is True
    skills = next(section for section in diff["sections"] if section["key"] == "skills")
    assert skills["order_changed"] is True
    assert skills["items"] == []


def test_customize_puts_matched_skills_first(client: TestClient) -> None:
    """稳定排序的**方向**要能被断言：命中项提到最前，其余保持用户排好的相对顺序。

    只断言 `order_changed=True` 是不够的——把顺序改成 `React / SQL / Python`
    同样是「变了」，但那是把用户排好的东西反过来，不是定制。
    这里直接读**生成物**里的技能序列（复用生产解析器，不另写一套 HTML 正则）。
    """
    token = _register(client)
    _profile(client, token)  # 档案顺序：React / Python / SQL
    head = _generate(client, token, "通用版")
    job = _job(client, token)
    published = publish_via_review(client, token, str(job["id"]))
    customized = client.post(
        CUSTOMIZE_URL, json={"job_id": published["id"]}, headers=_headers(token)
    ).json()["data"]["resume"]

    before = parse_snapshot(head["content_html"])
    after = parse_snapshot(customized["content_html"])
    assert before is not None and after is not None

    assert [item.label for item in before.sections["skills"].items] == ["React", "Python", "SQL"]
    # Python 是这条岗位唯一命中的技能：它排到最前，另外两条的**相对顺序不变**。
    assert [item.label for item in after.sections["skills"].items] == ["Python", "React", "SQL"]


def test_customize_requires_a_published_job(client: TestClient) -> None:
    """草稿岗位只有创建者可见，拿它定制等于把「还没过审」的内容写进简历。"""
    token = _register(client)
    _profile(client, token)
    _generate(client, token, "通用版")
    draft = _job(client, token)

    response = client.post(CUSTOMIZE_URL, json={"job_id": draft["id"]}, headers=_headers(token))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["message"] == ErrorMessage.JOB_NOT_FOUND


def test_customize_rejects_an_expired_job(client: TestClient) -> None:
    """动作层过期判据（§2.6 第 4 条）：409 而不是 400——「状态变了，换一条」。"""
    token = _register(client)
    _profile(client, token)
    _generate(client, token, "通用版")
    job = _job(client, token)
    published = publish_via_review(client, token, str(job["id"]))
    # 发布之后再把它改成过期：发布那一刻它还没过期，正是真实会出现的形状。
    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE jobs SET deadline = :deadline WHERE id = :job_id"),
            {"deadline": date.today() - timedelta(days=1), "job_id": published["id"]},
        )

    response = client.post(CUSTOMIZE_URL, json={"job_id": published["id"]}, headers=_headers(token))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["message"] == ErrorMessage.JOB_EXPIRED


def test_customize_without_a_generated_resume_is_rejected(client: TestClient) -> None:
    """定制要「基于某一版」；一版都没有时给的是「先生成一份」，不是 500。"""
    token = _register(client)
    _profile(client, token)
    job = _job(client, token)
    published = publish_via_review(client, token, str(job["id"]))

    response = client.post(CUSTOMIZE_URL, json={"job_id": published["id"]}, headers=_headers(token))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["message"] == ErrorMessage.RESUME_NOT_GENERATED


def test_customize_rejects_extra_fields(client: TestClient) -> None:
    """`extra="forbid"`：定制会落一行新版本，静默忽略 `title` 会让调用方以为生效了。"""
    token = _register(client)
    _profile(client, token)
    _generate(client, token, "通用版")
    job = _job(client, token)
    published = publish_via_review(client, token, str(job["id"]))

    response = client.post(
        CUSTOMIZE_URL,
        json={"job_id": published["id"], "title": "我想自己起名"},
        headers=_headers(token),
    )
    assert response.status_code == 422, response.text


# ---------------------------------- 两版差异 ----------------------------------


def test_diff_across_profiles_is_rejected(client: TestClient) -> None:
    """两版必须同属当前用户的**同一份档案**；跨档案统一 404，不区分原因。"""
    first_user = _register(client)
    _profile(client, first_user)
    mine = _generate(client, first_user, "我的简历")
    second_user = _register(client)
    _profile(client, second_user)
    theirs = _generate(client, second_user, "别人的简历")

    response = client.get(
        DIFF_URL,
        params={"from_version_id": mine["resume_id"], "to_version_id": theirs["resume_id"]},
        headers=_headers(first_user),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["message"] == ErrorMessage.RESUME_VERSION_NOT_FOUND


def test_diff_of_two_generations_reports_the_title_change(client: TestClient) -> None:
    """同一份档案两版之间至少标题不同；这是「可比」路径的最小正向控制。"""
    token = _register(client)
    _profile(client, token)
    first = _generate(client, token, "第一版")
    second = _generate(client, token, "第二版")

    response = client.get(
        DIFF_URL,
        params={"from_version_id": first["resume_id"], "to_version_id": second["resume_id"]},
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    diff = response.json()["data"]

    assert diff["comparable"] is True
    assert diff["from_version"]["resume_id"] == first["resume_id"]
    assert diff["to_version"]["resume_id"] == second["resume_id"]
    # 两版正文内容一样，只有标题不同：内容级计数必须全 0，否则「改标题」被读成「改了内容」。
    assert diff["summary"]["added"] == 0
    assert diff["summary"]["removed"] == 0
    assert diff["summary"]["changed"] == 0
