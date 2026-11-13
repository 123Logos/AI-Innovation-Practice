"""标准化题库测评与 AI 画像的集成测试（FR-04 / B9，`ADR-028` §4）。

用例名与 `ADR-028` §4 的验收表**逐字一致**（表里的一行 = 这里的一个函数），
另外补了五条表里没列、但边界必须钉住的：未登录 401、没有档案不能开考 400、
拿别人的档案开考 404、选项不合法 400、以及「送进模型的只有标签」这条隐私边界（§2.5）。

三层验证，缺一层就会留下「看起来对」的洞：

1. **响应体扫字段**：「标准答案永不出现在响应里」只能靠**扫 JSON**证明。
   `AssessmentQuestionRead` 少一个字段这件事没有编译器管——某天有人给它加个
   `answer_key: str | None = None` 或者让路由直接 `model_dump(mode="json")` 一个 ORM 对象，
   测评当场变开卷，而不会有任何报错；
2. **直接查库**：分数、标签、来源升级、`ai_summary_source` 都以库里的值为准。
   `AssessmentSessionRead` 每个字段都有默认值，响应里出现 `score=100` 完全可能只是
   pydantic 兜了默认值、库里仍是 NULL；
3. **确定性**：判分用例既跑「全对」也跑「按技能故意答错几题」。只跑全对的话，
   「提交即满分」这种实现也能绿——那正是这一批最容易写错的地方（判分口径在
   `app/core/assessment_rules.py`，服务层不该重算一遍）。

关于「测试里读标准答案」：`_bank()` 直接读 `assessment_questions.answer_key`，这不是
绕过边界，而是判分的**基准**——用它算出的期望分与实现对不上，就说明判分错了。
「接口会不会把答案漏给客户端」由 `test_catalog_never_leaks_answer_keys` 与
`test_start_session_returns_questions_without_answers` 单独守，与本文件其余用例互不代替。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.integrations.evidence_scorer import EvidenceScoreUnavailableError
from app.repositories.user_repository import get_engine
from app.services import dependencies
from tests.accounts import login, register

API = "/api/v1"
ASSESSMENTS_URL = f"{API}/assessments"
SESSIONS_URL = f"{ASSESSMENTS_URL}/sessions"
PROFILE_URL = f"{ASSESSMENTS_URL}/profile"
PROFILES_URL = f"{API}/profiles/me"

#: 假 BYOK 密钥。带不带它决定 `get_assessment_service` 装不装 summarizer（与真实依赖同构）。
FAKE_DEEPSEEK_KEY = "sk-fake"

#: 迁移 `0026` 的种子题库：4 项技能 × 3 题。写死在这里是有意的——
#: 断言「12 题 / 4 个技能」时若跟着实现读库取数，题库被删空了也是一片绿。
SEED_QUESTION_COUNT = 12
SEED_SKILLS = ("Git", "Python", "React", "SQL")


# --------------------------------------------------------------------------- #
# 帮手：账号、档案、题库、答案
# --------------------------------------------------------------------------- #


def _headers(client: TestClient, prefix: str = "b9") -> dict[str, str]:
    username = f"{prefix}_{uuid4().hex[:10]}"
    created = register(client, username, email=f"{username}@example.com")
    assert created.status_code == 201, created.text
    return login(client, username)


def _with_key(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "X-DeepSeek-Key": FAKE_DEEPSEEK_KEY}


def _profile(client: TestClient, headers: dict[str, str], skills: list[str]) -> dict[str, Any]:
    """建（或覆盖）一份最小可保存档案，返回档案对象。

    测评必须挂在某份档案上（`ADR-028` §2.4：画像聚合的是档案的技能），
    所以除了「没有档案 → 400」那个用例，其余用例都要先走这一步。
    """
    response = client.put(
        PROFILES_URL,
        json={
            "full_name": "张三",
            "contact": "13800000000",
            "skills": [{"name": name} for name in skills],
            "experiences": [],
            "educations": [],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    profile: dict[str, Any] = response.json()["data"]["profile"]
    return profile


def _read_profile(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get(PROFILES_URL, headers=headers)
    assert response.status_code == 200, response.text
    profile: dict[str, Any] = response.json()["data"]["profile"]
    return profile


def _start(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(SESSIONS_URL, json={}, headers=headers)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _put_answers(
    client: TestClient, headers: dict[str, str], session_id: str, answers: list[dict[str, str]]
) -> Any:
    return client.put(
        f"{SESSIONS_URL}/{session_id}/answers", json={"answers": answers}, headers=headers
    )


def _submit(client: TestClient, headers: dict[str, str], session_id: str) -> Any:
    return client.post(f"{SESSIONS_URL}/{session_id}/submit", headers=headers)


def _db(sql: str, **params: object) -> list[dict[str, Any]]:
    """只读查库（断言用）。表结构与迁移一一对应，这里不重复声明任何列。"""
    with get_engine().connect() as connection:
        rows = connection.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def _bank() -> dict[str, dict[str, Any]]:
    """题库全量：`question_id → {skill_name, answer_key, option_keys}`。

    `options` 是 JSON 列，走 `text()` 时不经过 SQLAlchemy 的类型处理，
    驱动可能给回字符串也可能给回对象，两种都接住。
    """
    rows = _db("SELECT id, skill_name, answer_key, options FROM assessment_questions")
    bank: dict[str, dict[str, Any]] = {}
    for row in rows:
        options = row["options"]
        if isinstance(options, str):
            options = json.loads(options)
        bank[str(row["id"])] = {
            "skill_name": str(row["skill_name"]),
            "answer_key": str(row["answer_key"]),
            "option_keys": [str(option["key"]) for option in options],
        }
    return bank


def _wrong_key(question: dict[str, Any]) -> str:
    return next(key for key in question["option_keys"] if key != question["answer_key"])


def _answers(
    bank: dict[str, dict[str, Any]], correct_by_skill: dict[str, int]
) -> list[dict[str, str]]:
    """按技能指定「这个技能答对几题」，其余挑错项。

    按 `(技能名, 题 id)` 排序后取前 N 题答对，保证同一份输入永远同一份答案——
    判分是确定性的，测试的输入也必须确定性，否则「偶发的红」会被当成环境问题。
    """
    grouped: dict[str, list[str]] = {}
    for question_id, info in bank.items():
        grouped.setdefault(info["skill_name"], []).append(question_id)

    answers: list[dict[str, str]] = []
    for skill_name in sorted(grouped):
        question_ids = sorted(grouped[skill_name])
        correct = correct_by_skill.get(skill_name, 0)
        for index, question_id in enumerate(question_ids):
            question = bank[question_id]
            chosen = question["answer_key"] if index < correct else _wrong_key(question)
            answers.append({"question_id": question_id, "chosen_key": chosen})
    return answers


def _all_correct() -> list[dict[str, str]]:
    """每一项技能都答对它的全部题（种子题库是 4 技能 × 3 题）。"""
    return _answers(_bank(), {skill: 3 for skill in SEED_SKILLS})


def _tags_by_name(payload: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["tag"]): item for item in payload}


def _db_tags(profile_id: str) -> list[dict[str, Any]]:
    return _db(
        "SELECT tag, status, score, source, evidence_count, session_id, created_at, updated_at "
        "FROM profile_tags WHERE student_profile_id = :profile_id ORDER BY tag",
        profile_id=profile_id,
    )


def _db_skill_sources(profile_id: str) -> dict[str, str]:
    """活跃技能行的来源（软删除的历史行不算：用户看到的技能只有这些）。"""
    rows = _db(
        "SELECT name, source FROM skills "
        "WHERE student_profile_id = :profile_id AND deleted_at IS NULL ORDER BY name",
        profile_id=profile_id,
    )
    return {str(row["name"]): str(row["source"]) for row in rows}


class _FakeSummarizer:
    """可注入的假画像摘要器：记录 prompt、可被要求抛上游异常。"""

    def __init__(self, *, text: str = "你在 Python 与 SQL 上已有可验证的基础。") -> None:
        self.text = text
        self.raise_error: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    def summarize(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.raise_error is not None:
            raise self.raise_error
        return self.text


@pytest.fixture
def summarizer(monkeypatch: pytest.MonkeyPatch) -> _FakeSummarizer:
    """把装配点上的真实适配器换成同一个假实例。

    monkeypatch 的是 `dependencies` 里的符号，所以「有 Key 才装」这条装配路径走的
    仍是真实代码——被测的不是替身本身（与 `tests/test_interviews.py` 的 `ai_coach` 同构）。
    """
    fake = _FakeSummarizer()
    monkeypatch.setattr(dependencies, "DeepSeekProfileSummarizer", lambda api_key: fake)
    return fake


# --------------------------------------------------------------------------- #
# 鉴权与前置条件
# --------------------------------------------------------------------------- #


def test_assessment_requires_auth(client: TestClient) -> None:
    """一组端点全部要登录：未登录一律 401（不是 404，也不能读别人的题库余额）。"""
    for method, url in (
        ("get", ASSESSMENTS_URL),
        ("post", SESSIONS_URL),
        ("get", PROFILE_URL),
        ("get", f"{SESSIONS_URL}/{uuid4()}"),
        ("post", f"{SESSIONS_URL}/{uuid4()}/submit"),
    ):
        response = getattr(client, method)(url)
        assert response.status_code == 401, f"{method} {url}: {response.text}"


def test_start_session_requires_a_profile(client: TestClient) -> None:
    """没有档案 → 400 `PROFILE_REQUIRED`。

    不给「凭空造一份测评专用档案」：画像要聚合档案的技能（`ADR-028` §2.4），
    造出来的档案会让画像页与简历页对不上。
    """
    headers = _headers(client, "b9np")
    response = client.post(SESSIONS_URL, json={}, headers=headers)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"]["reason"] == "PROFILE_REQUIRED"


def test_start_session_with_other_users_profile_is_not_found(client: TestClient) -> None:
    """拿别人的 `profile_id` 开考 → 404（不透露那份档案是否存在）。"""
    headers_a = _headers(client, "b9pa")
    profile_a = _profile(client, headers_a, ["Python"])
    headers_b = _headers(client, "b9pb")
    _profile(client, headers_b, ["Python"])

    response = client.post(SESSIONS_URL, json={"profile_id": profile_a["id"]}, headers=headers_b)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


# --------------------------------------------------------------------------- #
# 1. 题库与出题：标准答案永不上桌
# --------------------------------------------------------------------------- #


def test_catalog_never_leaks_answer_keys(client: TestClient) -> None:
    """目录只回答「能测什么」：没有题干、没有选项、更没有任何一处 `answer_key`。

    扫的是**整个响应体**而不是逐字段：答案一旦漏出，形式可能是一个新字段、
    也可能是 `details` 里的一句调试信息，逐字段断言抓不到后者。
    """
    headers = _headers(client, "b9cat")
    response = client.get(ASSESSMENTS_URL, headers=headers)
    assert response.status_code == 200, response.text

    catalog = response.json()["data"]["catalog"]
    assert catalog["bank_version"] == "v1"
    assert catalog["total_questions"] == SEED_QUESTION_COUNT
    assert tuple(sorted(skill["skill_name"] for skill in catalog["skills"])) == SEED_SKILLS
    assert all(skill["question_count"] == 3 for skill in catalog["skills"])
    # 目录行的字段集合是封闭的：多出 `prompt` / `options` 都算泄露题干。
    assert all(
        set(skill) == {"skill_key", "skill_name", "question_count"} for skill in catalog["skills"]
    )
    assert "answer_key" not in response.text


def test_start_session_returns_questions_without_answers(client: TestClient) -> None:
    """开考 201：题目可读、可作答，但 `answer_key` 不在响应里（详情页同一条）。"""
    headers = _headers(client, "b9start")
    _profile(client, headers, ["Python"])
    response = client.post(SESSIONS_URL, json={}, headers=headers)
    assert response.status_code == 201, response.text

    data = response.json()["data"]
    session = data["session"]
    questions = data["questions"]

    assert session["status"] == "in_progress"
    assert session["question_count"] == SEED_QUESTION_COUNT
    # 还没判分：两个分数字段都必须是 NULL，不能是 0（0 会被读成「一分没得」）。
    assert session["correct_count"] is None
    assert session["score"] is None
    assert session["submitted_at"] is None
    assert len(questions) == SEED_QUESTION_COUNT
    assert all(set(question) == {"id", "skill_name", "prompt", "options"} for question in questions)
    assert all(
        set(option) == {"key", "label"} for question in questions for option in question["options"]
    )
    assert "answer_key" not in response.text

    detail = client.get(f"{SESSIONS_URL}/{session['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["session"]["id"] == session["id"]
    # 题序必须与开考时一致：详情走的是 `WHERE id IN (...)`，SQL 不保证顺序，
    # 少了这层重排，刷新一次就可能换一次题序。
    assert [item["id"] for item in detail.json()["data"]["questions"]] == [
        item["id"] for item in questions
    ]
    assert "answer_key" not in detail.text


# --------------------------------------------------------------------------- #
# 2. 作答：覆盖而不是追加
# --------------------------------------------------------------------------- #


def test_answers_are_overwritten_not_appended(client: TestClient) -> None:
    """同一题改答案 → 覆盖；分批写 → 只动本批涉及的题（`ADR-028` §2.3）。"""
    headers = _headers(client, "b9ans")
    _profile(client, headers, ["Python"])
    session_id = _start(client, headers)["session"]["id"]
    bank = _bank()
    question_id = min(bank)
    question = bank[question_id]
    first, second = question["answer_key"], _wrong_key(question)

    first_write = _put_answers(
        client, headers, session_id, [{"question_id": question_id, "chosen_key": first}]
    )
    assert first_write.status_code == 200, first_write.text
    assert len(first_write.json()["data"]["answers"]) == 1
    # 提交前 `is_correct` 必须是 NULL：判分只在提交那一刻发生，提前算出来等于把答案漏了。
    assert first_write.json()["data"]["answers"][0]["is_correct"] is None

    second_write = _put_answers(
        client, headers, session_id, [{"question_id": question_id, "chosen_key": second}]
    )
    assert second_write.status_code == 200, second_write.text
    answers = second_write.json()["data"]["answers"]
    assert len(answers) == 1
    assert answers[0]["chosen_key"] == second

    # 全量写一遍：总数仍是 12，不是 13 或 14。
    full = _put_answers(client, headers, session_id, _all_correct())
    assert full.status_code == 200, full.text
    assert len(full.json()["data"]["answers"]) == SEED_QUESTION_COUNT

    detail = client.get(f"{SESSIONS_URL}/{session_id}", headers=headers).json()["data"]
    assert len(detail["answers"]) == SEED_QUESTION_COUNT
    rows = _db(
        "SELECT question_id FROM assessment_answers WHERE session_id = :session_id",
        session_id=session_id,
    )
    assert len(rows) == SEED_QUESTION_COUNT
    assert len({row["question_id"] for row in rows}) == SEED_QUESTION_COUNT


def test_answers_reject_unknown_question_and_option(client: TestClient) -> None:
    """题不在本场、或选项不属于该题 → 400，且**不落库**。

    选项校验必须在写库之前：选了不存在的 A/B/C/D，判分时 `is_correct` 恒为 False，
    用户看到的是「我明明选了 A」。
    """
    headers = _headers(client, "b9bad")
    _profile(client, headers, ["Python"])
    session_id = _start(client, headers)["session"]["id"]
    bank = _bank()
    question_id = min(bank)

    unknown_question = _put_answers(
        client, headers, session_id, [{"question_id": str(uuid4()), "chosen_key": "A"}]
    )
    assert unknown_question.status_code == 400, unknown_question.text
    assert unknown_question.json()["error"]["details"]["reason"] == "ASSESSMENT_ANSWER_INVALID"

    unknown_option = _put_answers(
        client, headers, session_id, [{"question_id": question_id, "chosen_key": "Z"}]
    )
    assert unknown_option.status_code == 400, unknown_option.text
    assert unknown_option.json()["error"]["details"]["reason"] == "ASSESSMENT_ANSWER_INVALID"

    rows = _db(
        "SELECT id FROM assessment_answers WHERE session_id = :session_id",
        session_id=session_id,
    )
    assert rows == []


# --------------------------------------------------------------------------- #
# 3. 判分与标签三态
# --------------------------------------------------------------------------- #


def test_submit_grades_and_writes_tags(client: TestClient) -> None:
    """全对 → 100 分 / 4 条 verified；按技能答错 → 分与三态**逐格**对上阈值。

    第二段（B 账号）是这条用例的主体：只验全对的话，「提交即满分」也能绿。
    """
    # --- A：全对 ---------------------------------------------------------
    headers_a = _headers(client, "b9full")
    profile_a = _profile(client, headers_a, ["Python"])
    session_a = _start(client, headers_a)["session"]["id"]
    assert _put_answers(client, headers_a, session_a, _all_correct()).status_code == 200

    submitted = _submit(client, headers_a, session_a)
    assert submitted.status_code == 200, submitted.text
    data = submitted.json()["data"]
    assert data["session"]["status"] == "submitted"
    assert data["session"]["correct_count"] == SEED_QUESTION_COUNT
    assert data["session"]["score"] == 100
    assert data["session"]["submitted_at"] is not None

    tags = _tags_by_name(data["tags"])
    assert tuple(sorted(tags)) == SEED_SKILLS
    for name in SEED_SKILLS:
        assert tags[name]["status"] == "verified", name
        assert tags[name]["score"] == 100, name
        assert tags[name]["evidence_count"] == 3, name

    # 判分结果落在答案行上，且真的进库了（不是只回显在响应里）。
    detail = client.get(f"{SESSIONS_URL}/{session_a}", headers=headers_a).json()["data"]
    assert all(answer["is_correct"] is True for answer in detail["answers"])
    assert {row["status"] for row in _db_tags(profile_a["id"])} == {"verified"}

    # --- B：按技能错开，三态全覆盖 ---------------------------------------
    headers_b = _headers(client, "b9mix")
    profile_b = _profile(client, headers_b, ["Python"])
    session_b = _start(client, headers_b)["session"]["id"]
    # Python 1/3（33%，<0.5 → improving）、SQL 2/3（67%，0.5≤x<0.8 → pending）、
    # React 0/3（improving）、Git 3/3（verified）。
    mixed = _answers(_bank(), {"Python": 1, "SQL": 2, "React": 0, "Git": 3})
    assert _put_answers(client, headers_b, session_b, mixed).status_code == 200

    mixed_submitted = _submit(client, headers_b, session_b)
    assert mixed_submitted.status_code == 200, mixed_submitted.text
    mixed_data = mixed_submitted.json()["data"]
    assert mixed_data["session"]["correct_count"] == 6
    assert mixed_data["session"]["score"] == 50

    expected = {
        "Python": ("improving", 33),
        "SQL": ("pending", 67),
        "React": ("improving", 0),
        "Git": ("verified", 100),
    }
    mixed_tags = _tags_by_name(mixed_data["tags"])
    assert {
        name: (mixed_tags[name]["status"], mixed_tags[name]["score"]) for name in mixed_tags
    } == expected

    # 画像端点读的是同一批标签（不是另算一遍）。
    profile_read = client.get(PROFILE_URL, headers=headers_b)
    assert profile_read.status_code == 200, profile_read.text
    body = profile_read.json()["data"]["profile"]
    assert body["student_profile_id"] == profile_b["id"]
    assert {item["tag"]: (item["status"], item["score"]) for item in body["tags"]} == expected
    assert all(item["source"] == "assessment" for item in body["tags"])


def test_second_submit_is_rejected(client: TestClient) -> None:
    """重复提交 → 409，且分数/提交时间**不变**（不许靠再点一次改分）。"""
    headers = _headers(client, "b9twice")
    _profile(client, headers, ["Python"])
    session_id = _start(client, headers)["session"]["id"]
    _put_answers(client, headers, session_id, _all_correct())

    first = _submit(client, headers, session_id)
    assert first.status_code == 200, first.text
    before = _db(
        "SELECT score, correct_count, submitted_at FROM assessment_sessions WHERE id = :id",
        id=session_id,
    )[0]

    second = _submit(client, headers, session_id)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["details"]["reason"] == "ASSESSMENT_STATE_CONFLICT"

    after = _db(
        "SELECT score, correct_count, submitted_at FROM assessment_sessions WHERE id = :id",
        id=session_id,
    )[0]
    assert after == before
    assert after["score"] == 100


def test_answers_are_frozen_after_submit(client: TestClient) -> None:
    """提交后改答案 → 409，且库里的选择**没被改动**。

    单独一条而不是并进上一条：重复提交被挡住，不代表改答案也被挡住——
    两条路径分别走 `submit` 与 `save_answers`，只测一条会漏掉另一条。
    """
    headers = _headers(client, "b9frozen")
    _profile(client, headers, ["Python"])
    session_id = _start(client, headers)["session"]["id"]
    bank = _bank()
    question_id = min(bank)
    original = bank[question_id]["answer_key"]
    assert (
        _put_answers(
            client, headers, session_id, [{"question_id": question_id, "chosen_key": original}]
        ).status_code
        == 200
    )
    assert _submit(client, headers, session_id).status_code == 200

    frozen = _put_answers(
        client,
        headers,
        session_id,
        [{"question_id": question_id, "chosen_key": _wrong_key(bank[question_id])}],
    )
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["error"]["details"]["reason"] == "ASSESSMENT_STATE_CONFLICT"

    rows = _db(
        "SELECT chosen_key FROM assessment_answers "
        "WHERE session_id = :session_id AND question_id = :question_id",
        session_id=session_id,
        question_id=question_id,
    )
    assert [row["chosen_key"] for row in rows] == [original]


# --------------------------------------------------------------------------- #
# 4. 归属与终态
# --------------------------------------------------------------------------- #


def test_other_users_session_is_not_found(client: TestClient) -> None:
    """别人的会话：五个端点全是 404，不是 403（403 会承认「这个 id 存在」）。"""
    headers_a = _headers(client, "b9owna")
    _profile(client, headers_a, ["Python"])
    session_id = _start(client, headers_a)["session"]["id"]
    assert _put_answers(client, headers_a, session_id, _all_correct()).status_code == 200

    headers_b = _headers(client, "b9ownb")
    _profile(client, headers_b, ["Python"])
    attempts = (
        client.get(f"{SESSIONS_URL}/{session_id}", headers=headers_b),
        _put_answers(client, headers_b, session_id, _all_correct()),
        _submit(client, headers_b, session_id),
        client.post(f"{SESSIONS_URL}/{session_id}/abandon", headers=headers_b),
        client.post(f"{SESSIONS_URL}/{session_id}/summary", headers=headers_b),
    )
    for response in attempts:
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "NOT_FOUND"

    # B 的动作没有污染 A 的会话：仍是 in_progress、没有标签。
    mine = client.get(f"{SESSIONS_URL}/{session_id}", headers=headers_a).json()["data"]
    assert mine["session"]["status"] == "in_progress"
    assert _db_tags(_read_profile(client, headers_a)["id"]) == []


def test_abandoned_session_writes_no_tags(client: TestClient) -> None:
    """放弃：状态变 `abandoned`、`abandoned_at` 有值，**不判分、不写标签**。"""
    headers = _headers(client, "b9aband")
    profile = _profile(client, headers, ["Python"])
    session_id = _start(client, headers)["session"]["id"]
    # 先认真答完再放弃：这条用例要证明的是「答了也不算」，不是「没答所以没标签」。
    assert _put_answers(client, headers, session_id, _all_correct()).status_code == 200

    abandoned = client.post(f"{SESSIONS_URL}/{session_id}/abandon", headers=headers)
    assert abandoned.status_code == 200, abandoned.text
    session = abandoned.json()["data"]["session"]
    assert session["status"] == "abandoned"
    assert session["abandoned_at"] is not None
    assert session["correct_count"] is None
    assert session["score"] is None

    assert _db_tags(profile["id"]) == []
    assert _db_skill_sources(profile["id"]) == {"Python": "self_reported"}

    # 终态不可逆：再放弃、再提交都是 409。
    assert client.post(f"{SESSIONS_URL}/{session_id}/abandon", headers=headers).status_code == 409
    assert _submit(client, headers, session_id).status_code == 409


def test_retake_overwrites_tags(client: TestClient) -> None:
    """重考覆盖同一标签（`UNIQUE(student_profile_id, tag)`），画像不会越长越胖。"""
    headers = _headers(client, "b9retake")
    profile = _profile(client, headers, ["Python"])

    first_session = _start(client, headers)["session"]["id"]
    assert _put_answers(client, headers, first_session, _answers(_bank(), {})).status_code == 200
    assert _submit(client, headers, first_session).status_code == 200
    before = _db_tags(profile["id"])
    assert len(before) == len(SEED_SKILLS)
    assert {row["status"] for row in before} == {"improving"}
    assert {row["score"] for row in before} == {0}

    second_session = _start(client, headers)["session"]["id"]
    assert _put_answers(client, headers, second_session, _all_correct()).status_code == 200
    assert _submit(client, headers, second_session).status_code == 200
    after = _db_tags(profile["id"])

    assert len(after) == len(SEED_SKILLS)  # 覆盖，不是追加
    assert {row["status"] for row in after} == {"verified"}
    assert {row["score"] for row in after} == {100}
    assert {row["session_id"] for row in after} == {second_session}
    # 覆盖是 UPDATE：`created_at` 保留，`updated_at` 前移。若实现写成「先删后插」，
    # 标签的 id 与 created_at 会变，前端列表会跳。
    assert {row["tag"]: row["created_at"] for row in before} == {
        row["tag"]: row["created_at"] for row in after
    }


# --------------------------------------------------------------------------- #
# 5. 与 B7 的接线：只升级**已有**技能、只升 verified
# --------------------------------------------------------------------------- #


def test_verified_tags_upgrade_skill_source(client: TestClient) -> None:
    """verified 的标签把**已有**技能来源升成 `assessment`；未验证的不动、没技能的不新建。

    四格断言各挡一件事：
    - Python 全对 → `assessment`（这是 `ADR-027` §2.2 说的「assessment 只能服务端写」的生产者）；
    - SQL 只对 1 题 → 仍是 `self_reported`（`ADR-028` §2.6 只升 verified，不升「练过」）；
    - Go 没被测过 → 不变（不按「没测过就降级」乱改）；
    - 技能条数仍是 3（React / Git 不会因为测评自动进简历）。
    """
    headers = _headers(client, "b9src")
    profile = _profile(client, headers, ["Python", "SQL", "Go"])
    assert _db_skill_sources(profile["id"]) == {
        "Python": "self_reported",
        "SQL": "self_reported",
        "Go": "self_reported",
    }

    session_id = _start(client, headers)["session"]["id"]
    answers = _answers(_bank(), {"Python": 3, "SQL": 1})
    assert _put_answers(client, headers, session_id, answers).status_code == 200
    submitted = _submit(client, headers, session_id)
    assert submitted.status_code == 200, submitted.text

    assert _db_skill_sources(profile["id"]) == {
        "Python": "assessment",
        "SQL": "self_reported",
        "Go": "self_reported",
    }
    read_back = _read_profile(client, headers)
    assert len(read_back["skills"]) == 3
    assert {skill["name"]: skill["source"] for skill in read_back["skills"]} == {
        "Python": "assessment",
        "SQL": "self_reported",
        "Go": "self_reported",
    }
    # 来源升级不碰用户声明的事实：`last_used_on` 仍是空（没人说过「最近用过」）。
    assert all(skill["last_used_on"] is None for skill in read_back["skills"])


# --------------------------------------------------------------------------- #
# 6. AI 摘要与降级
# --------------------------------------------------------------------------- #


def _submitted_session(client: TestClient, headers: dict[str, str]) -> str:
    _profile(client, headers, ["Python"])
    session_id = _start(client, headers)["session"]["id"]
    assert _put_answers(client, headers, session_id, _all_correct()).status_code == 200
    assert _submit(client, headers, session_id).status_code == 200
    return session_id


def test_summary_falls_back_without_a_key(client: TestClient) -> None:
    """没有 Key → 200 + `source='fallback'` + 一段事实完整的话（`ADR-028` §2.5）。"""
    headers = _headers(client, "b9nokey")
    session_id = _submitted_session(client, headers)

    response = client.post(f"{SESSIONS_URL}/{session_id}/summary", headers=headers)
    assert response.status_code == 200, response.text
    summary = response.json()["data"]["summary"]
    assert summary["source"] == "fallback"
    assert summary["note"] and "自动整理" in summary["note"]
    # 事实一个字都不少：题数、答对数、按三态分组的技能名。
    assert f"共 {SEED_QUESTION_COUNT} 题" in summary["summary"]
    assert f"答对 {SEED_QUESTION_COUNT} 题" in summary["summary"]
    assert "已验证" in summary["summary"]
    assert "Python" in summary["summary"]

    # 画像页读到的是同一份：降级原因随摘要一起返回，前端据此换标题。
    profile_read = client.get(PROFILE_URL, headers=headers).json()["data"]["profile"]
    assert profile_read["latest_session_id"] == session_id
    assert profile_read["latest_summary"] == summary["summary"]
    assert profile_read["latest_summary_source"] == "fallback"


def test_fallback_summary_is_labelled_as_fallback(
    client: TestClient, summarizer: _FakeSummarizer
) -> None:
    """有 Key 但上游挂了 → 仍是 200，但 `ai_summary_source` 必须落 `fallback`。

    「降级不伪装成 AI」是可查的事实而不是措辞偏好：这条断言落在**库里那一列**上，
    因为它就是前端换标题、排障时问「这段是谁写的」的唯一依据。
    """
    summarizer.raise_error = EvidenceScoreUnavailableError("AI 服务暂时不可用。")
    headers = _headers(client, "b9down")
    session_id = _submitted_session(client, headers)

    response = client.post(f"{SESSIONS_URL}/{session_id}/summary", headers=_with_key(headers))
    assert response.status_code == 200, response.text
    summary = response.json()["data"]["summary"]
    assert summary["source"] == "fallback"
    assert summary["note"] and "暂时不可用" in summary["note"]
    assert summarizer.calls, "有 Key 时必须真的调用过适配器，否则这条用例只验了无 Key 分支"

    rows = _db(
        "SELECT ai_summary, ai_summary_source FROM assessment_sessions WHERE id = :id",
        id=session_id,
    )
    assert rows[0]["ai_summary_source"] == "fallback"
    assert rows[0]["ai_summary_source"] != "ai"
    assert rows[0]["ai_summary"] == summary["summary"]


def test_summary_uses_ai_when_available(client: TestClient, summarizer: _FakeSummarizer) -> None:
    """AI 正常 → `source='ai'`、`note` 为空，且送进模型的**只有标签**。

    最后那段是 `ADR-028` §2.5 的隐私边界：画像要把数据交给第三方，那就只给需要的那几格。
    档案里明明有姓名和手机号，prompt 里一个都不许出现。
    """
    summarizer.text = "你在 Python 上已有可验证的基础，可以继续做项目来加深它。"
    headers = _headers(client, "b9ai")
    session_id = _submitted_session(client, headers)

    response = client.post(f"{SESSIONS_URL}/{session_id}/summary", headers=_with_key(headers))
    assert response.status_code == 200, response.text
    summary = response.json()["data"]["summary"]
    assert summary["source"] == "ai"
    assert summary["note"] is None
    assert summary["summary"] == summarizer.text

    rows = _db(
        "SELECT ai_summary, ai_summary_source FROM assessment_sessions WHERE id = :id",
        id=session_id,
    )
    assert rows[0]["ai_summary_source"] == "ai"
    assert rows[0]["ai_summary"] == summarizer.text

    system_prompt, user_prompt = summarizer.calls[0]
    for prompt in (system_prompt, user_prompt):
        assert "13800000000" not in prompt
        assert "张三" not in prompt
        assert "@example.com" not in prompt
    # 该给的给了：技能名与题量（否则模型只能编）。
    assert "Python" in user_prompt
    assert f"共 {SEED_QUESTION_COUNT} 题" in user_prompt


def test_summary_requires_submitted_session(client: TestClient) -> None:
    """未提交的会话没有标签，不能生成摘要（409，而不是编一段空话）。"""
    headers = _headers(client, "b9early")
    _profile(client, headers, ["Python"])
    session_id = _start(client, headers)["session"]["id"]

    response = client.post(f"{SESSIONS_URL}/{session_id}/summary", headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "ASSESSMENT_STATE_CONFLICT"
