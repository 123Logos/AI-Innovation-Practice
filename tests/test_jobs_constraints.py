"""岗位库的**数据库层**契约（Phase 4 · B1）。

这个文件只问一件事：迁移建出来的约束，在真实 MySQL 上是不是真的拦得住
（AGENTS.md §4.4「去重语义必须由测试证明，不得靠推断」）。
API 行为在 `tests/test_jobs.py`，这里一律绕过 HTTP 直接写 SQL ——
否则「应用层校验」会把数据库的洞遮起来，测试绿了但约束并不存在。

五件必须钉住的事：

1. `ck_jobs_status` 只有 `draft`/`published`，审核态写不进去（1.A/1.B）；
2. `ck_job_skills_origin` **只有两个值**（`jd_verifiable` / `unverified`，1.D v7 改名），
   v1 的 `user_edited` 已经删掉；
3. `UNIQUE (job_id, name)` 在 `utf8mb4_0900_ai_ci` 下真的把
   `Python` / `python` / `Ｐython`（首字母全角）折成同一行（1.D 的假设，必须实测）；
4. 两条外键的删除规则不同：删账号挡住（RESTRICT），删岗位连带技能（CASCADE）；
5. 「同一个技能」的判定在应用层与数据库必须同口径：`skill_name_key` 相等 ⟹ 撞
   `UNIQUE (job_id, name)`，`skill_name_key` 不等 ⟹ 库里真的留两行（C1 补正：
   提取端要按这个键做重名合并，两处判断不一致就会把注定失败的 payload 交给用户）。

另有一条架构断言：`app/services/job_service.py` 里不出现 `HTTPException` / `fastapi`
（AGENTS.md §4.1 + ADR-011 1.F）——这条纪律不能只写在 docstring 里。
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.core.skill_names import skill_name_key
from app.integrations.skill_extractor import normalize_for_trace
from app.repositories import job_repository
from app.repositories.user_repository import get_engine
from app.services import job_service

JOB_COLUMNS = {
    "id",
    "created_by_user_id",
    "company_name",
    "title",
    "location",
    "employment_type",
    "description",
    "salary_min",
    "salary_max",
    "experience_required",
    "deadline",
    "status",
    "extract_model",
    "extract_prompt_version",
    "extracted_at",
    "deleted_at",
    "created_at",
    "updated_at",
}

SKILL_COLUMNS = {
    "id",
    "job_id",
    "name",
    "type",
    "origin",
    "confidence",
    "evidence_text",
    "created_at",
    "updated_at",
}


def _naive_utc_now() -> datetime:
    """库里存的是 naive UTC，测试直接写时间要同口径（见 `app/core/datetime_display.py`）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _insert_user() -> str:
    """插一个用户行（不走 HTTP）：约束测试只关心数据库，不该依赖接口是否可用。"""
    user_id = str(uuid4())
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, created_at, updated_at) "
                "VALUES (:id, :username, 'x', :now, :now)"
            ),
            {
                "id": user_id,
                "username": f"probe_{user_id[:12]}",
                "now": _naive_utc_now(),
            },
        )
    return user_id


def _insert_job(user_id: str, **overrides: Any) -> str:
    """插一行岗位，返回 id。默认值覆盖所有 NOT NULL 列，用例只改自己在意的那一列。"""
    job_id = str(uuid4())
    now = _naive_utc_now()
    row: dict[str, Any] = {
        "id": job_id,
        "created_by_user_id": user_id,
        "company_name": "探针科技",
        "title": "探针岗位",
        "description": "JD 原文",
        "status": "draft",
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    columns = ", ".join(row)
    placeholders = ", ".join(f":{key}" for key in row)
    with get_engine().begin() as connection:
        connection.execute(text(f"INSERT INTO jobs ({columns}) VALUES ({placeholders})"), row)
    return job_id


def _insert_skill(job_id: str, name: str, **overrides: Any) -> str:
    skill_id = str(uuid4())
    now = _naive_utc_now()
    row: dict[str, Any] = {
        "id": skill_id,
        "job_id": job_id,
        "name": name,
        "origin": "unverified",
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    columns = ", ".join(row)
    placeholders = ", ".join(f":{key}" for key in row)
    with get_engine().begin() as connection:
        connection.execute(text(f"INSERT INTO job_skills ({columns}) VALUES ({placeholders})"), row)
    return skill_id


def _skill_count(job_id: str) -> int:
    with get_engine().connect() as connection:
        return int(
            connection.execute(
                text("SELECT COUNT(*) FROM job_skills WHERE job_id = :id"), {"id": job_id}
            ).scalar_one()
        )


def _table_columns(table: str) -> dict[str, dict[str, str]]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
            ),
            {"table": table},
        ).all()
    return {
        row.COLUMN_NAME: {
            "type": str(row.COLUMN_TYPE),
            "nullable": row.IS_NULLABLE,
            "default": row.COLUMN_DEFAULT,
            "extra": str(row.EXTRA),
        }
        for row in rows
    }


# --------------------------------------------------------------------------- #
# 列形状（ADR-011 1.B / 1.D 逐字对照）
# --------------------------------------------------------------------------- #


def test_jobs_columns_match_adr_011_1b() -> None:
    """`jobs` 的列集合与关键列型必须逐字等于 1.B 的表，多一列少一列都算实现偏离设计。"""
    columns = _table_columns("jobs")
    assert set(columns) == JOB_COLUMNS

    assert columns["id"]["type"] == "varchar(36)"
    assert columns["created_by_user_id"]["type"] == "varchar(36)"
    assert columns["created_by_user_id"]["nullable"] == "NO"
    assert columns["company_name"]["type"] == "varchar(200)"
    assert columns["title"]["type"] == "varchar(200)"
    assert columns["location"]["type"] == "varchar(160)"
    assert columns["location"]["nullable"] == "YES"
    assert columns["employment_type"]["type"] == "varchar(20)"
    assert columns["employment_type"]["nullable"] == "YES"
    # JD 原文是 TEXT，8000 字上限在应用层（2.F），不靠列宽兜
    assert columns["description"]["type"] == "text"
    assert columns["salary_min"]["type"] == "decimal(12,2)"
    assert columns["salary_max"]["type"] == "decimal(12,2)"
    assert columns["experience_required"]["type"] == "varchar(100)"
    assert columns["deadline"]["type"] == "date"
    assert columns["deadline"]["nullable"] == "YES"
    assert columns["status"]["type"] == "varchar(16)"
    assert columns["status"]["nullable"] == "NO"
    assert columns["status"]["default"] == "draft"
    assert columns["extract_model"]["type"] == "varchar(60)"
    assert columns["extract_prompt_version"]["type"] == "varchar(20)"
    # 时间列统一 DATETIME(6)（微秒），与全仓口径一致
    for name in ("extracted_at", "deleted_at", "created_at", "updated_at"):
        assert columns[name]["type"] == "datetime(6)", name
    assert columns["deleted_at"]["nullable"] == "YES", "软删靠 NULL / 非 NULL 表达"


def test_job_skills_columns_match_adr_011_1d() -> None:
    """`job_skills` 的列集合与列型；`type`/`confidence`/`evidence_text` 必须可空（1.D）。"""
    columns = _table_columns("job_skills")
    assert set(columns) == SKILL_COLUMNS

    assert columns["job_id"]["type"] == "varchar(36)"
    assert columns["job_id"]["nullable"] == "NO"
    assert columns["name"]["type"] == "varchar(100)"
    assert columns["type"]["type"] == "varchar(10)"
    assert columns["type"]["nullable"] == "YES", "AI 的分级不可信，宁可为空让用户填"
    assert columns["origin"]["type"] == "varchar(16)"
    assert columns["origin"]["nullable"] == "NO"
    assert columns["confidence"]["type"] == "decimal(5,4)"
    assert columns["confidence"]["nullable"] == "YES"
    assert columns["evidence_text"]["type"] == "varchar(60)"
    assert columns["evidence_text"]["nullable"] == "YES"


def test_both_tables_are_innodb_utf8mb4_0900_ai_ci() -> None:
    """表参数沿用 0001 的 `_table_args()`：InnoDB + utf8mb4 + `utf8mb4_0900_ai_ci`。

    排序规则不是装饰：下面 `test_unique_key...` 那条「三种写法撞同一键」的假设
    完全建立在 `_0900_ai_ci`（大小写/重音/全半角不敏感）上。
    哪天有人改了 collation，那条用例的行为会变，这条用例先红。
    """
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('jobs', 'job_skills')"
            )
        ).all()
    assert {row.TABLE_NAME: (row.ENGINE, row.TABLE_COLLATION) for row in rows} == {
        "jobs": ("InnoDB", "utf8mb4_0900_ai_ci"),
        "job_skills": ("InnoDB", "utf8mb4_0900_ai_ci"),
    }


# --------------------------------------------------------------------------- #
# CHECK 约束：API 发不出的非法值，数据库仍然挡得住
# --------------------------------------------------------------------------- #


def _expect_check_violation(constraint: str, write: Callable[[], Any]) -> None:
    """断言「这一行被**哪条** CHECK 挡下」。

    两个不那么显然的事实，写在这里免得下一个人以为可以简化成 `IntegrityError`：

    1. MySQL 的 CHECK 违规是 errno 3819，经 pymysql/SQLAlchemy 映射成
       `OperationalError` 而**不是** `IntegrityError`（唯一键 1062、外键 1451 才是
       `IntegrityError`）。所以这里捕获两者的共同父类 `DatabaseError`。
    2. 必须点名约束：只断言「抛错了」的话，`NOT NULL`、外键、长度截断都算绿，
       而我们要证明的是 `ck_jobs_status` 这条约束真的建起来了。
    """
    with pytest.raises(DatabaseError) as captured:
        write()
    assert constraint in str(captured.value), str(captured.value)


def test_status_check_rejects_review_and_expired_states() -> None:
    """1.B：七态砍成两态。审核态与 `expired` 都写不进去（没有审核角色、没有定时任务）。"""
    user_id = _insert_user()

    for illegal in ("pending_review", "approved", "rejected", "closed", "expired"):
        _expect_check_violation(
            "ck_jobs_status", lambda illegal=illegal: _insert_job(user_id, status=illegal)
        )

    _insert_job(user_id, status="draft")
    _insert_job(user_id, status="published")


def test_employment_type_check_accepts_only_the_five_values() -> None:
    """五种取值 + NULL；`remote` 不在其列（1.G：远程/驻场本轮留在 JD 文本里）。"""
    user_id = _insert_user()

    _expect_check_violation(
        "ck_jobs_employment_type", lambda: _insert_job(user_id, employment_type="remote")
    )

    for value in (None, "internship", "full_time", "part_time", "contract", "other"):
        _insert_job(user_id, employment_type=value)


def test_salary_check_rejects_inverted_range_but_allows_open_ends() -> None:
    """两端都选填（1.B 的 CHECK 原文）：只有一端时必须放行。"""
    user_id = _insert_user()

    _expect_check_violation(
        "ck_jobs_salary_range",
        lambda: _insert_job(user_id, salary_min=6000, salary_max=3000),
    )

    _insert_job(user_id, salary_min=6000, salary_max=None)
    _insert_job(user_id, salary_min=None, salary_max=3000)
    _insert_job(user_id, salary_min=None, salary_max=None)
    _insert_job(user_id, salary_min=3000, salary_max=6000)
    _insert_job(user_id, salary_min=3000, salary_max=3000)


def test_origin_check_has_exactly_two_values_and_no_user_edited() -> None:
    """1.D 的已定结论：值域只有 `jd_verifiable` / `unverified`，`user_edited` 已删除。

    值名由人类负责人于 2026-11-05 裁定为方案 (a)（ADR-011 1.D v7）：这一列只能证明
    「这句话在 JD 原文里出现过」，证明不了「这句话是谁写的」，所以旧的「AI 提取 / 手动」
    两个值名会把逐字粘贴原文的条目误标成 AI 产出。**约束名与列名不变**，只有值域变。

    这条用例同时是「值域改名真的进了库」的证据：新值写得进去、旧值名不再被接受之外，
    还直接读 `information_schema.CHECK_CONSTRAINTS` 里那条约束的原文（见末尾断言）。

    顺带记录一个排序规则的既有事实：`UNVERIFIED` 这种**大小写不同的写法是能写进去的**
    （`utf8mb4_0900_ai_ci` 大小写不敏感，CHECK 也按该列的排序规则比较）。
    这不是漏洞而是同一个假设的另一面 —— 值域由「不敏感相等」定义，
    写入方只有仓储一处（`ORIGIN_UNVERIFIED` 常量），不存在漂移的大小写来源。
    """
    user_id = _insert_user()
    job_id = _insert_job(user_id)

    for illegal in ("user_edited", "ai", ""):
        _expect_check_violation(
            "ck_job_skills_origin",
            lambda illegal=illegal: _insert_skill(job_id, f"技能-{illegal}", origin=illegal),
        )

    _insert_skill(job_id, "Python", origin="unverified")
    _insert_skill(job_id, "FastAPI", origin="jd_verifiable")
    # 大小写不敏感：这条也写得进去，断言的是「值域只有那两个（不分大小写）」
    _insert_skill(job_id, "MySQL", origin="UNVERIFIED")
    with get_engine().connect() as connection:
        stored = connection.execute(
            text("SELECT origin FROM job_skills WHERE job_id = :id AND name = 'MySQL'"),
            {"id": job_id},
        ).scalar_one()
    assert stored == "UNVERIFIED", "数据库按原样保存写入的字符串（不敏感的是比较，不是存储）"

    # 约束原文里就是这两个新值：改名落在 DDL 上，而不是只落在 Python 常量里。
    # （旧值名此刻已是非法值 —— 库里那条 CHECK 只认这两个新值，所以不需要
    #   再写一条「旧值被拒」的用例；新值写得进去本身就证明了旧值域已被替换。）
    with get_engine().connect() as connection:
        clause = str(
            connection.execute(
                text(
                    "SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
                    "WHERE CONSTRAINT_SCHEMA = DATABASE() "
                    "AND CONSTRAINT_NAME = 'ck_job_skills_origin'"
                )
            ).scalar_one()
        )
    assert "jd_verifiable" in clause, clause
    assert "unverified" in clause, clause


def test_skill_type_and_confidence_checks() -> None:
    """`type` 三值可空；`confidence` 落在 0–1 之间（PRD FR-03 要求保存置信度）。"""
    user_id = _insert_user()
    job_id = _insert_job(user_id)

    _expect_check_violation(
        "ck_job_skills_type", lambda: _insert_skill(job_id, "越界类型", type="general")
    )
    _expect_check_violation(
        "ck_job_skills_confidence", lambda: _insert_skill(job_id, "越界置信度", confidence=1.5)
    )
    _expect_check_violation(
        "ck_job_skills_confidence", lambda: _insert_skill(job_id, "负置信度", confidence=-0.0001)
    )

    _insert_skill(job_id, "hard 类", type="hard")
    _insert_skill(job_id, "soft 类", type="soft")
    _insert_skill(job_id, "domain 类", type="domain")
    _insert_skill(job_id, "未分类", type=None)
    _insert_skill(job_id, "零置信", confidence=0)
    _insert_skill(job_id, "满置信", confidence=1)


# --------------------------------------------------------------------------- #
# 唯一键：ADR-011 1.D 明确要求实测的假设
# --------------------------------------------------------------------------- #


def test_unique_key_collapses_three_spellings_to_one_row() -> None:
    """`UNIQUE (job_id, name)` 拦得住 `Python` / `python` / `Ｐython`（首字母全角）。

    ADR-011 1.D 的验收原文：「集成测试同时提交这三种写法，断言只有 1 行落库；
    测试红了就说明假设错了，届时改用应用层归一化后的 `name_norm` 列做唯一键，
    **而不是删掉断言**。」

    这里写第二/第三行时必须报错（`IntegrityError`），所以落库的是第一行、
    后两次写入被数据库拒掉 —— 表里最终 1 行。这与「静默覆盖成 1 行」不同：
    数据库是**拒绝**而不是合并，应用层因此不可能悄悄塞进重复技能。

    注意用 `INSERT IGNORE` 之外的真实 INSERT：只有报错才算「撞了同一条唯一键」。
    """
    user_id = _insert_user()
    job_id = _insert_job(user_id)

    _insert_skill(job_id, "Python")
    assert _skill_count(job_id) == 1

    with pytest.raises(IntegrityError):
        _insert_skill(job_id, "python")
    with pytest.raises(IntegrityError):
        # Ｐ 是 U+FF30（全角大写 P）
        _insert_skill(job_id, "Ｐython")
    with pytest.raises(IntegrityError):
        _insert_skill(job_id, "PYTHON")

    assert _skill_count(job_id) == 1, "三种写法必须撞同一条唯一键，只剩第一行"

    # 反面对照：真正不同的技能名不该被误折叠（唯一键不是模糊匹配）
    _insert_skill(job_id, "PyTorch")
    assert _skill_count(job_id) == 2


#: 「这两条写法是不是同一个技能」必须由应用层与数据库给出同一个答案。
#: 上游是提取端点的重名合并键（`skill_name_key`）与 `UNIQUE (job_id, name)`；
#: 两边判断不一致时，用户会碰到「提取时已经合并了、点保存却报技能名称重复」
#: 或者反过来「提取给了两条、落库撞 1062 变成 500」。
#: 每一对都在这里实测，不靠对 `utf8mb4_0900_ai_ci` 语义的推断。
_NAME_PAIRS_SAME_SKILL = [
    ("Python", "python"),
    ("Python", "PYTHON"),
    ("Python", "Ｐython"),  # Ｐ = U+FF30，首字母全角
    ("C++", "c++"),
]
_NAME_PAIRS_DIFFERENT_SKILL = [
    ("C#", "C"),  # # 是 U+0023，类别 Po：四步归一化会把它删掉并成一条，库不会
    ("C++", "C"),  # + 的类别是 Sm，两种口径都删不掉，本来就分得开
    ("user_id", "userid"),  # _ 是 Pc：四步归一化删下划线，会误并
    ("python.", "python"),  # 尾点是 Po，同上
    ("Web  开发", "Web 开发"),  # 内部两个空格：四步归一化去空白会误并
    ("  C  ", "C"),  # 列是 NO PAD，首尾空格参与比较
]


@pytest.mark.parametrize(("first", "second"), _NAME_PAIRS_SAME_SKILL)
def test_skill_name_key_matches_unique_key_for_equivalent_spellings(
    first: str, second: str
) -> None:
    """`skill_name_key` 判为同一个技能 ⟹ `UNIQUE (job_id, name)` 也判同一个（第二行被拒）。"""
    assert skill_name_key(first) == skill_name_key(second), (
        f"用例前提：应用层键应把 {first!r} 与 {second!r} 折成同一个键"
    )
    user_id = _insert_user()
    job_id = _insert_job(user_id)
    _insert_skill(job_id, first)
    with pytest.raises(IntegrityError):
        _insert_skill(job_id, second)
    assert _skill_count(job_id) == 1


@pytest.mark.parametrize(("first", "second"), _NAME_PAIRS_DIFFERENT_SKILL)
def test_skill_name_key_matches_unique_key_for_distinct_names(first: str, second: str) -> None:
    """`skill_name_key` 判为两个技能 ⟹ 库里必须真的留下两行（这才是同一个答案）。

    反向护栏由 `test_four_step_normalization_is_not_a_valid_skill_name_key` 单独测：
    这一批里 `C#`、`user_id`、`python.`、内部双空格那几对，若改用 §2.D.1 的四步归一化
    做合并键会被判成同一个技能，而数据库认为它们是两行。
    """
    assert skill_name_key(first) != skill_name_key(second)
    user_id = _insert_user()
    job_id = _insert_job(user_id)
    _insert_skill(job_id, first)
    _insert_skill(job_id, second)
    assert _skill_count(job_id) == 2, f"{first!r} 与 {second!r} 在库里必须是两行"


def test_four_step_normalization_is_not_a_valid_skill_name_key() -> None:
    """四步归一化**不能**当技能名合并键：它比数据库宽，会静默吞掉一条岗位要求。

    这是 C1 补正里真发生过的口径错误（提取端去重最初被建议按 §2.D.1 的函数做），
    所以留一条正向断言而不是只留注释：`normalize_for_trace` 在下列这些对上等于 True，
    而 `utf8mb4_0900_ai_ci` 判它们不相等（上一条用例已实测）。两者一旦合成一个函数，
    「重名合并」就会吃掉真实条目，且用户在结果里看不出少了东西。
    """
    would_falsely_merge = [
        ("C#", "C"),
        ("user_id", "userid"),
        ("python.", "python"),
        ("Web  开发", "Web 开发"),
    ]
    for first, second in would_falsely_merge:
        assert normalize_for_trace(first) == normalize_for_trace(second), (
            f"用例前提：四步口径会把 {first!r} 与 {second!r} 误并"
        )
        assert skill_name_key(first) != skill_name_key(second)


def test_unique_key_is_scoped_per_job() -> None:
    """`UNIQUE (job_id, name)` 是「同一岗位内不重复」，不同岗位可以各有一条 Python。"""
    first_user = _insert_user()
    second_user = _insert_user()
    first_job = _insert_job(first_user)
    second_job = _insert_job(second_user)

    _insert_skill(first_job, "Python")
    _insert_skill(second_job, "Python")

    assert _skill_count(first_job) == 1
    assert _skill_count(second_job) == 1


# --------------------------------------------------------------------------- #
# 外键删除规则
# --------------------------------------------------------------------------- #


def test_deleting_a_user_with_jobs_is_blocked_by_restrict() -> None:
    """1.B：`fk_jobs_created_by_user_id` 是 RESTRICT —— 岗位是公开数据，不跟着账号消失。"""
    user_id = _insert_user()
    _insert_job(user_id)

    with pytest.raises(IntegrityError), get_engine().begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def test_deleting_a_job_cascades_to_its_skills() -> None:
    """1.D：`fk_job_skills_job_id` 是 CASCADE —— 技能条目是岗位的组成部分，不独立存在。

    本轮 API 只做软删（`deleted_at`），物理删除只会发生在 `alembic downgrade`；
    正因为有 CASCADE，降级删表时不会因为子表引用而留下孤儿行。
    """
    user_id = _insert_user()
    job_id = _insert_job(user_id)
    _insert_skill(job_id, "Python")
    _insert_skill(job_id, "SQL")
    assert _skill_count(job_id) == 2

    with get_engine().begin() as connection:
        connection.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})

    assert _skill_count(job_id) == 0


# --------------------------------------------------------------------------- #
# 分层纪律（AGENTS.md §4.1 + ADR-011 1.F 第三条）
# --------------------------------------------------------------------------- #


def test_job_service_and_repository_do_not_touch_http() -> None:
    """service/repository 里不得出现 `HTTPException` / `fastapi` / `Request` / `Response`。

    「service 层禁止 raise HTTPException」是 ADR-011 1.F 明写的三条之一，
    理由是 HTTP 语义只属于 api 层。只写在 docstring 里就是建议，不是约束
    （AGENTS.md §2 对上一轮的批评正是「护栏缺失」），所以这里读源码钉住它。
    """
    for module in (job_service, job_repository):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "HTTPException" not in source, module.__name__
        assert "fastapi" not in source, module.__name__


def test_expiry_filter_sends_no_database_side_date_function(client: TestClient) -> None:
    """2.G：真正发往 MySQL 的 SQL 里不出现数据库侧的当日函数，「今天」以绑定参数进来。

    判据是**行为**而不是源码字符串：源码里出现 `CURDATE` 只是文笔问题，
    而这条用例要挡的是「把日期比较交给 DB 时钟」这件事本身。
    取证方式用 ADR-011 附录指定的那一种 —— 在 **Engine**（不是 Session）上挂
    `before_cursor_execute` 收集本次执行过的全部语句与参数：
    挂到 Session 上会直接抛 `InvalidRequestError: No such event ...`，
    那是 SQLAlchemy 2 的事件层级，不是可以二选一的写法。

    混用两侧时钟为什么必须挡：全仓时间列清一色由应用进程写入，
    而 `CURDATE()` 取的是 **DB 服务器时区**的日期。跨时区部署下
    「昨天截止」会变成「今天截止」，而这种不一致在本地永远测不出来。
    """
    captured: list[tuple[str, Any]] = []

    def listener(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        captured.append((statement, parameters))

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", listener)
    try:
        token = _register_and_login(client)
        user_id = _current_user_id(client, token)
        # 一行「昨天截止」的已发布岗位：默认列表必须把它筛掉
        _insert_job(user_id, deadline=date.today() - timedelta(days=1), status="published")
        response = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["jobs"] == []
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    job_queries = [statement for statement, _ in captured if "FROM jobs" in statement]
    assert job_queries, "列表请求必须真的查过 jobs，否则下面的断言是空过"

    executed_sql = " ".join(job_queries).lower()
    for forbidden in ("curdate", "current_date", "sysdate", "now("):
        assert forbidden not in executed_sql, forbidden

    # 反向确认：判据是绑定参数进来的，且值就是应用时钟的「今天」
    date_parameters = [
        value
        for _, parameters in captured
        if isinstance(parameters, dict)
        for value in parameters.values()
        if isinstance(value, date)
    ]
    assert date.today() in date_parameters, "过期比较的右值必须来自应用进程的 date.today()（2.G）"


# --------------------------------------------------------------------------- #
# 端到端一条链路（API → service → repository → DB）
# --------------------------------------------------------------------------- #


def test_full_slice_persists_every_layer_once(client: TestClient) -> None:
    """一条完整链路：登录 → 录入 → 发布 → 库里 jobs/job_skills 各就位。

    「集成测试覆盖从 API 到数据库的完整链路」是 AGENTS.md §3.1 对切片的要求；
    这条用例故意在断言里读原始表，证明 API 返回的 id 与库里的是同一行，
    而不是某个内存 fake 的自我一致性。
    """
    token = _register_and_login(client)
    user_id = _current_user_id(client, token)

    created = client.post(
        "/api/v1/jobs",
        json={
            "company_name": "端到端科技",
            "title": "AI 后端实习",
            "description": "熟练掌握 Python 与 FastAPI，熟悉 Docker。",
            "location": "上海",
            "employment_type": "internship",
            "salary_min": 3000,
            "salary_max": 6000,
            "experience_required": "在校即可",
            "skills": [
                {"name": "Python", "type": "hard"},
                {"name": "FastAPI", "type": "hard"},
                {"name": "沟通", "type": "soft"},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["data"]["job"]["id"]
    assert created.json()["data"]["job"]["status"] == "draft"

    published = client.put(
        f"/api/v1/jobs/{job_id}/publish", headers={"Authorization": f"Bearer {token}"}
    )
    assert published.status_code == 200, published.text

    with get_engine().connect() as connection:
        job = connection.execute(
            text("SELECT created_by_user_id, status, company_name FROM jobs WHERE id = :id"),
            {"id": job_id},
        ).one()
        skills = connection.execute(
            text(
                "SELECT name, type, origin, confidence, evidence_text FROM job_skills "
                "WHERE job_id = :id ORDER BY created_at"
            ),
            {"id": job_id},
        ).all()

    assert (job.created_by_user_id, job.status, job.company_name) == (
        user_id,
        "published",
        "端到端科技",
    )
    assert [row.name for row in skills] == ["Python", "FastAPI", "沟通"]
    # 三条技能的名字与顺序按录入顺序落库；这三条**都没带** `evidence_text`，
    # 所以 `origin` 仍是「未标注出处」、后两列仍是 NULL。
    # 口径出处（依据 ADR-011 v9 放开）：C1 起写侧**接受** `evidence_text` / `confidence`，
    # 「库里恒为 NULL」不再是自动成立的事实，而是这条用例没提交它们的结果——
    # 提交之后落什么值由 `tests/test_jobs.py` 的写回路径用例钉住。
    assert {row.origin for row in skills} == {"unverified"}
    assert all(row.confidence is None for row in skills)
    assert all(row.evidence_text is None for row in skills)


def _register_and_login(client: TestClient) -> str:
    username = f"jobc_{uuid4().hex[:10]}"
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "Passw0rd!2026", "email": None},
        ).status_code
        == 201
    )
    logged_in = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "Passw0rd!2026"}
    )
    assert logged_in.status_code == 200, logged_in.text
    token: str = logged_in.json()["data"]["access_token"]
    return token


def _current_user_id(client: TestClient, token: str) -> str:
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    user_id: str = response.json()["data"]["user"]["id"]
    return user_id
