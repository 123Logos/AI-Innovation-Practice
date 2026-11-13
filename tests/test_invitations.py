"""批次 B6：组织邀请制（`docs/adr/ADR-023`）。

守的是 ADR 里那几条会被静默违反的规则：

1. 发码 / 列 / 撤销必须同时过「权限」与「同组织」两道判断（§2.2）；
2. 兑换是**一次事务**：加成员、占次数、写审计要么一起成，要么一起不成（§2.3）；
3. 四种不可用（不存在 / 撤销 / 过期 / 用尽）对外**同一句**文案，不给枚举探针（§2.3）；
4. 一张可转发的码不能造出第二个校长（§2.1）；
5. 一个账号只能属于一个组织（B5 唯一键），重复兑换要显式拒绝；
6. 被拒绝的尝试留 `denied` 审计（ADR-021 §2.5 的延续）。

组织由仓储直接建立（B5 先例：不开自助建校端点）。
"""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.datetime_display import utc_now
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import session_scope

API = "/api/v1"
PASSWORD = "Strong-password-1"


def create_organization(code: str = "AI2026", name: str = "示例大学") -> str:
    with session_scope() as session:
        repository = OrganizationRepository(session)
        organization = repository.create(code=code, name=name, kind="school")
        repository.commit()
        return organization.id


def register(client: TestClient, username: str, **extra: object) -> object:
    body: dict[str, object] = {"username": username, "password": PASSWORD, **extra}
    return client.post(f"{API}/auth/register", json=body)


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(f"{API}/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def admin(
    client: TestClient, code: str = "AI2026", name: str = "示例大学"
) -> tuple[dict[str, str], str]:
    organization_id = create_organization(code=code, name=name)
    created = register(client, "admin01", role="school_admin", organization_code=code)
    assert created.status_code == 201, created.text
    return login(client, "admin01"), organization_id


def create_invitation(
    client: TestClient, headers: dict[str, str], organization_id: str, **payload: object
) -> object:
    return client.post(
        f"{API}/organizations/{organization_id}/invitations",
        headers=headers,
        json=payload or {},
    )


def redeem(client: TestClient, headers: dict[str, str], code: str) -> object:
    return client.post(f"{API}/invitations/redeem", headers=headers, json={"code": code})


def audit_actions(client: TestClient, headers: dict[str, str], organization_id: str) -> list[str]:
    response = client.get(f"{API}/organizations/{organization_id}/audit-logs", headers=headers)
    assert response.status_code == 200, response.text
    return [row["action"] for row in response.json()["data"]["items"]]


# -------------------------------- 发码权限 --------------------------------


def test_admin_creates_and_lists_invitations(client: TestClient) -> None:
    headers, organization_id = admin(client)
    created = create_invitation(client, headers, organization_id, role="teacher", max_uses=3)
    assert created.status_code == 201, created.text
    invitation = created.json()["data"]["invitation"]
    assert invitation["code"]
    assert invitation["role"] == "teacher"
    assert invitation["max_uses"] == 3
    assert invitation["used_count"] == 0
    assert invitation["revoked_at"] is None

    listed = client.get(f"{API}/organizations/{organization_id}/invitations", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["data"]["items"]] == [invitation["id"]]
    assert "invitation.create" in audit_actions(client, headers, organization_id)


def test_student_cannot_create_invitation(client: TestClient) -> None:
    _headers, organization_id = admin(client)
    register(client, "student01")
    response = create_invitation(client, login(client, "student01"), organization_id)
    assert response.status_code == 403, response.text


def test_unauthenticated_cannot_create_invitation(client: TestClient) -> None:
    _, organization_id = admin(client)
    response = client.post(f"{API}/organizations/{organization_id}/invitations", json={})
    assert response.status_code == 401, response.text


def test_cross_organization_create_is_denied_and_audited(client: TestClient) -> None:
    headers, _ = admin(client, code="AAA")
    other_id = create_organization(code="BBB", name="另一所大学")
    response = create_invitation(client, headers, other_id)
    assert response.status_code == 403, response.text
    # 越权尝试要留痕，但绝不能落到「别人的组织」的审计列表里。
    with session_scope() as session:
        denied = session.execute(
            text("SELECT result, organization_id FROM audit_logs WHERE result = 'denied'")
        ).all()
    assert denied
    assert all(row[1] != other_id for row in denied)


def test_unknown_field_and_privileged_role_are_rejected(client: TestClient) -> None:
    headers, organization_id = admin(client)
    assert create_invitation(client, headers, organization_id, is_admin=True).status_code == 422
    assert (
        create_invitation(client, headers, organization_id, role="school_admin").status_code == 422
    )
    extra_field = client.post(
        f"{API}/invitations/redeem",
        headers=headers,
        json={"code": "X" * 8, "unknown": 1},
    )
    assert extra_field.status_code == 422


# -------------------------------- 兑换 --------------------------------


def test_redeem_binds_organization_consumes_use_and_writes_audit(client: TestClient) -> None:
    headers, organization_id = admin(client)
    code = create_invitation(client, headers, organization_id, role="teacher").json()["data"][
        "invitation"
    ]["code"]
    register(client, "student01")

    response = redeem(client, login(client, "student01"), code)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["organization"]["id"] == organization_id
    assert response.json()["data"]["role"] == "teacher"

    actions = audit_actions(client, headers, organization_id)
    assert "invitation.redeem" in actions
    listed = client.get(
        f"{API}/organizations/{organization_id}/invitations", headers=headers
    ).json()["data"]["items"]
    assert listed[0]["used_count"] == 1


def test_four_unusable_reasons_share_one_message(client: TestClient) -> None:
    """不存在 / 撤销 / 过期 / 用尽：同一句，不区分（`ADR-023` §2.3）。"""
    headers, organization_id = admin(client)
    register(client, "student01")
    student = login(client, "student01")

    messages: list[str] = []
    # (a) 不存在
    messages.append(redeem(client, student, "NO-SUCH-CODE").json()["error"]["message"])

    # (b) 撤销
    revoked = create_invitation(client, headers, organization_id).json()["data"]["invitation"]
    assert (
        client.delete(
            f"{API}/organizations/{organization_id}/invitations/{revoked['id']}",
            headers=headers,
        ).status_code
        == 200
    )
    messages.append(redeem(client, student, revoked["code"]).json()["error"]["message"])

    # (c) 过期：直接把库里的 expires_at 拨到昨天（只动测试库这一行）。
    expired = create_invitation(client, headers, organization_id).json()["data"]["invitation"]
    with session_scope() as session:
        session.execute(
            text("UPDATE organization_invitations SET expires_at = :when WHERE id = :id"),
            {"when": utc_now() - timedelta(days=1), "id": expired["id"]},
        )
        session.commit()
    messages.append(redeem(client, student, expired["code"]).json()["error"]["message"])

    # (d) 用尽：1 次码给第一个账号用掉，第二个账号再来。
    limited = create_invitation(client, headers, organization_id).json()["data"]["invitation"]
    register(client, "student02")
    assert redeem(client, login(client, "student02"), limited["code"]).status_code == 200
    register(client, "student03")
    messages.append(
        redeem(client, login(client, "student03"), limited["code"]).json()["error"]["message"]
    )

    assert len(set(messages)) == 1, messages
    assert "invitation.revoke" in audit_actions(client, headers, organization_id)


def test_already_in_organization_cannot_redeem_twice(client: TestClient) -> None:
    headers, organization_id = admin(client)
    first = create_invitation(client, headers, organization_id).json()["data"]["invitation"]
    second = create_invitation(client, headers, organization_id).json()["data"]["invitation"]
    register(client, "student01")
    student = login(client, "student01")
    assert redeem(client, student, first["code"]).status_code == 200
    again = redeem(client, student, second["code"])
    assert again.status_code == 400, again.text
    assert "已加入组织" in again.json()["error"]["message"]


def test_revoke_is_idempotent_and_stays_in_list(client: TestClient) -> None:
    headers, organization_id = admin(client)
    invitation = create_invitation(client, headers, organization_id).json()["data"]["invitation"]
    url = f"{API}/organizations/{organization_id}/invitations/{invitation['id']}"
    assert client.delete(url, headers=headers).status_code == 200
    assert client.delete(url, headers=headers).status_code == 200
    rows = client.get(f"{API}/organizations/{organization_id}/invitations", headers=headers).json()[
        "data"
    ]["items"]
    assert rows[0]["revoked_at"] is not None
    # 幂等不写第二条无意义审计。
    assert audit_actions(client, headers, organization_id).count("invitation.revoke") == 1


def test_invitation_list_shows_who_redeemed_it(client: TestClient) -> None:
    """第 18 轮裁定 ④：发码人要能看到「该码已被谁兑换」，发现滥用可撤销。"""
    headers, organization_id = admin(client)
    code = create_invitation(client, headers, organization_id).json()["data"]["invitation"]["code"]
    register(client, "student07")
    assert redeem(client, login(client, "student07"), code).status_code == 200

    rows = client.get(f"{API}/organizations/{organization_id}/invitations", headers=headers).json()[
        "data"
    ]["items"]
    assert rows[0]["redeemed_by"] == ["student07"]

    # 没被兑换过的码是空列表，不是 null，也不是把别人的名字串过来。
    other = create_invitation(client, headers, organization_id).json()["data"]["invitation"]
    rows = client.get(f"{API}/organizations/{organization_id}/invitations", headers=headers).json()[
        "data"
    ]["items"]
    assert next(row for row in rows if row["id"] == other["id"])["redeemed_by"] == []
