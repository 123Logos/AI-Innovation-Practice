"""§2.D.1 归一化函数 `_normalize_for_trace` 的用例表逐条（ADR-011 2.D.1）。

这个文件只问一件事：**「标点是否参与比较」这个口径到底被实现了没有**。
它是本端点误杀率的直接决定项，不是实现细节，所以 ADR 把它写成可测的四步函数 +
一张五行用例表，这里逐行落成断言（AGENTS.md §4.4：不得靠推断）。

三条必须留住的口径证据：

1. **第 2、3 行是双向断言**：同一对字符串在「标点参与」的实现里**必须被丢弃**、
   在 ADR 选择的「标点不参与」实现里**必须通过**。反向那半是刻意写的——
   它钉住口径本身，防止后来人「顺手改成严格比较」而把测试一起改绿。
   第三行的例证是 v4 修正过的（v3 原例 `负责数据采集负责清洗` 自己漏了第二个「数据」，
   两种口径都丢弃，根本不构成「放过」）。
2. **`+` 必须保留**：它的 Unicode 类别是 `Sm`（数学符号），不在被删的 `P*` 里，
   所以「C++」里的加号是技能名的一部分而不是分隔符。删掉它就是实现错误。
3. **`_` 会被删是已知取舍，不是 bug**：`Pc`（连接标点）含下划线，于是
   `user_id` 与 `userid` 被判为相同。它只影响定位的严格度，不影响「是否出自原文」，
   ADR 明确接受。这条用例把取舍写在这里，是为了让后来人读到的是决定而不是缺陷报告。

第 4 行为什么必须两种口径都丢弃：真幻觉与标点无关——这就是「不参与」不损失
反幻觉强度的根据（ADR 2.D.1 原话）。

本文件不连数据库、不外呼：被测对象是一个纯函数。
"""

import unicodedata

import pytest

from app.integrations.skill_extractor import is_traceable, normalize_for_trace


def normalize_keeping_punctuation(text: str) -> str:
    """「标点参与比较」的那个**被否决**的方案，只在测试里存在。

    它就是四步函数删掉第 4 步：NFKC → casefold → 去空白。
    ADR 要求「同一对字符串在去掉第 4 步的实现里必须被丢弃」，而生产代码里
    不该留两份归一化实现（那正是「两处各写一份必然漂移」），所以对照口径放这儿。
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in folded if not char.isspace())


# --------------------------------------------------------------------------- #
# 一、用例表第 1 行：全角逗号 vs 半角逗号（两条路都过）
# --------------------------------------------------------------------------- #


def test_table_row_1_full_width_comma_passes_under_both_readings() -> None:
    """你举的这个反例两条路都过，因为 NFKC 已把全角「，」折成半角「,」。

    ADR 特别指出：真正的分歧不在第 1 行，在第 2、3 行——所以这一行必须
    两个口径都断言「通过」，不能被当成「严格比较也行」的证据。
    """
    jd = "熟练掌握 Python，熟悉 FastAPI"
    evidence = "熟练掌握 Python, 熟悉 FastAPI"

    assert normalize_for_trace(jd) == "熟练掌握python熟悉fastapi"
    assert normalize_for_trace(evidence) == "熟练掌握python熟悉fastapi"
    assert is_traceable(evidence, jd) is True
    # 反向：连标点一起比也过（NFKC 已经把全角标点折成半角）。
    assert normalize_keeping_punctuation(jd) == "熟练掌握python,熟悉fastapi"
    assert normalize_keeping_punctuation(evidence) in normalize_keeping_punctuation(jd), (
        "第 1 行在「标点参与」口径下也必须通过，否则它就不是两条路的公共反例"
    )


# --------------------------------------------------------------------------- #
# 二、用例表第 2 行：顿号「、」——选「不参与」的主因
# --------------------------------------------------------------------------- #


def test_table_row_2_ideographic_comma_only_passes_when_punctuation_is_dropped() -> None:
    """`、`(U+3001) 是 `Po` 但**没有** ASCII 兼容映射，NFKC 后原样保留。

    于是「、」对「,」这一对在严格比较下永不相等：中文标点怎么选与原文事实无关，
    却会整条误杀，而用户重试并不能改善——这就是 ADR 选「标点不参与」的主因。
    """
    jd = "熟悉 Python、FastAPI、Docker"
    evidence = "熟悉 Python, FastAPI, Docker"

    # ADR 附录里的实算中间值，逐字照抄成断言。
    assert normalize_for_trace(jd) == "熟悉pythonfastapidocker"
    assert normalize_for_trace(evidence) == "熟悉pythonfastapidocker"
    assert is_traceable(evidence, jd) is True, "标点不参与（本 ADR 选的口径）：必须通过"

    # 反向断言：同一对字符串在「标点参与」的实现里必须被丢弃。
    assert normalize_keeping_punctuation(jd) == "熟悉python、fastapi、docker"
    assert normalize_keeping_punctuation(evidence) == "熟悉python,fastapi,docker"
    assert normalize_keeping_punctuation(evidence) not in normalize_keeping_punctuation(jd), (
        "严格比较口径必须丢弃这条，否则第 2 行就证明不了两个口径有分歧"
    )


# --------------------------------------------------------------------------- #
# 三、用例表第 3 行：跨句去标点拼接——有意放过的代价
# --------------------------------------------------------------------------- #


def test_table_row_3_cross_sentence_join_is_deliberately_let_through() -> None:
    """v4 修正后的例证（v3 写成 `负责数据采集负责清洗` 是举错了例子）。

    `负责数据采集。负责数据清洗` × `负责数据采集负责数据清洗`：
    - 「标点参与」口径**必须丢弃**；
    - 「标点不参与」口径**必须通过**（这条 evidence 是 JD 去标点后的逐字片段，无省略）。

    这条**反向断言钉的就是口径本身**。放过的代价是有意接受的：两段短语都逐字出自 JD，
    「能回溯」这条判据并没有被削弱，而靠删标点拼出来的 evidence 藏不住一个
    JD 里根本不存在的技能名。
    """
    jd = "负责数据采集。负责数据清洗"
    evidence = "负责数据采集负责数据清洗"

    assert normalize_for_trace(jd) == "负责数据采集负责数据清洗"
    assert normalize_for_trace(evidence) == "负责数据采集负责数据清洗"
    assert is_traceable(evidence, jd) is True, "不参与口径：必须通过（这是有意放过）"

    assert normalize_keeping_punctuation(jd) == "负责数据采集。负责数据清洗"
    assert normalize_keeping_punctuation(evidence) == "负责数据采集负责数据清洗"
    assert normalize_keeping_punctuation(evidence) not in normalize_keeping_punctuation(jd), (
        "参与口径：必须丢弃——两半不同才构成「口径分歧」，改绿任何一半都是改口径不叫修 bug"
    )


# --------------------------------------------------------------------------- #
# 四、用例表第 4 行：真幻觉与标点无关（两种口径都拦得住）
# --------------------------------------------------------------------------- #


def test_table_row_4_real_hallucination_is_rejected_under_both_readings() -> None:
    """JD 里没有 Kubernetes，evidence 写「负责 Kubernetes 集群运维」→ 必须丢弃。

    这一行是整张表的存在理由：放宽标点**不损失**反幻觉强度，
    因为真正的编造在字符层面就不连续，两种口径都拦得住。
    """
    jd = "（JD 无 Kubernetes 字样）负责订单服务"
    evidence = "负责 Kubernetes 集群运维"

    assert is_traceable(evidence, jd) is False
    assert normalize_keeping_punctuation(evidence) not in normalize_keeping_punctuation(jd)


# --------------------------------------------------------------------------- #
# 五、用例表第 5 行：原文换行由第 3 步（去空白）覆盖
# --------------------------------------------------------------------------- #


def test_table_row_5_newline_is_removed_by_step_three() -> None:
    """JD 里写的是 `熟悉\\nPython`，模型给 `熟悉 Python`：去空白那一步负责。"""
    jd = "熟悉\nPython"
    evidence = "熟悉 Python"

    assert normalize_for_trace(jd) == "熟悉python"
    assert normalize_for_trace(evidence) == "熟悉python"
    assert is_traceable(evidence, jd) is True
    # 这一行两条路都过：分歧只在标点，与空白无关。
    assert normalize_keeping_punctuation(evidence) in normalize_keeping_punctuation(jd)


# --------------------------------------------------------------------------- #
# 六、符号类不删：`+` 是 `Sm`，是技能名的一部分
# --------------------------------------------------------------------------- #


def test_plus_sign_survives_because_sm_is_not_punctuation() -> None:
    """第 4 步只删 `P*`，符号类 `S` 一律不删：否则 `C++` 会被折成 `C`。"""
    assert unicodedata.category("+") == "Sm"
    assert normalize_for_trace("C++") == "c++"
    assert is_traceable("熟悉 C++", "岗位要求熟悉 C++ 与 Qt") is True
    # 反向：JD 里只有 `C` 而没有 `C++` 时不能被判成可回溯——
    # 把 `+` 当标点删掉就会给出一个假阳，那正是这条保留规则要防的。
    assert is_traceable("熟悉 C++", "岗位要求熟悉 C 语言") is False


def test_full_width_underscore_becomes_punctuation_and_is_dropped() -> None:
    """全角 `＿`(U+FF3F) 经 NFKC 得 `_`，类别 `Pc`，于是第 4 步把它删掉。"""
    assert unicodedata.normalize("NFKC", "＿") == "_"
    assert unicodedata.category("_") == "Pc"
    assert normalize_for_trace("表＿名") == "表名"


# --------------------------------------------------------------------------- #
# 七、已知取舍：下划线被删 → `user_id` ≡ `userid`
# --------------------------------------------------------------------------- #


def test_underscore_drop_is_an_accepted_tradeoff_not_a_bug() -> None:
    """`Pc` 含下划线，所以 `user_id` 与 `userid` 会被判为相同。

    **这是 ADR 2.D.1 明写的已知副作用，别当 bug 修**：它只影响「定位得够不够严」，
    不影响「这句话是否出自原文」。真要收紧，改的是口径、要另写 ADR，
    而不是在这里悄悄把第 4 步改成只删 `Po`/`Pd`。
    """
    assert normalize_for_trace("user_id") == "userid"
    assert is_traceable("用 userid 命名", "表结构里用 user_id 命名规范") is True
    # 反向：JD 里是 `user_id`、模型写的是 `userid`，只有「删掉 `Pc`」才判得相同。
    assert normalize_keeping_punctuation("用 userid 命名") not in normalize_keeping_punctuation(
        "表结构里用 user_id 命名规范"
    ), "保留下划线的口径下这是一次丢弃——「放过」确实发生，不是嘴上说说"


# --------------------------------------------------------------------------- #
# 八、函数自身的两条底线：空锚点与幂等
# --------------------------------------------------------------------------- #


def test_blank_anchor_is_never_traceable() -> None:
    """`"" in 任何串` 恒为真：空 evidence 必须先挡掉，否则幻觉会被判成「可回溯原文」。"""
    assert normalize_for_trace("") == ""
    assert normalize_for_trace("  \n\t ") == ""
    assert is_traceable("", "任何 JD") is False
    assert is_traceable("   ", "任何 JD") is False
    assert is_traceable("\u3000\n", "任何 JD") is False, "全角空格也被第 3 步吃掉"


def test_normalize_is_idempotent() -> None:
    """归一化两次与一次同值：核验路径对 `description` 与 `evidence` 各调一次，
    而写回路径的 `origin` 派生用的是**同一个函数**（不能一处归一化两次、另一处一次）。"""
    text = "熟练掌握 Python，熟悉 FastAPI、Docker（C++ 加分）"
    once = normalize_for_trace(text)
    assert normalize_for_trace(once) == once


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("Python", True),
        ("python", True),
        ("Ｐython", True),  # 首字母全角：NFKC 折成半角 → 仍是原文子串
        ("PYTHON", True),
        ("Python 框架", False),  # JD 里没有「框架」两个字连着 Python
        ("Kubernetes", False),
    ],
)
def test_case_and_width_folding_on_a_plain_jd(evidence: str, expected: bool) -> None:
    """大小写、全半角都不敏感；JD 里不存在的内容一律不敏感地挡在外面。"""
    jd = "后端开发，要求 Python 熟练"
    assert is_traceable(evidence, jd) is expected
