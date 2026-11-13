"""动作层的组织闸（`ADR-025` §2.5，第 18 轮裁定 ① 的落地形态）。

第 18 轮把存量无组织账号定成**渐进式强制**：数据层归属永久可空、不刷数据，
只在「必须依托组织才能完成的事」上拦截。这一组用例守的正是「只在那些事上」：

1. 发布岗位（提交审核）没有组织 → 400 且**当场拦下**，不落一个半截状态；
2. 个人功能（录岗位草稿、写档案）没有组织 → 照常可用，不许被连带锁死；
3. 判据本身是纯函数：`None` 与空串同等，且不进口任何数据库。

第 2 条不是「顺便测一下」：把整站锁在组织后面也能让第 1 条变绿，
而它会让「先注册试用」这条路上彻底消失——那正是收口要保住的部分体验。
"""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.action_gate import (
    ActionRequiresOrganization,
    OrganizationAction,
    require_organization,
)
from app.core.error_messages import ErrorMessage
from app.repositories.user_repository import get_engine
from tests.accounts import add_member, create_organization, join, login, register

API = "/api/v1"
JOBS_BASE = f"{API}/jobs"
PROFILES_URL = f"{API}/profiles/me"


def _job_payload() -> dict[str, object]:
    """一份最小可用的岗位请求体（三个必填 + 两条技能）。"""
    return {
        "company_name": "示例科技",
        "title": "后端实习（AI 方向）",
        "description": "负责后端接口开发，熟练掌握 Python 与 FastAPI。",
        "skills": [{"name": "Python", "type": "hard"}],
    }


def _create_job(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(JOBS_BASE, json=_job_payload(), headers=headers)
    assert response.status_code == 201, response.text
    job: dict[str, Any] = response.json()["data"]["job"]
    return job


# ------------------------------- 纯函数判据 -------------------------------


def test_require_organization_rejects_none_and_empty_string() -> None:
    """空串与 `None` 同等对待：它只可能来自某个没归一化的拼接。

    放过去的话，后面每一处按 id 查成员关系都会查不到，表现为莫名其妙的 404——
    那比「请先绑定组织」难排查得多。
    """
    for empty in (None, ""):
        try:
            require_organization(empty, action=OrganizationAction.PUBLISH_JOB)
        except ActionRequiresOrganization as error:
            assert error.action is OrganizationAction.PUBLISH_JOB
        else:  # pragma: no cover - 走到这里就是判据坏了
            raise AssertionError(f"{empty!r} 不该通过组织闸")


def test_require_organization_returns_the_id_it_was_given() -> None:
    assert require_organization("org-1", action=OrganizationAction.BULK_DOWNLOAD) == "org-1"


# ----------------------------- 必须依托组织的动作 -----------------------------


def test_submitting_a_job_without_an_organization_is_rejected(client: TestClient) -> None:
    """散人可以录草稿，但**提交审核**是必须依托组织的动作（裁定 ① 点名的那个）。"""
    assert register(client, "lonely02").status_code == 201
    headers = login(client, "lonely02")
    job = _create_job(client, headers)

    response = client.post(f"{JOBS_BASE}/{job['id']}/submit-review", headers=headers)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["message"] == ErrorMessage.JOB_ORGANIZATION_REQUIRED

    # 拦下就要停在原地：状态仍是 draft，没有「半提交」这种中间态。
    reread = client.get(f"{JOBS_BASE}/{job['id']}", headers=headers)
    assert reread.status_code == 200, reread.text
    assert reread.json()["data"]["job"]["status"] == "draft"

    # 被拒绝的尝试也要留证据（ADR-021 §2.5 的延续）：
    # `detail` 里的 no_organization 是「为什么被拒」的唯一机读线索。
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT result, detail FROM audit_logs "
                "WHERE action = 'job.submit_review' AND target_id = :job_id"
            ),
            {"job_id": job["id"]},
        ).all()
    assert len(rows) == 1
    assert rows[0].result == "denied"
    assert "no_organization" in str(rows[0].detail)


def test_joining_an_organization_unblocks_submission(client: TestClient) -> None:
    """反向控制：同一份草稿，加入组织后必须能提交。

    没有这一条，一个「永远拒绝」的闸也能让上面的用例变绿。
    """
    organization_id = create_organization()
    assert register(client, "teacher07").status_code == 201
    headers = login(client, "teacher07")
    job = _create_job(client, headers)

    add_member("teacher07", organization_id, "teacher")
    submitted = client.post(f"{JOBS_BASE}/{job['id']}/submit-review", headers=headers)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["data"]["job"]["status"] == "pending_review"


# ---------------------------- 不许被连带拦住的个人功能 ----------------------------


def test_job_drafting_and_listing_work_without_an_organization(client: TestClient) -> None:
    """个人版岗位能力（录 / 改 / 看自己的草稿）不依赖组织。"""
    assert register(client, "lonely03").status_code == 201
    headers = login(client, "lonely03")

    job = _create_job(client, headers)

    # `status` 默认是 `published`（广场语义），自己的草稿要显式按状态取。
    listing = client.get(JOBS_BASE, params={"status": "draft"}, headers=headers)
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()["data"]["jobs"]] == [job["id"]]

    updated = client.put(
        f"{JOBS_BASE}/{job['id']}",
        json={**_job_payload(), "title": "后端实习（改过标题）"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text


def test_profile_features_work_without_an_organization(client: TestClient) -> None:
    """档案是纯个人数据：没有组织必须照常读写（裁定 ① 的另一半）。"""
    assert register(client, "lonely04").status_code == 201
    headers = login(client, "lonely04")

    saved = client.put(
        PROFILES_URL,
        json={"full_name": "李四", "skills": [{"name": "Python"}]},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text

    read_back = client.get(PROFILES_URL, headers=headers)
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()["data"]["profile"]["full_name"] == "李四"


def test_joined_member_can_still_use_personal_features(client: TestClient) -> None:
    """加入组织也不该反过来把个人功能变成「必须先选组织」。"""
    organization_id = create_organization()
    headers = join(client, "student09", organization_id=organization_id)

    saved = client.put(
        PROFILES_URL,
        json={"full_name": "王五", "skills": [{"name": "SQL"}]},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
