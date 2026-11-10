"""`skill_aliases` 的映射与仓储（B1，契约见 `docs/adr/ADR-016-skill-alias-layer-and-matcher-versioning.md`）。

表结构由 Alembic 迁移建立（`20261109_0015_skill_aliases`），本模块只做映射与两个入口。
本仓没有 `models/` 目录，ORM 声明与仓储实现同文件，与 `job_repository.py` /
`student_profile_repository.py` 的形状一致（那两个文件的模块注释把这条写成了惯例）。

**这个文件只做两件别人做不了的事**：

1. **一次 SQL 载入整张词表**（`load_index()`）。ADR-016 §3 的「不许 N+1」在这一层落地：
   匹配的热路径按「岗位要求 × 学生技能 × 经历字段」算，逐条查库就是一条没人复核的隐式放大。
   所以判据层（`app/services/match_scoring.py`）拿到的永远是 `app/core` 的 `AliasIndex`
   值对象而不是这个仓储，**判定函数不许再持有一个 session**。谁来查库、谁来判定，
   这条界线由 import-linter 的层次契约守着，不靠注释。
2. **成对入库的唯一写入口**（`add()`）。ADR-016 §2.2 那条「版本尾巴不一致就不许成对」
   是**硬约束**，不是文档祈祷；它必须长在写入路径上，这样将来的种子迁移与阶段 3 的
   `ai_reviewed` 通道都走同一道闸，没有第二条入库路径可以绕过它。

`id` / `created_at` / `updated_at` 的口径与 `job_skills` 同一套（uuid4 主键 + 应用时钟 +
批量插入错开 1 微秒），这样「哪一行什么时候进来的」在按 `created_at, id` 排序时是稳定的。

**这张表没有归属**：全库共享的一张词表，`scope` 只有 `skill` / `role` 两个域，
所以这里没有任何 `user_id` 谓词——不是漏了权限，是它本来就不是按人的数据。
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import String, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.datetime_display import utc_now
from app.core.skill_names import (
    ALIAS_SCOPES,
    ALIAS_SOURCE_SEED,
    ALIAS_SOURCES,
    AliasIndex,
    alias_version_tail_conflict,
)

#: 别名列的宽度，与迁移 `20261109_0015` 逐字一致（`canonical_key` / `alias_key` / `note`）。
KEY_MAX_LENGTH = 200

#: 一次载入的别名行数上限。这张表按设计只有几百行（§3「宁缺毋滥」），
#: 所以这一格是**护栏而不是分页**：真超了就应当报错，而不是悄悄把词表截断——
#: 截断之后的判定是「部分词表」，而 `meta.alias_version` 说的却是整张表的那一版，
#: 那种不一致正是这三条版本轴要防的东西。
ALIAS_LOAD_LIMIT = 5000


class AliasBase(DeclarativeBase):
    """别名域的 declarative base；与 `Base` / `ProfileBase` / `JobBase` 互不干扰。"""


class SkillAlias(AliasBase):
    __tablename__ = "skill_aliases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    #: **归一后的键**（`skill_name_key` 的输出），不是原始写法（ADR-016 §2.2）。
    #: 原始写法永远只在 `skills.name` / `job_skills.name` 里，别名不许成为第二个写法来源。
    canonical_key: Mapped[str] = mapped_column(String(KEY_MAX_LENGTH), nullable=False)
    alias_key: Mapped[str] = mapped_column(String(KEY_MAX_LENGTH), nullable=False)
    #: `skill` / `role` 两个域（值域见 `ALIAS_SCOPES`，库侧由 `ck_skill_aliases_scope` 挡）。
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    #: `seed` / `ai_reviewed`（值域见 `ALIAS_SOURCES`）。运行时不查 AI，这一列只是溯源戳。
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=ALIAS_SOURCE_SEED)
    #: 「这一行为什么存在」；`ADR-016` §3 要求每一条都人工过一遍，那个结论得有地方放。
    note: Mapped[str | None] = mapped_column(String(KEY_MAX_LENGTH), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    # 本映射**不声明** CHECK / 唯一约束：本仓不用 `create_all`，DDL 的真相只有 Alembic
    # 迁移（AGENTS.md §4.2）。在映射里重抄一份约束，将来只会和迁移漂移。


class AliasPairRejectedError(ValueError):
    """成对入库被硬约束拒绝（ADR-016 §2.2 的版本尾巴约束）。

    为什么是 `ValueError` 的子类而不是 `LookupError`/自定义基类：本仓的
    `JobNotFoundError` / `AuthError` 都是 `ValueError` 的直系子类，服务层据此翻译 4xx；
    这一条今天只会被内部写入路径（种子脚本 / 迁移 / 测试）撞到，到不了请求体，
    所以它不需要一个新的错误码，只需要一条读得懂的原因。
    """


class SkillAliasRepository:
    """全库共享的一张别名词表：整体载入一次，或者按唯一写入口加一行。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---------------------------------------------------------------------- 读

    def load_index(self) -> AliasIndex:
        """**一次** SELECT 把整张词表载入内存，返回判定层能用的 `AliasIndex`。

        两条纪律：

        - **一次请求只许调用它一次**，且必须发生在 `_gather` 那一层（取数的地方），
          不在判定函数里。这条不是风格：ADR-016 §3 明写「不许每条技能各查一次」，
          而它由 `tests/test_skill_alias_layer.py` 数真实 SQL 次数钉住。
        - 排序固定 `created_at, id`：`AliasIndex` 的判定结果与顺序无关，
          但调试时两次运行看到同一批行是可复现性的最低成本。
        """
        statement = (
            select(SkillAlias.canonical_key, SkillAlias.alias_key, SkillAlias.scope)
            .order_by(SkillAlias.created_at, SkillAlias.id)
            .limit(ALIAS_LOAD_LIMIT)
        )
        rows = self._session.execute(statement).all()
        return AliasIndex.from_rows((str(row[0]), str(row[1]), str(row[2])) for row in rows)

    def count(self) -> int:
        """词表行数；只给测试与运维用，判定层不许读它（它不是判据的一部分）。"""
        return int(self._session.scalar(select(func.count()).select_from(SkillAlias)) or 0)

    # ---------------------------------------------------------------------- 写

    def add(
        self,
        canonical_key: str,
        alias_key: str,
        scope: str,
        *,
        source: str = ALIAS_SOURCE_SEED,
        note: str | None = None,
        now: datetime | None = None,
    ) -> SkillAlias:
        """加一行别名对；这是本仓**唯一**的别名写入口。

        四道校验，次序与报错可读性有关（先「值域」再「长度」再「同一性」再「版本尾巴」）：

        1. `scope` / `source` 必须在枚举里——库侧的 CHECK 也挡，但那里给的是
           MySQL 3819 原文，运营脚本读不懂；
        2. 两侧键不许为空、不许超宽（键是 `skill_name_key` 的输出，宽度与迁移一致）；
        3. 两个键不许相同：那是「A 与 A 成对」，除了给 `alias_version` +1 之外什么都不做，
           而它会让词表看起来比实际有效；
        4. **版本尾巴必须一致**（`alias_version_tail_conflict`，ADR-016 §2.2 的硬约束）。
           把 `python 2` 与 `python 3.13` 合成一回事，等于给用户一个他会带去面试的假匹配。

        入参是**已经归一的键**。这里刻意不替你 `skill_name_key()`：键的口径由调用方与
        判据层共用同一函数，仓储再折一次就是把归一写进存储层，
        而 §2.2 要的是「别名存在 key 的输出空间里」这件事只有一个来源。
        """
        row = self._build(
            canonical_key, alias_key, scope, source=source, note=note, now=now, index=0
        )
        self._session.add(row)
        self._session.commit()
        return row

    def add_pairs(
        self,
        pairs: Sequence[tuple[str, str, str]],
        *,
        source: str = ALIAS_SOURCE_SEED,
        note: str | None = None,
    ) -> list[SkillAlias]:
        """批量入口（种子用），**逐条走同一道闸**，一批一次提交。

        **不是性能路径**：种子是人工评审后的一次性动作，几百行的 INSERT 快慢无关紧要，
        而「批量入口绕过校验」是这张表最容易出的事故形状——§2.2 的约束一旦有了旁路，
        就等于没有。一次提交则是刻意的：一批里第 37 条撞约束时整批不入库，
        运营看到的是「这批复核没过」，而不是半张词表悄悄变了。
        """
        now = utc_now()
        rows = [
            self._build(canonical_key, alias_key, scope, source=source, note=note, now=now, index=i)
            for i, (canonical_key, alias_key, scope) in enumerate(pairs)
        ]
        self._session.add_all(rows)
        self._session.commit()
        return rows

    def _build(
        self,
        canonical_key: str,
        alias_key: str,
        scope: str,
        *,
        source: str,
        note: str | None,
        now: datetime | None,
        index: int,
    ) -> SkillAlias:
        """校验 + 造 ORM 行，不碰事务（`add` 与 `add_pairs` 共用同一段判据）。"""
        if scope not in ALIAS_SCOPES:
            raise AliasPairRejectedError(f"未知的别名域：{scope!r}，只允许 {ALIAS_SCOPES}")
        if source not in ALIAS_SOURCES:
            raise AliasPairRejectedError(f"未知的别名来源：{source!r}，只允许 {ALIAS_SOURCES}")
        for label, value in (("canonical_key", canonical_key), ("alias_key", alias_key)):
            if not value or not value.strip():
                raise AliasPairRejectedError(f"{label} 不能为空")
            if len(value) > KEY_MAX_LENGTH:
                raise AliasPairRejectedError(
                    f"{label} 超过 {KEY_MAX_LENGTH} 字符：别名存的是归一后的键，不是原始写法"
                )
        if canonical_key == alias_key:
            raise AliasPairRejectedError(
                f"别名两侧是同一个键 {canonical_key!r}：这一行不表达任何配对关系"
            )
        conflict = alias_version_tail_conflict(canonical_key, alias_key)
        if conflict is not None:
            raise AliasPairRejectedError(conflict)

        timestamp = (now if now is not None else utc_now()) + timedelta(microseconds=index)
        return SkillAlias(
            id=str(uuid4()),
            canonical_key=canonical_key,
            alias_key=alias_key,
            scope=scope,
            source=source,
            note=note,
            created_at=timestamp,
            updated_at=timestamp,
        )
