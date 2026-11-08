"""技能名「是不是同一个」的唯一判定口径。

为什么住在 `app/core` 而不是 `app/schemas/job.py`：import-linter 的契约是**传递**的。
这个键原本定义在 `schemas/job.py` 里，而 `schemas/job.py` 会 import
`repositories/job_repository`；一旦档案侧为了共用同一个口径去 import `schemas.job`，
就多出这条可达路径：

    integrations.resume_generator → schemas.profile → schemas.job → repositories.job_repository

它撞上「`app.integrations` 不允许 import `app.repositories`」，而这个键本身零依赖——
放 core 才是它真实的位置，不是为了让契约过关而做的迁走。
"""

import re
import unicodedata

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
SKILL_MATCHER_VERSION = "v1"

#: 版本号尾巴：`Python3` / `Python 3.13` / `Office2019` / `JDK 1.8` / `C++11` 的最后一截。
#: 分隔符集合刻意不含 `+` 与 `#`——那两类是语言名的一部分，不是版本号的分隔。
_VERSION_TAIL = re.compile(r"[ \t._/-]*\d+(?:[./]\d+)*$")


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


def skill_forms_match(a: str, b: str) -> bool:
    """匹配专用的「是不是同一个技能」，比 `skill_name_key` 宽一格、比子串推断严。

    为什么这个函数可以存在，而 ADR-013 §1.3 当年否决的那版 `match_key()` 不行：
    那次两个键回答的是**同一个问题**（去重），多一个名字只是多一层转发。这里两个键
    现在真的干两件不同的事——去重键必须继续把 `Python` 与 `Python3` 当两行存
    （用户想同时列 `C` 与 `C++` 时不许报「重复」），而匹配键要把它们当一个技能认。
    判据与理由见 ADR-013 §1.3 的 2026-11-08 更正。

    四条规则，全部**与领域无关**（没有一张计算机词表，所以财务/机械/设计/护理同样受益）：

    1. 折叠后全等 → 同一技能（今天的全部行为，一字不改）；
    2. 词干不同 → 不同技能。**没有任何前缀/子串推断**，于是 `C` / `C++` / `C#`、
       `Java` / `JavaScript`、`SQL` / `MySQL`、`Go` / `Golang`、`R` / `React` 全部分得开；
    3. 词干相同、恰好一侧带版本号 → 同一技能（`Python3` 认 `Python`）：
       白名单是对面真写过的那个不带号的写法，而不是我手写的词表；
    4. 词干相同、两侧都带版本号 → **主版本号相同**才算同一个
       （`Python3` 认 `Python 3.13`；`Python2` 不认 `Python3`；`Vue 2` 不认 `Vue 3`）。

    缩写别名（`K8s`、`PS`、`Golang`→`Go` 这类词面完全不同的写法）不在这套规则里，
    属于 ADR-013 §10 的 L1 词表，本轮按老板 2026-11-08 的裁定暂缓。
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
