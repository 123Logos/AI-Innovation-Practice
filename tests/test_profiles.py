"""T7.2 集成测试：学生档案读写，连接真实 MySQL（TEST_DATABASE_URL）。

覆盖：鉴权 → 未填写档案返回空结构 → PUT 落库（profiles/skills/experiences/educations）
→ 二次 PUT 全量替换 → 跨用户隔离 → 请求校验边界。
建表与用例间清理由 `tests/conftest.py` 统一负责（会话级 `upgrade head` + 逐用例清表）。

D4.1 起补充「多份简历 + 回收站」的用例（文件末尾）：越权、默认简历规则、
软删除/恢复/彻底删除、过期惰性清理。
"""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.document_style import DEFAULT_THEME, THEME_IDS, THEMES
from app.core.error_messages import ErrorMessage
from app.repositories.user_repository import get_engine

PROFILES_URL = "/api/v1/profiles/me"
PROFILES_BASE = "/api/v1/profiles"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
PASSWORD = "Passw0rd!2026"


def _register_and_login(client: TestClient) -> str:
    username = f"profile_{uuid4().hex[:10]}"
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


def _skill(name: str) -> dict[str, object]:
    return {"name": name}


def _experience(
    title: str = "校园二手交易平台",
    start_date: str = "2024-03-01",
    end_date: str | None = "2024-06-30",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "start_date": start_date,
        "description": "面向校内学生的二手交易 Web 应用",
        "responsibility": "负责后端 API 与数据库设计",
    }
    if end_date is not None:
        payload["end_date"] = end_date
    return payload


def _education(
    school: str = "示例大学",
    major: str = "计算机科学与技术",
    degree: str = "本科",
    edu_start: str = "2021-09-01",
    edu_end: str | None = "2025-06-30",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "school": school,
        "major": major,
        "degree": degree,
        "edu_start": edu_start,
    }
    if edu_end is not None:
        payload["edu_end"] = edu_end
    return payload


def _profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "full_name": "张三",
        "email": "zhangsan@example.com",
        "contact": "13800000000",
        "skills": [_skill("Python"), _skill("SQL"), _skill("React")],
        "experiences": [_experience()],
        "educations": [_education()],
    }
    payload.update(overrides)
    return payload


def _count(table: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def _count_active(table: str) -> int:
    """只数活跃行（ADR-015 §2.1：条数上限只数活跃行，历史行不算）。"""
    with get_engine().connect() as connection:
        return int(
            connection.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE deleted_at IS NULL")
            ).scalar_one()
        )


def test_profiles_require_authentication(client: TestClient) -> None:
    assert client.get(PROFILES_URL).status_code == 401
    assert client.put(PROFILES_URL, json=_profile_payload()).status_code == 401


def test_get_profile_returns_null_before_creation(client: TestClient) -> None:
    token = _register_and_login(client)

    response = client.get(PROFILES_URL, headers=_headers(token))

    assert response.status_code == 200
    assert response.json()["data"]["profile"] is None
    assert _count("student_profiles") == 0


def test_schema_dropped_profile_major_but_keeps_education_major() -> None:
    """T7.6：主表 `major` 必须被迁移删掉，`educations.major` 必须原样保留。

    用户反馈轮：主表新增可空的 `email`（简历邮箱），登录邮箱仍在 `users.email`。
    """
    with get_engine().connect() as connection:
        columns = {
            (row.TABLE_NAME, row.COLUMN_NAME)
            for row in connection.execute(
                text(
                    "SELECT TABLE_NAME, COLUMN_NAME "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME IN ('student_profiles', 'educations', 'users')"
                )
            ).all()
        }

    assert ("student_profiles", "major") not in columns
    assert ("educations", "major") in columns
    assert ("student_profiles", "email") in columns
    assert ("users", "email") in columns


def test_put_profile_ignores_legacy_major_field(client: TestClient) -> None:
    """旧客户端还会传顶层 `major`：不能报 422，也不能写到任何地方。"""
    token = _register_and_login(client)

    response = client.put(
        PROFILES_URL,
        json=_profile_payload(major="计算机科学与技术"),
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    assert "major" not in response.json()["data"]["profile"]
    # 专业只落在教育经历子表里
    assert _count("educations") == 1
    with get_engine().connect() as connection:
        majors = connection.execute(text("SELECT major FROM educations")).scalars().all()
    assert majors == ["计算机科学与技术"]


def test_put_profile_persists_profile_skills_experiences_and_educations(
    client: TestClient,
) -> None:
    token = _register_and_login(client)

    response = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    assert response.status_code == 200, response.text
    profile = response.json()["data"]["profile"]
    assert profile["full_name"] == "张三"
    # T7.6：主表 `major` 已彻底移除，专业只存在于每段教育经历。
    assert "major" not in profile
    # 年级已下架：列还在，但不再写业务值。
    assert profile["grade"] == ""
    assert profile["contact"] == "13800000000"
    # 简历邮箱落在档案表（与 users.email 登录邮箱互不影响）。
    assert profile["email"] == "zhangsan@example.com"
    assert {skill["name"] for skill in profile["skills"]} == {"Python", "SQL", "React"}
    assert len(profile["experiences"]) == 1
    assert profile["experiences"][0]["title"] == "校园二手交易平台"
    assert len(profile["educations"]) == 1
    assert profile["educations"][0]["school"] == "示例大学"
    assert profile["educations"][0]["degree"] == "本科"
    # 时间字段统一按东八区返回：带 +08:00 偏移，前端解析不会再差 8 小时。
    assert profile["created_at"].endswith("+08:00"), profile["created_at"]
    assert profile["updated_at"].endswith("+08:00"), profile["updated_at"]

    # 库内四张子/父表都要有真实记录，且 profile.user_id 指向当前登录用户
    assert _count("student_profiles") == 1
    assert _count("skills") == 3
    assert _count("experiences") == 1
    assert _count("educations") == 1
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT p.id, p.user_id, p.full_name, p.email, u.username "
                "FROM student_profiles AS p JOIN users AS u ON u.id = p.user_id"
            )
        ).one()
    assert row.id == profile["id"]
    assert row.user_id == profile["user_id"]
    assert row.full_name == "张三"
    assert row.email == "zhangsan@example.com"

    # 再次 GET 必须读到同一份数据
    read_back = client.get(PROFILES_URL, headers=_headers(token))
    assert read_back.status_code == 200
    assert read_back.json()["data"]["profile"]["id"] == profile["id"]


def test_put_profile_keeps_multiple_educations_then_replaces_them(client: TestClient) -> None:
    """T7.5：教育经历是一对多子表，PUT 语义为全量替换。

    ADR-015 §2.1 起替换是**软删除**：旧行盖 `deleted_at` 留在库里，
    读侧与上限只认活跃行——「旧行必须消失」指的是读侧消失，不是物理消失。
    """
    token = _register_and_login(client)
    two = _profile_payload(
        educations=[
            _education(
                school="清华大学",
                major="软件工程",
                degree="硕士",
                edu_start="2025-09-01",
                edu_end=None,
            ),
            _education(school="示例大学", major="计算机科学与技术", degree="本科"),
        ]
    )

    first = client.put(PROFILES_URL, json=two, headers=_headers(token))

    assert first.status_code == 200, first.text
    educations = first.json()["data"]["profile"]["educations"]
    assert len(educations) == 2
    assert _count("educations") == 2
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT school, major, degree, edu_start, edu_end FROM educations "
                "WHERE deleted_at IS NULL ORDER BY edu_start"
            )
        ).all()
    assert [row.school for row in rows] == ["示例大学", "清华大学"]
    assert rows[1].degree == "硕士"
    assert rows[1].edu_end is None  # 在读：edu_end 为 NULL
    assert str(rows[0].edu_end) == "2025-06-30"

    second = client.put(
        PROFILES_URL,
        json=_profile_payload(educations=[_education(school="示例大学", degree="专科")]),
        headers=_headers(token),
    )

    assert second.status_code == 200, second.text
    replaced = second.json()["data"]["profile"]["educations"]
    assert len(replaced) == 1
    assert replaced[0]["degree"] == "专科"
    # 活跃行只剩本次提交的一条；旧的两行盖戳留库（软删除），读侧一条都看不见。
    assert _count_active("educations") == 1
    assert _count("educations") == 3
    with get_engine().connect() as connection:
        remaining = connection.execute(
            text("SELECT school, degree FROM educations WHERE deleted_at IS NULL")
        ).all()
        history = connection.execute(
            text("SELECT COUNT(*) FROM educations WHERE deleted_at IS NOT NULL")
        ).scalar_one()
    assert [(row.school, row.degree) for row in remaining] == [("示例大学", "专科")]
    assert history == 2


def test_put_profile_rejects_unknown_degree(client: TestClient) -> None:
    token = _register_and_login(client)
    payload = _profile_payload(educations=[_education(degree="博士后")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _count("educations") == 0


def test_put_profile_rejects_education_date_range_violation(client: TestClient) -> None:
    token = _register_and_login(client)
    payload = _profile_payload(
        educations=[_education(edu_start="2025-06-01", edu_end="2021-09-01")]
    )

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_profile_allows_empty_educations(client: TestClient) -> None:
    """未填写教育经历时不该被后端拦住（生成器负责跳过整块）。"""
    token = _register_and_login(client)

    response = client.put(
        PROFILES_URL, json=_profile_payload(educations=[]), headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["profile"]["educations"] == []
    assert _count("educations") == 0


def test_put_profile_replaces_children_on_second_call(client: TestClient) -> None:
    """ADR-018 §5 / X1：**同体 PUT 两次是 no-op**——不 bump `updated_at`、不重建子表。

    这条用例在「全量替换 + 软删除」（ADR-015 §2.1）落地时按裁定改了方向：
    原先钉的是「每次 PUT 都重建子表」，现在钉的是「内容没变就不发副作用」——
    没有 no-op 检测，每次无变更保存都会给活跃行盖 `deleted_at` 再复制一份历史行，
    子表随保存次数爆炸。先例是 `rename_profile` 的「改成原名不写库」。
    """
    token = _register_and_login(client)
    first = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))
    assert first.status_code == 200
    first_profile = first.json()["data"]["profile"]
    first_ids = {
        "skills": {item["id"] for item in first_profile["skills"]},
        "experiences": {item["id"] for item in first_profile["experiences"]},
        "educations": {item["id"] for item in first_profile["educations"]},
    }

    second = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))

    assert second.status_code == 200, second.text
    profile = second.json()["data"]["profile"]
    assert profile["id"] == first_profile["id"]
    # updated_at 不 bump（列表顺序不该因为一次无变更保存而跳动）。
    assert profile["updated_at"] == first_profile["updated_at"]
    # 子表 id 集合不变：没有重建，也没有盖 deleted_at。
    assert {item["id"] for item in profile["skills"]} == first_ids["skills"]
    assert {item["id"] for item in profile["experiences"]} == first_ids["experiences"]
    assert {item["id"] for item in profile["educations"]} == first_ids["educations"]
    assert _count("skills") == 3
    assert _count("experiences") == 1
    assert _count("educations") == 1
    with get_engine().connect() as connection:
        stamped = connection.execute(
            text("SELECT COUNT(*) FROM skills WHERE deleted_at IS NOT NULL")
        ).scalar_one()
    assert stamped == 0, "no-op 保存不许给任何行盖 deleted_at"

    # 反面仍然成立：**内容变了**就全量替换（软删除旧行 + 插新行）。
    changed = client.put(
        PROFILES_URL,
        json=_profile_payload(
            full_name="李四",
            skills=[_skill("Go"), _skill("Kubernetes"), _skill("Linux"), _skill("MySQL")],
            experiences=[_experience("实习一"), _experience("实习二", "2023-01-01", None)],
        ),
        headers=_headers(token),
    )
    assert changed.status_code == 200, changed.text
    changed_profile = changed.json()["data"]["profile"]
    assert changed_profile["full_name"] == "李四"
    assert {skill["name"] for skill in changed_profile["skills"]} == {
        "Go",
        "Kubernetes",
        "Linux",
        "MySQL",
    }
    assert len(changed_profile["experiences"]) == 2
    # `updated_at` 这一格只证明「不倒退」：列是 `DATETIME(0)`（秒级），两次保存在同一秒内
    # 完成时字符串会逐字相同——no-op 检出上线后这条路径快了很多，靠等跨秒只会变成幽灵红。
    # 「内容变了就真的重写」由下面两条硬证据承担：子表 id 全换、旧行被盖 deleted_at。
    assert changed_profile["updated_at"] >= first_profile["updated_at"]
    assert {item["id"] for item in changed_profile["skills"]}.isdisjoint(first_ids["skills"]), (
        "内容变了必须全量替换：新行换全新 id，绝不原地改"
    )
    with get_engine().connect() as connection:
        stamped_after_change = connection.execute(
            text("SELECT COUNT(*) FROM skills WHERE deleted_at IS NOT NULL")
        ).scalar_one()
    assert stamped_after_change == 3, "四条旧技能都应被盖戳（历史行留着，不物理删）"
    # 活跃行只有本次提交的那些；旧行还在库里但盖了戳（软删除，不是物理 DELETE）。
    assert _count_active("skills") == 4
    assert _count_active("experiences") == 2
    assert _count_active("educations") == 1
    assert _count("skills") == 7, "3 条历史 + 4 条活跃"
    assert _count("experiences") == 3, "1 条历史 + 2 条活跃"
    assert _count("student_profiles") == 1


def test_profiles_are_isolated_per_user(client: TestClient) -> None:
    first_token = _register_and_login(client)
    second_token = _register_and_login(client)

    client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(first_token))
    second_profile = client.put(
        PROFILES_URL,
        json=_profile_payload(full_name="王五", contact="13900000000"),
        headers=_headers(second_token),
    )

    assert second_profile.status_code == 200
    first_read = client.get(PROFILES_URL, headers=_headers(first_token)).json()["data"]["profile"]
    second_read = client.get(PROFILES_URL, headers=_headers(second_token)).json()["data"]["profile"]
    assert first_read["full_name"] == "张三"
    assert second_read["full_name"] == "王五"
    assert first_read["id"] != second_read["id"]
    assert _count("student_profiles") == 2


def test_put_profile_allows_empty_children_but_rejects_overflow(client: TestClient) -> None:
    """保存不再拦必填（用户反馈）：技能/经历/教育都可为空，只拦超出上限。"""
    token = _register_and_login(client)
    empty = _profile_payload(skills=[], experiences=[], educations=[])

    saved = client.put(PROFILES_URL, json=empty, headers=_headers(token))

    assert saved.status_code == 200, saved.text
    profile = saved.json()["data"]["profile"]
    assert profile["skills"] == []
    assert profile["experiences"] == []
    assert profile["educations"] == []
    assert _count("student_profiles") == 1

    overflow = (
        _profile_payload(skills=[_skill(str(index)) for index in range(9)]),
        _profile_payload(experiences=[_experience(f"经历{index}") for index in range(4)]),
        _profile_payload(educations=[_education(school=f"学校{index}") for index in range(6)]),
    )
    for payload in overflow:
        response = client.put(PROFILES_URL, json=payload, headers=_headers(token))
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_profile_accepts_partially_filled_rows(client: TestClient) -> None:
    """只填了锚点字段（技能名 / 项目名 / 学校）也必须能保存，不因缺描述被拒。"""
    token = _register_and_login(client)
    payload = _profile_payload(
        skills=[_skill("Python")],
        experiences=[
            {"title": "还没写描述", "start_date": "2024-03-01"},
        ],
        educations=[
            {
                "school": "示例大学",
                "degree": "本科",
                "edu_start": "2021-09-01",
            }
        ],
    )

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 200, response.text
    profile = response.json()["data"]["profile"]
    assert profile["experiences"][0]["description"] == ""
    assert profile["experiences"][0]["responsibility"] == ""
    assert profile["educations"][0]["major"] == ""


def test_put_profile_stores_resume_email_without_touching_login_email(
    client: TestClient,
) -> None:
    """档案 email 是「简历上的邮箱」，与 users.email（登录邮箱）互不影响。"""
    token = _register_and_login(client)

    response = client.put(
        PROFILES_URL,
        json=_profile_payload(email="resume-only@example.com"),
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["profile"]["email"] == "resume-only@example.com"
    with get_engine().connect() as connection:
        stored = connection.execute(text("SELECT email FROM student_profiles")).scalar_one()
    assert stored == "resume-only@example.com"

    me = client.get("/api/v1/auth/me", headers=_headers(token))
    assert me.status_code == 200
    assert me.json()["data"]["user"]["email"] != "resume-only@example.com"

    # 空串等同「没填」，库里存 NULL 而不是空字符串
    cleared = client.put(PROFILES_URL, json=_profile_payload(email=""), headers=_headers(token))
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["profile"]["email"] is None
    with get_engine().connect() as connection:
        assert connection.execute(text("SELECT email FROM student_profiles")).scalar_one() is None


def test_put_profile_rejects_duplicate_skill_names(client: TestClient) -> None:
    token = _register_and_login(client)
    payload = _profile_payload(skills=[_skill("Python"), _skill("Python"), _skill("React")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_profile_rejects_case_variant_duplicate_skill_names(client: TestClient) -> None:
    """`Python` 与 `python` 是同一个技能，必须给可读 422，而不是把冲突推给数据库。

    库里 `UNIQUE (student_profile_id, name)` 建在 `utf8mb4_0900_ai_ci` 上（大小写不敏感），
    而校验器过去按**裸相等**判重复：这对名字能过校验、走到 INSERT、由数据库报错。
    用户看到的是 500/内部错误，而正确的报错是「技能名称重复」——同一个问题两处答案不同。
    """
    token = _register_and_login(client)
    payload = _profile_payload(skills=[_skill("Python"), _skill("python")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_profile_rejects_full_width_variant_duplicate_skill_names(client: TestClient) -> None:
    """全角 `Ｐython` 与半角 `Python` 同样算重复（键含 NFKC）。"""
    token = _register_and_login(client)
    payload = _profile_payload(skills=[_skill("Python"), _skill("\uff30ython")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422


def test_put_profile_trims_skill_name_ends(client: TestClient) -> None:
    """两端空白在校验边界就裁掉：库里存的必须是 `Python`，不是 ` Python `。

    岗位侧一直这么做（`JobSkillInput.normalize_name`），学生侧没有，于是同一个技能名
    在两张表上有两种规范化程度——匹配按后端键算会判「未覆盖」，前端角标 trim 后判「已具备」，
    同一屏自相矛盾。见 ADR-013 §1.3。
    """
    token = _register_and_login(client)
    payload = _profile_payload(skills=[_skill("  Python  ")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 200
    names = [item["name"] for item in response.json()["data"]["profile"]["skills"]]
    assert names == ["Python"]


def test_put_profile_rejects_padded_variant_duplicate_skill_names(client: TestClient) -> None:
    """trim 之后 `Python` 与 ` Python ` 变成同名 → 422，而不是悄悄存下两条。"""
    token = _register_and_login(client)
    payload = _profile_payload(skills=[_skill("Python"), _skill("  Python ")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422


def test_put_profile_round_trips_target_role(client: TestClient) -> None:
    """`target_role` 是档案内容的一部分：写进去要读得回来，且两端空白不留（ADR-013 §5）。"""
    token = _register_and_login(client)
    payload = _profile_payload(target_role="  大模型算法实习生  ")

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 200
    profile = response.json()["data"]["profile"]
    assert profile["target_role"] == "大模型算法实习生"
    read_back = client.get(PROFILES_URL, headers=_headers(token))
    assert read_back.json()["data"]["profile"]["target_role"] == "大模型算法实习生"


def test_put_profile_stores_blank_target_role_as_null(client: TestClient) -> None:
    """纯空白折成 `NULL`，不留第二种「没填」的写法。

    这不是格式洁癖：匹配侧靠「填没填」决定这一项是退出加权还是记 0.2（ADR-013 §5.1）。
    库里同时存在 `NULL` 和 `'   '` 会让「没填的人不被惩罚」取决于用户有没有敲过空格。
    """
    token = _register_and_login(client)
    response = client.put(
        PROFILES_URL, json=_profile_payload(target_role="   "), headers=_headers(token)
    )

    assert response.status_code == 200
    assert response.json()["data"]["profile"]["target_role"] is None


def test_put_profile_without_target_role_clears_it(client: TestClient) -> None:
    """`PUT` 是整体覆盖：不带这一格 = 清空，与 `email` 同一语义。

    故意把「会清掉」写成断言而不是注释——`paper_size`/`theme` 是「缺省不改动」的另一套
    语义，两个字段族挨在一起，靠读代码猜不出来。前端因此必须每次都带上它。
    """
    token = _register_and_login(client)
    client.put(
        PROFILES_URL, json=_profile_payload(target_role="前端开发实习生"), headers=_headers(token)
    )

    payload = _profile_payload()
    payload.pop("target_role", None)
    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 200
    assert response.json()["data"]["profile"]["target_role"] is None


def test_put_profile_rejects_blank_skill_name(client: TestClient) -> None:
    """纯空格不是技能名：`min_length=1` 单独挡不住它，必须裁完再判空。

    改之前 `{"name": "   "}` 是合法的，会往 `skills.name` 里存一条空白——
    它在简历里渲染出一个空条目，在匹配里既不算覆盖也不算缺口。
    """
    token = _register_and_login(client)
    payload = _profile_payload(skills=[_skill("   ")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_profile_rejects_invalid_date_range(client: TestClient) -> None:
    token = _register_and_login(client)
    payload = _profile_payload(experiences=[_experience(end_date="2024-02-28")])

    response = client.put(PROFILES_URL, json=payload, headers=_headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_profile_rejects_future_start_dates(client: TestClient) -> None:
    """**开始**时间不能是未来：入学 / 项目开始晚于今天一律 422。

    结束时间不在此列——应届生要填「预计毕业时间」（见下一个用例）。
    """
    token = _register_and_login(client)
    future = (date.today() + timedelta(days=400)).isoformat()

    for payload in (
        _profile_payload(educations=[_education(edu_start=future, edu_end=None)]),
        _profile_payload(experiences=[_experience("未来开始", future, None)]),
    ):
        response = client.put(PROFILES_URL, json=payload, headers=_headers(token))
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    assert _count("student_profiles") == 0


def test_put_profile_accepts_future_end_dates(client: TestClient) -> None:
    """**结束**时间允许未来：应届生的「预计毕业」必须能存。

    体验反馈修复：之前把 `edu_end` / `end_date` 也卡在「过去」，
    导致应届生无法填写毕业时间、在做的项目无法写预计结束时间。
    """
    token = _register_and_login(client)
    future = (date.today() + timedelta(days=400)).isoformat()

    response = client.put(
        PROFILES_URL,
        json=_profile_payload(
            educations=[_education(edu_start="2023-09-01", edu_end=future)],
            experiences=[_experience("预计结束的项目", "2024-03-01", future)],
        ),
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    profile = response.json()["data"]["profile"]
    assert profile["educations"][0]["edu_end"] == future
    assert profile["experiences"][0]["end_date"] == future

    # 真实落库，不只是回显：未来的结束时间必须原样写进 DATE 列。
    with get_engine().connect() as connection:
        edu_row = connection.execute(text("SELECT edu_end FROM educations")).one()
        exp_row = connection.execute(text("SELECT end_date FROM experiences")).one()
    assert str(edu_row.edu_end) == future
    assert str(exp_row.end_date) == future


def test_put_profile_accepts_today_as_date(client: TestClient) -> None:
    """边界：允许「今天」，只有严格晚于今天才拦。"""
    token = _register_and_login(client)
    today = date.today().isoformat()

    response = client.put(
        PROFILES_URL,
        json=_profile_payload(
            educations=[_education(edu_start="2021-09-01", edu_end=today)],
            experiences=[_experience("到今天为止", "2024-03-01", today)],
        ),
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text


def test_put_profile_rejects_non_phone_contact(client: TestClient) -> None:
    """手机号必须是大陆 11 位号码（`^1[3-9]\\d{9}$`）。"""
    token = _register_and_login(client)

    for contact in ("12345", "1380000000", "12345678901", "021-88886666", "手机13800000000"):
        response = client.put(
            PROFILES_URL, json=_profile_payload(contact=contact), headers=_headers(token)
        )
        assert response.status_code == 422, f"{contact} -> {response.text}"
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    assert _count("student_profiles") == 0


def test_put_profile_allows_empty_contact_as_draft(client: TestClient) -> None:
    """保存不拦必填：手机号留空也能存草稿，只在生成简历时强校验。"""
    token = _register_and_login(client)

    response = client.put(PROFILES_URL, json=_profile_payload(contact=""), headers=_headers(token))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["profile"]["contact"] == ""


# --------------------------------------------------------------------------- #
# D4.1：多份简历 + 回收站
# --------------------------------------------------------------------------- #


def _create(client: TestClient, token: str, **payload: object) -> dict[str, object]:
    response = client.post(PROFILES_BASE, json=payload, headers=_headers(token))
    assert response.status_code == 201, response.text
    profile: dict[str, object] = response.json()["data"]["profile"]
    return profile


def _list_profiles(client: TestClient, token: str) -> list[dict[str, object]]:
    response = client.get(PROFILES_BASE, headers=_headers(token))
    assert response.status_code == 200, response.text
    profiles: list[dict[str, object]] = response.json()["data"]["profiles"]
    return profiles


def _trash(client: TestClient, token: str) -> list[dict[str, object]]:
    response = client.get(f"{PROFILES_BASE}/trash", headers=_headers(token))
    assert response.status_code == 200, response.text
    profiles: list[dict[str, object]] = response.json()["data"]["profiles"]
    return profiles


def _user_id(client: TestClient, token: str) -> str:
    response = client.get("/api/v1/auth/me", headers=_headers(token))
    assert response.status_code == 200, response.text
    user_id: str = response.json()["data"]["user"]["id"]
    return user_id


def _naive_utc_now() -> datetime:
    """库里存的是 naive UTC，测试回填时间要同口径（不能塞带时区的 datetime）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def test_schema_allows_multiple_profiles_per_user() -> None:
    """D4.1：`user_id` 唯一约束必须消失，改为普通索引；三列必须存在。

    直接 `DROP INDEX user_id` 会被外键挡住（实测 1553），所以迁移里是先补
    `ix_student_profiles_user_id` 再摘唯一索引 —— 这里一并断言那个普通索引在。
    """
    with get_engine().connect() as connection:
        indexes = connection.execute(
            text(
                "SELECT INDEX_NAME, NON_UNIQUE, COLUMN_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'student_profiles'"
            )
        ).all()
        columns = {
            row.COLUMN_NAME
            for row in connection.execute(
                text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'student_profiles'"
                )
            ).all()
        }

    unique_on_user_id = [
        row.INDEX_NAME for row in indexes if row.COLUMN_NAME == "user_id" and row.NON_UNIQUE == 0
    ]
    assert unique_on_user_id == [], "user_id 上不应再有唯一索引（否则一人只能一份简历）"
    assert any(
        row.INDEX_NAME == "ix_student_profiles_user_id" and row.NON_UNIQUE == 1 for row in indexes
    ), "外键需要 user_id 上的普通索引"
    assert {"title", "is_default", "deleted_at"} <= columns


def test_create_profile_autoincrements_default_title(client: TestClient) -> None:
    """新建不传标题时递增命名：我的简历 → 我的简历 2 → …；显式标题原样保留。"""
    token = _register_and_login(client)

    first = _create(client, token)
    assert first["title"] == "我的简历"
    # 第一份自动成为默认简历
    assert first["is_default"] is True

    second = _create(client, token)
    assert second["title"] == "我的简历 2"
    assert second["is_default"] is False

    named = _create(client, token, title="投算法")
    assert named["title"] == "投算法"

    third = _create(client, token)
    assert third["title"] == "我的简历 3", "跳过已占用的编号，取最大值 + 1"

    assert _count("student_profiles") == 4


def test_list_and_detail_are_scoped_to_owner(client: TestClient) -> None:
    """用户 A 只能看到/改动自己的简历；B 猜 id 也一律 404（不是 403）。"""
    token_a = _register_and_login(client)
    token_b = _register_and_login(client)
    a_first = _create(client, token_a)
    a_second = _create(client, token_a)
    b_first = _create(client, token_b)

    assert sorted(profile["id"] for profile in _list_profiles(client, token_a)) == sorted(
        [a_first["id"], a_second["id"]]
    )
    assert [profile["id"] for profile in _list_profiles(client, token_b)] == [b_first["id"]]

    profile_url = f"{PROFILES_BASE}/{a_first['id']}"
    assert client.get(profile_url, headers=_headers(token_b)).status_code == 404
    assert (
        client.put(profile_url, json=_profile_payload(full_name="越权"), headers=_headers(token_b))
    ).status_code == 404
    assert client.delete(profile_url, headers=_headers(token_b)).status_code == 404
    assert (client.put(f"{profile_url}/restore", headers=_headers(token_b))).status_code == 404
    assert (client.delete(f"{profile_url}/permanent", headers=_headers(token_b))).status_code == 404

    # A 的数据必须完全没被 B 的尝试影响
    still_there = client.get(profile_url, headers=_headers(token_a))
    assert still_there.status_code == 200
    assert still_there.json()["data"]["profile"]["full_name"] == ""
    assert len(_list_profiles(client, token_a)) == 2


def test_create_profile_copy_from_deep_copies_content(client: TestClient) -> None:
    """`copy_from_id` 是服务端深拷贝：内容一致、子行 id 全新、改副本不影响源。"""
    token = _register_and_login(client)
    source = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]

    copied = _create(client, token, copy_from_id=source["id"], title="投后端")

    assert copied["id"] != source["id"]
    assert copied["title"] == "投后端"
    assert copied["full_name"] == "张三"
    assert copied["contact"] == "13800000000"
    assert {skill["name"] for skill in copied["skills"]} == {"Python", "SQL", "React"}
    assert len(copied["experiences"]) == 1
    assert copied["educations"][0]["school"] == "示例大学"
    assert copied["is_default"] is False
    assert {skill["id"] for skill in copied["skills"]}.isdisjoint(
        {skill["id"] for skill in source["skills"]}
    ), "子行必须重新分配 id，否则改副本会回写源简历"

    updated = client.put(
        f"{PROFILES_BASE}/{copied['id']}",
        json=_profile_payload(full_name="李四", skills=[_skill("Go")]),
        headers=_headers(token),
    )
    assert updated.status_code == 200, updated.text

    reread = client.get(f"{PROFILES_BASE}/{source['id']}", headers=_headers(token))
    source_after = reread.json()["data"]["profile"]
    assert source_after["full_name"] == "张三"
    assert {skill["name"] for skill in source_after["skills"]} == {"Python", "SQL", "React"}


def test_create_profile_as_default_clears_previous(client: TestClient) -> None:
    """新建时 `is_default=true` 要把旧的默认置为 0，且全局只有一个默认。"""
    token = _register_and_login(client)
    first = _create(client, token)
    second = _create(client, token, is_default=True)

    profiles = {profile["id"]: profile for profile in _list_profiles(client, token)}
    assert profiles[second["id"]]["is_default"] is True
    assert profiles[first["id"]]["is_default"] is False
    assert sum(1 for profile in profiles.values() if profile["is_default"]) == 1

    me = client.get(PROFILES_URL, headers=_headers(token))
    assert me.json()["data"]["profile"]["id"] == second["id"]

    # 显式切换默认：旧的自动取消
    switched = client.put(f"{PROFILES_BASE}/{first['id']}/default", headers=_headers(token))
    assert switched.status_code == 200
    assert switched.json()["data"]["profile"]["is_default"] is True
    profiles = {profile["id"]: profile for profile in _list_profiles(client, token)}
    assert profiles[second["id"]]["is_default"] is False


def test_put_by_id_only_touches_that_profile(client: TestClient) -> None:
    token = _register_and_login(client)
    first = _create(client, token)
    second = _create(client, token)

    client.put(
        f"{PROFILES_BASE}/{second['id']}",
        json=_profile_payload(full_name="李四", skills=[_skill("Go")]),
        headers=_headers(token),
    )

    untouched = client.get(f"{PROFILES_BASE}/{first['id']}", headers=_headers(token))
    assert untouched.json()["data"]["profile"]["full_name"] == ""
    assert untouched.json()["data"]["profile"]["skills"] == []


def test_soft_delete_moves_profile_to_trash(client: TestClient) -> None:
    """软删除：列表消失、详情 404、回收站可见；子表一行都不能少。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]

    deleted = client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {"profile_id": saved["id"], "deleted": True}

    assert _list_profiles(client, token) == []
    assert client.get(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token)).status_code == 404
    # 软删除只是打标记，子表不能动（恢复后内容必须还在）
    assert _count("student_profiles") == 1
    assert _count("skills") == 3
    assert _count("experiences") == 1
    assert _count("educations") == 1

    trash = _trash(client, token)
    assert [profile["id"] for profile in trash] == [saved["id"]]
    assert trash[0]["deleted_at"].endswith("+08:00"), trash[0]["deleted_at"]

    restored = client.put(f"{PROFILES_BASE}/{saved['id']}/restore", headers=_headers(token))
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["profile"]["deleted_at"] is None
    assert [profile["id"] for profile in _list_profiles(client, token)] == [saved["id"]]
    assert _trash(client, token) == []


def test_deleting_default_promotes_newest_remaining(client: TestClient) -> None:
    """删掉默认简历后，自动把**最新**的一份未删除简历设为默认。"""
    token = _register_and_login(client)
    oldest = _create(client, token)
    _create(client, token)
    newest = _create(client, token)
    assert oldest["is_default"] is True

    client.delete(f"{PROFILES_BASE}/{oldest['id']}", headers=_headers(token))

    remaining = _list_profiles(client, token)
    assert len(remaining) == 2
    defaults = [profile for profile in remaining if profile["is_default"]]
    assert [profile["id"] for profile in defaults] == [newest["id"]]

    me = client.get(PROFILES_URL, headers=_headers(token))
    assert me.json()["data"]["profile"]["id"] == newest["id"]


def test_permanent_delete_removes_profile_and_children(client: TestClient) -> None:
    """彻底删除只能对回收站里的简历执行，且要连带清掉四张子表。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]

    refused = client.delete(f"{PROFILES_BASE}/{saved['id']}/permanent", headers=_headers(token))
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "CLIENT_ERROR"
    assert _count("student_profiles") == 1, "未进回收站不能物理删除"

    client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token))
    purged = client.delete(f"{PROFILES_BASE}/{saved['id']}/permanent", headers=_headers(token))
    assert purged.status_code == 200, purged.text
    assert purged.json()["data"] == {"profile_id": saved["id"], "purged": True}

    assert _count("student_profiles") == 0
    assert _count("skills") == 0
    assert _count("experiences") == 0
    assert _count("educations") == 0
    # 物删之后连恢复都该 404
    assert (
        client.put(f"{PROFILES_BASE}/{saved['id']}/restore", headers=_headers(token)).status_code
        == 404
    )


def test_expired_trash_is_purged_lazily_on_list(client: TestClient) -> None:
    """回收站保留 30 天：过期记录在下一次读取列表时被惰性物理清理。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]
    client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token))

    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE student_profiles SET deleted_at = :at WHERE id = :id"),
            {"at": _naive_utc_now() - timedelta(days=31), "id": saved["id"]},
        )

    assert _trash(client, token) == []
    assert _count("student_profiles") == 0
    assert _count("skills") == 0
    assert _count("educations") == 0


def test_me_rebuilds_default_profile_after_everything_is_deleted(client: TestClient) -> None:
    """旧客户端（只认 `/profiles/me`）在全部删光后再点保存，要能重建一份默认简历。"""
    token = _register_and_login(client)
    saved = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token)).json()[
        "data"
    ]["profile"]
    client.delete(f"{PROFILES_BASE}/{saved['id']}", headers=_headers(token))

    me = client.get(PROFILES_URL, headers=_headers(token))
    assert me.status_code == 200
    assert me.json()["data"]["profile"] is None

    again = client.put(PROFILES_URL, json=_profile_payload(), headers=_headers(token))
    assert again.status_code == 200, again.text
    rebuilt = again.json()["data"]["profile"]
    assert rebuilt["is_default"] is True
    assert rebuilt["title"] == "我的简历"
    assert rebuilt["id"] != saved["id"]
    # 回收站里那份仍在（软删除不参与重名判断，所以标题可以重复）
    assert _count("student_profiles") == 2
    assert [profile["id"] for profile in _trash(client, token)] == [saved["id"]]


def test_profile_count_is_capped(client: TestClient) -> None:
    """每人最多 20 份（ADR-006 §4.4）：超出返回 400，不再新增行。"""
    token = _register_and_login(client)
    user_id = _user_id(client, token)
    now = _naive_utc_now()
    with get_engine().begin() as connection:
        for index in range(20):
            connection.execute(
                text(
                    "INSERT INTO student_profiles "
                    "(id, user_id, title, is_default, full_name, grade, contact, "
                    " created_at, updated_at) "
                    "VALUES (:id, :user_id, :title, :is_default, '', '', '', :now, :now)"
                ),
                {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "title": f"批量 {index}",
                    "is_default": 1 if index == 0 else 0,
                    "now": now,
                },
            )

    response = client.post(PROFILES_BASE, json={}, headers=_headers(token))

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "CLIENT_ERROR"
    assert "20" in response.json()["error"]["message"]
    assert _count("student_profiles") == 20


# --------------------------------------------------------------------------- #
# D4.3：列表排序 / 重命名 / 重名约束
# --------------------------------------------------------------------------- #


def _insert_profile_row(
    user_id: str,
    title: str,
    *,
    created_at: datetime,
    updated_at: datetime,
    is_default: bool = False,
) -> str:
    """直接插一行（绕过 API），用来构造「创建时间」与「修改时间」不一致的场景。

    走 API 建简历时两个时间戳几乎相同，无法验证「按 updated_at 排」与
    「按 created_at 排」的差异，所以这里手工指定。
    """
    profile_id = str(uuid4())
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO student_profiles "
                "(id, user_id, title, is_default, full_name, grade, contact, "
                " created_at, updated_at) "
                "VALUES (:id, :user_id, :title, :is_default, '', '', '', :created_at, :updated_at)"
            ),
            {
                "id": profile_id,
                "user_id": user_id,
                "title": title,
                "is_default": 1 if is_default else 0,
                "created_at": created_at,
                "updated_at": updated_at,
            },
        )
    return profile_id


def _list_profiles_sorted(client: TestClient, token: str, **params: str) -> list[dict[str, object]]:
    response = client.get(PROFILES_BASE, params=params, headers=_headers(token))
    assert response.status_code == 200, response.text
    profiles: list[dict[str, object]] = response.json()["data"]["profiles"]
    return profiles


def test_list_profiles_defaults_to_updated_at_desc(client: TestClient) -> None:
    """D4.3：默认按「最近修改」倒序，而不是按默认标记/创建时间。"""
    token = _register_and_login(client)
    user_id = _user_id(client, token)
    now = _naive_utc_now()
    stale = _insert_profile_row(
        user_id,
        "三小时前改过",
        created_at=now - timedelta(hours=3),
        updated_at=now - timedelta(hours=3),
    )
    middle = _insert_profile_row(
        user_id,
        "一小时前改过",
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=1),
    )
    freshest = _insert_profile_row(
        user_id,
        "刚改过",
        created_at=now - timedelta(hours=5),
        updated_at=now,
    )

    assert [profile["id"] for profile in _list_profiles(client, token)] == [
        freshest,
        middle,
        stale,
    ]


def test_list_profiles_supports_created_at_and_ascending(client: TestClient) -> None:
    """排序字段与方向都可由查询参数切换；`asc` 必须是 `desc` 的严格倒序。"""
    token = _register_and_login(client)
    user_id = _user_id(client, token)
    now = _naive_utc_now()
    first = _insert_profile_row(
        user_id, "最早建的", created_at=now - timedelta(days=3), updated_at=now
    )
    second = _insert_profile_row(
        user_id, "中间建的", created_at=now - timedelta(days=2), updated_at=now
    )
    third = _insert_profile_row(
        user_id, "最新建的", created_at=now - timedelta(days=1), updated_at=now
    )

    ascending = _list_profiles_sorted(client, token, order_by="created_at", order="asc")
    assert [profile["id"] for profile in ascending] == [first, second, third]

    descending = _list_profiles_sorted(client, token, order_by="created_at", order="desc")
    assert [profile["id"] for profile in descending] == [third, second, first]


def test_list_profiles_rejects_unknown_sort_field(client: TestClient) -> None:
    """白名单外的排序字段直接 422，不做字符串拼 SQL。"""
    token = _register_and_login(client)

    response = client.get(PROFILES_BASE, params={"order_by": "full_name"}, headers=_headers(token))

    assert response.status_code == 422, response.text


def test_rename_profile_updates_title_and_bumps_updated_at(client: TestClient) -> None:
    """改名成功：返回新标题、详情同步、并因为「刚修改过」跳到列表最前。"""
    token = _register_and_login(client)
    older = _create(client, token, title="投后端")
    _create(client, token, title="投算法")

    renamed = client.put(
        f"{PROFILES_BASE}/{older['id']}/title",
        json={"title": "  投基础架构  "},
        headers=_headers(token),
    )

    assert renamed.status_code == 200, renamed.text
    profile = renamed.json()["data"]["profile"]
    assert profile["title"] == "投基础架构", "两端空白由服务端裁掉"
    assert profile["id"] == older["id"]

    detail = client.get(f"{PROFILES_BASE}/{older['id']}", headers=_headers(token))
    assert detail.json()["data"]["profile"]["title"] == "投基础架构"
    assert _list_profiles(client, token)[0]["id"] == older["id"]


def test_rename_profile_rejects_duplicate_title(client: TestClient) -> None:
    """重名返回 400 + 固定文案，且原标题不变。"""
    token = _register_and_login(client)
    _create(client, token, title="投后端")
    target = _create(client, token, title="投算法")

    response = client.put(
        f"{PROFILES_BASE}/{target['id']}/title",
        json={"title": "投后端"},
        headers=_headers(token),
    )

    assert response.status_code == 400, response.text
    # 文案唯一来源是 G3 那张表（批次 F1c），断言枚举而不是抄中文。
    assert response.json()["error"]["message"] == ErrorMessage.PROFILE_TITLE_CONFLICT
    detail = client.get(f"{PROFILES_BASE}/{target['id']}", headers=_headers(token))
    assert detail.json()["data"]["profile"]["title"] == "投算法"


def test_rename_profile_to_same_title_is_idempotent(client: TestClient) -> None:
    """改成原名不算重复、也不推进 `updated_at`（否则点一次保存列表就跳）。"""
    token = _register_and_login(client)
    created = _create(client, token, title="投后端")
    before = _list_profiles(client, token)[0]["updated_at"]

    response = client.put(
        f"{PROFILES_BASE}/{created['id']}/title",
        json={"title": "投后端"},
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["profile"]["title"] == "投后端"
    assert _list_profiles(client, token)[0]["updated_at"] == before


def test_rename_profile_rejects_blank_title(client: TestClient) -> None:
    """全空白标题 422：下拉列表里不能出现一行没有名字的简历。"""
    token = _register_and_login(client)
    created = _create(client, token, title="投后端")

    response = client.put(
        f"{PROFILES_BASE}/{created['id']}/title",
        json={"title": "   "},
        headers=_headers(token),
    )

    assert response.status_code == 422, response.text


def test_rename_profile_is_scoped_to_owner(client: TestClient) -> None:
    """别人的简历、回收站里的简历都不能改名（统一 404，不泄露存在性）。"""
    owner = _register_and_login(client)
    intruder = _register_and_login(client)
    created = _create(client, owner, title="投后端")

    forbidden = client.put(
        f"{PROFILES_BASE}/{created['id']}/title",
        json={"title": "被改名了"},
        headers=_headers(intruder),
    )
    assert forbidden.status_code == 404, forbidden.text

    client.delete(f"{PROFILES_BASE}/{created['id']}", headers=_headers(owner))
    trashed = client.put(
        f"{PROFILES_BASE}/{created['id']}/title",
        json={"title": "回收到回收站再改"},
        headers=_headers(owner),
    )
    assert trashed.status_code == 404, trashed.text


def test_create_profile_rejects_duplicate_title(client: TestClient) -> None:
    """创建口子也要拦重名，否则「不能重名」这条约束一绕就破。"""
    token = _register_and_login(client)
    _create(client, token, title="投后端")

    response = client.post(PROFILES_BASE, json={"title": "投后端"}, headers=_headers(token))

    assert response.status_code == 400, response.text
    # 文案唯一来源是 G3 那张表（批次 F1c），断言枚举而不是抄中文。
    assert response.json()["error"]["message"] == ErrorMessage.PROFILE_TITLE_CONFLICT
    assert _count("student_profiles") == 1


def test_trashed_profile_does_not_hold_its_title(client: TestClient) -> None:
    """回收站里的简历不占名字：删掉后可以用同一个标题新建。"""
    token = _register_and_login(client)
    created = _create(client, token, title="投后端")
    client.delete(f"{PROFILES_BASE}/{created['id']}", headers=_headers(token))

    response = client.post(PROFILES_BASE, json={"title": "投后端"}, headers=_headers(token))

    assert response.status_code == 201, response.text
    assert response.json()["data"]["profile"]["title"] == "投后端"


# --------------------------------------------------------------------------- #
# D5.1：纸张尺寸（paper_size）
# --------------------------------------------------------------------------- #


def _read_profile(client: TestClient, token: str, profile_id: object) -> dict[str, object]:
    response = client.get(f"{PROFILES_BASE}/{profile_id}", headers=_headers(token))
    assert response.status_code == 200, response.text
    profile: dict[str, object] = response.json()["data"]["profile"]
    return profile


def _put_content(
    client: TestClient,
    token: str,
    profile_id: object,
    **overrides: object,
) -> dict[str, object]:
    response = client.put(
        f"{PROFILES_BASE}/{profile_id}",
        json=_profile_payload(**overrides),
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    profile: dict[str, object] = response.json()["data"]["profile"]
    return profile


def test_paper_size_column_is_not_null_with_a4_default() -> None:
    """迁移只加一列：`varchar(16) NOT NULL DEFAULT 'A4'`。

    用 VARCHAR 而不是 ENUM 是刻意的：加一种纸只改代码不改表（见迁移注释）。
    """
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'student_profiles' "
                "AND COLUMN_NAME = 'paper_size'"
            )
        ).one()

    assert str(row.COLUMN_TYPE).lower() == "varchar(16)"
    assert row.IS_NULLABLE == "NO"
    assert row.COLUMN_DEFAULT == "A4"


def test_paper_size_defaults_to_a4(client: TestClient) -> None:
    """新建简历默认 A4；读接口必须给出具体值（不是 null）。"""
    token = _register_and_login(client)
    created = _create(client, token)

    assert created["paper_size"] == "A4"
    assert _read_profile(client, token, created["id"])["paper_size"] == "A4"
    # 兼容入口 `/profiles/me` 也要带上这个字段，旧前端不该看到 null。
    me = client.get(PROFILES_URL, headers=_headers(token))
    assert me.status_code == 200, me.text
    assert me.json()["data"]["profile"]["paper_size"] == "A4"


def test_paper_size_can_be_switched_to_letter(client: TestClient) -> None:
    """写入 Letter 后立刻生效，且重新读取仍是 Letter。"""
    token = _register_and_login(client)
    created = _create(client, token)

    updated = _put_content(client, token, created["id"], paper_size="Letter")

    assert updated["paper_size"] == "Letter"
    assert _read_profile(client, token, created["id"])["paper_size"] == "Letter"


def test_put_without_paper_size_keeps_existing(client: TestClient) -> None:
    """【关键防御】旧客户端（请求体里没有 `paper_size`）不能把纸张清回 A4。

    这是 ADR-007 §10.2 的核心：`PUT` 是整体替换，若按「None 即默认」处理，
    任何缓存了旧版 JS 的浏览器点一次保存就会把用户选的纸张悄悄改掉。
    """
    token = _register_and_login(client)
    created = _create(client, token)
    _put_content(client, token, created["id"], paper_size="Letter")

    # `_profile_payload()` 不含 paper_size —— 等价于 D5.1 之前的客户端。
    assert "paper_size" not in _profile_payload()
    repainted = _put_content(client, token, created["id"], full_name="李四")

    assert repainted["full_name"] == "李四"
    assert repainted["paper_size"] == "Letter", "没传 paper_size 时应保留原值"
    assert _read_profile(client, token, created["id"])["paper_size"] == "Letter"


def test_explicit_null_paper_size_resets_to_a4(client: TestClient) -> None:
    """显式传 null = 恢复默认（与「没传」区分开）。"""
    token = _register_and_login(client)
    created = _create(client, token)
    _put_content(client, token, created["id"], paper_size="Letter")

    reset = _put_content(client, token, created["id"], paper_size=None)

    assert reset["paper_size"] == "A4"


def test_invalid_paper_size_is_rejected(client: TestClient) -> None:
    """枚举外的值 422，且不落库（库里仍是原值）。"""
    token = _register_and_login(client)
    created = _create(client, token)

    for value in ("Legal", "a4", "A3", ""):
        response = client.put(
            f"{PROFILES_BASE}/{created['id']}",
            json=_profile_payload(paper_size=value),
            headers=_headers(token),
        )
        assert response.status_code == 422, f"{value!r} -> {response.text}"
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    assert _read_profile(client, token, created["id"])["paper_size"] == "A4"


def test_copy_from_copies_paper_size(client: TestClient) -> None:
    """深拷贝必须带上纸张：复制简历最常见的动机就是「同内容、换版式再投」。"""
    token = _register_and_login(client)
    source = _create(client, token, title="投北美")
    _put_content(client, token, source["id"], paper_size="Letter")

    copied = _create(client, token, title="投北美 副本", copy_from_id=source["id"])

    assert copied["paper_size"] == "Letter"
    assert copied["id"] != source["id"]


def test_unknown_paper_size_in_db_falls_back_to_a4(client: TestClient) -> None:
    """读路径永不因装饰性字段失败：库里被手工写成非法值时回落 A4，而不是 500。

    写路径仍然严格（上一个用例证明 422），这个不对称是 ADR-007 §10.4 定的。
    """
    token = _register_and_login(client)
    created = _create(client, token)
    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE student_profiles SET paper_size = 'Legal' WHERE id = :id"),
            {"id": created["id"]},
        )

    assert _read_profile(client, token, created["id"])["paper_size"] == "A4"


# --------------------------------------------------------------------------- #
# D5.2：配色主题（theme）
# --------------------------------------------------------------------------- #


def _relative_luminance(hex_color: str) -> float:
    """WCAG 2.x 相对亮度（ADR-007 §5.4 的公式）。"""
    digits = hex_color.lstrip("#")
    channels = [int(digits[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [
        channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_on_white(hex_color: str) -> float:
    """白纸（#FFFFFF，亮度恒为 1）上的对比度。"""
    return 1.05 / (_relative_luminance(hex_color) + 0.05)


def test_theme_palette_has_eight_safe_presets() -> None:
    """8 套预设的对比度是**离线保证**的，这条用例把它变成回归红线。

    `accent` 染 13px 的 h2（正文级 → ≥4.5:1），`rule` 染 2px 页眉线（UI → ≥3:1）。
    以后谁想调色，先得让这两条断言过——避免「用户挑到一套看不清的配色」。
    """
    assert len(THEME_IDS) == 8
    assert [theme.id for theme in THEMES.values()] == list(THEME_IDS)
    assert DEFAULT_THEME == "forest"

    for theme in THEMES.values():
        assert _contrast_on_white(theme.accent) >= 4.5, f"{theme.id} 的 accent 未过 AA 正文"
        assert _contrast_on_white(theme.rule) >= 3.0, f"{theme.id} 的 rule 未过 UI 3:1"
        assert theme.accent.startswith("#") and len(theme.accent) == 7, theme.accent
        assert theme.rule.startswith("#") and len(theme.rule) == 7, theme.rule


def test_theme_column_is_not_null_with_forest_default() -> None:
    """迁移只加一列：`varchar(20) NOT NULL DEFAULT 'forest'`。

    存的是预设 id 而不是 hex：换色只需改代码里的预设表，不用迁移；
    且「任意 hex」无法保证对比度（ADR-007 §5.2）。
    """
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'student_profiles' "
                "AND COLUMN_NAME = 'theme'"
            )
        ).one()

    assert str(row.COLUMN_TYPE).lower() == "varchar(20)"
    assert row.IS_NULLABLE == "NO"
    assert row.COLUMN_DEFAULT == "forest"


def test_theme_defaults_to_forest(client: TestClient) -> None:
    """新建简历默认 forest；读接口必须给出具体值（不是 null）。"""
    token = _register_and_login(client)
    created = _create(client, token)

    assert created["theme"] == "forest"
    assert _read_profile(client, token, created["id"])["theme"] == "forest"
    me = client.get(PROFILES_URL, headers=_headers(token))
    assert me.status_code == 200, me.text
    assert me.json()["data"]["profile"]["theme"] == "forest"


def test_theme_can_be_switched_to_wine(client: TestClient) -> None:
    """写入预设 id 后立刻生效，且重新读取仍是它。"""
    token = _register_and_login(client)
    created = _create(client, token)

    updated = _put_content(client, token, created["id"], theme="wine")

    assert updated["theme"] == "wine"
    assert _read_profile(client, token, created["id"])["theme"] == "wine"


def test_put_without_theme_keeps_existing(client: TestClient) -> None:
    """【关键防御】旧客户端（请求体里没有 `theme`）不能把配色清回 forest。

    `PUT` 是整体替换，若按「None 即默认」处理，任何缓存了旧版 JS 的标签页
    点一次保存就会把用户挑的颜色悄悄改掉——与 D5.1 的纸张是同一个坑。
    """
    token = _register_and_login(client)
    created = _create(client, token)
    _put_content(client, token, created["id"], theme="navy")

    # `_profile_payload()` 不含 theme —— 等价于 D5.2 之前的客户端。
    assert "theme" not in _profile_payload()
    repainted = _put_content(client, token, created["id"], full_name="李四")

    assert repainted["full_name"] == "李四"
    assert repainted["theme"] == "navy", "没传 theme 时应保留原值"
    assert _read_profile(client, token, created["id"])["theme"] == "navy"


def test_explicit_null_theme_resets_to_forest(client: TestClient) -> None:
    """显式传 null = 恢复默认（与「没传」区分开）。"""
    token = _register_and_login(client)
    created = _create(client, token)
    _put_content(client, token, created["id"], theme="plum")

    reset = _put_content(client, token, created["id"], theme=None)

    assert reset["theme"] == "forest"


def test_invalid_theme_is_rejected(client: TestClient) -> None:
    """枚举外的值 422，且不落库（库里仍是原值）。

    特别包含 `#527873`：**不允许用户自己发 HEX**，颜色只能来自预设表。
    """
    token = _register_and_login(client)
    created = _create(client, token)

    for value in ("Forest", "red", "#527873", "navy2", ""):
        response = client.put(
            f"{PROFILES_BASE}/{created['id']}",
            json=_profile_payload(theme=value),
            headers=_headers(token),
        )
        assert response.status_code == 422, f"{value!r} -> {response.text}"
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    assert _read_profile(client, token, created["id"])["theme"] == "forest"


def test_copy_from_copies_theme(client: TestClient) -> None:
    """深拷贝必须带上配色：复制简历的动机就是「同内容、换风格再投」。"""
    token = _register_and_login(client)
    source = _create(client, token, title="投国企")
    _put_content(client, token, source["id"], theme="wine")

    copied = _create(client, token, title="投国企 副本", copy_from_id=source["id"])

    assert copied["theme"] == "wine"
    assert copied["id"] != source["id"]


def test_unknown_theme_in_db_falls_back_to_forest(client: TestClient) -> None:
    """读路径永不因装饰性字段失败：库里被手工写成非法值时回落 forest，而不是 500。

    写路径仍然严格（上一个用例证明 422），这个不对称是 ADR-007 §10.4 定的。
    """
    token = _register_and_login(client)
    created = _create(client, token)
    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE student_profiles SET theme = 'sunset' WHERE id = :id"),
            {"id": created["id"]},
        )

    assert _read_profile(client, token, created["id"])["theme"] == "forest"


# --------------------------------------------------------------------------- #
# D5.3：排版（layout_config）
# --------------------------------------------------------------------------- #

#: 一组合法的自定义排版：顺序打乱、标题全改（ADR-015 起 version=2、白名单 5 段）。
CUSTOM_LAYOUT: dict[str, object] = {
    "version": 2,
    "sections": [
        {"id": "skills", "title": "技术栈"},
        {"id": "experiences", "title": "项目经验"},
        {"id": "educations", "title": "教育背景"},
        {"id": "certificates", "title": "荣誉证书"},
        {"id": "portfolio", "title": "我的作品"},
    ],
}

#: 读侧在 `layout_config IS NULL` 时给出的解析结果（= 默认顺序，5 段）。
DEFAULT_LAYOUT: dict[str, object] = {
    "version": 2,
    "sections": [
        {"id": "educations", "title": "教育经历"},
        {"id": "skills", "title": "技能"},
        {"id": "experiences", "title": "项目经历"},
        {"id": "certificates", "title": "证书"},
        {"id": "portfolio", "title": "作品链接"},
    ],
}


def test_layout_config_column_is_nullable_json() -> None:
    """迁移只加一列：`JSON NULL`，且**没有默认值**。

    `NULL` 是有语义的（「从未自定义」），不能像 `paper_size` 那样给
    `server_default` —— 否则「没调过顺序」和「调成了默认顺序」就分不出来了。
    """
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'student_profiles' "
                "AND COLUMN_NAME = 'layout_config'"
            )
        ).one()

    assert str(row.DATA_TYPE).lower() == "json"
    assert row.IS_NULLABLE == "YES"
    assert row.COLUMN_DEFAULT is None


def test_layout_config_defaults_to_parsed_default_order(client: TestClient) -> None:
    """新简历库里是 NULL，但**读接口给的是解析后的默认顺序**（不是 null）。

    读侧恒为具体值，前端就不用把降级矩阵再实现一遍 —— 它拿到的顺序
    与生成器用的是同一份（ADR-007 §10.2 的既定口径）。
    """
    token = _register_and_login(client)
    created = _create(client, token)

    assert created["layout_config"] == DEFAULT_LAYOUT
    assert _read_profile(client, token, created["id"])["layout_config"] == DEFAULT_LAYOUT

    with get_engine().connect() as connection:
        stored = connection.execute(
            text("SELECT layout_config FROM student_profiles WHERE id = :id"),
            {"id": created["id"]},
        ).scalar_one()
    assert stored is None, "「从未自定义」必须真的存 NULL，否则将来无法自动跟上新默认顺序"


def test_layout_config_can_be_saved_and_reread(client: TestClient) -> None:
    """自定义顺序与标题落库后原样读回（顺序有意义，不能被排序丢掉）。"""
    token = _register_and_login(client)
    created = _create(client, token)

    saved = _put_content(client, token, created["id"], layout_config=CUSTOM_LAYOUT)

    assert saved["layout_config"] == CUSTOM_LAYOUT
    assert _read_profile(client, token, created["id"])["layout_config"] == CUSTOM_LAYOUT
    assert [item["id"] for item in saved["layout_config"]["sections"]] == [
        "skills",
        "experiences",
        "educations",
        "certificates",
        "portfolio",
    ]


def test_put_without_layout_config_keeps_existing(client: TestClient) -> None:
    """三态之一：请求体不含该键 → 保留库里原值（旧客户端安全）。"""
    token = _register_and_login(client)
    created = _create(client, token)
    _put_content(client, token, created["id"], layout_config=CUSTOM_LAYOUT)

    payload = _profile_payload()  # D5.3 之前旧前端的形状：没有 layout_config
    assert "layout_config" not in payload
    response = client.put(f"{PROFILES_BASE}/{created['id']}", json=payload, headers=_headers(token))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["profile"]["layout_config"] == CUSTOM_LAYOUT


def test_explicit_null_layout_config_resets_to_default(client: TestClient) -> None:
    """三态之二：显式 `null` → 恢复默认顺序（库里回到 NULL）。"""
    token = _register_and_login(client)
    created = _create(client, token)
    _put_content(client, token, created["id"], layout_config=CUSTOM_LAYOUT)

    reset = _put_content(client, token, created["id"], layout_config=None)

    assert reset["layout_config"] == DEFAULT_LAYOUT
    with get_engine().connect() as connection:
        stored = connection.execute(
            text("SELECT layout_config FROM student_profiles WHERE id = :id"),
            {"id": created["id"]},
        ).scalar_one()
    assert stored is None, "「恢复默认」要写回 NULL，而不是写一份默认顺序的快照"


def test_layout_config_rejects_duplicate_ids(client: TestClient) -> None:
    """写路径严格：重复 id 会让前端 `v-for` 的 key 冲突，直接 422。"""
    token = _register_and_login(client)
    created = _create(client, token)

    response = client.put(
        f"{PROFILES_BASE}/{created['id']}",
        json=_profile_payload(
            layout_config={
                "version": 2,
                "sections": [
                    {"id": "skills", "title": "技能"},
                    {"id": "skills", "title": "技能 2"},
                ],
            }
        ),
        headers=_headers(token),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _read_profile(client, token, created["id"])["layout_config"] == DEFAULT_LAYOUT


def test_layout_config_rejects_unknown_section_id(client: TestClient) -> None:
    """未知模块 id 在写入时 422（只有读路径才降级丢弃）。"""
    token = _register_and_login(client)
    created = _create(client, token)

    response = client.put(
        f"{PROFILES_BASE}/{created['id']}",
        json=_profile_payload(
            layout_config={"version": 2, "sections": [{"id": "awards", "title": "获奖"}]}
        ),
        headers=_headers(token),
    )

    assert response.status_code == 422, response.text


def test_layout_config_rejects_blank_or_overlong_title(client: TestClient) -> None:
    """标题为空 / 全空白 / 超过 12 字都 422：空标题在纸面上是个空 `h2`。"""
    token = _register_and_login(client)
    created = _create(client, token)

    for title in ("", "   ", "项目经历与开源贡献说明文档"):
        response = client.put(
            f"{PROFILES_BASE}/{created['id']}",
            json=_profile_payload(
                layout_config={"version": 2, "sections": [{"id": "skills", "title": title}]}
            ),
            headers=_headers(token),
        )
        assert response.status_code == 422, f"{title!r} -> {response.text}"


def test_layout_config_rejects_unknown_version_and_content_fields(client: TestClient) -> None:
    """`version` 只认 2（ADR-015 升版）；顺手在元信息里塞内容字段也 422（`extra="forbid"`）。"""
    token = _register_and_login(client)
    created = _create(client, token)

    bad_version = client.put(
        f"{PROFILES_BASE}/{created['id']}",
        json=_profile_payload(
            layout_config={"version": 1, "sections": [{"id": "skills", "title": "技能"}]}
        ),
        headers=_headers(token),
    )
    assert bad_version.status_code == 422, bad_version.text

    smuggled_content = client.put(
        f"{PROFILES_BASE}/{created['id']}",
        json=_profile_payload(
            layout_config={
                "version": 2,
                "sections": [{"id": "skills", "title": "技能", "items": ["Python"]}],
            }
        ),
        headers=_headers(token),
    )
    assert smuggled_content.status_code == 422, smuggled_content.text


def test_copy_from_copies_layout_config(client: TestClient) -> None:
    """深拷贝必须带上排版，且**互不影响**（ADR-007 §6.8(5) 的 `deepcopy`）。"""
    token = _register_and_login(client)
    source = _create(client, token, title="投算法")
    _put_content(client, token, source["id"], layout_config=CUSTOM_LAYOUT)

    copied = _create(client, token, title="投算法 副本", copy_from_id=source["id"])

    assert copied["layout_config"] == CUSTOM_LAYOUT

    # 改源简历的排版，副本必须纹丝不动（共享引用的话这里会一起变）。
    _put_content(
        client,
        token,
        source["id"],
        layout_config={
            "version": 2,
            "sections": [
                {"id": "educations", "title": "学历"},
                {"id": "skills", "title": "技术"},
                {"id": "experiences", "title": "项目"},
                {"id": "certificates", "title": "证书"},
                {"id": "portfolio", "title": "作品"},
            ],
        },
    )
    assert _read_profile(client, token, copied["id"])["layout_config"] == CUSTOM_LAYOUT


def test_broken_layout_in_db_falls_back_in_read(client: TestClient) -> None:
    """库里被写成非法结构时读接口不 500，一律退化为默认顺序。"""
    token = _register_and_login(client)
    created = _create(client, token)

    broken_values = ['"junk"', "[]", '{"version": 9, "sections": [{"id": "skills"}]}']
    for raw in broken_values:
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    "UPDATE student_profiles SET layout_config = CAST(:raw AS JSON) WHERE id = :id"
                ),
                {"raw": raw, "id": created["id"]},
            )

        assert _read_profile(client, token, created["id"])["layout_config"] == DEFAULT_LAYOUT, raw


def test_layout_config_is_not_in_profile_summary(client: TestClient) -> None:
    """列表刻意不带排版：列表只回答「是哪一份」，多一列就多一份全量读取。"""
    token = _register_and_login(client)
    created = _create(client, token)

    response = client.get(PROFILES_BASE, headers=_headers(token))

    assert response.status_code == 200, response.text
    row = next(item for item in response.json()["data"]["profiles"] if item["id"] == created["id"])
    assert "layout_config" not in row
