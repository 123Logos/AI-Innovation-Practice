"""T7.7：登录 / 注册限流（slowapi）与 429 的统一错误模型。

测试期 `tests/conftest.py` 默认关闭限流（否则认证用例自己会撞 429）；
本文件用 `enabled_limiter` 夹具临时打开，验证真实的「第 6 次被拦」行为。

ADR-010 起 `/resumes/polish` 也在闸内（10 次/分钟）：它烧的是**用户自己的** DeepSeek
额度，前端 bug 造成的循环调用会变成真金白银的损失，所以闸必须设在服务端。

连真实 MySQL（`TEST_DATABASE_URL`）：登录要查 `users`，注册要写 `users`，
不能用假的。建表与清理由 `tests/conftest.py` 的夹具统一负责。
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.rate_limit import AUTH_RATE_LIMIT, POLISH_RATE_LIMIT, limiter
from app.main import app

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
POLISH_URL = "/api/v1/resumes/polish"
PASSWORD = "Passw0rd!2026"


@pytest.fixture
def enabled_limiter() -> Iterator[None]:
    """临时打开限流并清空计数；用例结束恢复「测试期关闭」的默认状态。"""
    limiter.reset()
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = False
        limiter.reset()


def test_rate_limit_rule_is_five_per_minute() -> None:
    """规则本身固定为 5 次/分钟，防止有人顺手把它放宽。"""
    assert AUTH_RATE_LIMIT == "5/minute"


def test_polish_rate_limit_rule_is_ten_per_minute() -> None:
    """润色 10 次/分钟：够真人连续调几档，不够脚本刷。"""
    assert POLISH_RATE_LIMIT == "10/minute"


def test_login_returns_429_after_five_attempts(enabled_limiter: None) -> None:
    """登录暴力破解：前 5 次走到凭据校验（401），第 6 次直接被限流拦下。"""
    client = TestClient(app)
    payload = {"username": f"nobody_{uuid4().hex[:8]}", "password": PASSWORD}

    attempts = [client.post(LOGIN_URL, json=payload).status_code for _ in range(5)]
    assert attempts == [401] * 5, attempts

    blocked = client.post(LOGIN_URL, json=payload)

    assert blocked.status_code == 429, blocked.text
    body = blocked.json()
    # 429 必须融入 T6 统一错误模型，前端不需要为它写特例。
    assert body["error"]["code"] == "RATE_LIMITED"
    assert body["error"]["message"] == "请求过于频繁，请稍后再试。"
    assert body["error"]["retryable"] is True
    assert body["error"]["details"]["limit"]
    assert body["meta"]["request_id"]
    assert blocked.headers["Retry-After"] == "60"


def test_register_returns_429_after_five_attempts(enabled_limiter: None) -> None:
    """注册刷号：前 5 次正常 201，第 6 次被限流拦下。"""
    client = TestClient(app)

    created = [
        client.post(
            REGISTER_URL,
            json={"username": f"flood_{uuid4().hex[:8]}", "password": PASSWORD},
        ).status_code
        for _ in range(5)
    ]
    assert created == [201] * 5, created

    blocked = client.post(
        REGISTER_URL,
        json={"username": f"flood_{uuid4().hex[:8]}", "password": PASSWORD},
    )

    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


# --------------------------------------------------------------------------- #
# ADR-010：/resumes/polish 的额度保护
# --------------------------------------------------------------------------- #


def _polish_body() -> dict[str, object]:
    """合法的润色请求体：必须过得了 422 校验，才会真正进入被限流包裹的端点函数。"""
    return {
        "profile": {"full_name": "张三", "contact": "13800000000"},
        "skills": [{"name": "Python"}],
        "experiences": [
            {
                "title": "校园二手交易平台",
                "description": "做了个 Web 应用",
                "responsibility": "负责后端",
            }
        ],
        "mode": "medium",
    }


def test_polish_requires_login_even_with_limiter_enabled(enabled_limiter: None) -> None:
    """第 1 道闸是登录态而不是限流：本接口烧用户额度，未登录一律 401。"""
    client = TestClient(app)

    response = client.post(POLISH_URL, json=_polish_body())

    assert response.status_code == 401, response.text


def test_polish_returns_429_after_ten_attempts(enabled_limiter: None, client: TestClient) -> None:
    """第 11 次必须在服务端被拦下：限流键是 IP，但被保护的是**每个用户自己的** DeepSeek 额度。

    前 10 次都故意**不带** `X-DeepSeek-Key`，于是稳定得到 400：计数发生在进入端点函数时，
    与后面的业务分支无关，这样这条用例只验限流，不牵扯假上游。
    """
    username = f"polishflood_{uuid4().hex[:8]}"
    assert (
        client.post(REGISTER_URL, json={"username": username, "password": PASSWORD}).status_code
        == 201
    )
    logged_in = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    # 注册 + 登录已经吃掉本分钟 2 次认证配额，把限流器重置一次，让下面的计数从干净状态开始。
    limiter.reset()
    headers = {"Authorization": f"Bearer {logged_in.json()['data']['access_token']}"}

    statuses = [
        client.post(POLISH_URL, json=_polish_body(), headers=headers).status_code for _ in range(10)
    ]
    assert statuses == [400] * 10, statuses

    blocked = client.post(POLISH_URL, json=_polish_body(), headers=headers)

    assert blocked.status_code == 429, blocked.text
    body = blocked.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    # slowapi 把规则串渲染成「10 per 1 minute」，不是原样的「10/minute」，所以只断言数字。
    assert "10" in body["error"]["details"]["limit"], body["error"]["details"]
    assert body["error"]["retryable"] is True
    assert blocked.headers["Retry-After"] == "60"
