"""批次 B5：组织、角色与审计日志（`docs/adr/ADR-021`）。

这一组用例守的不是「接口返回 200」，而是 ADR 里那几条**会被静默违反**的规则：

1. 声称教师却拿不到组织 → 必须显式失败，不许降级成学生（§2.4）；
2. 带未知字段注册 → 422，不许静默丢掉（§2.2）；
3. 跨组织的成员名单 → 403，不许「能查就能看」（§2.3 的两级判断）；
4. 改角色 → 审计里必须留一条，且不许改自己的角色（§2.5、服务层注释）。

组织由运维脚本建立（`scripts/seed_organization.py`），测试里直接落到仓储，
理由与脚本 docstring 相同：本批不开放「自助建校」的 HTTP 入口。
"""

from fastapi.testclient import TestClient

from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import session_scope

API = "/api/v1"
PASSWORD = "Strong-password-1"


def create_organization(code: str = "AI2026", name: str = "示例大学", kind: str = "school") -> str:
    with session_scope() as session:
        repository = OrganizationRepository(session)
        organization = repository.create(code=code, name=name, kind=kind)
        repository.commit()
        return organization.id


def register(
    client: TestClient,
    username: str = "teacher01",
    **extra: object,
) -> object:
    body: dict[str, object] = {"username": username, "password": PASSWORD, **extra}
    return client.post(f"{API}/auth/register", json=body)


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(f"{API}/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def me(client: TestClient, username: str) -> dict[str, object]:
    response = client.get(f"{API}/auth/me", headers=login(client, username))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_teacher_registration_binds_organization_and_me_reports_it(client: TestClient) -> None:
    organization_id = create_organization()
    created = register(client, "teacher01", role="teacher", organization_code="AI2026")
    assert created.status_code == 201, created.text
    # 回显角色是「不静默」的另一半：用户当场看见自己被授予了什么身份。
    assert created.json()["data"]["role"] == "teacher"

    data = me(client, "teacher01")
    assert data["role"] == "teacher"
    assert data["organization"]["id"] == organization_id
    assert data["organization"]["code"] == "AI2026"
    # 教师能看到成员名单，但不能改角色——权限是角色的超集关系，不是「有组织就全给」。
    assert "org:member:read" in data["permissions"]
    assert "org:member:manage" not in data["permissions"]


def test_teacher_without_organization_code_is_rejected(client: TestClient) -> None:
    response = register(client, "teacher02", role="teacher")
    assert response.status_code == 400, response.text
    assert "组织码" in response.json()["error"]["message"]


def test_teacher_with_unknown_organization_code_is_rejected(client: TestClient) -> None:
    response = register(client, "teacher03", role="teacher", organization_code="NOPE")
    assert response.status_code == 400, response.text
    # 不区分「码不存在」与「码已停用」：区分开就等于给了一个枚举组织码的探针。
    assert "组织码" in response.json()["error"]["message"]


def test_student_without_organization_is_allowed_and_reports_null(client: TestClient) -> None:
    response = register(client, "student01")
    assert response.status_code == 201, response.text
    data = me(client, "student01")
    assert data["role"] == "student"
    assert data["organization"] is None
    assert "profile:read" in data["permissions"]


def test_registering_with_an_unknown_field_is_rejected(client: TestClient) -> None:
    """`extra="forbid"`（ADR-021 §2.2）：多塞一个字段就 422，不静默忽略。

    这条用例是一次行为变更的守门人——加 `forbid` 之前，下面这个请求会成功注册，
    而 `is_admin=True` 被无声丢掉，用户以为自己是管理员。
    """
    response = register(client, "someone", is_admin=True)
    assert response.status_code == 422, response.text


def test_student_cannot_read_the_member_list(client: TestClient) -> None:
    organization_id = create_organization()
    register(client, "student02", organization_code="AI2026")
    response = client.get(
        f"{API}/organizations/{organization_id}/members", headers=login(client, "student02")
    )
    assert response.status_code == 403, response.text


def test_teacher_can_read_members_of_own_organization(client: TestClient) -> None:
    create_organization()
    register(client, "teacher04", role="teacher", organization_code="AI2026")
    register(client, "student03", organization_code="AI2026")
    organization_id = me(client, "teacher04")["organization"]["id"]

    response = client.get(
        f"{API}/organizations/{organization_id}/members", headers=login(client, "teacher04")
    )
    assert response.status_code == 200, response.text
    usernames = {item["username"] for item in response.json()["data"]["items"]}
    assert usernames == {"teacher04", "student03"}


def test_cross_organization_access_is_denied(client: TestClient) -> None:
    """两级判断的第二个：有 `org:member:read` 不等于「能读任何组织的名单」。"""
    create_organization("AI2026", "示例大学")
    other_id = create_organization("BETA01", "另一所大学")
    register(client, "teacher05", role="teacher", organization_code="AI2026")

    response = client.get(
        f"{API}/organizations/{other_id}/members", headers=login(client, "teacher05")
    )
    assert response.status_code == 403, response.text


def test_admin_role_change_is_audited(client: TestClient) -> None:
    create_organization()
    register(client, "admin01", role="school_admin", organization_code="AI2026")
    register(client, "teacher06", role="teacher", organization_code="AI2026")
    organization_id = me(client, "admin01")["organization"]["id"]
    teacher_id = me(client, "teacher06")["user"]["id"]

    changed = client.patch(
        f"{API}/organizations/{organization_id}/members/{teacher_id}",
        json={"role": "student"},
        headers=login(client, "admin01"),
    )
    assert changed.status_code == 200, changed.text
    assert me(client, "teacher06")["role"] == "student"

    logs = client.get(
        f"{API}/organizations/{organization_id}/audit-logs", headers=login(client, "admin01")
    )
    assert logs.status_code == 200, logs.text
    actions = [item["action"] for item in logs.json()["data"]["items"]]
    assert "member.role_change" in actions
    # 注册与加入也要能追溯（FR-01 要求「关键操作可定位操作者与对象」）。
    assert "user.register" in actions


def test_admin_cannot_change_own_role(client: TestClient) -> None:
    """防自锁：校长把自己降级后，就没有任何账号能把他改回来。"""
    create_organization()
    register(client, "admin02", role="school_admin", organization_code="AI2026")
    organization_id = me(client, "admin02")["organization"]["id"]
    admin_id = me(client, "admin02")["user"]["id"]

    response = client.patch(
        f"{API}/organizations/{organization_id}/members/{admin_id}",
        json={"role": "student"},
        headers=login(client, "admin02"),
    )
    assert response.status_code == 400, response.text
    assert "自己的角色" in response.json()["error"]["message"]
    # 角色没被改。
    assert me(client, "admin02")["role"] == "school_admin"


def test_platform_admin_cannot_be_granted_through_the_api(client: TestClient) -> None:
    create_organization()
    register(client, "admin03", role="school_admin", organization_code="AI2026")
    organization_id = me(client, "admin03")["organization"]["id"]
    target_id = me(client, "admin03")["user"]["id"]

    response = client.patch(
        f"{API}/organizations/{organization_id}/members/{target_id}",
        json={"role": "platform_admin"},
        headers=login(client, "admin03"),
    )
    # `AssignableRole` 只认三种角色，platform_admin 在 schema 层就被挡掉。
    assert response.status_code == 422, response.text
