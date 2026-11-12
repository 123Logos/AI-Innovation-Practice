"""文本模拟面试端点的集成测试（FR-07 / B11a，ADR-022）。

覆盖的是「从路由到仓储的链路」，重点钉住三条既有契约：

1. **鉴权**：未登录 → 401；读别人的会话 → 404 而不是 403（403 会暴露「该 id 存在」）；
2. **可见降级**：AI 失败时（无 Key / 上游异常）会话照常进行，响应带 `ai_degrade_reason`
   且状态码恒为 200，用户绝不会被误导以为「AI 说了这句」；
3. **数据归属**：列表只返回自己的会话；已结束的会话不能再发消息（400）。

AI 一律用假实现（AGENTS.md §4.4）：monkeypatch 的是 `dependencies` 里那个符号，
所以装配路径（「有 Key 才装 coach」）走的仍是真实代码，被测的不是替身本身。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.integrations.evidence_scorer import EvidenceScoreUnavailableError
from app.services import dependencies

INTERVIEWS_URL = "/api/v1/interviews"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"


def _register_and_login(client: TestClient, prefix: str = "iv") -> str:
    username = f"{prefix}_{uuid4().hex[:12]}"
    client.post(
        REGISTER_URL,
        json={"username": username, "password": PASSWORD, "email": f"{username}@example.com"},
    )
    logged_in = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    return logged_in.json()["data"]["access_token"]


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


#: 一个假的 BYOK 密钥；是否带它决定依赖装配是否生成 coach（与 `dependencies` 同构）。
FAKE_DEEPSEEK_KEY = "sk-fake"


def _key_headers(token: str) -> dict[str, str]:
    return _headers(token, **{"X-DeepSeek-Key": FAKE_DEEPSEEK_KEY})


def _create_session(
    client: TestClient, token: str, *, with_key: bool = False, **body: object
) -> dict:
    headers = _key_headers(token) if with_key else _headers(token)
    response = client.post(INTERVIEWS_URL, json=body or {}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


class _FakeCoach:
    """可注入的假面试官：默认回一句固定话，也可被要求抛异常。"""

    def __init__(self, *, raise_error: Exception | None = None) -> None:
        self._raise_error = raise_error

    def respond(self, system_prompt: str, user_prompt: str) -> str:
        if self._raise_error is not None:
            raise self._raise_error
        return "请介绍一下你最近做的一个项目。"


@pytest.fixture
def ai_coach(monkeypatch: pytest.MonkeyPatch) -> _FakeCoach:
    """把装配点上的真实适配器换成同一个假实例（正常返回）。"""
    fake = _FakeCoach()
    monkeypatch.setattr(dependencies, "DeepSeekInterviewCoach", lambda api_key: fake)
    return fake


@pytest.fixture
def failing_coach(monkeypatch: pytest.MonkeyPatch) -> _FakeCoach:
    """把装配点上的真实适配器换成「一调用就抛上游异常」的假实例。"""
    fake = _FakeCoach(raise_error=EvidenceScoreUnavailableError("AI 服务暂时不可用。"))
    monkeypatch.setattr(dependencies, "DeepSeekInterviewCoach", lambda api_key: fake)
    return fake


# ---------------------------------------------------------------- 鉴权与创建


def test_create_interview_requires_auth(client: TestClient) -> None:
    response = client.post(INTERVIEWS_URL, json={})
    assert response.status_code == 401, response.text


def test_create_interview_succeeds_and_gets_opening(
    client: TestClient, ai_coach: _FakeCoach
) -> None:
    token = _register_and_login(client)
    data = _create_session(client, token, with_key=True)
    assert "session" in data and "opening_turn" in data
    assert data["session"]["status"] == "active"
    # 开场问题由 AI 产生（ai_coach 注入），不应带降级标记。
    assert data["ai_degrade_reason"] is None
    assert data["opening_turn"]["speaker"] == "interviewer"
    assert data["opening_turn"]["sequence"] == 1
    assert data["opening_turn"]["ai_degrade_reason"] is None


# ---------------------------------------------------------------- 列表与详情


def test_list_interviews_returns_only_own(client: TestClient, ai_coach: _FakeCoach) -> None:
    token_a = _register_and_login(client, "iva")
    token_b = _register_and_login(client, "ivb")
    _create_session(client, token_a, with_key=True)
    _create_session(client, token_a, with_key=True)

    resp_a = client.get(INTERVIEWS_URL, headers=_headers(token_a))
    resp_b = client.get(INTERVIEWS_URL, headers=_headers(token_b))
    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text
    assert resp_a.json()["data"]["total"] == 2
    assert resp_b.json()["data"]["total"] == 0


def test_read_other_user_session_is_404(client: TestClient, ai_coach: _FakeCoach) -> None:
    token_a = _register_and_login(client, "iva")
    token_b = _register_and_login(client, "ivb")
    session = _create_session(client, token_a, with_key=True)
    session_id = session["session"]["id"]

    resp = client.get(f"{INTERVIEWS_URL}/{session_id}", headers=_headers(token_b))
    assert resp.status_code == 404, resp.text


def test_read_session_detail_includes_turns(client: TestClient, ai_coach: _FakeCoach) -> None:
    token = _register_and_login(client)
    session = _create_session(client, token, with_key=True)
    session_id = session["session"]["id"]
    # 发一条作答，产生 candidate + interviewer 两轮。
    msg = client.post(
        f"{INTERVIEWS_URL}/{session_id}/messages",
        json={"content": "我做过一个校园二手交易平台。"},
        headers=_key_headers(token),
    )
    assert msg.status_code == 200, msg.text

    detail = client.get(f"{INTERVIEWS_URL}/{session_id}", headers=_headers(token))
    assert detail.status_code == 200, detail.text
    turns = detail.json()["data"]["turns"]
    # 开场 + 一轮问答 = 3 轮，按 sequence 升序。
    assert [t["sequence"] for t in turns] == [1, 2, 3]
    assert turns[0]["speaker"] == "interviewer"
    assert turns[1]["speaker"] == "candidate"
    assert turns[2]["speaker"] == "interviewer"


# ---------------------------------------------------------------- 发消息与 AI


def test_send_message_gets_interviewer_reply(client: TestClient, ai_coach: _FakeCoach) -> None:
    token = _register_and_login(client)
    session = _create_session(client, token, with_key=True)
    session_id = session["session"]["id"]

    resp = client.post(
        f"{INTERVIEWS_URL}/{session_id}/messages",
        json={"content": "我做过一个校园二手交易平台。"},
        headers=_key_headers(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["candidate_turn"]["speaker"] == "candidate"
    assert data["interviewer_turn"]["speaker"] == "interviewer"
    # AI 正常返回，这一轮不应带降级标记。
    assert data["ai_degrade_reason"] is None
    assert data["interviewer_turn"]["ai_degrade_reason"] is None


def test_send_message_without_key_shows_degrade(
    client: TestClient,
) -> None:
    """无 Key → coach 装 None → 离线降级：200 + 可见 `ai_degrade_reason`，绝不误导。"""
    token = _register_and_login(client)
    session = _create_session(client, token)  # 这里也没带 Key，开场即降级
    assert session["ai_degrade_reason"] is not None  # 开场已是离线模式
    session_id = session["session"]["id"]

    resp = client.post(
        f"{INTERVIEWS_URL}/{session_id}/messages",
        json={"content": "我做过一个校园二手交易平台。"},
        # 不带 X-DeepSeek-Key，coach 为 None → 离线降级
        headers=_headers(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["ai_degrade_reason"] is not None
    assert data["interviewer_turn"]["ai_degrade_reason"] is not None
    # 降级内容仍是合法的一轮对话，会话继续。
    assert data["interviewer_turn"]["content"]


def test_send_message_when_ai_fails_shows_degrade(
    client: TestClient, failing_coach: _FakeCoach
) -> None:
    """有 Key 但上游异常 → 200 + `ai_degrade_reason='upstream'`，不触发 5xx。"""
    token = _register_and_login(client)
    # 带 Key 触发真实（此处为 failing）coach 装配。
    resp_create = client.post(
        INTERVIEWS_URL,
        json={},
        headers=_headers(token, **{"X-DeepSeek-Key": "sk-fake"}),
    )
    assert resp_create.status_code == 201, resp_create.text
    session_id = resp_create.json()["data"]["session"]["id"]
    assert resp_create.json()["data"]["ai_degrade_reason"] == "upstream"

    resp = client.post(
        f"{INTERVIEWS_URL}/{session_id}/messages",
        json={"content": "我做过一个校园二手交易平台。"},
        headers=_headers(token, **{"X-DeepSeek-Key": "sk-fake"}),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["ai_degrade_reason"] == "upstream"
    assert data["interviewer_turn"]["ai_degrade_reason"] == "upstream"


# ---------------------------------------------------------------- 结束会话


def test_end_session_then_reject_messages(client: TestClient, ai_coach: _FakeCoach) -> None:
    token = _register_and_login(client)
    session = _create_session(client, token, with_key=True)
    session_id = session["session"]["id"]

    ended = client.post(f"{INTERVIEWS_URL}/{session_id}/end", headers=_headers(token))
    assert ended.status_code == 200, ended.text
    assert ended.json()["data"]["session"]["status"] == "ended"

    resp = client.post(
        f"{INTERVIEWS_URL}/{session_id}/messages",
        json={"content": "再问一个。"},
        headers=_key_headers(token),
    )
    assert resp.status_code == 400, resp.text
