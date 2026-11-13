"""批次 B7：技能元数据（来源 / 最近使用 / 首次加入）——`docs/adr/ADR-027-*.md`。

守的是 ADR-027 §4 的十条判据，外加一条读侧降级（§2.1 第 1 条）。刻意分成三层：

1. **纯模型**（不启 FastAPI、不碰库）：读侧遇到未知来源降级成 `self_reported`。
   这条造不出 HTTP 输入（库上的 CHECK 会先拒），所以只能在这一层钉住；
2. **HTTP + 查库**：落库值是真的。这一层不能省——`SkillRead` 的每个新字段都有默认值，
   响应里出现 `source == "self_reported"` 完全可能只是 pydantic 兜了默认值，
   而库里什么都没有。那正是这一批最容易被写成「看起来对」的地方；
3. **迁移**：存量行回填 `first_added_at = created_at`（§2.1 第 2 条）。

两条最容易写错的判据（§1.2 / §1.3）在这里是这样验的：

- 「最近使用」**只能来自用户**：所以它的用例是「填了就有、不传就是空」，
  而不是「保存完自动有个时间」——后者恰恰是要被挡住的实现；
- 「首次加入」**跨全量替换保留**：所以既有「反复保存」也有「删掉再加回来」，
  且都要断言历史行数 ≥ 2，否则「保留」可能只是因为压根没重写。
"""

import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.core.datetime_display import shanghai_today
from app.core.error_messages import ErrorMessage
from app.repositories.user_repository import get_engine
from app.schemas.profile import SkillRead
from app.services.resume_diff import parse_snapshot
from tests.accounts import login, register

API = "/api/v1"
PROFILES_URL = f"{API}/profiles/me"
GENERATE_URL = f"{API}/resumes/generate"

#: 迁移 0025 的前一条 revision：`test_migration_backfills_first_added_at` 要先退到这里。
BEFORE_METADATA_REVISION = "20261113_0024"


# --------------------------------------------------------------------------- #
# 夹具与帮手
# --------------------------------------------------------------------------- #


def _headers(client: TestClient) -> dict[str, str]:
    username = f"b7_{uuid4().hex[:10]}"
    created = register(client, username, email=f"{username}@example.com")
    assert created.status_code == 201, created.text
    return login(client, username)


def _payload(skills: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    """一份**最小可保存**的档案：保存不拦必填（草稿式），所以空白骨架也合法。"""
    return {
        "full_name": "张三",
        "contact": "13800000000",
        "skills": skills,
        "experiences": [],
        "educations": [],
        **overrides,
    }


def _save(
    client: TestClient,
    headers: dict[str, str],
    skills: list[dict[str, Any]],
    **overrides: Any,
) -> dict[str, Any]:
    response = client.put(PROFILES_URL, json=_payload(skills, **overrides), headers=headers)
    assert response.status_code == 200, response.text
    profile: dict[str, Any] = response.json()["data"]["profile"]
    return profile


def _db_skills(profile_id: str) -> list[dict[str, Any]]:
    """按 `created_at` 取**全部**行（含软删除的历史行）。

    直接查库而不是从响应里看：全量替换之后，响应只回当前活跃的那几条，
    而「首次加入」的证据恰好在历史行里（ADR-027 §1.3）。
    """
    with get_engine().connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT name, source, last_used_on, first_added_at, created_at, deleted_at "
                    "FROM skills WHERE student_profile_id = :profile_id "
                    "ORDER BY created_at, name"
                ),
                {"profile_id": profile_id},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _active_db_skills(profile_id: str) -> list[dict[str, Any]]:
    return [row for row in _db_skills(profile_id) if row["deleted_at"] is None]


def _messages(response: Any) -> list[str]:
    """422 里逐字段那句话（顶层 `error.message` 是固定总述，不含具体原因）。"""
    details = response.json()["error"]["details"]
    return [str(item["message"]) for item in details]


# --------------------------------------------------------------------------- #
# 1. 纯模型：三条 422 与一条读侧降级
# --------------------------------------------------------------------------- #


def test_unknown_source_is_read_as_self_reported() -> None:
    """读侧遇到未知来源降级成 `self_reported`，不抛 500（ADR-027 §2.1 第 1 条）。

    库上的 `ck_skills_source` 保证今天写不进第五个值，所以这条分支正常走不到；
    它挡的是「将来多加了一个来源、这版 API 还没跟上」的滚动窗口——那时候若
    `Literal` 直接抛异常，**整份档案**都读不出来（一个技能标签把简历页打挂）。
    降级方向也是安全的那一侧：弱来源显示成基准档，不伪造证据。

    写成模型级用例而不是走 HTTP：走 HTTP 造不出这个输入（库会先拒），
    而「造不出的输入」恰恰是最容易在重构时被静默删掉的那条分支。
    """
    degraded = SkillRead.model_validate({"id": "x", "name": "Python", "source": "telepathy"})
    assert degraded.source == "self_reported"

    for known in ("self_reported", "assessment", "project", "teacher_confirmed"):
        kept = SkillRead.model_validate({"id": "x", "name": "Python", "source": known})
        assert kept.source == known


# --------------------------------------------------------------------------- #
# 2. HTTP + 查库：落库值、可写集合、两个 422
# --------------------------------------------------------------------------- #


def test_source_defaults_to_self_reported(client: TestClient) -> None:
    """不传 `source` 时落库为 `self_reported`（默认值是**真话**：没人核过）。

    同时钉住 `first_added_at` 在第一次保存后就有值、`last_used_on` 是空——
    后者是 ADR-027 §1.2 的核心：「最近使用」用户没说就是不知道，
    不许拿保存时间冒充。
    """
    headers = _headers(client)

    profile = _save(client, headers, [{"name": "Python"}])

    skill = profile["skills"][0]
    assert skill["source"] == "self_reported"
    assert skill["last_used_on"] is None
    assert skill["first_added_at"] is not None

    rows = _active_db_skills(profile["id"])
    assert [(row["name"], row["source"], row["last_used_on"]) for row in rows] == [
        ("Python", "self_reported", None)
    ]
    # 首次加入 = 这次保存（没有历史可查），但**不是** NULL：
    # 回填只管存量行，新行必须当场写进去，否则要等一次「有人重存」才出现。
    assert rows[0]["first_added_at"] is not None


def test_writable_sources_round_trip(client: TestClient) -> None:
    """三个可写来源原样落库并回读（`assessment` 不在其中，见下一条）。"""
    headers = _headers(client)

    profile = _save(
        client,
        headers,
        [
            {"name": "Python", "source": "project"},
            {"name": "SQL", "source": "teacher_confirmed"},
            {"name": "React", "source": "self_reported"},
        ],
    )

    assert {skill["name"]: skill["source"] for skill in profile["skills"]} == {
        "Python": "project",
        "SQL": "teacher_confirmed",
        "React": "self_reported",
    }
    assert {row["name"]: row["source"] for row in _active_db_skills(profile["id"])} == {
        "Python": "project",
        "SQL": "teacher_confirmed",
        "React": "self_reported",
    }


def test_metadata_only_change_is_saved(client: TestClient) -> None:
    """**只改元数据**也必须落库——不能被服务层的 no-op 检测吃掉（`ADR-027` §2.4）。

    `ProfileService._content_unchanged`（`ADR-018` §5）本来是省副作用用的：内容没变时
    不重建子表、不推进 `updated_at`。B7 第一版让它只比技能**名称**，于是
    「把自评改成项目」被判成没变更 → 用户的编辑被静默丢弃，而响应回的还是旧值，
    界面上看起来「保存成功」。这条用例就是那次真实的失败（不是假想出来的边界）。

    判据选「来源」而不是「最近使用」：后者在同批另一条用例里已经覆盖，
    而「只改来源、日期不动」正好是只比名称时唯一会漏的形状。
    """
    headers = _headers(client)

    first = _save(client, headers, [{"name": "Python"}])
    assert first["skills"][0]["source"] == "self_reported"

    second = _save(client, headers, [{"name": "Python", "source": "teacher_confirmed"}])

    assert second["skills"][0]["source"] == "teacher_confirmed"
    assert _active_db_skills(first["id"])[0]["source"] == "teacher_confirmed"
    # 真的重写了一次（旧行软删 + 新行插入），而不是「响应变了、库没动」。
    assert len(_db_skills(first["id"])) == 2


def test_omitted_source_keeps_server_written_assessment(client: TestClient) -> None:
    """省略 `source` = 沿用库里那一档，**不是**清空（`ADR-027` §2.3）。

    这个用例的主角是 `assessment`：它只能由测评提交时写进去（`ADR-028` §2.6），
    客户端「整格不传」才是正确的表态。若后端把省略当成 `self_reported`，
    用户测完评之后随便改一个字再保存，测评写下的来源就被自己的保存动作抹掉了，
    而界面上不会有任何提示——B9 的提交于是变成一次性装饰。

    造数据的方式是直接 UPDATE 那一列：B9 的提交端点本批还没写，
    这是唯一能在 B7 的用例里表达「服务端写过它」的方式。第三步同时钉住**反向**：
    用户显式选回「自评」时必须能覆盖掉它，否则这一格就成了用户改不动的东西。
    """
    headers = _headers(client)
    profile = _save(client, headers, [{"name": "Python"}])
    assert _active_db_skills(profile["id"])[0]["source"] == "self_reported"

    # 模拟 B9 的提交动作（服务端写，客户端不可写）。
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "UPDATE skills SET source = 'assessment' "
                "WHERE student_profile_id = :profile_id AND name = 'Python'"
            ),
            {"profile_id": profile["id"]},
        )

    kept = _save(client, headers, [{"name": "Python"}])
    assert kept["skills"][0]["source"] == "assessment"
    assert _active_db_skills(profile["id"])[0]["source"] == "assessment"

    overridden = _save(client, headers, [{"name": "Python", "source": "self_reported"}])
    assert overridden["skills"][0]["source"] == "self_reported"
    assert _active_db_skills(profile["id"])[0]["source"] == "self_reported"


def test_assessment_source_is_not_writable_by_clients(client: TestClient) -> None:
    """客户端传 `assessment` → 422，且**一行都不落库**。

    允许客户端自称「已被测评验证」，等于让人在没有测评的情况下伪造更强的来源
    （ADR-027 §2.2）。所以这里既断言状态码，也断言文案（用户要知道该选什么），
    最后再用一次合法保存证明上一次请求没有写进任何东西。
    """
    headers = _headers(client)

    rejected = client.put(
        PROFILES_URL,
        json=_payload([{"name": "Python", "source": "assessment"}]),
        headers=headers,
    )

    assert rejected.status_code == 422, rejected.text
    body = rejected.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert ErrorMessage.SKILL_SOURCE_ASSESSMENT in _messages(rejected)
    assert body["details"][0]["loc"] == ["body", "skills", 0, "source"]

    profile = _save(client, headers, [{"name": "Python"}])
    assert len(_db_skills(profile["id"])) == 1


def test_unknown_source_is_rejected(client: TestClient) -> None:
    """拼错的来源同样 422——但它不该说「测评来源不能选」（那是另一件事的提示）。"""
    headers = _headers(client)

    rejected = client.put(
        PROFILES_URL,
        json=_payload([{"name": "Python", "source": "self_rated"}]),
        headers=headers,
    )

    assert rejected.status_code == 422, rejected.text
    body = rejected.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"][0]["loc"] == ["body", "skills", 0, "source"]
    messages = _messages(rejected)
    # `CHOICE_FIELD` 是带 `{field}` 的模板（"技能选项无效，…"），不能整串比。
    assert any("选项无效" in message for message in messages), messages
    assert ErrorMessage.SKILL_SOURCE_ASSESSMENT not in messages


# --------------------------------------------------------------------------- #
# 3. 最近使用：填了就有、不传就清、未来不许
# --------------------------------------------------------------------------- #


def test_last_used_on_round_trip_and_clear(client: TestClient) -> None:
    """`last_used_on` 如实落库；第二次不带它就是清空（PUT 的整体覆盖语义）。

    「不传 = 清空」与 `target_role` 同一条口径（ADR-027 §2.3），不是「不传 = 保留」。
    这条必须显式测：全量替换下，「保留」和「清空」在库里长得一样（都是新行），
    只有断言值才能分开。
    """
    headers = _headers(client)

    filled = _save(client, headers, [{"name": "Python", "last_used_on": "2026-05-01"}])
    assert filled["skills"][0]["last_used_on"] == "2026-05-01"
    assert _active_db_skills(filled["id"])[0]["last_used_on"] == date(2026, 5, 1)

    cleared = _save(client, headers, [{"name": "Python"}])
    assert cleared["skills"][0]["last_used_on"] is None
    assert _active_db_skills(cleared["id"])[0]["last_used_on"] is None


def test_last_used_on_cannot_be_in_the_future(client: TestClient) -> None:
    """未来日期 422；**今天**正是边界，必须放行。

    边界只测一边等于没测：把判据写成 `>=` 的话，「今天用过」会莫名报错，
    而用户根本不知道自己哪里填错了。
    """
    headers = _headers(client)
    tomorrow = (shanghai_today() + timedelta(days=1)).isoformat()

    rejected = client.put(
        PROFILES_URL,
        json=_payload([{"name": "Python", "last_used_on": tomorrow}]),
        headers=headers,
    )

    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert ErrorMessage.SKILL_LAST_USED_FUTURE in _messages(rejected)
    assert rejected.json()["error"]["details"][0]["loc"] == ["body", "skills", 0, "last_used_on"]

    accepted = _save(
        client,
        headers,
        [{"name": "Python", "last_used_on": shanghai_today().isoformat()}],
    )
    assert accepted["skills"][0]["last_used_on"] == shanghai_today().isoformat()


# --------------------------------------------------------------------------- #
# 4. 首次加入：跨全量替换保留（本批唯一要查历史行的地方）
# --------------------------------------------------------------------------- #


def test_first_added_at_survives_resaves(client: TestClient) -> None:
    """反复保存不清零 `first_added_at`（ADR-027 §1.3）。

    隔 1.1 秒再保存：`first_added_at` 若错写成「本次保存时间」，差值会立刻显形；
    不等待的话两次保存可能落在同一微秒里，用例会「通过」得莫名其妙。
    """
    headers = _headers(client)

    first = _save(client, headers, [{"name": "Python"}])
    original = first["skills"][0]["first_added_at"]

    time.sleep(1.1)

    second = _save(client, headers, [{"name": "Python", "source": "project"}])

    assert second["skills"][0]["first_added_at"] == original
    # 全量替换确实发生了（旧的软删 + 新的插入），否则这条断言证明不了「保留」。
    rows = _db_skills(first["id"])
    assert len(rows) == 2
    assert sum(1 for row in rows if row["deleted_at"] is None) == 1


def test_first_added_at_survives_removal_and_readdition(client: TestClient) -> None:
    """删掉再加回来，仍取**最早**那一条的时间。

    和「反复保存」是两件事：那一条有活跃行可查，这一条只剩软删除的历史行——
    若 `_first_added_map` 只扫活跃行（写成 `list_skills()` 很容易这样），
    这里会退回「今天刚加」，而用户看到的是「我从 3 月就在用」。
    """
    headers = _headers(client)

    first = _save(client, headers, [{"name": "Python"}, {"name": "SQL"}])
    original = next(s for s in first["skills"] if s["name"] == "Python")["first_added_at"]

    time.sleep(1.1)
    _save(client, headers, [{"name": "SQL"}])  # Python 被软删

    time.sleep(1.1)
    restored = _save(client, headers, [{"name": "Python"}])  # 加回来

    assert (
        next(s for s in restored["skills"] if s["name"] == "Python")["first_added_at"] == original
    )
    rows = _db_skills(first["id"])
    assert len(rows) == 4  # Python×2（一删一留）+ SQL×2（一删一留）
    assert any(row["name"] == "Python" and row["deleted_at"] is not None for row in rows)


def test_migration_backfills_first_added_at(client: TestClient) -> None:
    """迁移 `0025` 给存量行回填 `first_added_at = created_at`（ADR-027 §2.1 第 2 条）。

    「退到 0024 → 造一行旧数据 → 升到 head」是唯一能证明回填真的执行了的方式：
    在 head 上插一行再断言，`first_added_at` 由写入路径给值，回填那行 SQL 删掉也照样绿。

    顺带钉住列精度：`skills.created_at` 是 `DATETIME(6)`，`first_added_at` 若是
    `DATETIME`（精度 0），回填会被四舍五入到整秒，两个值当场对不上——
    这正是本轮修掉的那个坑，用例把它留在这里当护栏。
    """
    headers = _headers(client)
    saved = _save(client, headers, [{"name": "Python"}])
    profile_id = saved["id"]

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    try:
        command.downgrade(config, BEFORE_METADATA_REVISION)
        with get_engine().connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'skills'"
                    )
                ).scalars()
            )
        # downgrade 真的有损：三列直接没了（ADR-027 §2.1 第 3 条，不假装可回退）。
        assert "first_added_at" not in columns
        assert "source" not in columns

        command.upgrade(config, "head")

        with get_engine().connect() as connection:
            precision = connection.execute(
                text(
                    "SELECT DATETIME_PRECISION FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'skills' "
                    "  AND COLUMN_NAME = 'first_added_at'"
                )
            ).scalar_one()
            skill = connection.execute(
                text(
                    "SELECT first_added_at, created_at, source FROM skills "
                    "WHERE student_profile_id = :profile_id"
                ),
                {"profile_id": profile_id},
            ).one()
    finally:
        # 无论断言是否失败都交回 head（与本仓库 `test_migrations.py` 同一纪律）。
        command.upgrade(config, "head")

    assert int(precision) == 6
    assert skill.first_added_at == skill.created_at
    assert skill.source == "self_reported"


# --------------------------------------------------------------------------- #
# 5. 来源不影响生成物
# --------------------------------------------------------------------------- #


def test_source_does_not_change_rendered_resume(client: TestClient) -> None:
    """改来源不改变生成的简历（ADR-027 §2.4）：生成器今天只渲染技能名。

    这不是「暂时不用测」而是**必须测**：来源迟早会进画像与筛选，
    届时最省事的错误做法是顺手把它塞进模板（`Python（项目）`）。
    那会让同一份档案在「来源从自评改成项目」后凭空多出一版差异，
    而用户看到的简历内容其实一个字都没改。
    """
    headers = _headers(client)

    base: dict[str, Any] = {
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

    _save(client, headers, [{"name": "Python"}, {"name": "SQL"}], **base)
    plain = client.post(GENERATE_URL, json={"title": "我的简历"}, headers=headers)
    assert plain.status_code == 200, plain.text

    _save(
        client,
        headers,
        [
            {"name": "Python", "source": "project", "last_used_on": "2026-05-01"},
            {"name": "SQL", "source": "teacher_confirmed"},
        ],
        **base,
    )
    labelled = client.post(GENERATE_URL, json={"title": "我的简历"}, headers=headers)
    assert labelled.status_code == 200, labelled.text

    # 只比技能区块：整份 HTML 里 `created_at` 之类的时间戳本来就会不同，
    # 拿整串比较等于把「时间戳没变」也写进了判据，红了也说不清是什么坏了。
    assert parse_snapshot(labelled.json()["data"]["content_html"]).item_labels("skills") == [
        "Python",
        "SQL",
    ]
    assert parse_snapshot(labelled.json()["data"]["content_html"]).item_labels(
        "skills"
    ) == parse_snapshot(plain.json()["data"]["content_html"]).item_labels("skills")
