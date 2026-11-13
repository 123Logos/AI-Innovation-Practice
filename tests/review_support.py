"""审核流的测试辅助：把一条岗位推进广场（`ADR-024` §2.2）。

`PUT /jobs/{id}/publish` 被删除之后，**「已发布」不再是一个原子动作**：
它至少需要两次请求（创建者提交 → 组织内审核人通过），而且创建者必须先有组织归属
（第 18 轮裁定 ①，`ADR-024` §2.3）。`test_jobs.py` 里那套 `_membership` 只服务于
自己的用例；其余文件（匹配、档案、约束、端到端）也需要同一件事，所以抽到这里。

三条口径写死在实现里，避免每个调用方各解一次：

1. **组织只建一次**：先问 `/auth/me` 的 `organization`，没有才建。跨用例重复建组织会让
   同一个人挂在两个组织下，而 `context.organization` 只能给一个——于是「提交时定格的组织」
   与「审核人所属组织」错开，表现为莫名其妙的 404。
2. **审核人按组织码推导用户名**（`teacher_<code>`）：同一组织第二次调用直接登录，
   不会出现「每调一次多一个老师」。
3. **每一步都断言状态码**，失败时把响应体带进断言消息：这里不是被测对象，
   失败就说明前置条件坏了，不该让它以 404/409 的形式糊到后面的业务断言上。
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import session_scope

JOBS_BASE = "/api/v1/jobs"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"
PASSWORD = "Passw0rd!2026"


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, username: str) -> dict[str, str] | None:
    response = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
    if response.status_code != 200:
        return None
    return headers(str(response.json()["data"]["access_token"]))


def _current_organization_code(client: TestClient, auth: dict[str, str]) -> str | None:
    response = client.get(ME_URL, headers=auth)
    assert response.status_code == 200, response.text
    organization = response.json()["data"].get("organization") or {}
    code = organization.get("code")
    return str(code) if code else None


def _create_organization_with_member(client: TestClient, auth: dict[str, str]) -> str:
    """建一个组织并把当前用户放进去（B5 先例：不开放自助建校端点）。"""
    me = client.get(ME_URL, headers=auth)
    assert me.status_code == 200, me.text
    user_id = me.json()["data"]["user"]["id"]
    code = f"R{uuid4().hex[:10].upper()}"
    with session_scope() as session:
        repository = OrganizationRepository(session)
        organization = repository.create(code=code, name="审核流测试学校", kind="school")
        repository.add_member(organization_id=organization.id, user_id=user_id, role="student")
        repository.commit()
    return code


def reviewer_headers(client: TestClient, code: str) -> dict[str, str]:
    """拿到该组织的审核人请求头：先登录，登录不上再按 `role=teacher` + 组织码注册。"""
    username = f"teacher_{code.lower()}"
    existing = _login(client, username)
    if existing is not None:
        return existing
    created = client.post(
        REGISTER_URL,
        json={
            "username": username,
            "password": PASSWORD,
            "role": "teacher",
            "organization_code": code,
        },
    )
    assert created.status_code == 201, created.text
    signed_in = _login(client, username)
    assert signed_in is not None, "刚注册的审核人登录失败"
    return signed_in


def publish_via_review(client: TestClient, token: str, job_id: str) -> dict[str, object]:
    """提交审核 → 同组织审核人通过，返回通过后的岗位读模型。"""
    auth = headers(token)
    code = _current_organization_code(client, auth)
    if code is None:
        code = _create_organization_with_member(client, auth)

    submitted = client.post(f"{JOBS_BASE}/{job_id}/submit-review", headers=auth)
    assert submitted.status_code == 200, submitted.text
    approved = client.post(
        f"{JOBS_BASE}/{job_id}/review",
        headers=reviewer_headers(client, code),
        json={"decision": "approve"},
    )
    assert approved.status_code == 200, approved.text
    job: dict[str, object] = approved.json()["data"]["job"]
    return job
