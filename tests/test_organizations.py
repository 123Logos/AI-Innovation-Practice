"""批次 B5：组织、角色与审计日志（`docs/adr/ADR-021`）+ B6 收口的注册路径。

这一组用例守的不是「接口返回 200」，而是 ADR 里那几条**会被静默违反**的规则：

1. 注册**只造散人**：`role` / `organization_code` 一律 422，不许「填了就悄悄生效」
   （B6 收口，`ADR-025` §1.1；`extra="forbid"` 是这条的守门人）；
2. 身份只能从成员关系来：加入组织后 `/auth/me` 才报得出角色与组织；
3. 跨组织的成员名单 → 403，不许「能查就能看」（§2.3 的两级判断）；
4. 改角色 → 审计里必须留一条，且不许改自己的角色（§2.5、服务层注释）。

**收口前后的差别**：以前「注册一个教师」是一次请求（带 `role` + 组织码），
现在拆成两件事——注册（HTTP）→ 入组织（成员关系）。测试里后者直接落仓储，
理由与运维脚本 `scripts/seed_organization.py` 相同：本仓不开放自助建校端点。

组织由运维脚本建立，测试里直接落到仓储。
"""

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.repositories.user_repository import get_engine
from tests.accounts import (
    create_organization,
    join,
    login,
    me,
    register,
)

API = "/api/v1"


def test_registration_creates_an_outsider(client: TestClient) -> None:
    """注册的产出是「散人」：没有角色可声明、没有组织、读模型里也没有 role。

    注册响应**不含** `role` 是刻意的：收口前它回显 `"teacher"`，让用户以为身份已定；
    现在身份只由成员关系决定，响应里再出现 role 就是在暗示一个不存在的状态。
    """
    created = register(client, "student01")
    assert created.status_code == 201, created.text
    assert "role" not in created.json()["data"]

    data = me(client, "student01")
    assert data["role"] == "student"
    assert data["organization"] is None
    assert "profile:read" in data["permissions"]


def test_registration_rejects_a_role_field(client: TestClient) -> None:
    """`extra="forbid"`：多塞 `role` 就 422，不静默忽略。

    这条用例是一次行为变更的守门人——收口前 `role="teacher"` 会连带要求组织码，
    收口后它必须连「被接受」都做不到，否则等于留了一个绕过邀请码的提权口子。
    """
    response = register(client, "teacher02", role="teacher")
    assert response.status_code == 422, response.text
    assert "role" in response.text


def test_registration_rejects_an_organization_code_field(client: TestClient) -> None:
    """组织码注册入口已废弃（`ADR-025` §1.1），残留字段必须显式拒绝。"""
    response = register(client, "teacher03", organization_code="AI2026")
    assert response.status_code == 422, response.text
    assert "organization_code" in response.text


def test_registering_with_an_unknown_field_is_rejected(client: TestClient) -> None:
    """`extra="forbid"`（ADR-021 §2.2）：多塞一个字段就 422，不静默忽略。

    这条用例是一次行为变更的守门人——加 `forbid` 之前，下面这个请求会成功注册，
    而 `is_admin=True` 被无声丢掉，用户以为自己是管理员。
    """
    response = register(client, "someone", is_admin=True)
    assert response.status_code == 422, response.text


def test_member_role_and_organization_are_reported_by_me(client: TestClient) -> None:
    """身份从成员关系来：加入组织后 `/auth/me` 才报得出角色与组织。"""
    organization_id = create_organization()
    headers = join(client, "teacher01", organization_id=organization_id, role="teacher")
    assert headers  # join 内部已断言 201/200

    data = me(client, "teacher01")
    assert data["role"] == "teacher"
    assert data["organization"]["id"] == organization_id
    assert data["organization"]["code"] == "AI2026"
    # 教师能看到成员名单，但不能改角色——权限是角色的超集关系，不是「有组织就全给」。
    assert "org:member:read" in data["permissions"]
    assert "org:member:manage" not in data["permissions"]


def test_student_cannot_read_the_member_list(client: TestClient) -> None:
    organization_id = create_organization()
    join(client, "student02", organization_id=organization_id)
    response = client.get(
        f"{API}/organizations/{organization_id}/members", headers=login(client, "student02")
    )
    assert response.status_code == 403, response.text


def test_teacher_can_read_members_of_own_organization(client: TestClient) -> None:
    organization_id = create_organization()
    join(client, "teacher04", organization_id=organization_id, role="teacher")
    join(client, "student03", organization_id=organization_id)

    response = client.get(
        f"{API}/organizations/{organization_id}/members", headers=login(client, "teacher04")
    )
    assert response.status_code == 200, response.text
    usernames = {item["username"] for item in response.json()["data"]["items"]}
    assert usernames == {"teacher04", "student03"}


def test_cross_organization_access_is_denied(client: TestClient) -> None:
    """两级判断的第二个：有 `org:member:read` 不等于「能读任何组织的名单」。"""
    organization_id = create_organization("AI2026", "示例大学")
    other_id = create_organization("BETA01", "另一所大学")
    join(client, "teacher05", organization_id=organization_id, role="teacher")

    response = client.get(
        f"{API}/organizations/{other_id}/members", headers=login(client, "teacher05")
    )
    assert response.status_code == 403, response.text


def test_admin_role_change_is_audited(client: TestClient) -> None:
    organization_id = create_organization()
    join(client, "admin01", organization_id=organization_id, role="school_admin")
    join(client, "teacher06", organization_id=organization_id, role="teacher")
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

    # 注册是**全局**事件，不再属于任何组织（B6 收口：注册只造散人）。
    # 它因此不会出现在某个组织的审计里——但「谁在什么时候注册了」必须查得到，
    # 所以这里直接读表，并且断言 `organization_id` 是 NULL：这一格变非空，
    # 就说明有人把「注册」与「加入组织」又揉回了一起。
    with get_engine().connect() as connection:
        registrations = connection.execute(
            text(
                "SELECT organization_id FROM audit_logs "
                "WHERE action = 'user.register' AND actor_user_id = :user_id"
            ),
            {"user_id": teacher_id},
        ).all()
    assert len(registrations) == 1
    assert registrations[0].organization_id is None


def test_admin_cannot_change_own_role(client: TestClient) -> None:
    """防自锁：校长把自己降级后，就没有任何账号能把他改回来。"""
    organization_id = create_organization()
    join(client, "admin02", organization_id=organization_id, role="school_admin")
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
    organization_id = create_organization()
    join(client, "admin03", organization_id=organization_id, role="school_admin")
    target_id = me(client, "admin03")["user"]["id"]

    response = client.patch(
        f"{API}/organizations/{organization_id}/members/{target_id}",
        json={"role": "platform_admin"},
        headers=login(client, "admin03"),
    )
    # `AssignableRole` 只认三种角色，platform_admin 在 schema 层就被挡掉。
    assert response.status_code == 422, response.text
