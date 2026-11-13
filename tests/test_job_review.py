"""批次 B6 另一半：岗位审核流（`docs/adr/ADR-024`）。

守的是 ADR 里那几条**会被静默违反**的规则：

1. 提交审核必须先绑定组织（第 18 轮裁定 ①）——没有组织的人拿到的是「请先绑定组织」，
   而不是一条偷偷发出去的岗位；
2. 状态机只有 `draft` / `rejected` → `pending_review` → `published` / `rejected`，
   其余流转一律 409，不许「编辑顺手发布」这种副作用；
3. 审核人 = 同组织的 `teacher` / `school_admin`，或跨组织的 `platform_admin`；
   范围外的岗位与「不存在」同样是 404（`ADR-021` §2.4「跨组织不可见」的延续）；
4. **过期拦截**（第 18 轮裁定 ③ / `ADR-017` §5）：提交与审核两个动作都过
   `app/core/deadline.is_expired`，过期就 409，状态一动不动；
5. 驳回理由要能回到创建者眼前，重新提交时清空（不残留上一轮的旧理由）；
6. 三个动作都留审计，越权尝试记 `denied`；
7. 编辑待审岗位会退回草稿——审核人批的必须是最终内容（§2.5）。

组织仍由仓储直接建立（B5 先例：`ADR-021` §3.3 不开自助建校端点）；
成员关系也直接写，因为本批没有「加入他人组织」的公开端点（B6 之前只有邀请码那条路）。
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.repositories.audit_log_repository import AuditAction
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import get_engine, session_scope

API = "/api/v1"
JOBS = f"{API}/jobs"
PASSWORD = "Strong-password-1"


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #


def _register(client: TestClient, username: str) -> None:
    created = client.post(
        f"{API}/auth/register",
        json={"username": username, "password": PASSWORD, "email": f"{username}@example.com"},
    )
    assert created.status_code == 201, created.text


def _login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(f"{API}/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _user_id(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get(f"{API}/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    user_id: str = response.json()["data"]["user"]["id"]
    return user_id


def _organization(code: str, name: str = "示例大学") -> str:
    with session_scope() as session:
        repository = OrganizationRepository(session)
        organization = repository.create(code=code, name=name, kind="school")
        repository.commit()
        return organization.id


def _member(user_id: str, organization_id: str, role: str) -> None:
    with session_scope() as session:
        repository = OrganizationRepository(session)
        repository.add_member(organization_id=organization_id, user_id=user_id, role=role)
        repository.commit()


def _user(
    client: TestClient,
    username: str,
    *,
    organization_id: str | None = None,
    role: str = "student",
) -> tuple[dict[str, str], str]:
    """注册 → 写成员关系 → 登录，返回 `(请求头, user_id)`。

    顺序不能倒：角色与组织是**登录那一刻**从 `organization_members` 读出来的
    （`ADR-021` §2.6），先登录再加成员会拿到一份没有角色的上下文。
    """
    _register(client, username)
    headers = _login(client, username)
    user_id = _user_id(client, headers)
    if organization_id is not None:
        _member(user_id, organization_id, role)
        headers = _login(client, username)
    return headers, user_id


def _job_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "company_name": "示例科技",
        "title": "后端实习",
        "description": "负责服务端接口开发，熟悉 Python 与 MySQL 优先。",
        "skills": [{"name": "Python"}],
    }
    payload.update(overrides)
    return payload


def _create(client: TestClient, headers: dict[str, str], **overrides: object) -> dict[str, object]:
    response = client.post(JOBS, json=_job_payload(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    job: dict[str, object] = response.json()["data"]["job"]
    return job


def _submit(client: TestClient, headers: dict[str, str], job_id: object) -> object:
    return client.post(f"{JOBS}/{job_id}/submit-review", headers=headers)


def _review(
    client: TestClient,
    headers: dict[str, str],
    job_id: object,
    decision: str,
    note: str | None = None,
) -> object:
    body: dict[str, object] = {"decision": decision}
    if note is not None:
        body["note"] = note
    return client.post(f"{JOBS}/{job_id}/review", headers=headers, json=body)


def _queue(client: TestClient, headers: dict[str, str]) -> list[dict[str, object]]:
    response = client.get(f"{JOBS}/review-queue", headers=headers)
    assert response.status_code == 200, response.text
    jobs: list[dict[str, object]] = response.json()["data"]["jobs"]
    return jobs


def _status(job_id: object) -> str:
    with get_engine().connect() as connection:
        return str(
            connection.execute(
                text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id}
            ).scalar_one()
        )


def _set_deadline(job_id: object, deadline: date) -> None:
    """把截止日期改到过去，模拟**时间流逝**（不是绕过审核造数据）。"""
    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE jobs SET deadline = :deadline WHERE id = :id"),
            {"deadline": deadline, "id": job_id},
        )


def _audit(action: AuditAction) -> list[tuple[str, str | None, str, str | None, str | None]]:
    """按动作取审计行：`(result, actor_user_id, target_type, target_id, detail)`。"""
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT result, actor_user_id, target_type, target_id, detail "
                "FROM audit_logs WHERE action = :action ORDER BY id"
            ),
            {"action": str(action)},
        ).all()
    return [tuple(row) for row in rows]  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# 1. 第 18 轮裁定 ①：发布岗位必须先绑定组织
# --------------------------------------------------------------------------- #


def test_submit_requires_organization_membership(client: TestClient) -> None:
    """没有组织的账号提交审核 → 400 且提示绑定组织；岗位仍是草稿，没有被偷着发出去。"""
    headers, _ = _user(client, "solo07")
    job = _create(client, headers)

    response = _submit(client, headers, job["id"])

    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"]["reason"] == "JOB_ORGANIZATION_REQUIRED"
    assert _status(job["id"]) == "draft"
    # 草稿仍在自己的列表里，内容没被动过
    drafts = client.get(f"{JOBS}", params={"status": "draft"}, headers=headers).json()["data"][
        "jobs"
    ]
    assert [row["id"] for row in drafts] == [job["id"]]


# --------------------------------------------------------------------------- #
# 2. 状态机
# --------------------------------------------------------------------------- #


def test_submit_only_from_draft_or_rejected(client: TestClient) -> None:
    """重复提交、以及提交一条已发布的岗位，都是 409（不是 200 的幂等，也不是 500）。"""
    organization_id = _organization("R24A")
    creator, _ = _user(client, "stu24a", organization_id=organization_id)
    reviewer, _ = _user(client, "tea24a", organization_id=organization_id, role="teacher")
    job = _create(client, creator)

    assert _submit(client, creator, job["id"]).status_code == 200
    again = _submit(client, creator, job["id"])
    assert again.status_code == 409, again.text
    assert again.json()["error"]["details"]["reason"] == "JOB_STATE_CONFLICT"

    assert _review(client, reviewer, job["id"], "approve").status_code == 200
    published = _submit(client, creator, job["id"])
    assert published.status_code == 409, published.text
    assert _status(job["id"]) == "published"


def test_review_only_from_pending(client: TestClient) -> None:
    """不在审核流程里的岗位，审核人碰不到。

    两种「碰不到」要分开断言，因为它们走的是两条不同的门：

    - **从未提交过的草稿**：`organization_id` 还是空的（提交那一刻才盖章），
      所以它根本不落在任何审核范围里 → 404，和「不存在」同一句文案；
    - **已经审完的岗位**：在范围内，但状态不是 `pending_review` → 409，
      因为审核人本来就看得见它，藏着只会让人反复点。
    """
    organization_id = _organization("R24B")
    creator, _ = _user(client, "stu24b", organization_id=organization_id)
    reviewer, _ = _user(client, "tea24b", organization_id=organization_id, role="teacher")
    draft = _create(client, creator)

    never_submitted = _review(client, reviewer, draft["id"], "approve")
    assert never_submitted.status_code == 404, never_submitted.text
    assert _status(draft["id"]) == "draft"

    assert _submit(client, creator, draft["id"]).status_code == 200
    assert _review(client, reviewer, draft["id"], "approve").status_code == 200
    already_reviewed = _review(client, reviewer, draft["id"], "reject", note="再审一次")
    assert already_reviewed.status_code == 409, already_reviewed.text
    assert already_reviewed.json()["error"]["details"]["reason"] == "JOB_STATE_CONFLICT"
    assert _status(draft["id"]) == "published", "被拒绝的重复审核不许改动状态"


# --------------------------------------------------------------------------- #
# 3. 审核权限与范围
# --------------------------------------------------------------------------- #


def test_review_requires_review_permission(client: TestClient) -> None:
    """学生没有 `job:review`：审核动作与队列都是 403（路由层就挡住，服务层到不了）。"""
    organization_id = _organization("R24C")
    creator, _ = _user(client, "stu24c", organization_id=organization_id)
    job = _create(client, creator)
    assert _submit(client, creator, job["id"]).status_code == 200

    assert _review(client, creator, job["id"], "approve").status_code == 403
    assert client.get(f"{JOBS}/review-queue", headers=creator).status_code == 403
    assert _status(job["id"]) == "pending_review"


def test_reviewer_cannot_review_another_organization(client: TestClient) -> None:
    """别校的岗位：404 + 一条 denied 审计；状态一动不动。"""
    organization_a = _organization("R24D1", "A 大学")
    organization_b = _organization("R24D2", "B 大学")
    creator, _ = _user(client, "stu24d", organization_id=organization_a)
    outsider, _ = _user(client, "tea24d", organization_id=organization_b, role="teacher")
    job = _create(client, creator)
    assert _submit(client, creator, job["id"]).status_code == 200

    response = _review(client, outsider, job["id"], "approve")
    assert response.status_code == 404, response.text
    assert _status(job["id"]) == "pending_review"

    denied = [row for row in _audit(AuditAction.JOB_APPROVE) if row[0] == "denied"]
    assert denied, "跨组织审核尝试必须留 denied 审计"
    assert "not_in_review_scope" in (denied[-1][4] or "")


def test_platform_admin_reviews_across_organizations(client: TestClient) -> None:
    """平台管理员是唯一跨组织的角色（`ADR-024` §2.2）：别校的待审岗位也能读、能批。"""
    organization_id = _organization("R24E")
    creator, _ = _user(client, "stu24e", organization_id=organization_id)
    platform, _ = _user(client, "admin24e", organization_id=organization_id, role="platform_admin")
    job = _create(client, creator)
    assert _submit(client, creator, job["id"]).status_code == 200

    detail = client.get(f"{JOBS}/{job['id']}", headers=platform)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["job"]["status"] == "pending_review"

    approved = _review(client, platform, job["id"], "approve")
    assert approved.status_code == 200, approved.text
    assert _status(job["id"]) == "published"


def test_creator_cannot_review_own_job(client: TestClient) -> None:
    """教师录的岗位也不能自己批（`ADR-024` §2.2）：否则审核退化成一次点击。

    教师角色可以由邀请码获得，所以「录入者」与「审核人」很可能是同一个账号——
    这一层不挡，整个审核流只需要点两下。
    """
    organization_id = _organization("R24L")
    teacher, _ = _user(client, "tea24l", organization_id=organization_id, role="teacher")
    job = _create(client, teacher)
    assert _submit(client, teacher, job["id"]).status_code == 200

    response = _review(client, teacher, job["id"], "approve")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "JOB_SELF_REVIEW"
    assert _status(job["id"]) == "pending_review"
    assert [row for row in _audit(AuditAction.JOB_APPROVE) if row[0] == "denied"]


# --------------------------------------------------------------------------- #
# 4. 过期拦截（第 18 轮裁定 ③）
# --------------------------------------------------------------------------- #


def test_submit_of_expired_job_is_blocked(client: TestClient) -> None:
    """已过截止时间的岗位连队列都不该进——否则审核人只能驳回，白干一次。"""
    organization_id = _organization("R24F")
    creator, _ = _user(client, "stu24f", organization_id=organization_id)
    job = _create(client, creator, deadline=(date.today() - timedelta(days=1)).isoformat())

    response = _submit(client, creator, job["id"])

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "JOB_EXPIRED"
    assert _status(job["id"]) == "draft"
    assert [row for row in _audit(AuditAction.JOB_SUBMIT_REVIEW) if row[0] == "denied"]


def test_review_of_expired_job_is_blocked(client: TestClient) -> None:
    """**裁定 ③ 的本体**：提交后岗位过期，通过和驳回都必须被 `is_expired` 拦下。"""
    organization_id = _organization("R24G")
    creator, _ = _user(client, "stu24g", organization_id=organization_id)
    reviewer, _ = _user(client, "tea24g", organization_id=organization_id, role="teacher")
    job = _create(client, creator, deadline=(date.today() + timedelta(days=3)).isoformat())
    assert _submit(client, creator, job["id"]).status_code == 200
    # 时间流逝：队列里的岗位在审核前过期了
    _set_deadline(job["id"], date.today() - timedelta(days=1))

    for decision in ("approve", "reject"):
        response = _review(client, reviewer, job["id"], decision, note="不合适")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["details"]["reason"] == "JOB_EXPIRED"
    assert _status(job["id"]) == "pending_review", "被拦下的审核不许改动状态"

    denied_actions = {row[4] or "" for row in _audit(AuditAction.JOB_APPROVE) if row[0] == "denied"}
    assert any("job_expired" in detail for detail in denied_actions)
    assert [row for row in _audit(AuditAction.JOB_REJECT) if row[0] == "denied"]


# --------------------------------------------------------------------------- #
# 5. 驳回理由与重新提交
# --------------------------------------------------------------------------- #


def test_reject_stores_note_and_creator_sees_it(client: TestClient) -> None:
    """驳回理由回到创建者眼前；重新提交时清空，不残留上一轮的旧话。"""
    organization_id = _organization("R24H")
    creator, _ = _user(client, "stu24h", organization_id=organization_id)
    reviewer, _ = _user(client, "tea24h", organization_id=organization_id, role="teacher")
    job = _create(client, creator)
    assert _submit(client, creator, job["id"]).status_code == 200

    rejected = _review(client, reviewer, job["id"], "reject", note="岗位职责写得太笼统")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["job"]["status"] == "rejected"

    detail = client.get(f"{JOBS}/{job['id']}", headers=creator).json()["data"]["job"]
    assert detail["status"] == "rejected"
    assert detail["review_note"] == "岗位职责写得太笼统"

    # 陌生人看不到这条岗位，也就看不到理由（可见性没变）
    stranger, _ = _user(client, "stranger24h")
    assert client.get(f"{JOBS}/{job['id']}", headers=stranger).status_code == 404

    # 重新提交后理由清空：驳回理由描述的是**上一轮**的判定，不能跟着新提交一起走
    assert _submit(client, creator, job["id"]).status_code == 200
    resubmitted = client.get(f"{JOBS}/{job['id']}", headers=creator).json()["data"]["job"]
    assert resubmitted["status"] == "pending_review"
    assert resubmitted["review_note"] is None


# --------------------------------------------------------------------------- #
# 6. 队列范围与内容
# --------------------------------------------------------------------------- #


def test_review_queue_is_scoped_and_pending_only(client: TestClient) -> None:
    """队列只出本组织的待审岗位：别校的不出现，审完的也不出现。"""
    organization_a = _organization("R24I1", "A 大学")
    organization_b = _organization("R24I2", "B 大学")
    creator_a, _ = _user(client, "stu24i1", organization_id=organization_a)
    creator_b, _ = _user(client, "stu24i2", organization_id=organization_b)
    reviewer_a, _ = _user(client, "tea24i1", organization_id=organization_a, role="teacher")

    first = _create(client, creator_a, title="待审一")
    second = _create(client, creator_a, title="待审二")
    approved = _create(client, creator_a, title="已通过的")
    foreign = _create(client, creator_b, title="B 校的待审")
    for job in (first, second, approved):
        assert _submit(client, creator_a, job["id"]).status_code == 200
    assert _submit(client, creator_b, foreign["id"]).status_code == 200
    assert _review(client, reviewer_a, approved["id"], "approve").status_code == 200

    queue = _queue(client, reviewer_a)

    assert [row["id"] for row in queue] == [first["id"], second["id"]]
    assert all(row["status"] == "pending_review" for row in queue)
    # 队列响应与广场列表同一个卡片形状：不带 JD 原文
    assert "description" not in queue[0]


# --------------------------------------------------------------------------- #
# 7. 编辑待审岗位 → 退回草稿（§2.5）
# --------------------------------------------------------------------------- #


def test_editing_pending_job_returns_it_to_draft(client: TestClient) -> None:
    """审核人批的必须是最终内容：编辑一份待审岗位会把它退回草稿，要重新提交。"""
    organization_id = _organization("R24J")
    creator, _ = _user(client, "stu24j", organization_id=organization_id)
    reviewer, _ = _user(client, "tea24j", organization_id=organization_id, role="teacher")
    job = _create(client, creator)
    assert _submit(client, creator, job["id"]).status_code == 200
    assert [row["id"] for row in _queue(client, reviewer)] == [job["id"]]

    edited = client.put(
        f"{JOBS}/{job['id']}",
        json=_job_payload(title="改了标题的岗位"),
        headers=creator,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["job"]["status"] == "draft"
    assert _queue(client, reviewer) == []
    # 队列空了也就审不了了：审核人拿到的是 409，不是「审了一个已经改过的版本」
    assert _review(client, reviewer, job["id"], "approve").status_code == 409


# --------------------------------------------------------------------------- #
# 8. 审计
# --------------------------------------------------------------------------- #


def test_review_actions_are_audited(client: TestClient) -> None:
    """提交 / 通过 / 驳回各留一条审计，带 actor 与 job id（`ADR-024` §2.7）。"""
    organization_id = _organization("R24K")
    creator, creator_id = _user(client, "stu24k", organization_id=organization_id)
    reviewer, reviewer_id = _user(client, "tea24k", organization_id=organization_id, role="teacher")
    first = _create(client, creator, title="会被通过的")
    second = _create(client, creator, title="会被驳回的")
    for job in (first, second):
        assert _submit(client, creator, job["id"]).status_code == 200
    assert _review(client, reviewer, first["id"], "approve").status_code == 200
    assert _review(client, reviewer, second["id"], "reject", note="信息不全").status_code == 200

    submitted = _audit(AuditAction.JOB_SUBMIT_REVIEW)
    assert len(submitted) == 2
    assert {row[1] for row in submitted} == {creator_id}
    assert {row[2] for row in submitted} == {"job"}
    assert {row[3] for row in submitted} == {first["id"], second["id"]}

    approved = _audit(AuditAction.JOB_APPROVE)
    assert len(approved) == 1
    assert approved[0][1] == reviewer_id
    assert approved[0][3] == first["id"]

    rejected = _audit(AuditAction.JOB_REJECT)
    assert len(rejected) == 1
    assert rejected[0][3] == second["id"]
    assert "信息不全" in (rejected[0][4] or "")
