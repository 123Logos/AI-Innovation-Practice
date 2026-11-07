"""人岗匹配端点的集成测试（ADR-013 §3/§6.5/§10 第 7c–7d 步）。

覆盖的是「一条从路由到仓储的链路」，不是纯函数（那部分在 `test_match_scoring.py`）：

1. 契约形状：五项分项、三档清单、`meta` 的对账数字；
2. 权限：不可见岗位 **404 而不是 403**（403 会确认「该 id 存在」）；
3. **§3 的状态码边界**：五种 AI 退化全部是 200，且**没有一个**进 `app/main.py` 的 AI 处理器
   ——这条是本端点与润色/提取唯一的语义差别，也是实现时最容易写反的地方；
4. §6 的「不发清单」：交给模型的片段里不含姓名/邮箱/手机号/学校/年级；
5. 反幻觉闸门：`covered[].evidence[].snippet` 是**库中经历原文**的连续子串；
6. **限流键按用户而不是按 IP**（§13 第 1 条）。TestClient 的 `client.host` 恒为 `testclient`
   （见 `tests/conftest.py:52-57`），所以这条用例同时证明了「依赖先于限流装饰器执行」——
   如果那个次序不成立，键会静默退回 IP，这条用例就会红。

AI 一律用假实现（AGENTS.md §4.4）：monkeypatch 的是 `dependencies` 里那个符号，
所以装配路径（「有 Key 才装 scorer」）走的仍是真实代码，被测的不是替身本身。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.rate_limit import MATCH_RATE_LIMIT, limiter
from app.integrations.evidence_scorer import (
    EvidenceScoreAuthError,
    EvidenceScoreBadOutputError,
    EvidenceScoreEmptyContentError,
    EvidenceScores,
    EvidenceScoreTimeoutError,
    EvidenceScoreTruncatedError,
    EvidenceScoreUnavailableError,
    ScoredFragment,
    ScoringFragment,
)
from app.services import dependencies

PROFILES_URL = "/api/v1/profiles/me"
JOBS_URL = "/api/v1/jobs"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"

#: 一份能同时喂给「技能覆盖」和「证据强度」的经历文本：三条技能名都真的写在原文里，
#: 这样 §3 的子串核验才有可核的对象，而不是靠断言绕过。
EXPERIENCE = {
    "title": "校园二手交易平台",
    "start_date": "2024-03-01",
    "end_date": "2024-06-30",
    "description": "用 Python 和 FastAPI 搭建后端接口，MySQL 存商品与订单",
    "responsibility": "负责接口设计与性能优化，把列表接口 P99 从 800ms 降到 120ms",
}

STUDENT_SKILLS = [{"name": "Python"}, {"name": "FastAPI"}, {"name": "Docker"}]

#: 岗位侧要求四条技能，其中 Docker 在经历里**不出现** → 稳定的「一条缺口」对照面。
JOB_SKILLS = [
    {"name": "Python", "type": "hard"},
    {"name": "FastAPI", "type": "hard"},
    {"name": "MySQL", "type": "domain"},
    {"name": "Docker", "type": "hard"},
]


def _register_and_login(client: TestClient, prefix: str = "match") -> str:
    username = f"{prefix}_{uuid4().hex[:10]}"
    client.post(
        REGISTER_URL,
        json={"username": username, "password": PASSWORD, "email": f"{username}@example.com"},
    )
    logged_in = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    return logged_in.json()["data"]["access_token"]


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _put_profile(client: TestClient, token: str, **overrides: Any) -> None:
    payload = {
        "full_name": "张小明",
        "email": "zhang@example.com",
        "contact": "13800138000",
        "target_role": "Python 后端开发工程师",
        "skills": STUDENT_SKILLS,
        "experiences": [EXPERIENCE],
        "educations": [
            {
                "school": "示例大学",
                "major": "计算机科学与技术",
                "degree": "本科",
                "edu_start": "2021-09-01",
                "edu_end": "2025-06-30",
            }
        ],
    }
    payload.update(overrides)
    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))
    assert response.status_code == 200, response.text


def _create_published_job(client: TestClient, token: str, **overrides: Any) -> str:
    payload = {
        "company_name": "某支付平台",
        "title": "Python 后端开发工程师",
        "location": "杭州",
        "employment_type": "internship",
        "description": "负责支付网关后端接口开发，需要 Python、FastAPI、MySQL 与 Docker。",
        "salary_min": "200",
        "salary_max": "300",
        "experience_required": None,
        "deadline": "2026-12-22",
        "skills": JOB_SKILLS,
    }
    payload.update(overrides)
    created = client.post(JOBS_URL, json=payload, headers=_headers(token))
    assert created.status_code == 201, created.text
    job_id = created.json()["data"]["job"]["id"]
    published = client.put(f"{JOBS_URL}/{job_id}/publish", headers=_headers(token))
    assert published.status_code == 200, published.text
    return job_id


def match_url(job_id: str) -> str:
    return f"{JOBS_URL}/{job_id}/match"


class FakeScorer:
    """可编程的证据打分替身：按脚本回分或抛异常，并记下收到的片段。"""

    def __init__(self, api_key: str, http_client: Any = None) -> None:
        self.api_key = api_key
        self.sent: list[ScoringFragment] = []
        self.mode = "scores"

    def score(self, fragments: list[ScoringFragment]) -> EvidenceScores:
        self.sent.extend(fragments)
        if self.mode == "auth":
            raise EvidenceScoreAuthError("Key 无效")
        if self.mode == "unavailable":
            raise EvidenceScoreUnavailableError("上游 5xx")
        if self.mode == "timeout":
            raise EvidenceScoreTimeoutError("连不上")
        if self.mode == "bad_output":
            raise EvidenceScoreBadOutputError("不是 JSON")
        if self.mode == "empty":
            raise EvidenceScoreEmptyContentError("空内容")
        if self.mode == "truncated":
            raise EvidenceScoreTruncatedError("被切断", finish_reason="length")
        return EvidenceScores(
            items=[
                ScoredFragment(fragment_id=item.fragment_id, skill=item.skill, strength=0.9)
                for item in fragments
            ],
            finish_reason="stop",
        )


@pytest.fixture
def fake_scorer(monkeypatch: pytest.MonkeyPatch) -> FakeScorer:
    """把装配点上的真实适配器换成**同一个**替身实例。

    只换 `dependencies` 里那个符号，所以「有 Key 才装 scorer、没 Key 装 None」这条真实分支
    仍然由被测代码走（AGENTS.md §4.4：外部依赖用 fake 替换，但装配路径不许被替身代掉）。
    """
    fake = FakeScorer("sk-fake")
    monkeypatch.setattr(dependencies, "DeepSeekEvidenceScorer", lambda api_key: fake)
    return fake


# ---------------------------------------------------------------- 契约形状


def test_match_returns_score_terms_and_three_lists(client: TestClient) -> None:
    token = _register_and_login(client)
    _put_profile(client, token)
    job_id = _create_published_job(client, token)

    response = client.post(match_url(job_id), headers=_headers(token))
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["weights_version"] == "v1"
    assert {term["name"] for term in data["terms"]} == {
        "coverage",
        "evidence",
        "relevance",
        "preference",
        "remaining_time",
    }
    # 岗位要求 4 条，学生技能命中 3 条（MySQL 没学过）。
    coverage = next(term for term in data["terms"] if term["name"] == "coverage")
    assert coverage["value"] == pytest.approx(3 / 4, abs=0.001)
    # 三档必须把「命中但零证据」的 Docker 装在 partial 里：它既不是 covered 也不是 gap。
    assert [gap["skill"] for gap in data["gaps"]] == ["MySQL"]
    assert "Docker" in {item["skill"] for item in data["partial"]}
    assert {item["skill"] for item in data["covered"]} <= {"Python", "FastAPI"}
    assert data["score"] > 0


def test_evidence_snippets_are_substrings_of_the_stored_text(client: TestClient) -> None:
    """§3 的反幻觉闸门，端到端版本：核的是**库里的原文**，不是内存里的构造。"""
    token = _register_and_login(client)
    _put_profile(client, token)
    job_id = _create_published_job(client, token)

    stored = client.get(PROFILES_URL, headers=_headers(token)).json()["data"]["profile"]
    original = (
        stored["experiences"][0]["title"]
        + stored["experiences"][0]["description"]
        + stored["experiences"][0]["responsibility"]
    )
    data = client.post(match_url(job_id), headers=_headers(token)).json()["data"]
    assert data["covered"], "这份数据本该至少有一条覆盖"
    for covered in data["covered"]:
        for evidence in covered["evidence"]:
            assert evidence["snippet"] in original


def test_unmeasurable_relevance_is_visible_in_meta_but_not_in_score(client: TestClient) -> None:
    """§7.3.1：v1 这一项恒不参与，`weight_sum_used` 如实少 0.15（老板裁定它不进 UI，但必须在 API 里）。"""
    token = _register_and_login(client)
    _put_profile(client, token)
    job_id = _create_published_job(client, token)

    meta = client.post(match_url(job_id), headers=_headers(token)).json()["data"]["meta"]
    assert meta["weight_sum_used"] == pytest.approx(0.85)


def test_match_is_deterministic_for_the_same_inputs(client: TestClient) -> None:
    """分数不落库的代价必须是「可复现」而不是「每次不一样」：同一份数据两次点击同分。"""
    token = _register_and_login(client)
    _put_profile(client, token)
    job_id = _create_published_job(client, token)

    first = client.post(match_url(job_id), headers=_headers(token)).json()["data"]
    second = client.post(match_url(job_id), headers=_headers(token)).json()["data"]
    assert first["score"] == second["score"]
    assert first["terms"] == second["terms"]


def test_response_names_the_profile_that_was_scored(client: TestClient) -> None:
    """§13 第 7 条：请求体为空 ⇒ 用哪份档案是隐式选择，必须回显。"""
    token = _register_and_login(client)
    _put_profile(client, token)
    job_id = _create_published_job(client, token)

    profile = client.get(PROFILES_URL, headers=_headers(token)).json()["data"]["profile"]
    data = client.post(match_url(job_id), headers=_headers(token)).json()["data"]
    assert data["profile_id"] == profile["id"]
    assert data["profile_title"] == profile["title"]
    assert data["job_id"] == job_id


# -------------------------------------------------------------------- 权限


def test_other_users_draft_is_404_not_403(client: TestClient) -> None:
    owner = _register_and_login(client, prefix="match_owner")
    viewer = _register_and_login(client, prefix="match_viewer")
    _put_profile(client, viewer)
    draft_payload = {
        "company_name": "某教育科技公司",
        "title": "算法实习生",
        "location": "北京",
        "employment_type": "internship",
        "description": "负责模型微调，需要 Python。",
        "salary_min": None,
        "salary_max": None,
        "experience_required": None,
        "deadline": None,
        "skills": [{"name": "Python", "type": "hard"}],
    }
    created = client.post(JOBS_URL, json=draft_payload, headers=_headers(owner))
    job_id = created.json()["data"]["job"]["id"]

    blocked = client.post(match_url(job_id), headers=_headers(viewer))
    assert blocked.status_code == 404, blocked.text
    assert blocked.json()["error"]["code"] == "NOT_FOUND"


def test_trashed_job_is_404(client: TestClient) -> None:
    token = _register_and_login(client)
    _put_profile(client, token)
    job_id = _create_published_job(client, token)
    client.delete(f"{JOBS_URL}/{job_id}", headers=_headers(token))

    assert client.post(match_url(job_id), headers=_headers(token)).status_code == 404


def test_missing_profile_still_scores(client: TestClient) -> None:
    """没填档案的人不该拿到 500：技能集为空是「真话」，不是异常。"""
    token = _register_and_login(client)
    job_id = _create_published_job(client, token)

    response = client.post(match_url(job_id), headers=_headers(token))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["profile_id"] is None
    assert data["terms"][0]["value"] == 0.0


def test_unauthenticated_is_401(client: TestClient) -> None:
    token = _register_and_login(client)
    job_id = _create_published_job(client, token)

    assert client.post(match_url(job_id)).status_code == 401


# ------------------------------------------------------- §6.5 五种退化都是 200


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("auth", "key_invalid"),
        ("unavailable", "upstream"),
        ("timeout", "timeout"),
        ("bad_output", "bad_output"),
        ("empty", "bad_output"),
        ("truncated", "bad_output"),
    ],
)
def test_ai_failures_degrade_to_200_not_an_error_envelope(
    client: TestClient, fake_scorer: FakeScorer, mode: str, reason: str
) -> None:
    """§3 的状态码边界：本端点**不**因为 AI 失败返回 400/401/502。

    断言里同时核「没有 `AI_*` 错误码」——那三个处理器如果被命中，响应会变成错误信封，
    而 `data` 直接消失。这条就是防止实现时让异常冒到全局处理器。
    """
    token = _register_and_login(client)
    _put_profile(client, token)
    job_id = _create_published_job(client, token)

    fake_scorer.mode = mode
    response = client.post(
        match_url(job_id), headers=_headers(token, **{"X-DeepSeek-Key": "sk-fake"})
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "data" in body, body
    assert body["data"]["meta"]["evidence_mode"] == "keyword_fallback"
    assert body["data"]["meta"]["degrade_reason"] == reason
    assert body["data"]["meta"]["model"] is None


def test_no_key_header_is_key_missing_not_a_400(client: TestClient) -> None:
    """没带 Key 时匹配照样给分（与润色/提取的唯一语义差别）。"""
    token = _register_and_login(client)
    _put_profile(client, token)
    job_id = _create_published_job(client, token)

    body = client.post(match_url(job_id), headers=_headers(token)).json()
    assert body["data"]["meta"]["degrade_reason"] == "key_missing"


def test_ai_success_marks_ai_scored_and_counts_pairs(
    client: TestClient, fake_scorer: FakeScorer
) -> None:
    token = _register_and_login(client)
    _put_profile(client, token)
    job_id = _create_published_job(client, token)

    body = client.post(
        match_url(job_id), headers=_headers(token, **{"X-DeepSeek-Key": "sk-fake"})
    ).json()
    meta = body["data"]["meta"]
    assert meta["evidence_mode"] == "ai_scored"
    assert meta["degrade_reason"] is None
    assert meta["model"] == "deepseek-flash"
    # 候选侧分区必须精确闭合（§8 更正版）。
    assert meta["candidate_pairs"] == meta["verified_pairs"] + meta["dropped_missing_output"]
    assert meta["candidate_pairs"] > 0


def test_zero_candidates_never_calls_the_model(client: TestClient, fake_scorer: FakeScorer) -> None:
    """技能零命中 → 没有候选对 → 一次都不该花用户的额度。"""
    token = _register_and_login(client)
    _put_profile(client, token, skills=[{"name": "Excel"}])
    job_id = _create_published_job(client, token)

    body = client.post(
        match_url(job_id), headers=_headers(token, **{"X-DeepSeek-Key": "sk-fake"})
    ).json()
    assert body["data"]["meta"]["candidate_pairs"] == 0
    assert body["data"]["meta"]["model"] is None


# ------------------------------------------------------- §6 隐私不发清单


def test_fragments_sent_to_model_carry_only_id_skill_text(
    client: TestClient, fake_scorer: FakeScorer
) -> None:
    token = _register_and_login(client)
    _put_profile(client, token)
    job_id = _create_published_job(client, token)

    client.post(match_url(job_id), headers=_headers(token, **{"X-DeepSeek-Key": "sk-fake"}))
    assert fake_scorer.sent, "没有候选对就没有这条证明"
    blob = json.dumps([fragment.__dict__ for fragment in fake_scorer.sent], ensure_ascii=False)
    for forbidden in ("张小明", "zhang@example.com", "13800138000", "示例大学", "计算机科学与技术"):
        assert forbidden not in blob, forbidden


# ------------------------------------------------------------------ 限流


def test_rate_limit_is_per_user_not_per_ip(client: TestClient) -> None:
    """§13 第 1 条的正证：同一 IP（TestClient 恒为 `testclient`）下两个用户互不挤占。

    这条同时证明了「鉴权依赖先于限流装饰器执行」：次序不成立时键会退回 IP，
    第 11 次请求就会把 B 也一起 429 掉。
    """
    limit = int(MATCH_RATE_LIMIT.split("/")[0])
    token_a = _register_and_login(client, prefix="match_rl_a")
    token_b = _register_and_login(client, prefix="match_rl_b")
    _put_profile(client, token_a)
    _put_profile(client, token_b)
    job_id = _create_published_job(client, token_a)

    limiter.reset()
    limiter.enabled = True
    try:
        for _ in range(limit):
            assert client.post(match_url(job_id), headers=_headers(token_a)).status_code == 200
        blocked = client.post(match_url(job_id), headers=_headers(token_a))
        assert blocked.status_code == 429, blocked.text
        # 关键一步：A 已经超限，B 在同一次测试里必须还能点。
        assert client.post(match_url(job_id), headers=_headers(token_b)).status_code == 200
    finally:
        limiter.enabled = False
        limiter.reset()


# ------------------------------------------------------------ 路由表闭合


def test_only_the_match_route_under_jobs_forwards_to_deepseek() -> None:
    """§6.1 第 3 条：能被 BYOK 正则命中的**已注册** POST 路由，必须就是匹配端点。

    这条守的是未来：有人新加一条 `/jobs/{a}/{b}` 形状而不转发 AI 的路由时，
    前端那条白名单正则就会把用户的 Key 送到一个不发 DeepSeek 的处理器上。
    判据用注册路由集合而不是「猜函数体」。
    """
    import re

    from app.main import app

    pattern = re.compile(r"^/api/v1/jobs/[^/]+/match$")
    paths = app.openapi()["paths"]
    matched = sorted(path for path, item in paths.items() if "post" in item and pattern.match(path))
    assert matched == ["/api/v1/jobs/{job_id}/match"]

    # 反向：`/jobs` 下其余 POST 路由都不该被这条正则命中。
    others = sorted(
        path
        for path, item in paths.items()
        if "post" in item and path.startswith("/api/v1/jobs") and path not in matched
    )
    assert all(not pattern.match(path) for path in others), others
    assert "/api/v1/jobs/extract-skills" in others


def test_dependency_wires_one_session_for_both_repositories() -> None:
    """§13 第 8 条的结构约束：一个 DI 函数、一个 session、两个仓储。"""
    # 不看源码看行为：两个仓储拿到的是**同一个** session 对象。
    # 用 `inspect.getsource` 断言字符串会被 docstring 里的同名句子骗过（第一版就是这么错的）。
    from fastapi import Request

    from app.services.dependencies import get_match_service

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/jobs/x/match",
            "headers": [(b"x-deepseek-key", b"sk-fake")],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
        }
    )
    service = next(get_match_service(request))
    assert service._jobs._session is service._profiles._session


def test_match_service_is_registered_as_a_dependency(client: TestClient) -> None:
    """路由真的走 `get_match_service`，而不是在函数体里自己 new（那样测试会全绿但装配是假的）。"""
    import inspect

    from app.api.v1 import jobs

    source = inspect.getsource(jobs)
    assert "Depends(get_match_service)" in source
    assert "MatchServiceDep" in source
