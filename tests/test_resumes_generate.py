"""T7.2 集成测试：简历生成走真实 MySQL，覆盖完整生命周期。

链路：注册 → 登录 → PUT 档案 → POST 生成 → 校验 `resumes` 表内的 HTML 与归属。
同时验证：未登录 401、无档案 400、请求体不能覆盖库内数据、标题缺失 422。
建表与用例间清理由 `tests/conftest.py` 统一负责。

D4.1 起补充「按 profile_id 指定简历生成」的用例（文件末尾）：
指定生效、不指定走默认、他人 id 与回收站 id 都是 404。
"""

import re
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.repositories.user_repository import get_engine

GENERATE_URL = "/api/v1/resumes/generate"
PROFILES_URL = "/api/v1/profiles/me"
PROFILES_BASE = "/api/v1/profiles"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"


def _register_and_login(client: TestClient) -> str:
    username = f"resume_{uuid4().hex[:10]}"
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


def _payload(title: str, **extra: object) -> dict[str, object]:
    """模板路径用例的统一请求体。

    ADR-010 之后 `POST /resumes/generate` 是**纯模板**：不调 AI、不要 Key，所以这里既没有
    `use_fallback` 开关，也不需要假上游。本文件验的是排版契约（主题、顺序、转义、落库归属），
    AI 相关的断言全部在 tests/test_resume_ai_copywriting.py。
    """
    return {"title": title, **extra}


def _educations() -> list[dict[str, object]]:
    """两段学历：硕士在读（较新）+ 本科（较早），用于验证倒序渲染。"""
    return [
        {
            "school": "清华大学",
            "major": "软件工程",
            "degree": "硕士",
            "edu_start": "2025-09-01",
        },
        {
            "school": "示例大学",
            "major": "计算机科学与技术",
            "degree": "本科",
            "edu_start": "2021-09-01",
            "edu_end": "2025-06-30",
        },
    ]


def _profile_payload(**overrides: object) -> dict[str, object]:
    experience: dict[str, object] = {
        "title": "校园二手交易平台",
        "start_date": "2024-03-01",
        "description": "面向校内学生的二手交易 Web 应用",
        "responsibility": "负责后端 API 与数据库设计",
    }
    payload: dict[str, object] = {
        "full_name": "张三",
        "email": "zhangsan@example.com",
        "contact": "13800000000",
        "skills": [
            {"name": "Python", "self_rating": 4},
            {"name": "SQL", "self_rating": 3},
            {"name": "React", "self_rating": 3},
        ],
        "educations": _educations(),
    }
    end_date = overrides.pop("end_date", "2024-06-30")
    if end_date is not None:
        experience["end_date"] = end_date
    payload["experiences"] = [experience]
    payload.update(overrides)
    return payload


def _current_profile(client: TestClient, token: str) -> dict[str, object]:
    response = client.get(PROFILES_URL, headers=_headers(token))
    assert response.status_code == 200, response.text
    profile: dict[str, object] = response.json()["data"]["profile"]
    return profile


def _count(table: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def test_generate_requires_authentication(client: TestClient) -> None:
    response = client.post(GENERATE_URL, json=_payload("我的简历"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "CLIENT_ERROR"
    assert _count("resumes") == 0


def test_generate_returns_400_when_profile_is_missing(client: TestClient) -> None:
    token = _register_and_login(client)

    response = client.post(GENERATE_URL, json=_payload("我的简历"), headers=_headers(token))

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "CLIENT_ERROR"
    assert "档案" in body["error"]["message"]
    assert body["meta"]["request_id"]
    assert _count("resumes") == 0


def test_generate_persists_resume_for_current_user(client: TestClient) -> None:
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))
    assert saved.status_code == 200, saved.text
    profile_id = saved.json()["data"]["profile"]["id"]

    response = client.post(GENERATE_URL, json=_payload("我的第一份简历"), headers=_headers(token))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["resume_id"]
    assert data["title"] == "我的第一份简历"
    # 时间统一东八区：带 +08:00 偏移，前端解析不会再差 8 小时。
    assert data["created_at"].endswith("+08:00"), data["created_at"]

    html = data["content_html"]
    assert "张三" in html
    # T7.6：专业只在教育经历区块出现一次，基本信息栏不再重复渲染。
    assert html.count("计算机科学与技术") == 1
    assert "Python" in html
    assert "校园二手交易平台" in html
    # 熟练度已下架：生成器只渲染技能名，HTML 里不得残留「自评 x/5」。
    assert "自评" not in html
    assert '<span class="skill-item">Python</span>' in html

    # 结构必须与 ResumePreview.vue 对齐：姓名大字打头，没有「我的简历」正文标题、
    # 没有单独的「基本信息」区块，也没有空分隔符。
    assert '<header class="paper-header"><h1 class="resume-name">张三</h1>' in html
    assert "<h1>我的简历</h1>" not in html
    assert "基本信息" not in html
    assert "｜｜" not in html

    # 联系方式行：手机号 + 邮箱（档案里填了 email 时）
    assert '<span class="contact-phone">13800000000</span>' in html
    assert '<span class="contact-email">zhangsan@example.com</span>' in html

    # 教育经历区块：学校全称 / 专业 / 学历 / 起止时间，且按入学时间倒序。
    assert "教育经历" in html
    assert '<h3 class="education-school">清华大学</h3>' in html
    for fragment in ("软件工程", "硕士", "2025.09", "至今"):
        assert fragment in html, fragment
    for fragment in ("示例大学", "计算机科学与技术", "本科", "2021.09", "2025.06"):
        assert fragment in html, fragment
    assert html.index("清华大学") < html.index("示例大学"), "教育经历必须按入学时间倒序"
    assert html.index("硕士") < html.index("本科")

    # 项目经历区块：标题 + 日期区间（**精确到日**，体验反馈修复）+ 描述 + 职责
    assert "项目经历" in html
    assert '<h3 class="experience-title">校园二手交易平台</h3>' in html
    assert '<span class="date">2024.03.01 — 2024.06.30</span>' in html

    # 落库校验：id / 归属 / 标题 / HTML 与响应一致
    with get_engine().connect() as connection:
        row = connection.execute(
            text("SELECT id, student_profile_id, title, content_html FROM resumes")
        ).one()
    assert row.id == data["resume_id"]
    assert row.student_profile_id == profile_id
    assert row.title == "我的第一份简历"
    assert row.content_html == data["content_html"]
    # 落库的 HTML 必须本身干净，不能靠前端剥离掩盖脏数据。
    assert "自评" not in row.content_html
    assert "教育经历" in row.content_html
    assert _count("resumes") == 1


def test_generate_skips_education_section_when_profile_has_none(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(
        PROFILES_URL,
        json=_profile_payload(educations=[]),
        headers=_headers(token),
    )

    response = client.post(GENERATE_URL, json=_payload("无学历简历"), headers=_headers(token))

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    # 区块整体不渲染，不能留一个空标题
    assert "教育经历" not in content_html
    assert "张三" in content_html


def test_generate_ignores_profile_fields_sent_in_request_body(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    response = client.post(
        GENERATE_URL,
        json=_payload(
            "被污染？",
            full_name="李四",
            skills=[{"name": "伪造技能", "self_rating": 5}],
            experiences=[{"title": "伪造经历"}],
        ),
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    assert "张三" in content_html
    assert "Python" in content_html
    assert "李四" not in content_html
    assert "伪造技能" not in content_html
    assert "伪造经历" not in content_html


def test_generate_renders_ongoing_experience(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(end_date=None), headers=_headers(token))

    response = client.post(GENERATE_URL, json=_payload("在读经历"), headers=_headers(token))

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    assert "至今" in content_html
    # 项目经历精确到日（体验反馈修复）；教育经历仍是月份精度，两者不再共用一个格式。
    assert '<span class="date">2024.03.01 — 至今</span>' in content_html
    assert "2024.03.01" in content_html
    for month_only in ("2021.09", "2025.06"):
        assert month_only in content_html, month_only
    assert '<span class="edu-end">2025.06</span>' in content_html


def test_generate_omits_email_span_when_profile_has_none(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(email=None), headers=_headers(token))

    response = client.post(GENERATE_URL, json=_payload("没填邮箱"), headers=_headers(token))

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    assert '<span class="contact-phone">13800000000</span>' in content_html
    assert "contact-email" not in content_html
    assert " · " not in content_html


def test_generate_requires_at_least_one_skill_and_experience(client: TestClient) -> None:
    """保存可以空着存，但生成必须完整——后端这条兜底不能被前面放宽校验削弱。"""
    token = _register_and_login(client)
    put = client.put(
        PROFILES_URL,
        json=_profile_payload(skills=[], experiences=[]),
        headers=_headers(token),
    )
    assert put.status_code == 200, put.text

    response = client.post(GENERATE_URL, json=_payload("空档案"), headers=_headers(token))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CLIENT_ERROR"
    assert _count("resumes") == 0


def test_generate_rejects_missing_title(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    response = client.post(GENERATE_URL, json={}, headers=_headers(token))

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "title" in body["error"]["details"][0]["loc"]
    assert _count("resumes") == 0


def test_each_generate_creates_a_new_resume_row(client: TestClient) -> None:
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    first = client.post(GENERATE_URL, json=_payload("第一版"), headers=_headers(token))
    second = client.post(GENERATE_URL, json=_payload("第二版"), headers=_headers(token))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["resume_id"] != second.json()["data"]["resume_id"]
    assert _count("resumes") == 2


# --------------------------------------------------------------------------- #
# D4.1：按 profile_id 生成
# --------------------------------------------------------------------------- #


def _create_profile(client: TestClient, token: str, **payload: object) -> dict[str, object]:
    response = client.post(PROFILES_BASE, json=payload, headers=_headers(token))
    assert response.status_code == 201, response.text
    profile: dict[str, object] = response.json()["data"]["profile"]
    return profile


def test_generate_uses_requested_profile(client: TestClient) -> None:
    """传 `profile_id` 时，生成内容与落库归属都必须指向那一份，而不是默认简历。"""
    token = _register_and_login(client)
    # 默认简历：张三 + Python/SQL/React
    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))
    algorithm = _create_profile(client, token, title="投算法")
    filled = client.put(
        f"{PROFILES_BASE}/{algorithm['id']}",
        json=_profile_payload(
            full_name="李四",
            email=None,
            contact="13900000000",
            skills=[{"name": "PyTorch", "self_rating": 5}],
            experiences=[],
        ),
        headers=_headers(token),
    )
    assert filled.status_code == 200, filled.text

    response = client.post(
        GENERATE_URL,
        json=_payload("算法版", profile_id=algorithm["id"]),
        headers=_headers(token),
    )

    assert response.status_code == 400, "算法版没填项目经历，后端应拒绝生成"
    assert _count("resumes") == 0

    # 补上项目经历后再生成
    client.put(
        f"{PROFILES_BASE}/{algorithm['id']}",
        json=_profile_payload(
            full_name="李四",
            email=None,
            contact="13900000000",
            skills=[{"name": "PyTorch", "self_rating": 5}],
        ),
        headers=_headers(token),
    )
    response = client.post(
        GENERATE_URL,
        json=_payload("算法版", profile_id=algorithm["id"]),
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    assert "李四" in content_html
    assert "PyTorch" in content_html
    assert "张三" not in content_html, "不能混入默认简历的内容"

    with get_engine().connect() as connection:
        owner = connection.execute(text("SELECT student_profile_id FROM resumes")).one()
    assert owner.student_profile_id == algorithm["id"]


def test_generate_without_profile_id_uses_default_profile(client: TestClient) -> None:
    """不传 `profile_id`（旧前端的请求形状）时回落到默认简历。"""
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))
    alternative = _create_profile(client, token, title="投后端", is_default=True)
    client.put(
        f"{PROFILES_BASE}/{alternative['id']}",
        json=_profile_payload(full_name="王五", contact="13900000000"),
        headers=_headers(token),
    )

    response = client.post(GENERATE_URL, json=_payload("默认那份"), headers=_headers(token))

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    assert "王五" in content_html
    assert "张三" not in content_html

    with get_engine().connect() as connection:
        owner = connection.execute(text("SELECT student_profile_id FROM resumes")).one()
    assert owner.student_profile_id == alternative["id"]


def test_generate_rejects_profile_of_another_user(client: TestClient) -> None:
    """拿别人的简历 id 生成：404（不是 400/403），且不写任何 `resumes` 行。"""
    token_a = _register_and_login(client)
    token_b = _register_and_login(client)
    a_profile = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token_a)).json()[
        "data"
    ]["profile"]
    # B 自己也要有一份完整简历，否则会先撞上「档案不完整」的 400，掩盖越权判定。
    b_profile = client.put(
        PROFILES_URL,
        json=_profile_payload(full_name="王五", contact="13900000000"),
        headers=_headers(token_b),
    ).json()["data"]["profile"]
    assert b_profile["id"] != a_profile["id"]

    response = client.post(
        GENERATE_URL,
        json=_payload("越权尝试", profile_id=a_profile["id"]),
        headers=_headers(token_b),
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert _count("resumes") == 0


def test_generate_rejects_profile_in_trash(client: TestClient) -> None:
    """回收站里的简历不能用来生成。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]
    client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token))

    response = client.post(
        GENERATE_URL,
        json=_payload("已删除的简历", profile_id=saved["id"]),
        headers=_headers(token),
    )

    assert response.status_code == 404, response.text
    assert _count("resumes") == 0


# --------------------------------------------------------------------------- #
# D5.2：配色内联到生成物
# --------------------------------------------------------------------------- #


def test_generate_inlines_default_theme_into_content_html(client: TestClient) -> None:
    """默认主题 forest 的色值必须**写进生成物**，而不是只存在前端样式表里。

    ADR-007 §5.6 的两个落点：前端 `.paper` 上的变量管「预览」，生成物里的内联
    变量管「导出 / 分享」。少了后者，导出的 HTML 换个环境就丢配色。
    """
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    response = client.post(GENERATE_URL, json=_payload("默认配色"), headers=_headers(token))

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    assert "--paper-accent: #527873" in content_html
    assert "--paper-rule: #6B8F8A" in content_html
    assert '<div class="paper-doc"' in content_html


def test_generate_uses_profile_theme(client: TestClient) -> None:
    """换成酒红后，生成物里必须出现酒红的一组色值（跟着简历走，不跟用户走）。"""
    token = _register_and_login(client)
    client.put(
        PROFILES_URL,
        json=_profile_payload(theme="wine"),
        headers=_headers(token),
    )

    response = client.post(GENERATE_URL, json=_payload("酒红版"), headers=_headers(token))

    assert response.status_code == 200, response.text
    content_html = response.json()["data"]["content_html"]
    assert "--paper-accent: #7A3B3B" in content_html
    assert "--paper-rule: #A98080" in content_html
    # 旧默认色不能残留，否则说明内联变量没真正覆盖
    assert "#527873" not in content_html


def test_generate_falls_back_to_forest_when_db_theme_is_unknown(client: TestClient) -> None:
    """库里的非法主题不能让生成 500，也不能生成一份没有配色的 HTML。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]
    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE student_profiles SET theme = 'sunset' WHERE id = :id"),
            {"id": saved["id"]},
        )

    response = client.post(GENERATE_URL, json=_payload("脏数据"), headers=_headers(token))

    assert response.status_code == 200, response.text
    assert "--paper-accent: #527873" in response.json()["data"]["content_html"]


# --------------------------------------------------------------------------- #
# D5.3：顺序与自定义标题进生成物
# --------------------------------------------------------------------------- #


def _h2_titles(html: str) -> list[str]:
    """按出现顺序取出所有 `<h2>` 的文本（不引入解析库，ADR-007 §7.4）。"""
    return re.findall(r"<h2>(.*?)</h2>", html)


def _layout(*pairs: tuple[str, str]) -> dict[str, object]:
    return {"version": 1, "sections": [{"id": key, "title": title} for key, title in pairs]}


def test_generate_uses_default_layout_when_never_customized(client: TestClient) -> None:
    """没有 `layout_config` 时顺序与标题都要与 D5.3 之前完全一致。"""
    token = _register_and_login(client)
    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    response = client.post(GENERATE_URL, json=_payload("默认排版"), headers=_headers(token))

    assert response.status_code == 200, response.text
    assert _h2_titles(response.json()["data"]["content_html"]) == ["教育经历", "技能", "项目经历"]


def test_generate_follows_layout_order_and_titles(client: TestClient) -> None:
    """顺序错乱 + 标题全改：生成物的 `<h2>` 必须逐个对上（ADR-007 §7.4(a)）。"""
    token = _register_and_login(client)
    client.put(
        PROFILES_URL,
        json=_profile_payload(
            layout_config=_layout(
                ("skills", "技术栈"),
                ("experiences", "项目经验"),
                ("educations", "教育背景"),
            )
        ),
        headers=_headers(token),
    )

    response = client.post(GENERATE_URL, json=_payload("自定义排版"), headers=_headers(token))

    assert response.status_code == 200, response.text
    html = response.json()["data"]["content_html"]
    assert _h2_titles(html) == ["技术栈", "项目经验", "教育背景"]
    # 顺序变了，正文内容也要跟着走：技能内容必须出现在「项目经验」之前。
    assert html.index("Python") < html.index("校园二手交易平台")
    # 默认标题不能再残留，否则说明拼装时还在用字面量。
    assert "<h2>技能</h2>" not in html


def test_generate_skips_empty_module_even_when_it_is_first(client: TestClient) -> None:
    """空模块被排在第一位时整段跳过 —— 不留空 `<h2>`，后面的模块自动顶上。"""
    token = _register_and_login(client)
    client.put(
        PROFILES_URL,
        json=_profile_payload(
            educations=[],
            layout_config=_layout(
                ("educations", "教育背景"),
                ("skills", "技术栈"),
                ("experiences", "项目经验"),
            ),
        ),
        headers=_headers(token),
    )

    response = client.post(GENERATE_URL, json=_payload("无学历"), headers=_headers(token))

    assert response.status_code == 200, response.text
    html = response.json()["data"]["content_html"]
    assert _h2_titles(html) == ["技术栈", "项目经验"]
    assert "教育背景" not in html
    # 空 `<h2></h2>` 一个都不许有（用户删光某段内容时的典型症状）。
    assert "<h2></h2>" not in html


def test_generate_escapes_custom_layout_title(client: TestClient) -> None:
    """标题是用户输入，必须 `escape()`：`<img src=x>` 不能变成真标签。

    这里用**直接改库**而不是 `PUT` 写入，因为写路径会按 12 字上限校验，
    而这条用例要验的是「渲染时转义」这一步本身（库里的值理论上也可能来自旧版本）。
    """
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]
    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE student_profiles SET layout_config = CAST(:raw AS JSON) WHERE id = :id"),
            {
                "raw": '{"version": 1, "sections": [{"id": "skills", "title": "<img src=x>"}]}',
                "id": saved["id"],
            },
        )

    response = client.post(GENERATE_URL, json=_payload("转义"), headers=_headers(token))

    assert response.status_code == 200, response.text
    html = response.json()["data"]["content_html"]
    assert "&lt;img src=x&gt;" in html
    assert "<img src=x>" not in html


def test_generate_falls_back_to_default_layout_for_broken_config(client: TestClient) -> None:
    """库里的排版坏掉时不能让生成 500，一律按默认顺序出稿（ADR-007 §6.8(3)）。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]

    broken_values = ['"junk"', "[]", '{"version": 9, "sections": []}']
    for raw in broken_values:
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    "UPDATE student_profiles SET layout_config = CAST(:raw AS JSON) WHERE id = :id"
                ),
                {"raw": raw, "id": saved["id"]},
            )

        response = client.post(GENERATE_URL, json=_payload("坏排版"), headers=_headers(token))

        assert response.status_code == 200, f"{raw} -> {response.text}"
        assert _h2_titles(response.json()["data"]["content_html"]) == [
            "教育经历",
            "技能",
            "项目经历",
        ], raw
