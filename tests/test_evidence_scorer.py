"""`app/integrations/evidence_scorer.py` 的用例（ADR-013 §6 / 落地顺序 7b）。

拦网点是自己构造的 `httpx2.MockTransport`（照 `tests/test_jobs_extract_skills.py`）：
`openai` 3.x 走 `httpx2`，而 pytest-httpx / respx 只给 `httpx` 打补丁。夹具是 **autouse**
的，所以本模块没有任何一条用例可能真出网（AGENTS.md §4.4）。

与提取侧的分工差异也在这里钉住：本层**只负责把失败如实抛出来**，
「抛出去之后变成 200 + keyword_fallback」是服务层的事（§6.5），别在适配器里做。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx2
import pytest

from app.integrations import evidence_scorer
from app.integrations.evidence_scorer import (
    EVIDENCE_PROMPT_VERSION,
    MAX_FRAGMENT_CHARS,
    MAX_OUTPUT_TOKENS,
    DeepSeekEvidenceScorer,
    EvidenceScoreAuthError,
    EvidenceScoreBadOutputError,
    EvidenceScoreEmptyContentError,
    EvidenceScoreTimeoutError,
    EvidenceScoreTruncatedError,
    EvidenceScoreUnavailableError,
    ScoringFragment,
    build_evidence_prompt,
)

API_KEY = "sk-test-evidence-scorer-local-fake"


def fragments(count: int = 2) -> list[ScoringFragment]:
    return [
        ScoringFragment(
            fragment_id=index,
            skill="Python",
            text=f"用 Python 重写订单服务，P99 降了 {index + 1}0%",
        )
        for index in range(count)
    ]


class FakeDeepSeek:
    """记录请求、按脚本回包的假传输层。"""

    def __init__(
        self, *, status: int = 200, content: Any = None, finish_reason: str = "stop"
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = status
        self.content = content
        self.finish_reason = finish_reason

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        self.requests.append({"body": body, "headers": dict(request.headers)})
        if self.status != 200:
            return httpx2.Response(
                self.status,
                request=request,
                content=json.dumps({"error": {"message": "upstream"}}).encode("utf-8"),
            )
        return httpx2.Response(
            200,
            request=request,
            content=json.dumps(
                {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": self.content},
                            "finish_reason": self.finish_reason,
                        }
                    ]
                }
            ).encode("utf-8"),
        )

    @property
    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def fake_deepseek(monkeypatch: pytest.MonkeyPatch) -> FakeDeepSeek:
    fake = FakeDeepSeek(content=json.dumps({"scores": []}))
    monkeypatch.setattr(evidence_scorer, "_build_http_client", lambda: fake.client)
    return fake


def scores_payload(items: list[dict[str, Any]]) -> str:
    return json.dumps({"scores": items}, ensure_ascii=False)


# ------------------------------------------------------------------ 请求参数


class TestRequestShape:
    def test_inherits_the_byok_call_parameters(self, fake_deepseek: FakeDeepSeek) -> None:
        """`deepseek-flash` / 非流式 / thinking 关闭 / max_retries=0 —— 与润色提取同口径。"""
        fake_deepseek.content = scores_payload(
            [{"fragment_id": 0, "skill": "Python", "strength": 0.6}]
        )
        DeepSeekEvidenceScorer(API_KEY).score(fragments(1))

        body = fake_deepseek.requests[0]["body"]
        assert body["model"] == "deepseek-flash"
        assert body["stream"] is False
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_tokens"] == MAX_OUTPUT_TOKENS
        # 不启用 response_format：它一旦生效会把不合法输出替换成空白，反而观测不到原始输出
        # （ADR-011 2.B 的三条理由在这里一字不改地继承）。
        assert "response_format" not in body
        assert fake_deepseek.requests[0]["headers"]["authorization"] == f"Bearer {API_KEY}"

    def test_max_output_tokens_covers_the_worst_case_pair_count(self) -> None:
        """`MAX_OUTPUT_TOKENS` 是算出来的，不是拍的：候选对上限 × 每条约 30 token。"""
        max_pairs = 8 * 3 * 3  # 技能 ≤8 × 经历 ≤3 × 字段 3
        assert max_pairs * 30 <= MAX_OUTPUT_TOKENS

    def test_user_payload_only_carries_id_skill_text(self, fake_deepseek: FakeDeepSeek) -> None:
        """§6 的「不发清单」在类型层就成立，这里核的是序列化之后仍然干净。"""
        DeepSeekEvidenceScorer(API_KEY).score(fragments(2))
        user_content = fake_deepseek.requests[0]["body"]["messages"][1]["content"]
        sent = json.loads(user_content)
        assert {key for item in sent["fragments"] for key in item} == {"id", "skill", "text"}
        assert len(sent["fragments"]) == 2

    def test_long_fragment_is_capped_before_leaving_the_server(
        self, fake_deepseek: FakeDeepSeek
    ) -> None:
        """输入预算：送出去的字数不由用户的自由文本决定（§13 复核第 3 条）。"""
        long = ScoringFragment(fragment_id=0, skill="Python", text="字" * 5000)
        DeepSeekEvidenceScorer(API_KEY).score([long])
        sent = json.loads(fake_deepseek.requests[0]["body"]["messages"][1]["content"])
        assert len(sent["fragments"][0]["text"]) == MAX_FRAGMENT_CHARS

    def test_no_candidates_means_no_http_call_at_all(self, fake_deepseek: FakeDeepSeek) -> None:
        """零候选就不该花用户的额度，也不该出现「ai_scored 但 candidate_pairs=0」。"""
        result = DeepSeekEvidenceScorer(API_KEY).score([])
        assert result.items == []
        assert fake_deepseek.requests == []


# ---------------------------------------------------------------- 失败语义


class TestFailureModes:
    def test_authentication_error_becomes_auth_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                401, request=request, content=b'{"error":{"message":"invalid key"}}'
            )

        client = httpx2.Client(transport=httpx2.MockTransport(handler))
        with pytest.raises(EvidenceScoreAuthError):
            DeepSeekEvidenceScorer(API_KEY, http_client=client).score(fragments(1))

    def test_server_error_becomes_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(500, request=request, content=b'{"error":{"message":"boom"}}')

        client = httpx2.Client(transport=httpx2.MockTransport(handler))
        with pytest.raises(EvidenceScoreUnavailableError):
            DeepSeekEvidenceScorer(API_KEY, http_client=client).score(fragments(1))

    def test_timeout_is_its_own_class_not_merged_into_upstream(self) -> None:
        """§6.5 要 `timeout` 与 `upstream` 两个 degrade_reason，前提是两个异常分得开。

        合并成一类就等于让服务层只能瞎猜——而猜错的用户动作是「等一等」对「查你的网络」。
        """

        def handler(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectTimeout("连接超时")

        client = httpx2.Client(transport=httpx2.MockTransport(handler))
        with pytest.raises(EvidenceScoreTimeoutError):
            DeepSeekEvidenceScorer(API_KEY, http_client=client).score(fragments(1))

    def test_timeout_is_a_subclass_so_existing_catch_still_works(self) -> None:
        assert issubclass(EvidenceScoreTimeoutError, EvidenceScoreUnavailableError)

    def test_truncated_output_is_named_before_parsing(self, fake_deepseek: FakeDeepSeek) -> None:
        """反向用例：半截 JSON + `finish_reason=length` 必须是第 7 情形，不是「不守格式」。

        两者的用户动作不同（第 7 是「少给点内容」，第 4 是「重试同一请求」），
        合并成一条就等于把诊断写错。
        """
        fake_deepseek.content = '{"scores": [{"fragment_id": 0, "skill": "Py'
        fake_deepseek.finish_reason = "length"
        with pytest.raises(EvidenceScoreTruncatedError) as caught:
            DeepSeekEvidenceScorer(API_KEY).score(fragments(1))
        assert caught.value.finish_reason == "length"

    def test_empty_content_is_named_as_empty(self, fake_deepseek: FakeDeepSeek) -> None:
        fake_deepseek.content = "   "
        with pytest.raises(EvidenceScoreEmptyContentError):
            DeepSeekEvidenceScorer(API_KEY).score(fragments(1))

    def test_non_json_is_bad_output(self, fake_deepseek: FakeDeepSeek) -> None:
        fake_deepseek.content = "我认为这个人很优秀。"
        with pytest.raises(EvidenceScoreBadOutputError):
            DeepSeekEvidenceScorer(API_KEY).score(fragments(1))

    def test_json_without_scores_array_is_bad_output(self, fake_deepseek: FakeDeepSeek) -> None:
        """不是 JSON 对象 / 没有 `scores` / `scores` 不是数组，三种都算契约不符。"""
        for content in ('{"score": []}', "[1, 2, 3]", '{"scores": "Python"}'):
            fake_deepseek.content = content
            with pytest.raises(EvidenceScoreBadOutputError):
                DeepSeekEvidenceScorer(API_KEY).score(fragments(1))


# ------------------------------------------------------------ 输出宽松度


class TestOutputContractIsDeliberatelyLoose:
    """单条坏评分不能报错也不能在这层丢弃——§8 的四个桶才是它的归宿。"""

    def test_non_numeric_strength_survives_parsing(self, fake_deepseek: FakeDeepSeek) -> None:
        fake_deepseek.content = scores_payload(
            [{"fragment_id": 0, "skill": "Python", "strength": "很高"}]
        )
        result = DeepSeekEvidenceScorer(API_KEY).score(fragments(1))
        assert result.items[0].strength == "很高"

    def test_missing_fields_do_not_raise(self, fake_deepseek: FakeDeepSeek) -> None:
        fake_deepseek.content = scores_payload([{}, {"fragment_id": 1}])
        result = DeepSeekEvidenceScorer(API_KEY).score(fragments(2))
        assert len(result.items) == 2

    def test_non_object_entries_are_dropped_quietly(self, fake_deepseek: FakeDeepSeek) -> None:
        """不是对象的条目连桶都进不去（§8 按条目对象计），这里先滤掉并留日志。"""
        fake_deepseek.content = json.dumps({"scores": ["Python", 3, None]})
        result = DeepSeekEvidenceScorer(API_KEY).score(fragments(1))
        assert result.items == []

    def test_fenced_json_is_unwrapped(self, fake_deepseek: FakeDeepSeek) -> None:
        fake_deepseek.content = (
            "```json\n"
            + scores_payload([{"fragment_id": 0, "skill": "Python", "strength": 0.8}])
            + "\n```"
        )
        result = DeepSeekEvidenceScorer(API_KEY).score(fragments(1))
        assert result.items[0].strength == 0.8


# ------------------------------------------------------------------ 脱敏


class TestNothingSensitiveLeaves:
    def test_key_never_reaches_logs(
        self, fake_deepseek: FakeDeepSeek, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake_deepseek.content = "not json"
        with caplog.at_level(logging.DEBUG), pytest.raises(EvidenceScoreBadOutputError):
            DeepSeekEvidenceScorer(API_KEY).score(fragments(1))
        assert API_KEY not in caplog.text
        # 片段正文也不进日志：只记长度与类型名。
        assert "P99" not in caplog.text

    def test_prompt_version_is_exposed_for_the_meta_field(self) -> None:
        assert EVIDENCE_PROMPT_VERSION == "v1"
        assert "fragment_id" in build_evidence_prompt()
