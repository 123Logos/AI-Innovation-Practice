"""前后端档案契约测试（保存 422 修复轮）。

背景：点「保存修改」返回 422，根因是前端 `toUpsertPayload()` 的假定与后端
`ProfileUpsertRequest` 的校验不一致。这个文件的职责只有一个：**把前端真实会发出的
JSON 形状原样喂给后端模型**，让契约断裂在 CI 里就炸，而不是等用户点保存。

三种验证层次：
1. 纯 Pydantic（不需要数据库）：`ProfileUpsertRequest.model_validate(...)` 直接校验
   前端 payload 的字段名、类型与取值；
2. 走 HTTP（需要 TEST_DATABASE_URL）：同样的 payload `PUT /profiles/me` 必须 200；
3. 负向用例：后端仍然拒绝的输入（非法手机号、空入学日期），确保前端
   `validateForSave()` 拦的是同一批规则，而不是各说各话。
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.document_style import DEFAULT_SECTIONS, THEMES
from app.schemas.profile import ProfileUpsertRequest

PROFILES_URL = "/api/v1/profiles/me"
GENERATE_URL = "/api/v1/resumes/generate"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"

#: 前端 `ProfileUpsertRequest` 接口（`frontend/src/api/types.ts`）的字段全集。
#: D5.1 补 `paper_size`、D5.2 补 `theme`：这两者都是 `toUpsertPayload()` **显式传值**
#: 的字段（不是省略），所以必须出现在这份「前端真实会发的 JSON」里，
#: 否则三态语义的「不传即保留」分支就永远测不到真实调用形态。
#: D5.3 补 `layout_config`：前端同样是显式传（默认排版发 `null`，自定义发快照）。
FRONTEND_PAYLOAD_KEYS = {
    "full_name",
    "email",
    "contact",
    "paper_size",
    "theme",
    "layout_config",
    "skills",
    "experiences",
    "educations",
}


def _frontend_payload() -> dict[str, object]:
    """`toUpsertPayload()` 对一个「正常填过」的草稿实际会发出的 JSON。

    逐字段对应 `frontend/src/stores/resume.ts`：
    - `email` 空串 → `null`；
    - `self_rating` 固定 3（UI 不采集，但后端 `SkillInput` 必填 1–5）；
    - `end_date` 空串 → `null`（「至今」）；
    - `edu_start` / `edu_end` 由 `YYYY-MM` 补成当月 1 号，空 → `null`；
    - 不再有顶层 `major` / `grade`。
    """
    return {
        "full_name": "张三",
        "email": "zhangsan@example.com",
        "contact": "13800000000",
        # 这两项由 `toUpsertPayload()` 显式带上（当前草稿里的值，不是省略）。
        "paper_size": "A4",
        "theme": "forest",
        # 草稿没调过顺序 → 前端发 `null`（让库里保持 NULL，将来能跟上新默认顺序）。
        "layout_config": None,
        "skills": [
            {"name": "Python", "self_rating": 3},
            {"name": "SQL", "self_rating": 3},
        ],
        "experiences": [
            {
                "title": "校园二手交易平台",
                "start_date": "2024-03-01",
                "end_date": None,
                "description": "面向校内学生的二手交易 Web 应用",
                "responsibility": "负责后端 API 与数据库设计",
            }
        ],
        "educations": [
            {
                "school": "示例大学",
                "major": "计算机科学与技术",
                "degree": "本科",
                "edu_start": "2021-09-01",
                "edu_end": None,
            }
        ],
    }


def _empty_draft_payload() -> dict[str, object]:
    """空白骨架点保存时的 payload：空行被 `meaningful*()` 过滤，全空即空档案。"""
    return {
        "full_name": "",
        "email": None,
        "contact": "",
        "paper_size": "A4",
        "theme": "forest",
        "layout_config": None,
        "skills": [],
        "experiences": [],
        "educations": [],
    }


def test_frontend_payload_is_accepted_by_backend_model() -> None:
    """前端 payload 的字段名/类型必须被后端模型原样接受（契约的最小断言）。"""
    payload = _frontend_payload()

    parsed = ProfileUpsertRequest.model_validate(payload)

    assert set(payload) == FRONTEND_PAYLOAD_KEYS
    assert parsed.full_name == "张三"
    assert parsed.contact == "13800000000"
    assert [skill.self_rating for skill in parsed.skills] == [3, 3]
    assert parsed.experiences[0].end_date is None
    assert parsed.educations[0].edu_start.isoformat() == "2021-09-01"
    assert parsed.educations[0].edu_end is None
    assert parsed.paper_size == "A4"
    assert parsed.theme == "forest"
    # 前端在「默认排版」时发的是 `null`，语义是「恢复默认顺序」，不是「没传」。
    assert "layout_config" in parsed.model_fields_set
    assert parsed.layout_config is None


def test_frontend_custom_layout_payload_is_accepted() -> None:
    """用户调过顺序 / 改过标题时，前端发的是完整快照（`toUpsertPayload()` 的另一支）。

    这条与上一条一起覆盖了 `layout_config` 的两种真实请求形状：
    默认 → `null`，自定义 → `{version, sections[]}`。
    """
    payload = {
        **_frontend_payload(),
        "layout_config": {
            "version": 1,
            "sections": [
                {"id": "skills", "title": "技术栈"},
                {"id": "educations", "title": "教育背景"},
                {"id": "experiences", "title": "项目经验"},
            ],
        },
    }

    parsed = ProfileUpsertRequest.model_validate(payload)

    assert parsed.layout_config is not None
    assert [section.id for section in parsed.layout_config.sections] == [
        "skills",
        "educations",
        "experiences",
    ]
    # 落库前会被 `model_dump()` 成纯 dict（JSON 列不能塞 pydantic 实例）。
    assert parsed.layout_config.model_dump()["sections"][0] == {
        "id": "skills",
        "title": "技术栈",
    }


def test_empty_draft_payload_is_accepted() -> None:
    """「保存不拦必填」：全空的草稿必须能通过模型校验（原先卡在 full_name）。"""
    parsed = ProfileUpsertRequest.model_validate(_empty_draft_payload())

    assert parsed.full_name == ""
    assert parsed.contact == ""
    assert parsed.skills == []
    assert parsed.experiences == []
    assert parsed.educations == []


def test_legacy_grade_key_is_ignored() -> None:
    """旧客户端可能还带着 `grade`：允许传，但不参与保存。"""
    payload = {**_frontend_payload(), "grade": "大四"}

    parsed = ProfileUpsertRequest.model_validate(payload)

    assert parsed.grade == "大四"
    assert parsed.educations[0].school == "示例大学"


def test_backend_still_rejects_inputs_frontend_precheck_blocks() -> None:
    """后端仍会 422 的输入 —— 前端 `validateForSave()` 必须拦同一批规则。

    这里断言的是「规则还在」：如果哪天后端放宽了手机号或日期，这条用例会红，
    提醒同步删掉前端那条 precheck，避免两边各说各话。
    """
    bad_phone = {**_frontend_payload(), "contact": "123331"}
    with pytest.raises(ValueError, match="contact must be a mainland China mobile number"):
        ProfileUpsertRequest.model_validate(bad_phone)

    empty_edu_start = _frontend_payload()
    empty_edu_start["educations"] = [
        {
            "school": "示例大学",
            "major": "计算机科学与技术",
            "degree": "本科",
            "edu_start": "",
            "edu_end": None,
        }
    ]
    with pytest.raises(ValueError, match="valid date"):
        ProfileUpsertRequest.model_validate(empty_edu_start)


def _register_and_login(client: TestClient) -> str:
    username = f"contract_{uuid4().hex[:10]}"
    registered = client.post(
        REGISTER_URL,
        json={"username": username, "password": PASSWORD, "email": f"{username}@example.com"},
    )
    assert registered.status_code == 201, registered.text
    logged_in = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    token: str = logged_in.json()["data"]["access_token"]
    return token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_frontend_payload_saves_over_http(client: TestClient) -> None:
    """同一份前端 payload 走真实 HTTP 必须 200（保存 422 的回归防线）。"""
    token = _register_and_login(client)

    response = client.put(PROFILES_URL, json=_frontend_payload(), headers=_headers(token))

    assert response.status_code == 200, response.text
    profile = response.json()["data"]["profile"]
    assert profile["full_name"] == "张三"
    assert profile["email"] == "zhangsan@example.com"
    assert [skill["name"] for skill in profile["skills"]] == ["Python", "SQL"]
    assert profile["experiences"][0]["end_date"] is None
    assert profile["educations"][0]["edu_end"] is None


def test_empty_draft_saves_and_generate_returns_400(client: TestClient) -> None:
    """空草稿能存；随后生成简历必须是 400（档案不完整），而不是 500。"""
    token = _register_and_login(client)

    saved = client.put(PROFILES_URL, json=_empty_draft_payload(), headers=_headers(token))
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["profile"]["full_name"] == ""

    generated = client.post(GENERATE_URL, json={"title": "我的简历"}, headers=_headers(token))
    assert generated.status_code == 400, generated.text
    assert generated.json()["error"]["code"] == "CLIENT_ERROR"


# --------------------------------------------------------------------------- #
# D5.2：8 套配色必须双端同源
# --------------------------------------------------------------------------- #

FRONTEND_RESUME_STORE = Path(__file__).parents[1] / "frontend" / "src" / "stores" / "resume.ts"

#: 匹配 `THEME_OPTIONS` 里的一行一条：`{ id: 'navy', name: '藏青', accent: '#2F4A6B', rule: '#78899F' }`
_THEME_ENTRY_PATTERN = re.compile(
    r"\{\s*id:\s*'(?P<id>[a-z]+)',\s*name:\s*'(?P<name>[^']+)',\s*"
    r"accent:\s*'(?P<accent>#[0-9A-Fa-f]{6})',\s*rule:\s*'(?P<rule>#[0-9A-Fa-f]{6})'\s*,?\s*\}"
)


def test_theme_palette_matches_frontend() -> None:
    """后端的 8 套色值必须与前端 `THEME_OPTIONS` 逐字一致。

    这是「选预设」能保证双端一致的前提（ADR-007 §5.2）：预设靠的是**同一张静态表**，
    两边各写一份 hex 就是两张表。改了后端忘了前端时，预览色与导出色会不同，
    而前端没有测试框架、肉眼也几乎看不出这点色差——所以把「同一张表」变成 CI 断言。
    """
    if not FRONTEND_RESUME_STORE.exists():
        pytest.skip("前端源码不在当前检出中（后端单独部署的场景）")

    source = FRONTEND_RESUME_STORE.read_text(encoding="utf-8")
    start = source.find("export const THEME_OPTIONS")
    assert start != -1, "前端 `stores/resume.ts` 里找不到 `export const THEME_OPTIONS`"

    entries = {
        match.group("id"): (
            match.group("name"),
            match.group("accent").upper(),
            match.group("rule").upper(),
        )
        for match in _THEME_ENTRY_PATTERN.finditer(source[start:])
    }
    assert len(entries) == len(THEMES), f"前端主题数量 {len(entries)} ≠ 后端 {len(THEMES)}"

    for theme in THEMES.values():
        assert theme.id in entries, f"前端缺少主题 {theme.id}"
        name, accent, rule = entries[theme.id]
        expected = (theme.name, theme.accent.upper(), theme.rule.upper())
        assert (name, accent, rule) == expected, (
            f"{theme.id} 双端不一致：后端 {expected} vs 前端 {(name, accent, rule)}"
        )


# --------------------------------------------------------------------------- #
# D5.3：默认排版顺序与标题必须双端同源
# --------------------------------------------------------------------------- #

#: 匹配 `DEFAULT_LAYOUT_SECTIONS` 里的一行：`{ id: 'skills', title: '技能' }`
_LAYOUT_ENTRY_PATTERN = re.compile(
    r"\{\s*id:\s*'(?P<id>[a-z]+)',\s*title:\s*'(?P<title>[^']+)'\s*\}"
)


def test_default_layout_matches_frontend() -> None:
    """默认顺序与默认标题在后端 `DEFAULT_SECTIONS` 与前端常量之间必须逐字一致。

    为什么值得测：两处默认值各自服务不同的阶段——前端在「还没有后端响应」的
    空白骨架阶段就要渲染，后端在 `layout=None` 时也要出稿。它们一旦漂移，
    用户会看到「预览里技能在第二、生成出来的 PDF 里技能在第三」，
    而这正是 ADR-007 §7.1 说的「手工对齐的绳子断了」，肉眼很难在上线前发现。
    """
    if not FRONTEND_RESUME_STORE.exists():
        pytest.skip("前端源码不在当前检出中（后端单独部署的场景）")

    source = FRONTEND_RESUME_STORE.read_text(encoding="utf-8")
    start = source.find("export const DEFAULT_LAYOUT_SECTIONS")
    assert start != -1, "前端 `stores/resume.ts` 里找不到 `export const DEFAULT_LAYOUT_SECTIONS`"
    # 从 `= [` 开始切，不能用「第一个 `]`」——类型标注 `LayoutSection[]` 里就有一个，
    # 会把切片截在数组之前，得到空列表（那样测试会「通过」得莫名其妙）。
    array_start = source.find("= [", start)
    assert array_start != -1, "`DEFAULT_LAYOUT_SECTIONS` 后面没有 `= [`"
    array_end = source.find("\n]", array_start)
    assert array_end != -1, "前端 `DEFAULT_LAYOUT_SECTIONS` 数组没有闭合"

    entries = [
        (match.group("id"), match.group("title"))
        for match in _LAYOUT_ENTRY_PATTERN.finditer(source[array_start:array_end])
    ]
    expected = [(section.id, section.title) for section in DEFAULT_SECTIONS]
    assert entries == expected, f"默认排版双端不一致：后端 {expected} vs 前端 {entries}"
