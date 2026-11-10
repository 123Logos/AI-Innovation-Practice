"""技能名「是不是同一个」的唯一判定口径。

为什么住在 `app/core` 而不是 `app/schemas/job.py`：import-linter 的契约是**传递**的。
这个键原本定义在 `schemas/job.py` 里，而 `schemas/job.py` 会 import
`repositories/job_repository`；一旦档案侧为了共用同一个口径去 import `schemas.job`，
就多出这条可达路径：

    integrations.resume_generator → schemas.profile → schemas.job → repositories.job_repository

它撞上「`app.integrations` 不允许 import `app.repositories`」，而这个键本身零依赖——
放 core 才是它真实的位置，不是为了让契约过关而做的迁走。

B1（`ADR-016`）把**别名层**也放在这里，理由与上面同构：别名是「判定口径的一部分」，
不是「一种存储」。它在这里以三种形态存在：

- `AliasIndex`：一次匹配请求载入**一次**的内存形状（`scope → 键 → 可对上的键集合`，
  已含双向），热路径只查它，不查库；
- `alias_version_tail_conflict()`：成对入库的硬约束（§2.2「版本尾巴必须一致」），
  写侧与将来的种子脚本共用同一个判据；
- `skill_forms_match(..., aliases=...)`：四条词面规则**全部判否之后**的兜底查询。

「不许 N+1」由 `tests/test_skill_alias_layer.py` 数 SQL 次数钉住，不是靠这段注释。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

#: 「写法等价」判据的版本号，随两个响应一起对外（`meta.matcher_version`）。
#:
#: 为什么要有这个常量：`skill_forms_match` 的四条规则一旦改动，同一个人、同一份档案、
#: 同一个岗位的判定结果就会变，而响应里没有任何东西能说明「这次用的是哪版判据」——
#: 用户看到的现象是「昨天还标已具备，今天没了」，谁都排查不出来。
#: 第 3c 条的 `GET /jobs/{job_id}/requirement-match` 与匹配端点**共用同一份判据**，
#: 所以两边必须吐同一个值；ADR-013 §11 定的回归断言就是拿这个字段做锁。
#:
#: 什么时候必须 +1：改动 `skill_forms_match` / `skill_stem` 的四条规则、分隔符集合
#: `_VERSION_TAIL`、或主版本号比较方式。只改注释、只改文档不算。
#:
#: **v1 → v2（B1，`ADR-016` §2.5）**：判据里多了「查别名表兜底」这一步，**哪怕表是空的、
#: 一次都没命中**。按上一条自己写的规则，这就是必须升的情形——升的不是分数算法而是
#: **判据的构成**：v2 的答案由「词面四条 + 一张人工词表」共同决定，v1 只由词面决定。
#: 词表**内容**的版本另有一格（`SKILL_ALIAS_VERSION`），两条轴各管各的上升判据。
SKILL_MATCHER_VERSION = "v2"

#: 版本号尾巴：`Python3` / `Python 3.13` / `Office2019` / `JDK 1.8` / `C++11` 的最后一截。
#: 分隔符集合刻意不含 `+` 与 `#`——那两类是语言名的一部分，不是版本号的分隔。
_VERSION_TAIL = re.compile(r"[ \t._/-]*\d+(?:[./]\d+)*$")

#: 别名的两个域（`ADR-016` §2.2「必须有第二个域」）。
#: `skill` 管技能写法的同义（`JS`↔`JavaScript`），`role` 管目标方向与岗位标题的同义
#: （`前端开发工程师`↔`React 工程师`）。第二个域不是可选项：老板点名的「同方向不同词」
#: 恰好**不在技能表里**，而在自由文本 `student_profiles.target_role` 与 `jobs.title` 上。
ALIAS_SCOPE_SKILL = "skill"
ALIAS_SCOPE_ROLE = "role"
#: 值域与 `skill_aliases.scope` 的 CHECK 约束同一段（迁移 `20261109_0015`），两处不许分叉。
ALIAS_SCOPES = (ALIAS_SCOPE_SKILL, ALIAS_SCOPE_ROLE)

#: **别名表内容**的版本号，随两条端点的 `meta.alias_version` 对外（`ADR-016` §2.5 第三条轴）。
#:
#: 为什么它不和 `SKILL_MATCHER_VERSION` 合成一格：判据是代码、词表是数据。词表会随真实用户
#: 反馈频繁增删而规则不变，「同一份代码 + 不同词表给出不同判定」只有这一格能说明是哪一版，
#: 合并成一格就再也回答不了「分数为什么变了」。
#:
#: 什么时候必须 +1：`skill_aliases` 里**任何一行**增、删、改。B1 交付时空表，起始值仍是
#: `v1`——「还没有数据」不等于「这一格不发」（§2.5 末句写死了这一点）。
SKILL_ALIAS_VERSION = "v1"

#: `source` 的两个值（§2.2）：`seed` = 人工评审入库，`ai_reviewed` = 阶段 3 的 AI 建议经
#: 人工评审后入库。**运行时永远不查 AI**：匹配链路「确定性可复现」的既有承诺不因别名层松动，
#: 所以这一列记的是「这一行当初怎么进来的」，与本次判定用了什么手段无关。
ALIAS_SOURCE_SEED = "seed"
ALIAS_SOURCE_AI_REVIEWED = "ai_reviewed"
ALIAS_SOURCES = (ALIAS_SOURCE_SEED, ALIAS_SOURCE_AI_REVIEWED)


@dataclass(frozen=True)
class AliasIndex:
    """`skill_aliases` 的内存形状：`scope -> 归一后的键 -> 能与它对上的键集合`。

    三个设计决定，每个都对应一条会被撞到的失败：

    1. **存的是 key 而不是原始写法**（§2.2「别名对存在 `skill_name_key` 的输出空间里」）：
       别名是 key 之后的**追加判据**，不是 key 的替代。原始写法永远只在 `skills.name` /
       `job_skills.name` 里，所以 `ADR-013` 的回显契约（`name` 回岗位侧原值、
       `matched_form` 回学生侧原值）不受这一层影响。
    2. **入库单向一行、在这里展开成双向**（§2.2）：不展开就会出现「A 匹配 B、B 不匹配 A」
       这种无法解释的不对称；而存两行则要冒「漏一行」的风险，两种里选前者，
       因为对称闭包在这里是**一次构造**、`in` 是 O(1)。
    3. **不可变**（frozen + `MappingProxyType` + `frozenset`）：它跨着一次匹配请求的
       好几个函数被传递，可变就等于「谁都能顺手改一下判据」，而那正是 `ADR-013`
       一路在防的「第二个口径」。
    """

    #: `skill` 域：喂给 `skill_forms_match` 的兜底那一支。
    skill: Mapping[str, frozenset[str]] = MappingProxyType({})
    #: `role` 域：喂给 `preference` 第四档（§2.4），只在 `target_role` × `job.title` 上查。
    role: Mapping[str, frozenset[str]] = MappingProxyType({})

    @classmethod
    def empty(cls) -> AliasIndex:
        """空索引 = 今天的纯词面行为（§2.2「表可以空。空表 = 纯词面」）。"""
        return cls()

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[str, str, str]]) -> AliasIndex:
        """把 `(canonical_key, alias_key, scope)` 三元组展开成双向索引。

        未知 scope 的行**静默忽略**而不是抛错：这一层的判据是「读到的行里只认这两个域」，
        而库里那一列由 CHECK 约束挡住第三种值（`ck_skill_aliases_scope`）。
        在这里再抛一次，等于把同一个约束写两遍——两遍都会过期，且这一遍在热路径上。
        """
        skill: dict[str, set[str]] = {}
        role: dict[str, set[str]] = {}
        for canonical_key, alias_key, scope in rows:
            bucket = {ALIAS_SCOPE_SKILL: skill, ALIAS_SCOPE_ROLE: role}.get(scope)
            if bucket is None:
                continue
            bucket.setdefault(canonical_key, set()).add(alias_key)
            bucket.setdefault(alias_key, set()).add(canonical_key)
        return cls(
            skill=MappingProxyType({key: frozenset(partners) for key, partners in skill.items()}),
            role=MappingProxyType({key: frozenset(partners) for key, partners in role.items()}),
        )

    def hit(self, scope: str, left_key: str, right_key: str) -> bool:
        """这一对 key 在该域里成不成对（两个方向都算，见上面的决定 2）。"""
        bucket = self.skill if scope == ALIAS_SCOPE_SKILL else self.role
        return right_key in bucket.get(left_key, frozenset())

    @property
    def is_empty(self) -> bool:
        return not self.skill and not self.role


_EMPTY_ALIAS_INDEX = AliasIndex()


def alias_index_or_empty(aliases: AliasIndex | None) -> AliasIndex:
    """把「没传索引」翻成「空索引」。

    为什么不把默认值直接写成 `AliasIndex()`：调用点有两类，语义不同——
    打分链路上永远有一个真索引（`_gather` 载入的那一份），而**单元测试与将来的其它消费者**
    只想问词面四条。用 `None` 当参数默认，才能让「没查表」与「查了一张空表」在签名上分得开，
    同时两者判定结果恒同（那是 `ADR-016` §5.1 的第一条：空表 = v1 逐字相同）。
    """
    return _EMPTY_ALIAS_INDEX if aliases is None else aliases


def skill_name_key(name: str) -> str:
    """「这两条是不是同一个技能」的判定键：NFKC（全角→半角）+ casefold，只有两步。

    为什么**不是** §2.D.1 的 `normalize_for_trace()`（四步，多去空白与去标点）：
    那套是给 `evidence` 的子串核验用的，第 4 步删标点这一刀砍在技能名上会误并真实条目——
    `#` 的类别是 `Po`，于是 `C#` 与 `C` 折成同一个键；`_` 是 `Pc`，`user_id` 与
    `userid` 同理。对核验来说这点宽容是划算的（误杀集中在信息量最低的标点差异上），
    但技能名被这样合并掉的是一条真实的岗位要求，而且用户在结果里看不出少了东西。
    （`+` 的类别是 `Sm`、不删，所以 `C++` 与 `C` 两种口径下都分得开。）

    为什么三处必须共用这一个函数：提取侧的重名合并、岗位写侧的「技能名称重复」422、
    档案写侧的同名 422 回答的是同一个问题。各写一份就会出现「提取时已经合并了、点保存却
    报重复」这种用户没法理解的抖动；数据库那侧的宽容度由 `utf8mb4_0900_ai_ci` 提供，
    这里只是把它提前成可读的报错与一条干净的结果。

    不含去空白：两端空白归各自的入口负责（岗位写侧 `JobSkillInput.normalize_name`、
    档案写侧 `SkillInput.normalize_name`、提取侧 `_verify_item`），在这里再 strip 一次
    只会让「谁负责什么」变得不可查。
    """
    return unicodedata.normalize("NFKC", name).casefold()


def skill_stem(name: str) -> tuple[str, str | None]:
    """`(词干, 版本号原文)`：没有版本号时第二项是 `None`。

    `Python3` → `('python', '3')`；`Python 3.13` → `('python', '3.13')`；
    `Python` → `('python', None)`；`C++` → `('c++', None)`（结尾不是数字）。
    整串就是数字（`2019`）不算版本号：那不是「某个名字的版本」。
    """
    folded = skill_name_key(name)
    match = _VERSION_TAIL.search(folded)
    if match is None:
        return folded, None
    stem = folded[: match.start()].rstrip(" \t._/-")
    if not stem:
        return folded, None
    return stem, folded[match.start() :].lstrip(" \t._/-")


def _major(version: str) -> str:
    """版本号的主号：`3.13` → `3`，`1.8` → `1`，`2019` → `2019`。"""
    return version.split(".", 1)[0]


def alias_version_tail_conflict(canonical_key: str, alias_key: str) -> str | None:
    """成对入库的硬约束：两侧的版本尾巴必须**一致**（`ADR-016` §2.2，不是文档祈祷）。

    判据用的是现成的 `skill_stem`（它内部就是 `_VERSION_TAIL`），所以这一条与词面四条
    共用同一把尺子——不会出现「判据说有版本号、约束说没有」。

    「一致」取**严格**读法：两侧尾巴逐字相同才放行，其中「两侧都没有版本号」也算相同。
    于是 `python 2` / `python 3.13` 被拒（主号不同，正是 §2.2 点名的假匹配），
    `python` / `python3` 也被拒（一侧有、一侧没有：把「整个系列」与「某一个发行版」
    合成一回事，同样会让用户带着错话去面试）。这条读法比 §2.2 举的那个例子更严，
    代价与备选取向记在 B1 交付说明里，等老板拍。

    返回 `None` 表示允许；否则返回一句能直接进报错信息的原因。

    **落点说明**（B1 的任务书把这格钉成「写入路径的校验函数 + 一条测试」）：
    本批不写任何种子迁移（`ADR-016` §3「每条都要人工过一遍」），所以库里没有「迁移校验」
    可放；校验放在 `SkillAliasRepository.add()` 这个**唯一写入口**上，将来的种子迁移同样
    必须走它。库里另有 `uq_skill_aliases_pair` 挡重复行，但它挡不住版本尾巴不同的**那一**
    对——两件事不许混：一个管「同一对存了两遍」，一个管「这对本身是假的」。
    """
    _, tail_left = skill_stem(canonical_key)
    _, tail_right = skill_stem(alias_key)
    if tail_left == tail_right:
        return None
    return (
        f"别名两侧的版本尾巴必须一致：{canonical_key!r} 的尾巴是 {tail_left!r}，"
        f"{alias_key!r} 的尾巴是 {tail_right!r}"
    )


def _lexical_forms_match(a: str, b: str) -> bool:
    """四条**纯词面**规则，B1 之前 `skill_forms_match` 的全部本体，一字未改。

    拆成私有函数只为一件事：别名兜底必须发生在「四条规则全部判否之后」
    （`ADR-016` §2.3 的次序是刻意的——便宜的、可解释的、用户看得见的先跑，
    人工词表只做兜底）。而规则 2 是「词干不同 → 立刻 `return False`」，
    直接在原函数尾部追加兜底就永远轮不到 `JS`↔`JavaScript` 这种最常见的别名对。
    """
    if skill_name_key(a) == skill_name_key(b):
        return True
    stem_a, version_a = skill_stem(a)
    stem_b, version_b = skill_stem(b)
    if stem_a != stem_b:
        return False
    if (version_a is None) != (version_b is None):
        return True
    if version_a is not None and version_b is not None:
        return _major(version_a) == _major(version_b)
    return False


def skill_forms_match(a: str, b: str, aliases: AliasIndex | None = None) -> bool:
    """匹配专用的「是不是同一个技能」，比 `skill_name_key` 宽一格、比子串推断严。

    为什么这个函数可以存在，而 ADR-013 §1.3 当年否决的那版 `match_key()` 不行：
    那次两个键回答的是**同一个问题**（去重），多一个名字只是多一层转发。这里两个键
    现在真的干两件不同的事——去重键必须继续把 `Python` 与 `Python3` 当两行存
    （用户想同时列 `C` 与 `C++` 时不许报「重复」），而匹配键要把它们当一个技能认。
    判据与理由见 ADR-013 §1.3 的 2026-11-08 更正。

    四条词面规则，全部**与领域无关**（没有一张计算机词表，所以财务/机械/设计/护理同样受益）：

    1. 折叠后全等 → 同一技能（今天的全部行为，一字不改）；
    2. 词干不同 → 不同技能。**没有任何前缀/子串推断**，于是 `C` / `C++` / `C#`、
       `Java` / `JavaScript`、`SQL` / `MySQL`、`Go` / `Golang`、`R` / `React` 全部分得开；
    3. 词干相同、恰好一侧带版本号 → 同一技能（`Python3` 认 `Python`）：
       白名单是对面真写过的那个不带号的写法，而不是我手写的词表；
    4. 词干相同、两侧都带版本号 → **主版本号相同**才算同一个
       （`Python3` 认 `Python 3.13`；`Python2` 不认 `Python3`；`Vue 2` 不认 `Vue 3`）。

    第四条之后才是别名兜底（`aliases`，B1 / `ADR-016` §2.3）：缩写别名（`K8s`↔`Kubernetes`、
    `JS`↔`JavaScript`）词面判不出，只能由一张**人工评审过的词表**认。三个约束：

    - **只在 `skill` 域查**，且只在四条规则全判否之后查。次序反过来会让「词表里有一条过期的
      `python2`/`python3`」覆盖掉规则 4 那条正确的判否；
    - **不查库**：`aliases` 是一次匹配请求载入一份的 `AliasIndex`（§3「不许 N+1」）。
      传 `None` 就是空表 = 纯词面行为，与 B1 之前的判定逐字相同（§5.1 的第一条）；
    - **不改判重**：这里判的是「匹配时认不认是一回事」，`skill_name_key` 那 5 个写入点
      一个没动（§2.1 选 (c) 的全部内容）。

    `SKILL_MATCHER_VERSION` 因此从 v1 升到 v2：判据的**构成**里多了这一步，哪怕表是空的。
    """
    if _lexical_forms_match(a, b):
        return True
    index = alias_index_or_empty(aliases)
    return index.hit(ALIAS_SCOPE_SKILL, skill_name_key(a), skill_name_key(b))

