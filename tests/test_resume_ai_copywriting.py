"""ADR-010 集成测试：`POST /resumes/polish` 润色链路，全程 Mock，不连线上。

**本文件同时守住「解耦」这条主线**：润色与生成是两条互不调用的路。所以除了润色自己的用例，
这里还反向断言 `POST /resumes/generate` **一次都不外呼**、**没有 Key 也能出简历** ——
哪天有人把 AI 塞回生成路径，必须由本文件先红。

**拦截点为什么是自己构造的 `httpx2.MockTransport`**：`openai` 3.x 的 HTTP 层是
`httpx2`，而 pytest-httpx / respx 只给 `httpx` 打补丁。实测用 pytest-httpx 写「Mock
测试」时请求会真的打到 `api.deepseek.com` 并拿回线上 401 —— 那等于把真实外呼塞进了
CI（AGENTS.md 4.4 明确禁止）。自己注入 transport 之后，拦截发生在传输层，断言的就是
真正发出去的字节：`model` / `stream` / `thinking` / `Authorization` / System Prompt
里的档位指令全部可见。该夹具是 **autouse**，所以本模块没有任何一条用例可能真出网。

用例覆盖：线级请求参数、四档 Prompt 差异与 `max_words` 注入、`expand` 的「目标 + 超限标注」
（ADR-018：不砍尾，超目标报数、超契约段级截断）、润色不落库、Key 缺失、生成路径零外呼、
上游 401/402/429/5xx/超时、模型不合契约（段数不符 / 非 JSON）、AI 不得凭空加技能、
请求体校验上限、Key 不进日志。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.integrations import copy_writer
from app.integrations.copy_writer import build_system_prompt
from app.repositories.user_repository import get_engine
from app.schemas.resume import POLISH_MODES

GENERATE_URL = "/api/v1/resumes/generate"
POLISH_URL = "/api/v1/resumes/polish"
PROFILES_URL = "/api/v1/profiles/me"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"
API_KEY = "sk-deepseek-test-0123456789abcdef"

ORIGINAL_DESCRIPTION = "面向校内学生的二手交易 Web 应用"
ORIGINAL_RESPONSIBILITY = "负责后端 API 与数据库设计"
POLISHED_DESCRIPTION = "主导校内二手交易平台的后端设计，把下单与检索接口收敛为统一分页协议"

#: 润色声称「不碰任何表」，这条断言就把与简历相关的 4 张表全查一遍。
DB_TABLES = ("resumes", "student_profiles", "skills", "experiences")


def _polish_payload(**overrides: object) -> dict[str, object]:
    """润色请求体：形状就是前端 Pinia 里那份草稿，允许尚未保存。"""
    payload: dict[str, object] = {
        "profile": {"full_name": "张三", "contact": "13800000000", "email": "zhangsan@example.com"},
        "skills": [
            {"name": "Python"},
            {"name": "SQL"},
        ],
        "experiences": [
            {
                "title": "校园二手交易平台",
                "description": ORIGINAL_DESCRIPTION,
                "responsibility": ORIGINAL_RESPONSIBILITY,
            }
        ],
    }
    payload.update(overrides)
    return payload


class FakeDeepSeek:
    """可编程的 DeepSeek 上游：返回文案 / 返回状态码 / 抛超时，并记录真实请求。"""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.mode = "copy"
        self.status = 500
        self.raw_content: str | None = None
        self.copy_payload: dict[str, Any] = {
            "skills": ["Python", "SQL"],
            "experiences": [
                {
                    "index": 0,
                    "description": POLISHED_DESCRIPTION,
                    "responsibility": ORIGINAL_RESPONSIBILITY,
                }
            ],
        }

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content.decode("utf-8")) for request in self.requests]

    @property
    def system_prompt(self) -> str:
        """最近一次外呼里的 System Prompt 全文 —— 档位断言就看它。"""
        for message in self.bodies[-1]["messages"]:
            if message.get("role") == "system":
                return str(message["content"])
        raise AssertionError("请求体里没有 system 消息")

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.mode == "timeout":
            raise httpx2.ConnectTimeout("simulated read timeout")
        if self.mode == "status":
            return httpx2.Response(self.status, json={"error": {"message": "upstream refused"}})
        content = (
            self.raw_content
            if self.raw_content is not None
            else json.dumps(self.copy_payload, ensure_ascii=False)
        )
        return httpx2.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )

    @property
    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def fake_deepseek(monkeypatch: pytest.MonkeyPatch) -> FakeDeepSeek:
    """把 writer 的默认出网客户端换成 Mock transport（writer 每次请求新建，无需覆盖依赖）。

    **autouse 是刻意的**：本文件只要有一条用例漏写 fixture 参数，那条用例就会**真的打到
    api.deepseek.com** 并拿回线上 401 —— 断言照样「通过」，但 CI 里多了一次真实外呼，
    违反 AGENTS.md 4.4（测试不得依赖真实网络）。写成本模块的默认状态，而不是靠每条用例
    自己记得，是让这类事故结构上不可能发生。
    """
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


def _polish(client: TestClient, token: str, **overrides: object):
    return client.post(POLISH_URL, json=_polish_payload(**overrides), headers=_byok(token))


def _copy(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _polished_item(response: Any, index: int = 0) -> dict[str, Any]:
    items = _copy(response)["experiences"]
    assert isinstance(items, list)
    item: dict[str, Any] = items[index]
    return item


def _rows(table: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def _complete_profile(client: TestClient, token: str) -> None:
    saved = client.put(
        PROFILES_URL,
        json={
            "full_name": "张三",
            "contact": "13800000000",
            "skills": [{"name": "Python"}],
            "experiences": [
                {
                    "title": "校园二手交易平台",
                    "start_date": "2024-03-01",
                    "end_date": "2024-06-30",
                    "description": ORIGINAL_DESCRIPTION,
                    "responsibility": ORIGINAL_RESPONSIBILITY,
                }
            ],
            "educations": [],
        },
        headers=_auth(token),
    )
    assert saved.status_code == 200, saved.text


# --------------------------------------------------------------------------- #
# 一、请求级契约：模型、流式、思考模式、Key 的去处
# --------------------------------------------------------------------------- #


def test_polish_calls_deepseek_with_pinned_model_and_no_stream(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    token = _register_and_login(client)

    response = _polish(client, token)

    assert len(fake_deepseek.requests) == 1, "SDK 重试次数已钉死为 0，一次润色只外呼一次"
    request = fake_deepseek.requests[0]
    body = fake_deepseek.bodies[0]
    assert str(request.url).startswith("https://api.deepseek.com")
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert body["model"] == "deepseek-flash"
    assert body["stream"] is False
    # deepseek-flash 默认开思考模式，必须显式关掉，否则每次润色都白烧一段思维链。
    assert body["thinking"] == {"type": "disabled"}
    # 未指定档位时走 medium。
    assert "档位=中幅" in fake_deepseek.system_prompt
    assert _copy(response)["skills"] == ["Python", "SQL"]
    item = _polished_item(response)
    assert item["index"] == 0
    assert item["description"] == POLISHED_DESCRIPTION


def test_polish_prompt_carries_the_draft_not_the_database(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """发给模型的必须是前端草稿里的原文：用户可能根本没保存，库里那份是旧的。"""
    token = _register_and_login(client)
    _complete_profile(client, token)

    response = _polish(
        client,
        token,
        experiences=[
            {
                "title": "知识图谱问答系统",
                "description": "做了一个问答系统",
                "responsibility": "负责检索",
            }
        ],
    )

    assert response.status_code == 200, response.text
    messages = json.dumps(fake_deepseek.bodies[0]["messages"], ensure_ascii=False)
    assert "知识图谱问答系统" in messages
    assert "做了一个问答系统" in messages
    assert ORIGINAL_DESCRIPTION not in messages, "润色用的是请求体，不是库里的档案"


def test_polish_writes_nothing_to_database(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """ADR-010 的核心承诺：润色是纯计算。落库发生在用户点「保存修改」之后。"""
    token = _register_and_login(client)

    response = _polish(client, token)

    assert response.status_code == 200, response.text
    assert len(fake_deepseek.requests) == 1
    for table in DB_TABLES:
        assert _rows(table) == 0, f"{table} 被润色接口写入了"


# --------------------------------------------------------------------------- #
# 二、四档 Prompt：档位差异与 max_words 注入
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", POLISH_MODES)
def test_every_mode_prompt_keeps_the_anti_fabrication_rules(mode: str) -> None:
    """档位可以改写法，但不能改事实边界：硬约束每一档都必须原样带上。"""
    prompt = build_system_prompt(mode)  # type: ignore[arg-type]
    assert "只能使用用户给定的事实" in prompt
    assert "不得虚构量化指标" in prompt
    assert "experiences 必须与输入等长" in prompt
    assert "不得新增、改写或翻译" in prompt
    assert "{max_words}" not in prompt, "未替换的模板占位符不能出现在成品 Prompt 里"


def test_mode_directives_reach_the_model(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """四档各发一次，断言真实外呼里的 System Prompt 各不相同且带自己的档位标记。"""
    token = _register_and_login(client)
    markers = {
        "detail": ("档位=细节", "每段字数变化不超过 ±20%"),
        "medium": ("档位=中幅", "提升专业度"),
        "heavy": ("档位=大幅", "STAR"),
        "expand": ("档位=扩写", "严格不超过"),
    }

    seen: list[str] = []
    for mode, fragments in markers.items():
        response = _polish(client, token, mode=mode)
        assert response.status_code == 200, response.text
        prompt = fake_deepseek.system_prompt
        for fragment in fragments:
            assert fragment in prompt, f"{mode} 档缺少 {fragment}"
        seen.append(prompt)

    assert len(set(seen)) == 4, "四档发出同一个 Prompt，等于档位没生效"


def test_heavy_mode_forbids_invented_metrics(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """`heavy` 要求写「结果与价值」，是最容易诱导模型编数字的一档，必须显式收口。"""
    token = _register_and_login(client)

    response = _polish(client, token, mode="heavy")

    assert response.status_code == 200, response.text
    assert "结果只允许引用输入中已出现的数字" in fake_deepseek.system_prompt


def test_expand_injects_default_max_words(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """`expand` 不传 `max_words` 时补 200，与前端输入框的默认值一致。"""
    token = _register_and_login(client)

    response = _polish(client, token, mode="expand")

    assert response.status_code == 200, response.text
    assert "严格不超过 200 个汉字" in fake_deepseek.system_prompt


@pytest.mark.parametrize("max_words", [80, 400])
def test_expand_injects_given_max_words(
    fake_deepseek: FakeDeepSeek, client: TestClient, max_words: int
) -> None:
    token = _register_and_login(client)

    response = _polish(client, token, mode="expand", max_words=max_words)

    assert response.status_code == 200, response.text
    assert f"严格不超过 {max_words} 个汉字" in fake_deepseek.system_prompt


def test_non_expand_modes_ignore_max_words(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """其它档位收下 `max_words` 也必须作废：校验层已把它置空，Prompt 里不该出现那个数字。"""
    token = _register_and_login(client)

    response = _polish(client, token, mode="heavy", max_words=473)

    assert response.status_code == 200, response.text
    assert "473" not in fake_deepseek.system_prompt


# --------------------------------------------------------------------------- #
# 三、expand 的字数是「目标 + 超限标注」，不再砍尾（ADR-018 §2 / §6）
#
# 下面两条用例在 ADR-018 之前锁的是「超了就砍」（_cap_pair），裁定之后锁的是
# 「超了就标注」：断言方向反转，且 over_limit_by 必须验**数值**而不是只验字段存在。
# --------------------------------------------------------------------------- #


def test_expand_caps_oversized_model_output(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """ADR-018：模型超了目标**不砍尾**，原文返回 + 报出超出多少，让用户自己减。

    旧断言方向是「合计 ≤ max_words」；新方向是「合计 == 模型原文长度」且
    `over_limit_by == len(返回文字) - target_words`（必须有数，§6 机械判据）。
    """
    token = _register_and_login(client)
    description = "建设" * 120  # 240 字
    responsibility = "承担" * 60  # 120 字
    fake_deepseek.copy_payload = {
        "skills": ["Python", "SQL"],
        "experiences": [{"index": 0, "description": description, "responsibility": responsibility}],
    }

    response = _polish(client, token, mode="expand", max_words=60)

    assert response.status_code == 200, response.text
    item = _polished_item(response)
    # 不砍尾：模型原文一字不动地回来（240 + 120 = 360，远超目标 60）。
    assert item["description"] == description
    assert item["responsibility"] == responsibility
    # 超限必须有数：360 - 60 = 300，不是只验字段存在。
    assert item["target_words"] == 60
    assert item["over_limit_by"] == 300
    assert item["over_limit_by"] == len(item["description"]) + len(item["responsibility"]) - 60
    # 超的是「目标」不是「契约」（1200/600）：不许截断，也不许整单报错。
    assert item["truncated"] is False


def test_expand_caps_at_sentence_boundary(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """ADR-018：句子完整不再是「截到句末」的结果，而是「根本没截」的结果。

    旧断言方向是「截断落在句末且合计 ≤ 40」；新方向是「原文逐字返回（自然以句号
    结尾）且 over_limit_by == 49 - 40 == 9」。
    """
    token = _register_and_login(client)
    description = "负责检索模块搭建。支撑多路召回与重排。接入向量索引服务。持续迭代排序策略。"
    responsibility = "负责召回与排序链路质量。"
    fake_deepseek.copy_payload = {
        "skills": ["Python", "SQL"],
        "experiences": [
            {
                "index": 0,
                "description": description,
                "responsibility": responsibility,
            }
        ],
    }

    response = _polish(client, token, mode="expand", max_words=40)

    assert response.status_code == 200, response.text
    item = _polished_item(response)
    assert item["description"] == description
    assert item["description"].endswith("。"), item["description"]
    assert item["responsibility"] == responsibility
    # 37 + 12 = 49，超目标 40 共 9 字——数值必须对上。
    assert item["target_words"] == 40
    assert item["over_limit_by"] == 9
    assert item["over_limit_by"] == len(item["description"]) + len(item["responsibility"]) - 40
    assert item["truncated"] is False


def test_expand_within_target_reports_zero_over_limit(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """不超目标时 `over_limit_by` 恒为 0（不是 null、不是缺字段）。"""
    token = _register_and_login(client)

    response = _polish(client, token, mode="expand", max_words=800)

    assert response.status_code == 200, response.text
    item = _polished_item(response)
    assert item["target_words"] == 800
    assert item["over_limit_by"] == 0
    assert item["truncated"] is False


def test_non_expand_modes_have_no_target_and_zero_over_limit(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """`target_words` 只有 expand 有；其它档 None，`over_limit_by` 恒 0（ADR-018 §2.2）。"""
    token = _register_and_login(client)

    for mode in ("detail", "medium", "heavy"):
        response = _polish(client, token, mode=mode)
        assert response.status_code == 200, response.text
        item = _polished_item(response)
        assert item["target_words"] is None, mode
        assert item["over_limit_by"] == 0, mode
        assert item["truncated"] is False, mode


def test_over_contract_output_is_truncated_per_segment_not_502(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """ADR-018 §2.4 / §6：超**契约**（1200/600）时段级截断 + truncated=true，其余段照常。

    这是放宽契约后第一次可达的路径：模型写飞不再毁掉整次润色。
    契约那道边只要求「出现 + 标注」，**不要求报出截断前长度**
    （truncated_from_words 之类字段被老板否掉，§6 末）——所以这里反向钉住它不存在。
    """
    token = _register_and_login(client)
    fake_deepseek.copy_payload = {
        "skills": ["Python", "SQL"],
        "experiences": [
            {"index": 0, "description": "超" * 1500, "responsibility": "责" * 700},
            {"index": 1, "description": "正常的一段", "responsibility": "负责后端"},
        ],
    }

    response = _polish(
        client,
        token,
        mode="expand",
        max_words=800,
        experiences=[
            {"title": "第一段", "description": "原文一", "responsibility": "职责一"},
            {"title": "第二段", "description": "原文二", "responsibility": "职责二"},
        ],
    )

    assert response.status_code == 200, response.text
    items = _copy(response)["experiences"]
    bad, good = items[0], items[1]
    # 坏的那段：截到契约上限并标注，不是整单 502。
    assert bad["truncated"] is True
    assert len(bad["description"]) == 1200
    assert len(bad["responsibility"]) == 600
    assert bad["description"] == "超" * 1200
    assert bad["responsibility"] == "责" * 600
    # over_limit_by 按**返回文字**算：1200 + 600 - 800 = 1000。
    assert bad["over_limit_by"] == 1000
    # 好的那段照常返回，不被连坐。
    assert good["truncated"] is False
    assert good["description"] == "正常的一段"
    assert good["responsibility"] == "负责后端"
    assert good["over_limit_by"] == 0
    # 被否掉的字段不许出现（ADR-018 §6 末）。
    assert "truncated_from_words" not in bad
    assert "truncated_from_words" not in good


def test_other_modes_over_contract_also_truncate_per_segment(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """契约是响应侧的最后一道硬边，与档位无关：heavy 写飞了也是段级截断，不是 502。"""
    token = _register_and_login(client)
    fake_deepseek.copy_payload = {
        "skills": ["Python", "SQL"],
        "experiences": [{"index": 0, "description": "长" * 1300, "responsibility": "负责"}],
    }

    response = _polish(client, token, mode="heavy")

    assert response.status_code == 200, response.text
    item = _polished_item(response)
    assert item["truncated"] is True
    assert len(item["description"]) == 1200
    assert item["target_words"] is None
    assert item["over_limit_by"] == 0


def test_other_modes_keep_model_text_untouched(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """只有 `expand` 有服务端字数闸；其它档位不得擅自改写用户将要看到的那段文字。"""
    token = _register_and_login(client)
    long_text = "主导" * 90
    fake_deepseek.copy_payload = {
        "skills": ["Python", "SQL"],
        "experiences": [{"index": 0, "description": long_text, "responsibility": "负责"}],
    }

    response = _polish(client, token, mode="heavy")

    assert response.status_code == 200, response.text
    assert _polished_item(response)["description"] == long_text


# --------------------------------------------------------------------------- #
# 四、生成路径的「零 AI」证明（ADR-010 的另一半）
# --------------------------------------------------------------------------- #


def test_generate_never_calls_deepseek_even_with_a_key(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """带着 Key 生成也必须零外呼：生成的是库里的事实，不是模型的意见。"""
    token = _register_and_login(client)
    _complete_profile(client, token)

    response = client.post(GENERATE_URL, json={"title": "纯模板简历"}, headers=_byok(token))

    assert response.status_code == 200, response.text
    assert fake_deepseek.requests == [], "生成路径不得调用 AI"
    html = response.json()["data"]["content_html"]
    assert ORIGINAL_DESCRIPTION in html, "生成用的必须是库里原文"


def test_generate_works_without_any_key(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """没有 Key 也能出简历 —— 模板路径与 AI 彻底无关，不再需要任何降级开关。"""
    token = _register_and_login(client)
    _complete_profile(client, token)

    response = client.post(GENERATE_URL, json={"title": "无 Key 简历"}, headers=_auth(token))

    assert response.status_code == 200, response.text
    assert fake_deepseek.requests == []
    assert _rows("resumes") == 1


def test_generate_request_has_no_fallback_switch(client: TestClient) -> None:
    """`use_fallback` 已从契约里删除：OpenAPI 里的生成请求体只剩 title 与 profile_id。"""
    spec = client.get("/openapi.json")

    assert spec.status_code == 200, spec.text
    schema = spec.json()["components"]["schemas"]["ResumeGenerateRequest"]["properties"]
    assert "use_fallback" not in schema
    assert sorted(schema) == ["profile_id", "title"]


# --------------------------------------------------------------------------- #
# 五、鉴权、Key 缺失与上游异常映射
# --------------------------------------------------------------------------- #


def test_polish_requires_authentication(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """本接口烧的是用户自己的额度，必须登录；IP 限流只是第二道闸。"""
    response = client.post(POLISH_URL, json=_polish_payload())

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "CLIENT_ERROR"
    assert fake_deepseek.requests == []


def test_polish_missing_key_returns_400_and_never_calls_deepseek(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    token = _register_and_login(client)

    response = client.post(POLISH_URL, json=_polish_payload(), headers=_auth(token))

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["code"] == "AI_KEY_MISSING"
    assert "DeepSeek API Key" in body["error"]["message"]
    assert body["meta"]["request_id"]
    assert fake_deepseek.requests == [], "没 Key 就不该产生任何外呼"


def test_polish_blank_key_header_is_treated_as_missing(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """清空设置里的 Key 后前端可能仍发一个空头：按「没带」处理，而不是发给上游撞 401。"""
    token = _register_and_login(client)
    headers = _auth(token)
    headers["X-DeepSeek-Key"] = "   "

    response = client.post(POLISH_URL, json=_polish_payload(), headers=headers)

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "AI_KEY_MISSING"
    assert fake_deepseek.requests == []


def test_upstream_unauthorized_maps_to_401(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """Key 无效就是无效：401 透传，用户要的是去修 Key，而不是重试或换模板。"""
    token = _register_and_login(client)
    fake_deepseek.mode = "status"
    fake_deepseek.status = 401

    response = _polish(client, token)

    assert response.status_code == 401, response.text
    body = response.json()
    assert body["error"]["code"] == "AI_KEY_INVALID"
    assert API_KEY not in response.text, "响应体不得回显 Key"


@pytest.mark.parametrize("status_code", [400, 402, 429, 500, 503])
def test_other_upstream_status_maps_to_service_unavailable(
    fake_deepseek: FakeDeepSeek, client: TestClient, status_code: int
) -> None:
    """402（欠费）与 429（上游限流）也归「不可用」：前端给的动都是「稍后重试」。"""
    token = _register_and_login(client)
    fake_deepseek.mode = "status"
    fake_deepseek.status = status_code

    response = _polish(client, token)

    assert response.status_code == 502, response.text
    body = response.json()
    assert body["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert body["error"]["retryable"] is True
    # 服务端文案是**作用域中性**的（`app/main.py` 同一个处理器也服务 `/jobs/extract-skills`），
    # 所以这里只钉住中性句子里对两端都为真的那半句，不抄整句当字符串钉子。
    # 「润色失败不影响生成」那句 guidance 由前端 `StylePanel.vue` 按 code 自己补。
    assert "您的资料不受影响" in body["error"]["message"]


def test_upstream_timeout_maps_to_service_unavailable(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    token = _register_and_login(client)
    fake_deepseek.mode = "timeout"

    response = _polish(client, token)

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"
    assert len(fake_deepseek.requests) == 1, "服务端不自动重试，重试是用户点出来的"


def test_polish_failure_writes_nothing(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """任何上游失败都不该留下半份结果或一行数据。"""
    token = _register_and_login(client)
    fake_deepseek.mode = "status"
    fake_deepseek.status = 500

    response = _polish(client, token)

    assert response.status_code == 502, response.text
    for table in DB_TABLES:
        assert _rows(table) == 0


# --------------------------------------------------------------------------- #
# 六、模型输出不合契约时的处理
# --------------------------------------------------------------------------- #


def test_experience_count_mismatch_is_rejected(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """模型少给/多给段数不能静默通过：前端按 index 回填，错位等于改用户的事实。"""
    token = _register_and_login(client)
    fake_deepseek.copy_payload = {
        "skills": ["Python"],
        "experiences": [
            {"index": 0, "description": "第一段", "responsibility": "负责"},
            {"index": 1, "description": "凭空多出来的一段", "responsibility": "负责"},
        ],
    }

    response = _polish(client, token)

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"


def test_non_json_reply_maps_to_service_unavailable(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """模型偶尔会开始「解释」而不是输出 JSON：不能把半截结果交给用户确认。"""
    token = _register_and_login(client)
    fake_deepseek.raw_content = "抱歉，我需要更多信息才能润色这份简历。"

    response = _polish(client, token)

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"


def test_markdown_fenced_json_is_parsed(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """模型爱把 JSON 包进 ``` 围栏：这是可容忍的抖动，剥掉即可，不该报错。"""
    token = _register_and_login(client)
    fake_deepseek.raw_content = (
        "```json\n"
        + json.dumps(
            {
                "skills": ["Python", "SQL"],
                "experiences": [
                    {"index": 0, "description": "围栏里的文案", "responsibility": "负责后端"}
                ],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )

    response = _polish(client, token)

    assert response.status_code == 200, response.text
    assert _polished_item(response)["description"] == "围栏里的文案"


def test_ai_cannot_invent_or_drop_skills(fake_deepseek: FakeDeepSeek, client: TestClient) -> None:
    """技能以输入为准：模型加的一律丢弃，漏掉的按原顺序补回，名字回贴用户写过的写法。"""
    token = _register_and_login(client)
    fake_deepseek.copy_payload = {
        "skills": ["Kubernetes", "python ", "SQL", "Python"],
        "experiences": [
            {"index": 0, "description": POLISHED_DESCRIPTION, "responsibility": "负责后端设计"}
        ],
    }

    response = _polish(client, token)

    assert response.status_code == 200, response.text
    assert _copy(response)["skills"] == ["Python", "SQL"]


def test_blank_polish_falls_back_to_original_text(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """模型把文字润成空白时，保留用户原文而不是制造空洞。"""
    token = _register_and_login(client)
    fake_deepseek.copy_payload = {
        "skills": ["Python", "SQL"],
        "experiences": [{"index": 0, "description": "   ", "responsibility": " "}],
    }

    response = _polish(client, token)

    assert response.status_code == 200, response.text
    item = _polished_item(response)
    assert item["description"] == ORIGINAL_DESCRIPTION
    assert item["responsibility"] == ORIGINAL_RESPONSIBILITY

    # 真正的空串是契约违规（min_length=1），按上游不可用处理，不能悄悄交回空字段。
    fake_deepseek.copy_payload = {
        "skills": ["Python", "SQL"],
        "experiences": [{"index": 0, "description": "", "responsibility": "负责后端设计"}],
    }
    broken = _polish(client, token)
    assert broken.status_code == 502, broken.text
    assert broken.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"


def test_polished_text_is_returned_as_plain_text(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """本接口不产出 HTML —— 存储型 XSS 的入口从一开始就不存在。

    文案回到前端后写进 textarea（Vue 转义），用户保存后由模板的 `escape()` 兜住；
    转义契约本身的用例见 tests/test_resumes_generate.py。
    """
    token = _register_and_login(client)
    injected = "<img src=x onerror=alert(1)>写了二手交易平台"
    fake_deepseek.copy_payload = {
        "skills": ["Python", "SQL"],
        "experiences": [
            {
                "index": 0,
                "description": injected,
                "responsibility": "<script>steal()</script>负责后端",
            }
        ],
    }

    response = _polish(client, token)

    assert response.status_code == 200, response.text
    assert _polished_item(response)["description"] == injected
    assert "--paper-accent" not in response.text, "润色不得返回任何排版产物"
    assert "<h2" not in response.text


# --------------------------------------------------------------------------- #
# 七、请求体校验（第一道闸：Prompt 体积 + 防被当开放 AI 代理）
# --------------------------------------------------------------------------- #


def test_polish_rejects_empty_skills_and_experiences(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    token = _register_and_login(client)

    empty_skills = client.post(POLISH_URL, json=_polish_payload(skills=[]), headers=_byok(token))
    empty_experiences = client.post(
        POLISH_URL, json=_polish_payload(experiences=[]), headers=_byok(token)
    )

    assert empty_skills.status_code == 422, empty_skills.text
    assert empty_skills.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "skills" in empty_skills.json()["error"]["details"][0]["loc"]
    assert empty_experiences.status_code == 422, empty_experiences.text
    assert fake_deepseek.requests == [], "校验失败不该产生任何外呼"


def test_polish_rejects_unknown_mode(client: TestClient) -> None:
    token = _register_and_login(client)

    response = client.post(POLISH_URL, json=_polish_payload(mode="creative"), headers=_byok(token))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("max_words", [10, 900])
def test_polish_rejects_max_words_out_of_range(client: TestClient, max_words: int) -> None:
    """上限 800：`expand` 的字数是给 Prompt 体积兜底的，不是让用户要一篇小作文。"""
    token = _register_and_login(client)

    response = client.post(
        POLISH_URL,
        json=_polish_payload(mode="expand", max_words=max_words),
        headers=_byok(token),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_polish_rejects_oversized_experience_count(
    fake_deepseek: FakeDeepSeek, client: TestClient
) -> None:
    """20 段以上直接 422，不给「把整个项目仓库贴进请求体」留口子。"""
    token = _register_and_login(client)
    experiences = [
        {"title": f"经历 {i}", "description": ORIGINAL_DESCRIPTION, "responsibility": "负责"}
        for i in range(21)
    ]

    response = client.post(
        POLISH_URL, json=_polish_payload(experiences=experiences), headers=_byok(token)
    )

    assert response.status_code == 422, response.text
    assert fake_deepseek.requests == []


def test_polish_accepts_draft_without_dates(client: TestClient) -> None:
    """草稿里一段还没填时间的经历同样值得润色：本接口不套生成接口的日期校验。"""
    token = _register_and_login(client)

    response = _polish(
        client,
        token,
        experiences=[
            {
                "title": "还没定时间的项目",
                "description": "做了一个系统",
                "responsibility": "负责开发",
            }
        ],
    )

    assert response.status_code == 200, response.text


def test_polish_accepts_draft_with_blank_profile(client: TestClient) -> None:
    """姓名还没填就想润色经历：不该被生成接口的必填标准卡成 422。"""
    token = _register_and_login(client)

    response = _polish(client, token, profile={"full_name": "", "contact": ""})

    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- #
# 八、Key 不进日志（BYOK 最容易翻车的地方）
# --------------------------------------------------------------------------- #


def test_api_key_never_reaches_logs(
    fake_deepseek: FakeDeepSeek, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """按最坏配置（全库 DEBUG）验一次：成功与 401 两条路都不许出现明文 Key。"""
    token = _register_and_login(client)

    with caplog.at_level(logging.DEBUG):
        ok = _polish(client, token)
        assert ok.status_code == 200, ok.text
        fake_deepseek.mode = "status"
        fake_deepseek.status = 401
        bad = _polish(client, token)
        assert bad.status_code == 401

    assert API_KEY not in caplog.text
    assert API_KEY not in ok.text and API_KEY not in bad.text
    # 上游失败也不能把整段 Prompt / 用户草稿写进日志。
    assert ORIGINAL_DESCRIPTION not in caplog.text
