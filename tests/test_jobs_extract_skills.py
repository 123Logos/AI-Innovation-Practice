"""C1 集成测试：`POST /api/v1/jobs/extract-skills`（ADR-011 2.A / 2.B / 2.C / 2.D / 2.F）。

覆盖四件事，逐条对着 ADR 的「附：实现阶段验收清单」核：

1. **七条失败语义**（附录表 1–7 行）：本文件里每条都有独立用例，第 7 与第 4 还互为反向，
   防止后来人把「我们的 token 上限切断」和「模型不守格式」合并成一条 JSON 解析失败；
   第 5 情形顺带钉住一件事——走 `app/main.py` 全局处理器的那句 502 文案**不得**出现
   「润色」或「简历」，因为同一个处理器同时服务两个端点（提取侧复用润色的异常族、不新建错误码）；
2. **输出封顶参数**：`max_tokens=1500`、`thinking.disabled`、`stream=False`、`max_retries=0`
   在 `MockTransport` 捕获的**真实请求体**里逐条可验，不是看源码；
3. **服务端核验**（2.D 的 1–6 条）：子串核验、name/evidence 长度、type 白名单、
   15 条截断、`received_count` / `dropped_count` / `deduped_count` 计数（重名合并是
   C1 之后补的一条：不合并就会把「保存岗位」必败的 payload 交给用户）；
4. **零写入证明**：按附录指定的方式在 **Engine**（不是 Session——挂 Session 会抛
   `InvalidRequestError`，那是 SQLAlchemy 2 的事件层级）上挂 `before_cursor_execute`，
   收集本次请求执行过的全部语句，断言其中没有 INSERT / UPDATE / DELETE。
   **不用**「事务回滚后行数不变」那种会空过的写法。

**拦截点是自己构造的 `httpx2.MockTransport`**（照 `tests/test_resume_ai_copywriting.py`）：
`openai` 3.x 走 `httpx2`，而 pytest-httpx / respx 只给 `httpx` 打补丁，实测那种「Mock」
会把请求真的发出去。夹具是 **autouse** 的，所以本模块没有任何一条用例可能真出网
（AGENTS.md §4.4：测试不得依赖真实网络）。

Key 相关：`API_KEY` 只是本地假串，本机没有任何真实凭据；它不许出现在响应体或日志里，
这一点由 `test_api_key_never_reaches_response_or_logs` 钉住。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.api.v1 import jobs
from app.api.v1.jobs import EXTRACT_TRUNCATED_MESSAGE
from app.core.rate_limit import EXTRACT_RATE_LIMIT, limiter
from app.integrations import skill_extractor
from app.integrations.skill_extractor import (
    DEEPSEEK_MODEL,
    EXTRACT_PROMPT_VERSION,
    MAX_EVIDENCE_CHARS,
    MAX_EXTRACTED_SKILLS,
    MAX_OUTPUT_TOKENS,
    MAX_SKILL_NAME_CHARS,
    build_extraction_prompt,
)
from app.repositories.user_repository import get_engine
from app.schemas.job import MAX_DESCRIPTION_LENGTH

EXTRACT_URL = "/api/v1/jobs/extract-skills"
JOBS_BASE = "/api/v1/jobs"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"
PASSWORD = "Passw0rd!2026"
API_KEY = "sk-deepseek-extract-test-0123456789"

#: 一份够用的 JD 原文：四条真锚点 + 一条真幻觉的对照面（库里没有 Kubernetes 字样）。
#: 每个锚点都单独成句，是为了让「合格/不合格」两组用例的判据只取决于锚点本身，
#: 而不是取决于某句里恰好有没有那两个字。
JD = "负责后端接口开发，熟练掌握 Python。熟悉 FastAPI。熟悉 Docker。具备良好的跨部门沟通能力。"

VERIFIED_ITEMS: list[dict[str, Any]] = [
    {"name": "Python", "type": "hard", "evidence": "熟练掌握 Python", "confidence": 0.9},
    {"name": "FastAPI", "type": "hard", "evidence": "熟悉 FastAPI", "confidence": 0.8},
    {"name": "Docker", "type": "hard", "evidence": "熟悉 Docker", "confidence": 0.7},
    {
        "name": "跨部门沟通",
        "type": "soft",
        "evidence": "具备良好的跨部门沟通能力",
        "confidence": 0.6,
    },
]
HALLUCINATED_ITEM = {
    "name": "Kubernetes",
    "type": "hard",
    "evidence": "负责 Kubernetes 集群运维",
    "confidence": 0.95,
}

#: DML 的判据只认这三个动词开头（附录的要求）。用词边界而不是 `in`，
#: 免得一条 `SELECT ... updated_at`因为子串里含 "update" 被误判成写。
WRITE_PATTERN = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


class FakeDeepSeek:
    """可编程的 DeepSeek 上游：返回条目 / 返回状态码 / 抛超时与断连，并记录真实请求。"""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.mode = "items"
        self.status = 500
        self.raw_content: str | None = None
        #: `content` 这一格到底发 `null` 还是发字符串（第 6 情形要的是前者）。
        self.null_content = False
        self.finish_reason: str | None = "stop"
        self.items_payload: dict[str, Any] = {"skills": list(VERIFIED_ITEMS)}

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content.decode("utf-8")) for request in self.requests]

    @property
    def system_prompt(self) -> str:
        """最近一次外呼里的 System Prompt 全文——反幻觉指令有没有发出去就看它。"""
        for message in self.bodies[-1]["messages"]:
            if message.get("role") == "system":
                return str(message["content"])
        raise AssertionError("请求体里没有 system 消息")

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.mode == "timeout":
            raise httpx2.ReadTimeout("simulated read timeout")
        if self.mode == "network":
            raise httpx2.ConnectError("simulated connection reset")
        if self.mode == "status":
            return httpx2.Response(self.status, json={"error": {"message": "upstream refused"}})
        if self.null_content:
            # 第 6 情形的真实形态：`message.content` 是 JSON null，不是空串。
            content: Any = None
        else:
            content = (
                self.raw_content
                if self.raw_content is not None
                else json.dumps(self.items_payload, ensure_ascii=False)
            )
        return httpx2.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": self.finish_reason}]
            },
        )

    @property
    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def fake_deepseek(monkeypatch: pytest.MonkeyPatch) -> FakeDeepSeek:
    """把提取适配器的默认出网客户端换成 Mock transport。

    **autouse 是刻意的**：只要有一条用例漏写 fixture 参数，那条用例就会真的打到
    api.deepseek.com 并拿回线上 401——断言照样「通过」，但 CI 里多了一次真实外呼。
    写成本模块的默认状态，是让这类事故结构上不可能发生。
    """
    fake = FakeDeepSeek()
    monkeypatch.setattr(skill_extractor, "_build_http_client", lambda: fake.client)
    return fake


# --------------------------------------------------------------------------- #
# 夹具式辅助
# --------------------------------------------------------------------------- #


def _register_and_login(client: TestClient) -> str:
    username = f"extract_{uuid.uuid4().hex[:10]}"
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


def _extract(
    client: TestClient,
    token: str,
    *,
    with_key: bool = True,
    **overrides: object,
) -> Any:
    body: dict[str, object] = {"description": JD, "title": "后端实习（AI 方向）"}
    body.update(overrides)
    headers = _byok(token) if with_key else _auth(token)
    return client.post(EXTRACT_URL, json=body, headers=headers)


def _data(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _meta(response: Any) -> dict[str, Any]:
    return _data(response)["meta"]


def _error_body(response: Any) -> dict[str, Any]:
    body = response.json()
    assert body["error"]["code"], response.text
    assert body["meta"]["request_id"], response.text
    return body


def _rows(table: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def _valid_items(count: int) -> list[dict[str, Any]]:
    """造 `count` 条**全部能回溯**的条目：锚点统一用 JD 里真实存在的短语。

    条数封顶的用例要「18 条全合格」才测得出截断，用假锚点会先被核验杀掉。
    """
    anchors = ["熟练掌握 Python", "熟悉 FastAPI", "熟悉 Docker", "具备良好的跨部门沟通能力"]
    return [
        {
            "name": f"技能{index}",
            "type": "hard",
            "evidence": anchors[index % len(anchors)],
            "confidence": 0.5,
        }
        for index in range(count)
    ]


# --------------------------------------------------------------------------- #
# 一、路由本身：注册位置、鉴权、限流装饰器
# --------------------------------------------------------------------------- #


def test_extract_route_is_declared_before_the_dynamic_job_id_route() -> None:
    """静态段 `/jobs/extract-skills` 必须排在 `/jobs/{job_id}` 之前（`profiles.py:3-5` 的坑）。

    判据是**路由表里的实际顺序**而不是「今天正好撞不到」：
    `/{job_id}` 现在只有 GET/PUT/DELETE，POST 落不进去，但哪天有人补一条
    `POST /jobs/{job_id}/…` 之外的动态 POST，顺序错了就会变成「提取端点打不开」。
    """
    paths = [getattr(route, "path", "") for route in jobs.router.routes]
    static_index = paths.index("/jobs/extract-skills")
    dynamic_index = paths.index("/jobs/{job_id}")
    assert static_index < dynamic_index, paths
    # 同组的回收站也必须排在动态段之前（B1 已钉，这里连带守住，防止有人重排路由）。
    assert paths.index("/jobs/trash") < dynamic_index


def test_extract_skills_is_not_swallowed_as_a_job_id(client: TestClient) -> None:
    """行为侧的同一件事：这个 POST 走到的是提取处理器，不是 `/{job_id}`。

    不带 Key 时稳定得到 `AI_KEY_MISSING`（400）——只有真的进了提取函数才可能出这个码；
    若路径被当成岗位 id，FastAPI 会给 405（`/{job_id}` 上没有 POST）或 404。
    """
    token = _register_and_login(client)

    response = _extract(client, token, with_key=False)

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "AI_KEY_MISSING"


def test_extract_requires_authentication(client: TestClient, fake_deepseek: FakeDeepSeek) -> None:
    """本接口烧的是用户自己的额度，必须登录；匿名请求一次都不该外呼。"""
    response = client.post(EXTRACT_URL, json={"description": JD})

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "CLIENT_ERROR"
    assert fake_deepseek.requests == []


def test_extract_rate_limit_rule_is_ten_per_minute() -> None:
    """规则本身固定 10 次/分钟（2.F），防止有人顺手把它放宽成「不限」。"""
    assert EXTRACT_RATE_LIMIT == "10/minute"


def test_extract_returns_429_after_ten_attempts(client: TestClient) -> None:
    """限流装饰器真的挂在端点上：第 11 次在**进入端点函数之前**被拦下。

    前 10 次故意不带 Key，于是稳定得到 400——计数发生在装饰器层，与后面的业务分支无关，
    这样这条用例只验限流，不牵扯假上游（`/resumes/polish` 的同名用例同一写法）。
    """
    token = _register_and_login(client)
    limiter.reset()
    limiter.enabled = True
    try:
        statuses = [_extract(client, token, with_key=False).status_code for _ in range(10)]
        assert statuses == [400] * 10, statuses

        blocked = _extract(client, token, with_key=False)

        assert blocked.status_code == 429, blocked.text
        body = blocked.json()
        assert body["error"]["code"] == "RATE_LIMITED"
        assert body["error"]["retryable"] is True
        # slowapi 把规则串渲染成「10 per 1 minute」，不是原样的「10/minute」，所以只断数字。
        assert "10" in body["error"]["details"]["limit"], body["error"]["details"]
    finally:
        limiter.enabled = False
        limiter.reset()


# --------------------------------------------------------------------------- #
# 二、线级请求参数：三封顶 + 继承自润色的那几个（2.B）
# --------------------------------------------------------------------------- #


def test_extract_sends_the_pinned_wire_parameters_including_max_tokens(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """`max_tokens=1500` / `stream=False` / `thinking.disabled` / `max_retries=0` 逐条可见。

    `max_retries=0` 不在请求体里，它由「一次提取**只**外呼一次」证明——SDK 默认重试 2 次，
    那样这里就会是 3 条请求。`max_tokens` 是本轮唯一新增的输出参数：
    润色那条链路没有它（`grep -rn max_tokens app/` 只应命中 `skill_extractor.py`）。
    """
    token = _register_and_login(client)

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    assert len(fake_deepseek.requests) == 1, "重试次数已钉死为 0，一次提取只外呼一次"
    request = fake_deepseek.requests[0]
    body = fake_deepseek.bodies[0]
    assert str(request.url).startswith("https://api.deepseek.com")
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert body["model"] == DEEPSEEK_MODEL == "deepseek-flash"
    assert body["stream"] is False
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == MAX_OUTPUT_TOKENS == 1500
    # 2.B 明确不启用结构化输出参数：`deepseek-flash` 与 `thinking.disabled` 的组合
    # 在无凭据环境下无法自证，而它会把不合法输出替换成空白字符串，观测不到原始输出。
    assert "response_format" not in body


def test_extract_prompt_carries_the_hard_rules_of_2d(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """Prompt 的四条硬约束（2.D 第 1 条）真的发出去了：单对象、≤15 条、最短连续片段、不许编。"""
    token = _register_and_login(client)

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    prompt = fake_deepseek.system_prompt
    assert "只输出一个 JSON 对象" in prompt
    assert f"最多 {MAX_EXTRACTED_SKILLS} 条" in prompt
    assert "最短的连续片段" in prompt
    assert f"{MAX_EVIDENCE_CHARS} 个字" in prompt
    assert f"{MAX_SKILL_NAME_CHARS} 个字" in prompt
    assert "不得输出 JD 里读不到的技术栈" in prompt
    assert "{MAX_" not in prompt, "未替换的模板占位符不能出现在成品 Prompt 里"


def test_build_extraction_prompt_is_stable_and_versioned() -> None:
    """版本号是溯源戳的来源（2.B）：常量必须在模块里显式存在，且 Prompt 可独立复算。"""
    assert EXTRACT_PROMPT_VERSION == "v1"
    assert build_extraction_prompt() == build_extraction_prompt()


def test_user_message_carries_the_jd_from_the_request_body(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """发给模型的是请求体里那份 JD，不是库里任何一份数据（2.A：岗位此刻还没落库）。"""
    token = _register_and_login(client)

    response = _extract(client, token, description="只写一行：会 Python 就行", title="极简岗")

    assert response.status_code == 502, "只有一句的 JD 里，模型给的锚点对不上 → 第 5 情形"
    messages = json.dumps(fake_deepseek.bodies[0]["messages"], ensure_ascii=False)
    assert "只写一行：会 Python 就行" in messages
    assert "极简岗" in messages


def test_title_is_omitted_from_the_payload_when_absent(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """不传 `title` 时不该往 Prompt 里塞一个空格子：多一个歧义来源，没有任何信息量。"""
    token = _register_and_login(client)

    response = _extract(client, token, title=None)

    assert response.status_code == 200, response.text
    user_message = fake_deepseek.bodies[0]["messages"][1]["content"]
    assert "title" not in json.loads(user_message)


# --------------------------------------------------------------------------- #
# 三、成功路径与 `meta` 七字段契约（2.A / 2.D 的「计数必须可见」）
# --------------------------------------------------------------------------- #


def test_extract_returns_skills_and_all_seven_meta_fields(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    token = _register_and_login(client)

    response = _extract(client, token)

    data = _data(response)
    assert data["skills"] == [
        {"name": "Python", "type": "hard", "evidence": "熟练掌握 Python", "confidence": 0.9},
        {"name": "FastAPI", "type": "hard", "evidence": "熟悉 FastAPI", "confidence": 0.8},
        {"name": "Docker", "type": "hard", "evidence": "熟悉 Docker", "confidence": 0.7},
        {
            "name": "跨部门沟通",
            "type": "soft",
            "evidence": "具备良好的跨部门沟通能力",
            "confidence": 0.6,
        },
    ]
    assert sorted(data["meta"]) == [
        "deduped_count",
        "dropped_count",
        "finish_reason",
        "model",
        "prompt_version",
        "received_count",
        "truncated",
    ], "meta 少一个字段，API.md 的契约就只剩端点名"
    # 字段顺序即响应 JSON 顺序：`deduped_count` 紧跟 `dropped_count`（两个计数并排读），
    # 挪到末尾会让「合并了几条」离「剔除几条」整整一个契约的距离。
    assert list(data["meta"]) == [
        "model",
        "prompt_version",
        "received_count",
        "dropped_count",
        "deduped_count",
        "truncated",
        "finish_reason",
    ]
    assert data["meta"] == {
        "model": "deepseek-flash",
        "prompt_version": "v1",
        "received_count": 4,
        "dropped_count": 0,
        "deduped_count": 0,
        "truncated": False,
        "finish_reason": "stop",
    }


def test_extract_response_has_a_request_id_and_no_key_or_extra_field(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    token = _register_and_login(client)
    headers = _byok(token)
    headers["X-Request-ID"] = "req-extract-42"

    response = client.post(EXTRACT_URL, json={"description": JD}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["meta"]["request_id"] == "req-extract-42"
    assert API_KEY not in response.text, "响应体不得回显用户自带的 Key"


def test_markdown_fenced_json_is_parsed(client: TestClient, fake_deepseek: FakeDeepSeek) -> None:
    """模型爱把 JSON 包进 ``` 围栏：这是可容忍的抖动，复用润色那套剥围栏策略。"""
    token = _register_and_login(client)
    fake_deepseek.raw_content = (
        "```json\n" + json.dumps({"skills": [VERIFIED_ITEMS[0]]}, ensure_ascii=False) + "\n```"
    )

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    assert _data(response)["skills"][0]["name"] == "Python"


# --------------------------------------------------------------------------- #
# 四、服务端核验（2.D 逐条，不指望 Prompt）
# --------------------------------------------------------------------------- #


def test_hallucinated_evidence_is_dropped_and_counted(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """真幻觉对照：JD 里没有 Kubernetes → 丢弃该条且 `dropped_count` +1（附录必测项）。"""
    token = _register_and_login(client)
    fake_deepseek.items_payload = {
        "skills": [VERIFIED_ITEMS[0], HALLUCINATED_ITEM, VERIFIED_ITEMS[1]]
    }

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    data = _data(response)
    assert [skill["name"] for skill in data["skills"]] == ["Python", "FastAPI"]
    assert data["meta"]["received_count"] == 3
    assert data["meta"]["dropped_count"] == 1


def test_punctuation_only_difference_is_let_through(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """§2.D.1 的口径落到端点上：顿号 vs 半角逗号必须通过（误杀率是本端点的直接决定项）。"""
    token = _register_and_login(client)
    fake_deepseek.items_payload = {
        "skills": [
            {
                "name": "技术栈",
                "type": "hard",
                "evidence": "熟悉 Python, FastAPI, Docker",
                "confidence": 0.5,
            }
        ]
    }

    response = client.post(
        EXTRACT_URL,
        json={"description": "熟悉 Python、FastAPI、Docker 者优先"},
        headers=_byok(token),
    )

    assert response.status_code == 200, response.text
    assert _meta(response)["dropped_count"] == 0


def test_oversized_or_blank_fields_are_dropped(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """2.D 第 6 条：`name` 空或 > 40 字、`evidence` 空或 > 60 字 → 丢弃该条。"""
    token = _register_and_login(client)
    fake_deepseek.items_payload = {
        "skills": [
            VERIFIED_ITEMS[0],
            {"name": "长名字" * 20, "type": "hard", "evidence": "熟练掌握 Python"},
            {"name": "没锚点", "type": "hard", "evidence": ""},
            {"name": "", "type": "hard", "evidence": "熟悉 Docker"},
            {"name": "超长锚点", "type": "hard", "evidence": "熟练掌握 Python" + "。" * 60},
        ]
    }

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    data = _data(response)
    assert [skill["name"] for skill in data["skills"]] == ["Python"]
    assert data["meta"]["received_count"] == 5
    assert data["meta"]["dropped_count"] == 4


def test_out_of_enum_type_is_kept_with_null_type(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """2.D 第 5 条：`type` 不在枚举内 → **保留该条**、`type` 置 NULL（所以 1.D 的 type 可空）。"""
    token = _register_and_login(client)
    fake_deepseek.items_payload = {
        "skills": [
            {"name": "Python", "type": "senior", "evidence": "熟练掌握 Python", "confidence": 0.9},
            {"name": "FastAPI", "evidence": "熟悉 FastAPI"},
        ]
    }

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    skills = _data(response)["skills"]
    assert [skill["type"] for skill in skills] == [None, None]
    assert [skill["name"] for skill in skills] == ["Python", "FastAPI"]
    assert _meta(response)["dropped_count"] == 0, "type 判不准不是丢弃理由"


def test_more_than_fifteen_survivors_are_truncated(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """2.D 第 4 条：条数 > 15 → 截断到 15、`meta.truncated = true`（截断可见，不是偷换）。"""
    token = _register_and_login(client)
    fake_deepseek.items_payload = {"skills": _valid_items(MAX_EXTRACTED_SKILLS + 3)}

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    data = _data(response)
    assert len(data["skills"]) == MAX_EXTRACTED_SKILLS == 15
    assert data["skills"][-1]["name"] == "技能14", "截断必须保序取前 15 条"
    assert data["meta"]["truncated"] is True
    assert data["meta"]["received_count"] == 18
    assert data["meta"]["dropped_count"] == 0


def test_truncated_is_false_when_drops_already_leave_fifteen_or_fewer(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """`truncated` 说的是「确实切掉了东西」：模型给了 18 条但 5 条不合格、剩 13 条全给回去时，
    报 `true` 会让前端说一句假话（「只保留了前 15 条」并没有发生）。"""
    token = _register_and_login(client)
    fake_deepseek.items_payload = {"skills": _valid_items(13) + [HALLUCINATED_ITEM] * 5}

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    data = _data(response)
    assert len(data["skills"]) == 13
    assert data["meta"]["truncated"] is False
    assert data["meta"]["received_count"] == 18
    assert data["meta"]["dropped_count"] == 5


# --------------------------------------------------------------------------- #
# 四·补、重名合并（键口径 = `skill_name_key`，与写侧「技能名称重复」422 同一个函数）
#
# 为什么这一组用例存在：模型经常把同一个技能拆成 `Python` / `python` 两条。不合并的
# 后果不是难看，而是**必败**——用户点「保存岗位」时 `JobWriteRequest` 会报 422、
# 库的 `UNIQUE (job_id, name)` 也会拦，等于我们主动把一个打不进去的 payload 交给用户。
# --------------------------------------------------------------------------- #


def test_duplicate_names_collapse_to_the_first_occurrence(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """三种写法（`Python` / `python` / ` Python `）合成一条，留下的是**第一条**。

    保留首条而不是末条：模型给的优先级顺序与原始大小写都落在第一条上，
    后来者只是同一种东西的另一种写法，没有信息可取。
    """
    token = _register_and_login(client)
    fake_deepseek.items_payload = {
        "skills": [
            {"name": "Python", "type": "hard", "evidence": "熟练掌握 Python", "confidence": 0.9},
            {"name": "python", "type": "domain", "evidence": "熟悉 FastAPI", "confidence": 0.5},
            {"name": "  Python  ", "type": "soft", "evidence": "熟悉 Docker", "confidence": 0.4},
        ]
    }

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    data = _data(response)
    assert data["skills"] == [
        {"name": "Python", "type": "hard", "evidence": "熟练掌握 Python", "confidence": 0.9}
    ]
    meta = data["meta"]
    assert meta["received_count"] == 3
    assert meta["deduped_count"] == 2
    assert meta["dropped_count"] == 0
    assert meta["truncated"] is False
    # 三个数在没被封顶切的时候必须闭合，否则「AI 给出 3 条」这句话对不上结果。
    assert meta["dropped_count"] + meta["deduped_count"] + len(data["skills"]) == 3


def test_cpp_csharp_and_c_are_three_different_skills(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """去重键**不是** §2.D.1 那套四步归一化的回归护栏，两种写法都要钉。

    为什么非得带上 `C#`：`+` 的 Unicode 类别是 `Sm`，四步归一化的第 4 步（删标点）删不到它，
    所以「`C++` vs `C`」在两种键下都分得开——只测它，键写错了这条用例照样绿。
    `#` 的类别是 `Po`、会被删，`C#` 与 `C` 只有在 `skill_name_key` 下才是两门语言；
    同理 `_`（`Pc`）会让 `user_id` 与 `userid` 在四步口径下并成一条。
    """
    token = _register_and_login(client)
    jd = "精通 C++，熟悉 C 语言。有 C# 桌面端开发经验。"
    fake_deepseek.items_payload = {
        "skills": [
            {"name": "C++", "type": "hard", "evidence": "精通 C++", "confidence": 0.9},
            {"name": "C", "type": "hard", "evidence": "熟悉 C", "confidence": 0.8},
            {"name": "C#", "type": "hard", "evidence": "有 C# 桌面端开发经验", "confidence": 0.7},
        ]
    }

    response = client.post(EXTRACT_URL, json={"description": jd}, headers=_byok(token))

    assert response.status_code == 200, response.text
    data = _data(response)
    assert [skill["name"] for skill in data["skills"]] == ["C++", "C", "C#"]
    assert data["meta"]["deduped_count"] == 0
    assert data["meta"]["dropped_count"] == 0


def test_fullwidth_name_merges_with_the_halwidth_one(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """全角首字母 `Ｐython` 与半角 `Python` 是同一条（NFKC 生效），留下的是全角那条。

    「留下先出现的那条」在这里有个副作用要写明：库里存的会是全角写法。这是模型自己的
    输出顺序决定的，不是合并规则的错——用户在草稿态逐条过目（2.A）时看到的也是这一条。
    """
    token = _register_and_login(client)
    fake_deepseek.items_payload = {
        "skills": [
            {"name": "Ｐython", "type": "hard", "evidence": "熟练掌握 Python", "confidence": 0.9},
            {"name": "Python", "type": "hard", "evidence": "熟悉 FastAPI", "confidence": 0.6},
        ]
    }

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    data = _data(response)
    assert [skill["name"] for skill in data["skills"]] == ["Ｐython"]
    assert data["meta"]["deduped_count"] == 1
    assert data["meta"]["dropped_count"] == 0


def test_dropped_and_deduped_do_not_leak_into_each_other(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """一条不可回溯 + 一条重名 → `dropped_count=1` 且 `deduped_count=1`，各说各的。

    两个数合并成一个（或让重名的那条也进 dropped）会让前端两句文案都说错话：
    「无法回溯原文」该让人怀疑核验规则、换一份 JD，「重名已合并」什么都不用做。
    反方向也要成立：核验失败的条目**不进**重名集合，所以两条同名的幻觉是 dropped=2、
    deduped=0（同一个 JD 里两次都找不到依据，跟重名无关）。
    """
    token = _register_and_login(client)
    fake_deepseek.items_payload = {
        "skills": [
            VERIFIED_ITEMS[0],
            {"name": "python", "type": "hard", "evidence": "熟悉 FastAPI", "confidence": 0.4},
            HALLUCINATED_ITEM,
        ]
    }

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    data = _data(response)
    assert [skill["name"] for skill in data["skills"]] == ["Python"]
    meta = data["meta"]
    assert meta["received_count"] == 3
    assert meta["dropped_count"] == 1
    assert meta["deduped_count"] == 1
    assert meta["dropped_count"] + meta["deduped_count"] + len(data["skills"]) == 3

    fake_deepseek.items_payload = {
        "skills": [VERIFIED_ITEMS[0], HALLUCINATED_ITEM, dict(HALLUCINATED_ITEM)]
    }

    two_hallucinations = _extract(client, token)

    assert two_hallucinations.status_code == 200, two_hallucinations.text
    second_meta = _data(two_hallucinations)["meta"]
    assert (second_meta["dropped_count"], second_meta["deduped_count"]) == (2, 0)


def test_duplicates_are_merged_before_the_fifteen_item_cap(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """合并跑在截断**之前**：20 条里 3 条重名、0 条不可回溯 → 17 条幸存者 → 返回 15 条。

    若反过来（先切 15 再合并），`deduped_count` 说的就变成「返回的那 15 条里有几条重名」，
    而封顶切掉的条目根本没进结果，两个数互相吃掉；用户还会看到「只保留了前 15 条」
    却只拿到 12 条这种没法解释的结果。
    """
    token = _register_and_login(client)
    unique_items = _valid_items(MAX_EXTRACTED_SKILLS + 2)
    fake_deepseek.items_payload = {
        "skills": [
            *unique_items,
            dict(unique_items[0]),
            dict(unique_items[3]),
            dict(unique_items[9]),
        ]
    }

    response = _extract(client, token)

    assert response.status_code == 200, response.text
    data = _data(response)
    meta = data["meta"]
    assert meta["received_count"] == 20
    assert meta["dropped_count"] == 0
    assert meta["deduped_count"] == 3
    assert meta["truncated"] is True
    assert len(data["skills"]) == MAX_EXTRACTED_SKILLS == 15
    assert data["skills"][-1]["name"] == "技能14", "合并过的名单里仍按模型顺序取前 15 条"
    # 被 15 条上限切掉的那 2 条不进任何一格，所以「`dropped + deduped + len(skills)`
    # 加起来等于 received」这条本该闭合的等式，在 `truncated` 为真时**不成立**，
    # 差值恰好是封顶吃掉的数量。把差值钉成字面量而不是删掉这条断言：将来谁给封顶
    # 补了第四个计数，这条会红并逼他同步改这段注释，而不是让等式悄悄失效。
    cut_by_cap = (
        meta["received_count"] - meta["dropped_count"] - meta["deduped_count"] - len(data["skills"])
    )
    assert cut_by_cap == 2


# --------------------------------------------------------------------------- #
# 五、零写入证明（附录指定的取证方式：挂在 Engine 上，不是 Session）
# --------------------------------------------------------------------------- #


def test_extract_endpoint_executes_no_write_statement(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """本端点**不写任何表**：收集本次请求在 Engine 上执行过的全部语句，筛出 DML 必须为空。

    为什么挂在 Engine 而不是 Session（B1 的实测结论）：
    `before_cursor_execute` 是 Connection/Engine 层事件，`event.listen(session, ...)`
    会直接抛 `InvalidRequestError: No such event ...`——写成挂 Session 的测试
    会在收集阶段就报错或被 except 掉，等于没有断言。

    为什么不用「回滚后行数不变」：代码压根不 flush 时那种断言会空过，是典型的
    「绿色但无意义」。这里用**语句清单**做证据，并把清单原样打出来
    （`uv run pytest -s -k no_write` 可见），交付说明贴的是实际取证而不是转述。
    """
    token = _register_and_login(client)
    captured: list[str] = []

    def record(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        captured.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = _extract(client, token)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    writes = [statement for statement in captured if WRITE_PATTERN.match(statement)]
    print(f"[zero-write] 本次请求在 Engine 上执行过的语句共 {len(captured)} 条")
    for index, statement in enumerate(captured):
        print(f"[zero-write] {index}: {statement!r}")
    print(f"[zero-write] 其中命中 INSERT/UPDATE/DELETE 的：{writes!r}")

    assert response.status_code == 200, response.text
    assert writes == [], f"提取端点写出了 DML：{writes}"
    # 采集器本身要证明「不是空过」：鉴权必然读一次 users，清单里必须有 SELECT。
    assert captured, "语句采集器没捕到任何东西，这条断言就是假的"
    assert any(statement.lstrip().upper().startswith("SELECT") for statement in captured)
    assert _rows("jobs") == 0
    assert _rows("job_skills") == 0


def test_failed_extract_writes_nothing(client: TestClient, fake_deepseek: FakeDeepSeek) -> None:
    """任何一条失败语义都不该留下半份结果或一行数据（与润色的同名用例同口径）。"""
    token = _register_and_login(client)
    fake_deepseek.mode = "status"
    fake_deepseek.status = 500

    response = _extract(client, token)

    assert response.status_code == 502, response.text
    assert _rows("jobs") == 0
    assert _rows("job_skills") == 0


# --------------------------------------------------------------------------- #
# 六、七条失败语义（ADR-011 附录表，逐条）
# --------------------------------------------------------------------------- #


def test_failure_1_missing_key_is_ai_key_missing_400(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """第 1 行：请求头没有 Key → `AI_KEY_MISSING`（400），且一次都不外呼。"""
    token = _register_and_login(client)

    response = _extract(client, token, with_key=False)

    assert response.status_code == 400, response.text
    body = _error_body(response)
    assert body["error"]["code"] == "AI_KEY_MISSING"
    assert body["error"]["retryable"] is False
    assert "DeepSeek API Key" in body["error"]["message"]
    assert fake_deepseek.requests == [], "没 Key 就不该产生任何外呼"


def test_failure_1_blank_key_header_is_treated_as_missing(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """清空设置里的 Key 后前端可能仍发一个空头：按「没带」处理，而不是发给上游撞 401。"""
    token = _register_and_login(client)
    headers = _auth(token)
    headers["X-DeepSeek-Key"] = "   "

    response = client.post(EXTRACT_URL, json={"description": JD}, headers=headers)

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "AI_KEY_MISSING"
    assert fake_deepseek.requests == []


def test_failure_2_rejected_key_is_401_ai_key_invalid_without_logging_out(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """第 2 行：DeepSeek 拒绝这把 Key → `AI_KEY_INVALID`（401）。

    这条 401 的含义是「DeepSeek 拒绝这把 Key」，**不是**「我们的登录态失效」：
    判据是错误码而不是 URL，所以前端按 code 分流后既不刷新令牌也不踢下线。
    这里用「同一个令牌紧接着还能打到 `/auth/me`」把这件事钉成行为而不是措辞。
    """
    token = _register_and_login(client)
    fake_deepseek.mode = "status"
    fake_deepseek.status = 401

    response = _extract(client, token)

    assert response.status_code == 401, response.text
    body = _error_body(response)
    assert body["error"]["code"] == "AI_KEY_INVALID"
    assert API_KEY not in response.text, "响应体不得回显 Key"
    # 我们的会话没被这次 401 影响：令牌仍然有效（前端因此不该走登出分支）。
    still_signed_in = client.get(ME_URL, headers=_auth(token))
    assert still_signed_in.status_code == 200, still_signed_in.text


@pytest.mark.parametrize("status_code", [400, 402, 429, 500, 503])
def test_failure_3_upstream_status_is_502_retryable(
    client: TestClient, fake_deepseek: FakeDeepSeek, status_code: int
) -> None:
    """第 3 行：上游限流 / 欠费 / 5xx → `AI_SERVICE_UNAVAILABLE`（502，`retryable`）。"""
    token = _register_and_login(client)
    fake_deepseek.mode = "status"
    fake_deepseek.status = status_code

    response = _extract(client, token)

    assert response.status_code == 502, response.text
    body = _error_body(response)
    assert body["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert body["error"]["retryable"] is True


def test_failure_3_timeout_and_network_break_are_502(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """第 3 行的另外两半：读超时与连接断开。超时也只外呼一次（重试交给用户点）。"""
    token = _register_and_login(client)

    fake_deepseek.mode = "timeout"
    timed_out = _extract(client, token)
    assert timed_out.status_code == 502, timed_out.text
    assert timed_out.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert len(fake_deepseek.requests) == 1, "服务端不自动重试"

    fake_deepseek.mode = "network"
    broken = _extract(client, token)
    assert broken.status_code == 502, broken.text
    assert broken.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"


def test_failure_4_unparsable_json_is_502_not_the_truncation_case(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """第 4 行：模型不守格式（`finish_reason=stop` + 非法 JSON）→ 502。

    与第 7 情形互为反向：这里 `finish_reason` 是 `stop`，说明内容确实是模型「答完了」
    的产物，诊断应当是「它没守格式」，不该出现「被 token 上限切断」那句用户动作。
    """
    token = _register_and_login(client)
    fake_deepseek.finish_reason = "stop"
    fake_deepseek.raw_content = "抱歉，我需要更多信息才能拆解这份 JD。"

    response = _extract(client, token)

    assert response.status_code == 502, response.text
    body = _error_body(response)
    assert body["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert body["error"]["retryable"] is True
    assert body["error"]["message"] != EXTRACT_TRUNCATED_MESSAGE, "第 4 情形不得说成切断"
    assert "token 上限" not in body["error"]["message"]
    assert body["meta"].get("finish_reason") != "length"


def test_failure_5_every_evidence_failing_verification_is_502(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """第 5 行：所有条目的 evidence 都通不过子串核验 → 502，**不得返回空列表**。

    返回 `[]` 会把「AI 一句都没答对」伪装成「这个岗位不要技能」，
    用户既无从纠正也无从重试。
    """
    token = _register_and_login(client)
    fake_deepseek.items_payload = {"skills": [HALLUCINATED_ITEM]}

    response = _extract(client, token)

    assert response.status_code == 502, response.text
    body = _error_body(response)
    assert body["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert "skills" not in response.text or "[]" not in response.text


def test_global_handler_502_message_is_scoped_neutral(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """走 `app/main.py` 全局处理器的那句 502 文案，不许出现「润色」或「简历」。

    为什么钉在第 5 情形而不是伪造上游：它是七条里唯一由**我们自己的规则**触发的 502，
    不需要 Mock 出抖动就能稳定命中处理器（`job_service` 抛 `ResumeCopyUnavailableError`，
    api 层不 catch）。
    为什么这件事必须写在测试里而不是只写在注释里：提取侧刻意复用润色的异常族、
    一个错误码都没新增（ADR-011 2.F），所以**同一个处理器同时服务两个端点**——
    上一轮的文案让岗位录入页说出「请稍后重试润色。生成简历不受影响。」，
    两句都与用户当前操作无关，第二句还是假的。
    「本次请求未被保存」对两端都是真话（两端都纯计算：ADR-010 §5 / ADR-011 2.A），
    所以断言它存在，而不是只断言两句否定。

    第 7 情形**不在**本条范围内：它的文案在 api 层就地组装
    （`app/api/v1/jobs.py` 的 `EXTRACT_TRUNCATED_MESSAGE`），是刻意专属的，
    也不含「未被保存」这句——正因如此它不会被这条用例误当成中性文案放过。
    """
    token = _register_and_login(client)
    fake_deepseek.items_payload = {"skills": [HALLUCINATED_ITEM]}

    response = _extract(client, token)

    assert response.status_code == 502, response.text
    body = _error_body(response)
    assert body["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert body["error"]["retryable"] is True
    message = body["error"]["message"]
    assert "润色" not in message, f"文案串到了另一个端点上：{message}"
    assert "简历" not in message, f"文案串到了另一个端点上：{message}"
    assert "未被保存" in message, message


def test_failure_5_empty_skill_list_is_502(client: TestClient, fake_deepseek: FakeDeepSeek) -> None:
    """模型给一个空数组同样是「没有可用结果」：不许静默 200 + 空列表。"""
    token = _register_and_login(client)
    fake_deepseek.items_payload = {"skills": []}

    response = _extract(client, token)

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("raw_content", "null_content"),
    [("", False), ("   \n  ", False), (None, True)],
)
def test_failure_6_blank_content_is_502_as_empty_not_as_unparsable(
    client: TestClient,
    fake_deepseek: FakeDeepSeek,
    raw_content: str | None,
    null_content: bool,
) -> None:
    """第 6 行：`choices[0].message.content` 为空、全空白、或干脆是 `null` → 502（`retryable`）。

    这条必须**在解析之前**判：润色那边的 `(content or "").strip()` 兜住 `None` 之后
    仍会走解析路径，最终报「不是 JSON」，把「模型根本没答」误诊成「模型答得不对」，
    甚至退化成一条意义不明的 4xx。
    """
    token = _register_and_login(client)
    fake_deepseek.raw_content = raw_content
    fake_deepseek.null_content = null_content
    fake_deepseek.finish_reason = "stop"

    response = _extract(client, token)

    assert response.status_code == 502, response.text
    body = _error_body(response)
    assert body["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert body["error"]["retryable"] is True


def test_failure_7_length_finish_reason_is_502_with_meta_finish_reason(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """第 7 行：`finish_reason == "length"` → 502 + `meta.finish_reason = "length"`。

    Mock 构造的是最真实的场景：半截 JSON（`{"skills": [{"name": "Python", "evidence": "熟练`）。
    三个断言按附录逐条来：① 502 `retryable`、② `meta.finish_reason == "length"`、
    ③ **没有**走到第 4 情形的「不是合法 JSON」文案——它是被我们自己的 `max_tokens` 切了。
    """
    token = _register_and_login(client)
    fake_deepseek.finish_reason = "length"
    fake_deepseek.raw_content = '{"skills": [{"name": "Python", "evidence": "熟练'

    response = _extract(client, token)

    assert response.status_code == 502, response.text
    body = _error_body(response)
    assert body["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert body["error"]["retryable"] is True
    assert body["meta"]["finish_reason"] == "length"
    assert body["error"]["message"] == EXTRACT_TRUNCATED_MESSAGE
    assert "请重试" in body["error"]["message"], "第 7 情形的用户动作是少给点内容，不是重试同一请求"


def test_failure_7_and_4_are_not_merged(client: TestClient, fake_deepseek: FakeDeepSeek) -> None:
    """附录要求两条测试**互为反向**，防止后来人把第 7 与第 4 合并成「JSON 解析失败」。

    同一个半截 JSON：`finish_reason=length` 必须是「被切断」，`finish_reason=stop`
    必须是「模型没守格式」——内容一模一样而诊断不同，差别只在那个 `finish_reason`。
    """
    token = _register_and_login(client)
    half_json = '{"skills": [{"name": "Python", "evidence": "熟练'

    fake_deepseek.raw_content = half_json
    fake_deepseek.finish_reason = "length"
    truncated = _extract(client, token)
    assert truncated.status_code == 502, truncated.text
    assert truncated.json()["meta"]["finish_reason"] == "length"
    assert truncated.json()["error"]["message"] == EXTRACT_TRUNCATED_MESSAGE

    fake_deepseek.finish_reason = "stop"
    malformed = _extract(client, token)
    assert malformed.status_code == 502, malformed.text
    assert malformed.json()["error"]["message"] != EXTRACT_TRUNCATED_MESSAGE
    assert malformed.json()["meta"].get("finish_reason") != "length"


# --------------------------------------------------------------------------- #
# 七、输入上限（2.F：第一道闸是 Pydantic 而不是 Prompt）
# --------------------------------------------------------------------------- #


def test_extract_rejects_oversized_description_and_title(
    client: TestClient, fake_deepseek: FakeDeepSeek
) -> None:
    """`description` ≤ 8000、`title` ≤ 200，超限 422 且**一次都不外呼**。

    这条闸保护三件事：Prompt 体积、用户自带 Key 的钱，以及「别把这台服务器当成
    可以任意塞文本的转发器」。空 `description` 同样不收——空 JD 提取不出任何东西，
    而让它走到上游只会得到一条意义不明的 502。
    """
    token = _register_and_login(client)

    for body in (
        {"description": "x" * 8001},
        {"description": ""},
        {"description": JD, "title": "T" * 201},
    ):
        response = client.post(EXTRACT_URL, json=body, headers=_byok(token))
        assert response.status_code == 422, str(body)[:60] + response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert response.json()["meta"]["request_id"]

    assert fake_deepseek.requests == [], "校验失败的请求不该产生任何外呼"


def test_extract_accepts_the_eight_thousand_character_boundary(client: TestClient) -> None:
    """8000 是**含**的：整份 JD 粘进来正好卡在上限，不该被 `<` 误伤成 422。"""
    token = _register_and_login(client)

    response = client.post(
        EXTRACT_URL, json={"description": "负" * MAX_DESCRIPTION_LENGTH}, headers=_auth(token)
    )

    # 不带 Key：说明请求体已经过完校验、进到「有没有 Key」这一格（400 而不是 422）。
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "AI_KEY_MISSING"


# --------------------------------------------------------------------------- #
# 八、日志与异常消息的脱敏（BYOK 最容易翻车的地方）
# --------------------------------------------------------------------------- #


def test_api_key_never_reaches_response_or_logs(
    client: TestClient, fake_deepseek: FakeDeepSeek, caplog: pytest.LogCaptureFixture
) -> None:
    """按最坏配置（全库 DEBUG）验一次：成功与 401 两条路都不许出现明文 Key。"""
    token = _register_and_login(client)

    with caplog.at_level(logging.DEBUG):
        ok = _extract(client, token)
        assert ok.status_code == 200, ok.text
        fake_deepseek.mode = "status"
        fake_deepseek.status = 401
        bad = _extract(client, token)
        assert bad.status_code == 401

    assert API_KEY not in caplog.text
    assert API_KEY not in ok.text and API_KEY not in bad.text


def test_logs_carry_counts_and_finish_reason_but_not_the_jd_or_evidence(
    client: TestClient, fake_deepseek: FakeDeepSeek, caplog: pytest.LogCaptureFixture
) -> None:
    """2.D 的日志口径：只有三个计数（received / dropped / deduped）、`finish_reason`
    与 `request_id`。

    JD 正文与 evidence 内容都不许进日志（它们是大段用户粘进来的第三方文本，
    而且「模型答了什么」本就该由 `dropped_count` 这个数回答，不是由日志正文回答）。
    """
    token = _register_and_login(client)
    fake_deepseek.items_payload = {"skills": [VERIFIED_ITEMS[0], HALLUCINATED_ITEM]}
    headers = _byok(token)
    headers["X-Request-ID"] = "req-counts-7"

    with caplog.at_level(logging.INFO):
        response = client.post(EXTRACT_URL, json={"description": JD}, headers=headers)

    assert response.status_code == 200, response.text
    assert "received=2" in caplog.text
    assert "dropped=1" in caplog.text
    assert "deduped=0" in caplog.text, "重名合并是第三个必须能读数的失败桶"
    assert "finish_reason=stop" in caplog.text
    assert "req-counts-7" in caplog.text
    assert JD not in caplog.text, "JD 正文不得进日志"
    assert "熟练掌握 Python" not in caplog.text, "evidence 内容不得进日志"
    assert API_KEY not in caplog.text
