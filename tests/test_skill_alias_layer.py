"""B1 别名层（`ADR-016`）的**层内**判据：写入路径的硬约束、索引形状、词表与判据的边界。

这个文件只管三件事，端点级的四条验收（§5.1 的读数、§5.2 的并集不变式、§5.3 的那 +0.06、
以及「一次请求只查一次词表」）在 `tests/test_job_match.py`，纯打分层的档位顺序在
`tests/test_match_scoring.py`。分文件是为了失败能读出来：这里红了是**词表层**坏了，
那边红了是**判定链**坏了。

钉住的东西，按 `ADR-016` 的小节：

1. §2.2 的硬约束——`canonical_key` 与 `alias_key` 的版本尾巴不一致就**拒绝入库**。
   任务书把这格的落点定成「写入路径的校验函数 + 一条测试」（本批不写种子迁移，
   没有种子可校验），所以这里是那条「不许只有文档」的测试。
2. §2.2 的「单向存一行、判定双向查」+ 两个域互不相通。
3. §5.1 的判据半边——空词表时 `skill_forms_match` 必须与四条纯词面规则**逐字同判**，
   且同一对 key 在词表有行时必须翻成 True（否则前一半就是空检）。
4. 迁移建出来的两个值域与那条三列唯一键在**真实 MySQL** 上拦得住（AGENTS.md §4.4：
   不许靠推断；与 `tests/test_jobs_constraints.py` 同一个理由）。
5. `load_index()` 是**一条** SQL——这是 §3「不许 N+1」的存储侧前提。
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.core.skill_names import (
    ALIAS_SCOPE_ROLE,
    ALIAS_SCOPE_SKILL,
    ALIAS_SOURCE_AI_REVIEWED,
    ALIAS_SOURCE_SEED,
    AliasIndex,
    _lexical_forms_match,
    skill_forms_match,
    skill_name_key,
)
from app.repositories.skill_alias_repository import (
    AliasPairRejectedError,
    SkillAliasRepository,
)
from app.repositories.user_repository import get_engine, session_scope


def _insert(*pairs: tuple[str, str, str]) -> None:
    """走**唯一写入口**插行：测试不许有第二条插法，否则校验就被绕过了。"""
    with session_scope() as session:
        repository = SkillAliasRepository(session)
        for canonical_key, alias_key, scope in pairs:
            repository.add(canonical_key, alias_key, scope)


def _row_count() -> int:
    with session_scope() as session:
        return SkillAliasRepository(session).count()


# ------------------------------------------------ §2.2 的硬约束（§5.4 的那一条测试）


@pytest.mark.parametrize(
    ("canonical_key", "alias_key", "scope"),
    [
        # §2.2 点名的形状：两个版本号被合成一回事（主号 2 与 3.13 不是一回事）。
        ("python 2", "python 3.13", ALIAS_SCOPE_SKILL),
        ("vue 2", "vue 3", ALIAS_SCOPE_SKILL),
        ("office 2019", "office 365", ALIAS_SCOPE_SKILL),
        # 一侧有尾巴、一侧没有：把「整个系列」与「某一个发行版」配对，同样是假匹配。
        ("python", "python3", ALIAS_SCOPE_SKILL),
        ("excel", "excel2016", ALIAS_SCOPE_SKILL),
        ("jdk", "jdk 1.8", ALIAS_SCOPE_SKILL),
        # 角色域也受同一条约束：这一格不是「只有技能表才管型号」。
        # （挑的是**尾部带年份**的角色写法——`skill_stem` 只认串尾的数字，
        # 「react 16 工程师」那种号在中间的不算版本号尾巴，拿它测等于没测。）
        ("算法工程师 2024", "算法工程师 2025", ALIAS_SCOPE_ROLE),
    ],
)
def test_a_pair_with_mismatched_version_tails_is_refused(
    canonical_key: str, alias_key: str, scope: str
) -> None:
    """§5.4：**版本尾巴不同的成对提交被拒绝**，且拒绝发生在入库之前。

    为什么这条不能只写在 ADR 里：每一行都会真实改变判定结果（§3 的原话），
    而 `python 2` ↔ `python 3.13` 这种行会给用户一个他会带去面试的假匹配。
    词表里少一行只是「没修到」，多一行假配对是「说错话」——后者更贵。
    """
    with pytest.raises(AliasPairRejectedError, match="版本尾巴"):
        _insert((canonical_key, alias_key, scope))
    # 拒绝必须是「整行没进去」，不是「进去一半」——否则词表会出现一条没人记得加过的行。
    assert _row_count() == 0


@pytest.mark.parametrize(
    ("canonical_key", "alias_key", "scope"),
    [
        # §3 点名的三类高频写法同义：两侧都没有版本号尾巴，所以约束放行。
        ("js", "javascript", ALIAS_SCOPE_SKILL),
        ("k8s", "kubernetes", ALIAS_SCOPE_SKILL),
        ("golang", "go", ALIAS_SCOPE_SKILL),
        # 尾巴**逐字相同**也算一致（`Excel2016` ↔ `Excel 2016` 是纯分隔符之差）。
        ("excel2016", "excel 2016", ALIAS_SCOPE_SKILL),
        # 角色域：老板点名的「同方向不同词」只在第二个域里。
        ("前端开发工程师", "react 工程师", ALIAS_SCOPE_ROLE),
    ],
)
def test_a_pair_with_consistent_version_tails_is_stored(
    canonical_key: str, alias_key: str, scope: str
) -> None:
    """约束的另一半：**不许挡下合法的行**。只测拒绝的那半边，词表会被收紧成一张空表。"""
    _insert((canonical_key, alias_key, scope))
    assert _row_count() == 1
    with session_scope() as session:
        index = SkillAliasRepository(session).load_index()
    assert index.hit(scope, canonical_key, alias_key) is True


def test_the_only_write_entry_checks_domain_source_and_self_pair() -> None:
    """三道前置校验：未知别名域、未知来源、两侧同一个键。

    这三条在库里各有/没有对应约束（`scope`/`source` 有 CHECK，自配对没有），
    所以自配对那条**只能**长在这里——这也是「唯一写入口」必须存在的原因。
    """
    with pytest.raises(AliasPairRejectedError, match="别名域"):
        _insert(("js", "javascript", "tool"))
    with pytest.raises(AliasPairRejectedError, match="别名来源"):
        with session_scope() as session:
            SkillAliasRepository(session).add("js", "javascript", ALIAS_SCOPE_SKILL, source="ai")
    with pytest.raises(AliasPairRejectedError, match="同一个键"):
        _insert(("js", "js", ALIAS_SCOPE_SKILL))
    assert _row_count() == 0


def test_ai_reviewed_source_is_an_allowed_provenance() -> None:
    """§2.2 的 `source` 两个值都要真能写：阶段 3 的通道今天就留好，但运行时不查 AI。"""
    with session_scope() as session:
        repository = SkillAliasRepository(session)
        repository.add("js", "javascript", ALIAS_SCOPE_SKILL, source=ALIAS_SOURCE_SEED)
        repository.add(
            "k8s", "kubernetes", ALIAS_SCOPE_SKILL, source=ALIAS_SOURCE_AI_REVIEWED, note="评审 X"
        )
    assert _row_count() == 2


# --------------------------------------------------------- 索引的形状（§2.2 后半）


def test_one_stored_row_answers_both_directions() -> None:
    """单向存一行、判定双向查（§2.2）：漏掉对称闭包就会出现 A 认 B、B 不认 A。"""
    index = AliasIndex.from_rows([("js", "javascript", ALIAS_SCOPE_SKILL)])
    assert index.hit(ALIAS_SCOPE_SKILL, "js", "javascript") is True
    assert index.hit(ALIAS_SCOPE_SKILL, "javascript", "js") is True
    # 键的顺序不参与判定，所以两条方向共用同一份 frozenset。
    assert skill_forms_match("JavaScript", "js", index) is True
    assert skill_forms_match("js", "JavaScript", index) is True


def test_the_two_domains_do_not_leak_into_each_other() -> None:
    """`skill` 域的行**不许**参与角色域判定，反过来也不行（§2.4 只在 `scope='role'` 里成对）。

    这条边界有实测意义：`前端开发工程师` ↔ `react 工程师` 若是 `skill` 域的行，
    它就不该把 `preference` 从 0.0 捞到 0.6——否则「技能同义表」与「方向同义表」
    会互相顶名，而 §2.2 分两个域的全部理由就是这两件事的修正成本不同。
    """
    skill_only = AliasIndex.from_rows([("前端开发工程师", "react 工程师", ALIAS_SCOPE_SKILL)])
    assert skill_only.hit(ALIAS_SCOPE_ROLE, "前端开发工程师", "react 工程师") is False
    role_only = AliasIndex.from_rows([("js", "javascript", ALIAS_SCOPE_ROLE)])
    assert role_only.hit(ALIAS_SCOPE_SKILL, "js", "javascript") is False


def test_an_empty_index_is_the_lexical_behaviour_for_every_pair_we_could_ask() -> None:
    """§5.1 的判据半边：空词表时 `skill_forms_match` 与四条纯词面规则**逐字同判**。

    用例表里**故意**放了六对「词表里有行才会成对」的别名（`js`/`javascript` 一类）：
    它们在空表下必须判否，而同一批 pair 装上行之后必须判真（下一用例负责）。
    只放词面本来就判得出的 pair，这条就成了空检。
    """
    pairs = [
        ("Python", "Python3"),
        ("Python3", "Python 3.13"),
        ("C", "C++"),
        ("Java", "JavaScript"),
        ("Go", "Golang"),
        ("js", "JavaScript"),
        ("k8s", "Kubernetes"),
        ("golang", "Go"),
        ("React", "前端开发"),
        ("Docker", "Kubernetes"),
    ]
    empty = AliasIndex.empty()
    for left, right in pairs:
        for aliases in (None, empty):
            assert skill_forms_match(left, right, aliases) is _lexical_forms_match(left, right)
            assert skill_forms_match(right, left, aliases) is _lexical_forms_match(right, left)


def test_the_same_pairs_flip_to_true_once_the_table_has_the_row() -> None:
    """上一条的反向探针：把那六对别名真的写进索引，判定必须翻。

    没有这一条，上一条的「逐字同判」可能只是因为兜底那一支根本没被接到线上。
    """
    alias_pairs = [
        ("js", "JavaScript"),
        ("k8s", "Kubernetes"),
        ("golang", "Go"),
    ]
    index = AliasIndex.from_rows(
        [(skill_name_key(a), skill_name_key(b), ALIAS_SCOPE_SKILL) for a, b in alias_pairs]
    )
    for left, right in alias_pairs:
        assert skill_forms_match(left, right) is False
        assert skill_forms_match(left, right, index) is True
    # 词面四条本来就判真的那一对，不许因为接了别名层而变坏。
    assert skill_forms_match("Python", "Python3", index) is True


def test_unknown_scope_rows_are_ignored_rather_than_crashing_the_matcher() -> None:
    """构造索引时读到未知 scope 就跳过：值域由库侧 CHECK 挡，热路径不重复一遍判据。"""
    index = AliasIndex.from_rows([("js", "javascript", "tool")])
    assert index.is_empty is True
    assert skill_forms_match("js", "JavaScript", index) is False


# ------------------------------------------- 迁移建出来的约束在真实 MySQL 上拦得住


def _raw_insert(canonical_key: str, alias_key: str, scope: str, source: str = "seed") -> None:
    """**绕过仓储**直接写 SQL：这一组用例要测的就是数据库本身，不是应用层的自觉。

    `id` 用随机 uuid4，**不许复用**：这一组用例要撞的是 `uq_skill_aliases_pair`，
    如果两行的主键相同，MySQL 会先报主键冲突，那条断言就变成「测到了别的东西」。
    """
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO skill_aliases "
                "(id, canonical_key, alias_key, scope, source, note, created_at, updated_at) "
                "VALUES (:id, :canonical_key, :alias_key, :scope, :source, NULL, NOW(6), NOW(6))"
            ),
            {
                "id": str(uuid4()),
                "canonical_key": canonical_key,
                "alias_key": alias_key,
                "scope": scope,
                "source": source,
            },
        )


def _expect_check_violation(constraint: str, write: Callable[[], None]) -> None:
    """CHECK 违规的捕获口径，抄 `tests/test_jobs_constraints.py:251-262` 的两条教训：

    1. MySQL 的 CHECK 违规是 errno 3819，经 pymysql/SQLAlchemy 映射成
       `OperationalError` 而**不是** `IntegrityError`（唯一键 1062、外键 1451 才是
       `IntegrityError`），所以要捕共同父类 `DatabaseError`；
    2. 必须**点名约束**：只断言「抛错了」的话，`NOT NULL`、长度截断、外键都算绿，
       而这里要证明的是那一条 CHECK 真的建起来了。
    """
    with pytest.raises(DatabaseError) as captured:
        write()
    assert constraint in str(captured.value), str(captured.value)


def test_the_database_enforces_the_pair_uniqueness_per_scope() -> None:
    """`uq_skill_aliases_pair (canonical_key, alias_key, scope)` 的两个方向都要实测。

    - 同一对、同一域写两遍必须撞键：否则「单向存一行」会变成重复行，
      而下一次读词表时谁也不知道哪一行算数；
    - 同一对、**不同域**必须能共存：`js` ↔ `javascript` 在 `skill` 域是写法同义，
      放进 `role` 域它说的是另一件事。少了 `scope` 这一列，这两个语义会互相顶掉。
    """
    _raw_insert("js", "javascript", ALIAS_SCOPE_SKILL)
    with pytest.raises(IntegrityError):
        _raw_insert("js", "javascript", ALIAS_SCOPE_SKILL)
    _raw_insert("js", "javascript", ALIAS_SCOPE_ROLE)
    with session_scope() as session:
        index = SkillAliasRepository(session).load_index()
    assert index.hit(ALIAS_SCOPE_SKILL, "js", "javascript") is True
    assert index.hit(ALIAS_SCOPE_ROLE, "js", "javascript") is True


@pytest.mark.parametrize("bad_value", ["tool", "", "consulting", "skills"])
def test_the_scope_check_constraint_rejects_values_outside_the_two_domains(
    bad_value: str,
) -> None:
    """`ck_skill_aliases_scope` 只认 `skill` / `role`（`ADR-016` §2.2「两个值」）。

    `skills` 这一条是刻意选的**近似串**：列宽 10 装得下它，而 `IN` 是逐字比较，
    所以它必须被拒——这一格挡的是「以后有人把域名单复数搞混」。
    大小写之差是另一件事，见下一条：排序规则会放过 `SKILL`，那条不能混在这里测。
    """
    _expect_check_violation(
        "ck_skill_aliases_scope", lambda: _raw_insert("js", "javascript", bad_value)
    )


def test_the_collation_folds_case_in_the_check_but_the_value_domain_stays_lowercase() -> None:
    """实测一个会骗人的地方：`utf8mb4_0900_ai_ci` 让 `CHECK (scope IN ('skill','role'))`
    对 `'SKILL'` **也成立**（排序规则大小写不敏感），所以数据库那侧挡不住大写。

    真正守住值域的是应用层的 `ALIAS_SCOPES` 逐字比较：`add()` 拒绝 `SKILL`，
    而 `AliasIndex.from_rows()` 读到库里存量的大写行会**忽略**它（不是当成 `skill`）。
    这条不钉住就会出现「库里有一行、判定却从不看它」的幽灵行。
    与 `test_jobs_constraints.py` 对 `uq_job_skills_job_name` 的处理同一口径：
    排序规则的行为由测试实测，不靠推断（AGENTS.md §4.4）。
    """
    _raw_insert("js", "javascript", "SKILL")
    assert _row_count() == 1
    with session_scope() as session:
        index = SkillAliasRepository(session).load_index()
    # 库里那一行在两个域里都不参与判定：大写行是惰性残留，不是第二条口径。
    assert index.hit(ALIAS_SCOPE_SKILL, "js", "javascript") is False
    assert index.hit(ALIAS_SCOPE_ROLE, "js", "javascript") is False
    assert index.is_empty is True
    # 写侧逐字比较，所以 `add()` 拒绝 `SKILL`：库里那种行只能来自绕过仓储的 SQL。
    with pytest.raises(AliasPairRejectedError, match="别名域"):
        with session_scope() as another_session:
            SkillAliasRepository(another_session).add("py", "python", "SKILL")


@pytest.mark.parametrize("bad_value", ["human", "", "runtime_ai"])
def test_the_source_check_constraint_rejects_unknown_provenance(bad_value: str) -> None:
    """`ck_skill_aliases_source`：只有 `seed` / `ai_reviewed`。

    为什么这一格值得单独挡：`source` 是「这一行当初怎么进来的」，而 §2.2 承诺
    **运行时永远不查 AI**。放进第三个值（比如 `runtime_ai`）就等于给
    「匹配链路的确定性」留了一个可以自己写行的入口。
    """
    _expect_check_violation(
        "ck_skill_aliases_source",
        lambda: _raw_insert("js", "javascript", ALIAS_SCOPE_SKILL, source=bad_value),
    )


def test_the_stored_values_are_keys_not_surface_forms() -> None:
    """库里存的是归一后的键：`JS` 与 `js` 撞同一条唯一键（§2.2 的「key 的输出空间」）。

    这条实测的是**写侧纪律**而不是数据库的宽容度：`add()` 收到什么就存什么，
    所以传原始写法进去会留下 `JS` 这种永不被查到的行。这里用两条 key（同一条）
    与一条伪 key（`JS` 原样）各验一次，证明口径只有一个。
    """
    _raw_insert("js", "javascript", ALIAS_SCOPE_SKILL)
    with pytest.raises(IntegrityError):
        # 排序规则大小写不敏感，所以 `JS` 这条原始写法在库里就是同一个键。
        _raw_insert("JS", "JavaScript", ALIAS_SCOPE_SKILL)
    assert _row_count() == 1


# ------------------------------------------------ §3 的「一次载入」在仓储侧成立


def test_load_index_issues_exactly_one_statement_regardless_of_row_count() -> None:
    """`load_index()` 只发一条 SQL——这是判定层「不许 N+1」的存储侧前提。

    端点级的那条（一次 `POST /match` 全链路只查一次）在
    `tests/test_job_match.py::test_a_match_request_reads_the_alias_table_exactly_once`；
    这一条管的是「就算将来有人给判定层换了别的取数方式，仓储自己也不许变成 N 条」。
    """
    _insert(
        ("js", "javascript", ALIAS_SCOPE_SKILL),
        ("k8s", "kubernetes", ALIAS_SCOPE_SKILL),
        ("golang", "go", ALIAS_SCOPE_SKILL),
        ("前端开发工程师", "react 工程师", ALIAS_SCOPE_ROLE),
    )

    statements: list[str] = []
    engine: Engine = get_engine()

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        if "skill_aliases" in statement.lower():
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        with session_scope() as session:
            index = SkillAliasRepository(session).load_index()
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    assert index.hit(ALIAS_SCOPE_SKILL, "js", "javascript") is True
    assert len(statements) == 1, f"载入词表发了 {len(statements)} 条 SQL：{statements}"
