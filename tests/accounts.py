"""测试账号的**唯一**辅助入口（批次 B6 收口后重写，`ADR-025` §1.1）。

收口之前，测试里造一个教师/校长只要一次请求：`register(..., role="teacher",
organization_code="AI2026")`。那条路已经删除，所以「注册 + 某个角色 + 某个组织」
现在是**两件事**：注册（HTTP）→ 加入组织（`organization_members` 一行）。

为什么加入组织这一半直接落仓储而不是走邀请码：
`tests/test_invitations.py` 测的正是邀请码那条路，其它文件如果也要先「凭码注册」，
就会把「邀请码兑换」变成造数据的前置条件——它一旦坏了，**所有**文件的用例一起红，
而红的地方跑在别的断言上，看起来像另一个 bug。B5 的 `create_organization` 也是
同一个先例（组织由仓储直建，因为不开自助建校端点）。

`join()` 把两件事串起来，**每一步都断言**：这里不是被测对象，失败就说明前置条件坏了，
不该让它以 401/403 的形式糊到后面的业务断言上。
"""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import User, session_scope

API = "/api/v1"
PASSWORD = "Strong-password-1"


def register(client: TestClient, username: str, **extra: object) -> object:
    """注册一个**散人**账号；`extra` 只用于 `email` / `phone` 或「不该被接受」的字段。"""
    body: dict[str, object] = {"username": username, "password": PASSWORD, **extra}
    return client.post(f"{API}/auth/register", json=body)


def login(client: TestClient, identifier: str, password: str = PASSWORD) -> dict[str, str]:
    """登录并返回请求头。`identifier` 可以是用户名 / 邮箱 / 手机号（`ADR-025` §2.1）。"""
    response = client.post(
        f"{API}/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def me(client: TestClient, identifier: str) -> dict[str, object]:
    response = client.get(f"{API}/auth/me", headers=login(client, identifier))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def create_organization(code: str = "AI2026", name: str = "示例大学", kind: str = "school") -> str:
    with session_scope() as session:
        repository = OrganizationRepository(session)
        organization = repository.create(code=code, name=name, kind=kind)
        repository.commit()
        return organization.id


def organization_id(code: str) -> str:
    """按组织码取 id。

    `join()` 需要 id，而多数调用方手里只有一个码（审核流按码推导审核人用户名、
    邀请码用例按码建组织）。让它们各写一遍 `get_by_code`，迟早在某一份里漏掉
    「先断言存在」——那样组织没建成功会以 404/403 糊到业务断言上。
    """
    with session_scope() as session:
        organization = OrganizationRepository(session).get_by_code(code)
        assert organization is not None, f"组织不存在：{code}"
        return organization.id


def add_member(username: str, organization_id: str, role: str) -> str:
    """把已注册的账号放进组织，返回它的 user id。

    角色在**这里**给定（而不是注册时声明），这正是收口之后唯一合法的来源形状：
    真实链路上由邀请码决定（`ADR-023` §2.1），测试里由调用方决定。
    """
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == username.lower()))
        assert user is not None, f"先注册再入组织：{username} 不存在"
        repository = OrganizationRepository(session)
        repository.add_member(organization_id=organization_id, user_id=user.id, role=role)
        repository.commit()
        return user.id


def join(
    client: TestClient,
    username: str,
    *,
    organization_id: str,
    role: str = "student",
) -> dict[str, str]:
    """注册 → 入组织 → 登录，返回可用请求头。"""
    created = register(client, username)
    assert created.status_code == 201, created.text
    add_member(username, organization_id, role)
    return login(client, username)
