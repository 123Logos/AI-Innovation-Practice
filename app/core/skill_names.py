"""技能名「是不是同一个」的唯一判定口径。

为什么住在 `app/core` 而不是 `app/schemas/job.py`：import-linter 的契约是**传递**的。
这个键原本定义在 `schemas/job.py` 里，而 `schemas/job.py` 会 import
`repositories/job_repository`；一旦档案侧为了共用同一个口径去 import `schemas.job`，
就多出这条可达路径：

    integrations.resume_generator → schemas.profile → schemas.job → repositories.job_repository

它撞上「`app.integrations` 不允许 import `app.repositories`」，而这个键本身零依赖——
放 core 才是它真实的位置，不是为了让契约过关而做的迁走。
"""

import unicodedata


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
