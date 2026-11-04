"""ADR-009 集成测试：DeepSeek BYOK 文案润色链路，全程 Mock，不连线上。

**拦截点为什么是自己构造的 `httpx2.MockTransport`**：`openai` 3.x 的 HTTP 层是
`httpx2`，而 pytest-httpx / respx 只给 `httpx` 打补丁。实测用 pytest-httpx 写「Mock
测试」时请求会真的打到 `api.deepseek.com` 并拿回线上 401 —— 那等于把真实外呼塞进了
CI（AGENTS.md 4.4 明确禁止）。自己注入 transport 之后，拦截发生在传输层，断言的
就是真正发出去的字节：`model` / `stream` / `thinking` / `Authorization` 全部可见。

用例覆盖：线级请求参数、文案落库、Key 缺失、显式模板降级、上游 401/429/5xx/超时、
模型不合契约（经历数量不符）、AI 不得凭空加技能、润色文本必须被转义、Key 不进日志。
"""

from __future__ import annotations

import json
import logging
import uuid

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.integrations import copy_writer
from app.repositories.user_repository import get_engine

GENERATE_URL = "/api/v1/resumes/generate"
PROFILES_URL = "/api/v1/profiles/me"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"
API_KEY = "sk-deepseek-test-0123456789abcdef"

ORIGINAL_DESCRIPTION = "面向校内学生的二手交易 Web 应用"
POLISHED_DESCRIPTION = "主导校内二手交易平台的后端设计，把下单与检索接口收敛为统一分页协议"
POLISHED_RESPONSIBILITY = "负责后端 API 与数据库设计"

PROFILE_PAYLOAD: dict[str, object] = {
    "full_name": "张三",
    "contact": "13800000000",
    "email": "zhangsan@example.com",
    "skills": [
        {"name": "Python", "self_rating": 4},
        {"name": "SQL", "self_rating": 3},
    ],
    "educations": [
        {
            "school": "示例大学",
            "major": "人工智能",
            "degree": "本科",
            "edu_start": "2021-09-01",
            "edu_end": "2025-06-30",
        }
    ],
    "experiences": [
        {
            "title": "校园二手交易平台",
            "start_date": "2024-03-01",
            "end_date": "2024-06-30",
            "description": ORIGINAL_DESCRIPTION,
            "responsibility": POLISHED_RESPONSIBILITY,
        }
    ],
}


class FakeDeepSeek:
    """可编程的 DeepSeek 上游：返回文案 / 返回状态码 / 抛超时，并记录真实请求。"""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.mode = "copy"
        self.status = 500
        self.copy_payload: dict[str, object] = {
            "skills": ["Python", "SQL"],
            "experiences": [
                {"index": 0, "description": POLISHED_DESCRIPTION, "responsibility": "负责后端设计"}
            ],
        }

    @property
    def bodies(self) -> list[dict[str, object]]:
        return [json.loads(request.content.decode("utf-8")) for request in self.requests]

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.mode == "timeout":
            raise httpx2.ConnectTimeout("simulated read timeout")
        if self.mode == "status":
            return httpx2.Response(self.status, json={"error": {"message": "upstream refused"}})
        content = json.dumps(self.copy_payload, ensure_ascii=False)
        return httpx2.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            },
        )

    @property
    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.handler))


@pytest.fixture
def fake_deepseek(monkeypatch: pytest.MonkeyPatch) -> FakeDeepSeek:
    """把 writer 的默认出网客户端换成 Mock transport（writer 每次请求新建，无需覆盖依赖）。"""
    fake = FakeDeepSeek()
    monkeypatch.setattr(copy_writer, "_build_http_client", lambda: fake.client)
    return fake


def _register_and_login(client: TestClient) -> str:
    username = f"airesume_{uuid.uuid4().hex[:10]}"
    registered = client.post(
        REGISTER_URL,
        json={"username": username, "password": PASSWORD, "email": f"{username}@example.com"},
    )
    assert registered.status_code == 201, registered.text
    logged_in = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    token: str = logged_in.json()["data"]["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _byok(token: str) -> dict[str, str]:
    headers = _auth(token)
    headers["X-DeepSeek-Key"] = API_KEY
    return headers


def _prepare_profile(client: TestClient, token: str) -> None:
    saved = client.put(PROFILES_URL, json=PROFILE_PAYLOAD, headers=_auth(token))
    assert saved.status_code == 200, saved.text


def _generate(client: TestClient, token: str, **body: object):
    return client.post(GENERATE_URL, json={"title": "AI 简历", **body}, headers=_byok(token))


def _resume_rows() -> list[str]:
    with get_engine().connect() as connection:
        return [
            str(row[0])
            for row in connection.execute(text("SELECT content_html FROM resumes")).fetchall()
        ]


def test_generate_calls_deepseek_with_pinned_model_and_no_stream(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """任务要求的第 1 条断言：发出去的 body 必须是 deepseek-flash + stream=False。"""
    token = _register_and_login(client)
    _prepare_profile(client, token)

    response = _generate(client, token)

    assert response.status_code == 200, response.text
    assert len(fake_deepseek.requests) == 1, "SDK 重试次数已钉死为 0，一次生成只外呼一次"
    request = fake_deepseek.requests[0]
    body = fake_deepseek.bodies[0]
    assert str(request.url).startswith("https://api.deepseek.com")
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert body["model"] == "deepseek-flash"
    assert body["stream"] is False
    # deepseek-flash 默认开思考模式，必须显式关掉，否则每次生成都白烧一段思维链。
    assert body["thinking"] == {"type": "disabled"}
    # prompt 里带的是库里的原始事实，模型只能在这些事实上改写。
    assert ORIGINAL_DESCRIPTION in json.dumps(body["messages"], ensure_ascii=False)


def test_ai_copy_is_persisted_and_keeps_template_contract(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """润色文案要落进 `resumes.content_html`，同时排版契约（DOM/主题）一点不能变。"""
    token = _register_and_login(client)
    _prepare_profile(client, token)

    response = _generate(client, token)

    assert response.status_code == 200, response.text
    html = response.json()["data"]["content_html"]
    assert POLISHED_DESCRIPTION in html
    assert ORIGINAL_DESCRIPTION not in html, "润色后不应再残留原始描述"
    # 这些是 ResumePreview.vue / ADR-006 / ADR-007 的契约，AI 路径必须照旧满足。
    assert '<h1 class="resume-name">张三</h1>' in html
    assert '<span class="skill-item">Python</span>' in html
    assert "--paper-accent: #527873" in html
    assert "<h2>项目经历</h2>" in html
    assert '<h3 class="experience-title">校园二手交易平台</h3>' in html

    stored = _resume_rows()
    assert len(stored) == 1
    assert stored[0] == html


def test_missing_key_returns_400_and_never_calls_deepseek(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    token = _register_and_login(client)
    _prepare_profile(client, token)

    response = client.post(
        GENERATE_URL, json={"title": "没带 Key"}, headers=_auth(token)
    )

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["code"] == "AI_KEY_MISSING"
    assert "DeepSeek API Key" in body["error"]["message"]
    assert body["meta"]["request_id"]
    assert fake_deepseek.requests == [], "没 Key 就不该产生任何外呼"
    assert _resume_rows() == []


def test_use_fallback_skips_deepseek_and_uses_template(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """降级必须是用户点出来的：`use_fallback=True` 时零外呼，内容回到原始文案。"""
    token = _register_and_login(client)
    _prepare_profile(client, token)

    response = client.post(
        GENERATE_URL,
        json={"title": "模板版", "use_fallback": True},
        headers=_byok(token),
    )

    assert response.status_code == 200, response.text
    html = response.json()["data"]["content_html"]
    assert fake_deepseek.requests == []
    assert ORIGINAL_DESCRIPTION in html
    assert POLISHED_DESCRIPTION not in html


def test_fallback_works_without_any_key(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """没 Key 但显式要模板：照样 200，这样「免费模板」这条路仍然走得通。"""
    token = _register_and_login(client)
    _prepare_profile(client, token)

    response = client.post(
        GENERATE_URL,
        json={"title": "无 Key 模板版", "use_fallback": True},
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    assert fake_deepseek.requests == []
    assert len(_resume_rows()) == 1


def test_upstream_unauthorized_maps_to_401_not_fallback(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """Key 无效绝不降级：用户需要的是去修 Key，而不是一份冒充 AI 结果的模板简历。"""
    token = _register_and_login(client)
    _prepare_profile(client, token)
    fake_deepseek.mode = "status"
    fake_deepseek.status = 401

    response = _generate(client, token)

    assert response.status_code == 401, response.text
    body = response.json()
    assert body["error"]["code"] == "AI_KEY_INVALID"
    assert API_KEY not in response.text, "响应体不得回显 Key"
    assert _resume_rows() == []


@pytest.mark.parametrize("status_code", [400, 429, 500, 503])
def test_other_upstream_status_maps_to_service_unavailable(
    fake_deepseek: FakeDeepSeek, client: TestClient, status_code: int
) -> None:
    token = _register_and_login(client)
    _prepare_profile(client, token)
    fake_deepseek.mode = "status"
    fake_deepseek.status = status_code

    response = _generate(client, token)

    assert response.status_code == 502, response.text
    body = response.json()
    assert body["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert body["error"]["retryable"] is True
    assert _resume_rows() == []


def test_upstream_timeout_maps_to_service_unavailable(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    token = _register_and_login(client)
    _prepare_profile(client, token)
    fake_deepseek.mode = "timeout"

    response = _generate(client, token)

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert _resume_rows() == []


def test_experience_count_mismatch_is_rejected(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """模型少给/多给经历段数不能静默通过：那等于悄悄改了用户的事实。"""
    token = _register_and_login(client)
    _prepare_profile(client, token)
    fake_deepseek.copy_payload = {
        "skills": ["Python"],
        "experiences": [
            {"index": 0, "description": "第一段", "responsibility": "负责"},
            {"index": 1, "description": "凭空多出来的一段", "responsibility": "负责"},
        ],
    }

    response = _generate(client, token)

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert _resume_rows() == []


def test_ai_cannot_invent_or_drop_skills(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """技能以库为准：模型加的一律丢弃，漏掉的按原顺序补回。"""
    token = _register_and_login(client)
    _prepare_profile(client, token)
    fake_deepseek.copy_payload = {
        "skills": ["Kubernetes", "python ", "SQL", "Python"],
        "experiences": [
            {"index": 0, "description": POLISHED_DESCRIPTION, "responsibility": "负责后端设计"}
        ],
    }

    response = _generate(client, token)

    assert response.status_code == 200, response.text
    html = response.json()["data"]["content_html"]
    assert "Kubernetes" not in html
    # `python`（大小写+空格抖动）与 Python 是同一个技能，只出现一次。
    assert html.count('<span class="skill-item">Python</span>') == 1
    assert '<span class="skill-item">SQL</span>' in html


def test_polished_text_is_escaped_in_html(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """这是选「AI 只写文案」而不是「AI 直接写 HTML」的核心理由。

    `ResumePreview.vue` 用 `v-html` 注入生成物，`<img onerror>` 会真的执行。
    文案回归纯文本、排版交给模板，转义由 `escape()` 兜住，注入点就消失了。
    """
    token = _register_and_login(client)
    _prepare_profile(client, token)
    fake_deepseek.copy_payload = {
        "skills": ["Python"],
        "experiences": [
            {
                "index": 0,
                "description": '<img src=x onerror=alert(1)>写了二手交易平台',
                "responsibility": "<script>steal()</script>负责后端",
            }
        ],
    }

    response = _generate(client, token)

    assert response.status_code == 200, response.text
    html = response.json()["data"]["content_html"]
    assert "<img src=x" not in html
    assert "<script>steal()</script>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert _resume_rows()[0] == html, "落库的 HTML 本身就必须是转义过的"


def test_blank_polish_falls_back_to_original_text(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """模型把文字润成空白时，保留库里原文而不是制造空洞。"""
    token = _register_and_login(client)
    _prepare_profile(client, token)
    fake_deepseek.copy_payload = {
        "skills": ["Python"],
        "experiences": [{"index": 0, "description": "   ", "responsibility": " "}],
    }

    response = _generate(client, token)

    assert response.status_code == 200, response.text
    html = response.json()["data"]["content_html"]
    assert ORIGINAL_DESCRIPTION in html
    assert POLISHED_RESPONSIBILITY in html
    assert "<h2></h2>" not in html

    # 真正的空串是契约违规（min_length=1），按上游不可用处理，不能悄悄出半份简历。
    fake_deepseek.copy_payload = {
        "skills": ["Python"],
        "experiences": [{"index": 0, "description": "", "responsibility": "负责后端设计"}],
    }
    broken = _generate(client, token)
    assert broken.status_code == 502, broken.text
    assert broken.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"


def test_api_key_never_reaches_logs(
    fake_deepseek: FakeDeepSeek, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """BYOK 最容易翻车的地方：Key 落进日志。这里按最坏配置（全库 DEBUG）验一次。"""
    token = _register_and_login(client)
    _prepare_profile(client, token)

    with caplog.at_level(logging.DEBUG):
        ok = _generate(client, token)
        assert ok.status_code == 200, ok.text
        fake_deepseek.mode = "status"
        fake_deepseek.status = 401
        bad = _generate(client, token)
        assert bad.status_code == 401

    assert API_KEY not in caplog.text
    assert API_KEY not in ok.text and API_KEY not in bad.text
    # 上游失败也不能把整段 prompt / 用户档案写进日志。
    assert ORIGINAL_DESCRIPTION not in caplog.text
