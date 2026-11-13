"""批次 B6 收口：登录标识与改密（`docs/adr/ADR-025`）。

守的是 ADR 里那几条**会被静默违反**的规则：

1. 三种标识共用一个入口，且**用户名优先**（§2.3）——存量库里真有人叫 `a@b`，
   按形状先判邮箱会让这个人从上线那天起再也登不进来；
2. 手机号**只存 11 位数字**（§2.2）：存两种写法等于让唯一键只挡住一半的重复；
3. 改密 = 换哈希 + **撤销全部** refresh token + 一条审计（§2.4），
   不是「改成功但别处还登着」；
4. 注册侧不接受「长得像邮箱/手机号」的用户名（§2.3 末），
   否则新数据会持续制造「谁的标识被截胡」的碰撞。

用例里的账号一律走 `tests.accounts`（注册当散人 → 需要角色时再入组织），
不在这里重复造数据的前置条件。
"""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.error_messages import ErrorMessage
from app.core.security import hash_password
from app.repositories.user_repository import UserRepository, get_engine, session_scope
from tests.accounts import me, register

API = "/api/v1"
LOGIN_URL = f"{API}/auth/login"
PASSWORD_URL = f"{API}/auth/password"
REFRESH_URL = f"{API}/auth/refresh"

PASSWORD = "Strong-password-1"
NEW_PASSWORD = "Another-password-2"


def _token_pair(client: TestClient, identifier: str, password: str = PASSWORD) -> dict[str, Any]:
    """登录并返回**整对**令牌（`login()` 只返回请求头，这里要拿 refresh 做撤销断言）。"""
    response = client.post(LOGIN_URL, json={"identifier": identifier, "password": password})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _seed_legacy_user(username: str, password: str) -> str:
    """直接落一行「收口前形状」的账号。

    存量用户没走过新校验器，`username` 可以是 `a@b` 这种形状；
    新注册路径已经不允许了（见文件末尾的用例），所以只能从仓储造。
    """
    with session_scope() as session:
        repository = UserRepository(session)
        user = repository.create_user(
            username=username,
            password_hash=hash_password(password),
            commit=True,
        )
        return user.id


# -------------------------------- 三种标识 --------------------------------


def test_login_with_email(client: TestClient) -> None:
    created = register(client, "mailer01", email="mailer01@example.com")
    assert created.status_code == 201, created.text

    data = _token_pair(client, "mailer01@example.com")
    assert data["access_token"]
    # 大小写不该影响：邮箱在注册时已归一为小写，登录侧也归一。
    assert _token_pair(client, "Mailer01@Example.com")["access_token"]


def test_login_with_phone_and_store_it_normalized(client: TestClient) -> None:
    """手机号只存 11 位数字：注册时的 `+86` 与空格都在落库前剥掉。"""
    created = register(client, "phoneuser01", phone="+86 138-0000-0001")
    assert created.status_code == 201, created.text
    assert created.json()["data"]["user"]["phone"] == "13800000001"

    # 用户下次可能换个写法：带 `+86`、带空格、带横线，都要能登进来。
    assert _token_pair(client, "+8613800000001")["access_token"]
    assert _token_pair(client, "138 0000 0001")["access_token"]

    assert me(client, "13800000001")["user"]["phone"] == "13800000001"


def test_duplicate_phone_is_rejected_even_with_a_different_spelling(client: TestClient) -> None:
    """唯一键只挡得住一种写法——所以归一化必须发生在落库前（§2.2）。"""
    assert register(client, "phoneuser02", phone="13800000002").status_code == 201

    duplicate = register(client, "phoneuser03", phone="+86 138 0000 0002")
    assert duplicate.status_code == 400, duplicate.text
    assert duplicate.json()["error"]["message"] == ErrorMessage.PHONE_EXISTS


def test_username_wins_over_email_for_legacy_accounts(client: TestClient) -> None:
    """§2.3 的优先级：**用户名 > 邮箱 > 手机号**。

    造法就是真实场景：存量账号的用户名形如 `a@b`，后来又有人拿 `a@b` 当邮箱注册。
    按形状先判邮箱的实现会让前者永远登不进来——而他自己并不知道「我的用户名
    在别人眼里是个邮箱」。
    """
    _seed_legacy_user("a@b", "Legacy-pass-1")
    created = register(client, "newbie01", email="a@b")
    assert created.status_code == 201, created.text

    # 同一个字符串、两套密码：只有「用户名那一位」能进。
    _token_pair(client, "a@b", "Legacy-pass-1")
    impostor = client.post(LOGIN_URL, json={"identifier": "a@b", "password": PASSWORD})
    assert impostor.status_code == 401, impostor.text


def test_register_rejects_usernames_that_look_like_identifiers(client: TestClient) -> None:
    """新数据不许再制造 §2.3 那种碰撞；存量无法约束，从这里起不再增加。

    文案必须是**专用**的那一句：归到「长度或空白不对」会让用户照着改十次也过不去。
    """
    for username in ("someone@example.com", "13800000000", "+8613800000000"):
        response = register(client, username)
        assert response.status_code == 422, f"{username}: {response.text}"
        detail = response.json()["error"]["details"][0]
        assert detail["loc"] == ["body", "username"]
        assert detail["message"] == ErrorMessage.USERNAME_NOT_AN_IDENTIFIER


def test_register_rejects_a_phone_that_is_not_a_mainland_mobile(client: TestClient) -> None:
    for index, phone in enumerate(("010-88886666", "+1 415 555 0100", "1380000000", "12800000000")):
        response = register(client, f"badphone{index}", phone=phone)
        assert response.status_code == 422, f"{phone}: {response.text}"


def test_unknown_identifier_is_indistinguishable(client: TestClient) -> None:
    """三列都不命中与「密码错」必须对外长一样（§2.3）。

    这不是洁癖：只要「这个邮箱存在但密码错」与「这个邮箱根本不存在」给出不同句子的
    任何一个字，`/auth/login` 就变成一台免费的账号枚举机——而它不需要登录、
    每分钟还有 5 次额度，正好够把常见邮箱扫一遍。
    """
    assert register(client, "exist01", email="exist01@example.com").status_code == 201
    assert register(client, "exist02", phone="13800000003").status_code == 201

    wrong_password = client.post(
        LOGIN_URL, json={"identifier": "exist01", "password": "Wrong-password-9"}
    )
    assert wrong_password.status_code == 401, wrong_password.text

    for identifier in ("nobody01", "nobody@example.com", "13800000099"):
        response = client.post(LOGIN_URL, json={"identifier": identifier, "password": PASSWORD})
        assert response.status_code == 401, f"{identifier}: {response.text}"
        # 同一句文案 + 同一个 `details.reason`：连排障用的枚举名都不能泄露出「哪一列命中过」。
        assert response.json()["error"]["message"] == wrong_password.json()["error"]["message"]
        assert response.json()["error"].get("details") == wrong_password.json()["error"].get(
            "details"
        )


# --------------------------------- 改密 ----------------------------------


def test_change_password_revokes_every_refresh_token(client: TestClient) -> None:
    """改密是「我在别处也有会话」时按的那个按钮（§2.4）。

    三件事一起断言，因为它们可以各自独立地坏掉：
    旧 refresh 必须失效、旧密码必须登不进、审计必须留一条。
    """
    assert register(client, "changer01").status_code == 201
    first = _token_pair(client, "changer01")
    second = _token_pair(client, "changer01")
    headers = {"Authorization": f"Bearer {first['access_token']}"}

    wrong_current = client.post(
        PASSWORD_URL,
        json={"current_password": "Wrong-password-9", "new_password": NEW_PASSWORD},
        headers=headers,
    )
    assert wrong_current.status_code == 400, wrong_current.text
    assert wrong_current.json()["error"]["message"] == ErrorMessage.PASSWORD_CURRENT_WRONG

    unchanged = client.post(
        PASSWORD_URL,
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        headers=headers,
    )
    assert unchanged.status_code == 400, unchanged.text
    assert unchanged.json()["error"]["message"] == ErrorMessage.PASSWORD_UNCHANGED

    changed = client.post(
        PASSWORD_URL,
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=headers,
    )
    assert changed.status_code == 204, changed.text

    # 两条会话（两次登录各一条）都必须失效，而不是只失效当前这条。
    for pair in (first, second):
        refreshed = client.post(REFRESH_URL, json={"refresh_token": pair["refresh_token"]})
        assert refreshed.status_code == 401, refreshed.text

    stale = client.post(LOGIN_URL, json={"identifier": "changer01", "password": PASSWORD})
    assert stale.status_code == 401, stale.text
    _token_pair(client, "changer01", NEW_PASSWORD)
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT detail FROM audit_logs "
                "WHERE action = 'user.password_change' ORDER BY id DESC"
            )
        ).all()
    assert len(rows) == 1
    # 撤销条数是**可断言的事实**：只看「旧 token 401」的话，验签/轮换/过期
    # 任何一道闸都能让测试在撤销没生效时照样变绿。
    assert '"revoked_sessions":2' in str(rows[0].detail)


def test_change_password_requires_authentication(client: TestClient) -> None:
    response = client.post(
        PASSWORD_URL,
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 401, response.text


def test_change_password_rejects_extra_fields(client: TestClient) -> None:
    """`extra="forbid"`：多塞一个 `user_id` 不该被静默忽略。

    服务层只认 JWT 里的用户，但「发了什么就处理什么」比「悄悄丢掉」更容易排障；
    这条用例保证那个约定没被后来的 `model_config` 改动悄悄关掉。
    """
    assert register(client, "changer02").status_code == 201
    pair = _token_pair(client, "changer02")

    response = client.post(
        PASSWORD_URL,
        json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "user_id": "someone-else",
        },
        headers={"Authorization": f"Bearer {pair['access_token']}"},
    )
    assert response.status_code == 422, response.text
