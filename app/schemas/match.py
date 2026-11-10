"""人岗匹配的响应模型（ADR-013 §3）。

**只依赖 pydantic**：这些模型是 HTTP 契约，不是业务规则。打分逻辑与计数都在
`app/services/match_scoring.py` 与 `app/services/match_service.py` 里，这里只做形状与
边界声明。分层上刻意不 import `services`——反向（services import schemas）已经是
`job_service` / `profile_service` 的既有方向，反过来会把 schemas 变成循环的起点。

`terms[].value` 允许 `null` 是有语义的：「这一项没算出来」与「算出来是 0」必须能在
响应里区分开（§5.1）。如果把它压成 0，前端就再也无法解释为什么两个人的 0.72 不同义。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EvidenceMode = Literal["ai_scored", "keyword_fallback"]

#: §6.5 的五种退化原因。`null` = AI 正常参与。
#: 这五个值必须是封闭枚举：前端按它分句，多一个值就少一句文案，
#: 而「文案缺失」在用户侧表现为「什么也没说」。
DegradeReason = Literal["key_missing", "key_invalid", "upstream", "timeout", "bad_output"]


class MatchTerm(BaseModel):
    name: str
    weight: float
    value: float | None
    computed: bool
    note: str | None = None


class MatchEvidence(BaseModel):
    experience_id: str
    field: str
    #: 经历原文的**连续子串**（§3 的反幻觉闸门；由构造保证，见 match_scoring 的窗口函数）。
    snippet: str
    strength: float


class MatchCovered(BaseModel):
    skill: str
    evidence: list[MatchEvidence] = Field(default_factory=list)


class MatchPartial(BaseModel):
    skill: str
    reason: str


class MatchGap(BaseModel):
    skill: str
    action: str
    verify: str


class MatchMeta(BaseModel):
    model: str | None
    prompt_version: str | None
    evidence_mode: EvidenceMode
    degrade_reason: DegradeReason | None
    candidate_pairs: int
    verified_pairs: int
    dropped_unreferenced: int
    dropped_unknown_skill: int
    dropped_unusable_strength: int
    dropped_missing_output: int
    clamped_strength: int
    fragments_truncated: int
    truncated: bool
    finish_reason: str | None
    weight_sum_used: float
    no_required_skills: bool
    #: 「这次用的是哪版写法判据」，与 `GET /jobs/{job_id}/requirement-match` 的
    #: `meta.matcher_version` 同源（`app/core/skill_names.py::SKILL_MATCHER_VERSION`）。
    #: 它存在的意义是第 3c 条的一致性断言：两个端点报的必须是同一版判据，
    #: 否则「同屏一致」只能靠人记得同步改。
    matcher_version: str
    #: 「这次用的是**哪一版别名词表**」（`ADR-016` §2.5 的第三条轴，起始 `v1`）。
    #: 与上一格不是一回事：`matcher_version` 管判据**规则**（代码），这一格管词表**内容**（数据）。
    #: 词表会随真实用户反馈频繁增删而规则不变，分数变了要靠这两格才分得清是哪一轴造成的。
    #: 空表也发这一格——§2.5 末句写死了「不因为还没有数据而不发这一格」。
    alias_version: str
    #: 本次读数的**组装时刻**（东八区 ISO 串，应用时钟）。FR-05 要求结果展示「更新时间」，
    #: 这一格就是那个「更新」。它刻意**不是**「分数被算出来的时刻」——我们现在没有缓存，
    #: 那个时刻与这一格是同一个瞬间，写成两格只会让人以为它们会分叉（`ADR-020` §2.1）。
    generated_at: str
    #: 这一格回答的是 FR-05 的「数据新鲜度」：**这次读数背后的岗位行与档案行，最后一次被
    #: 改动是什么时候**（两者取较晚者，落库值，不是应用时钟）。
    #: 与 `generated_at` 分开是因为它们回答两个不同问题：「这份报告几点打的」与
    #: 「它底下的数据旧不旧」。只有前者时，用户看到「刚刚生成」会以为岗位是新的。
    data_updated_at: str
    #: 不给分的原因。`not_evaluated` = 岗位没填要求技能，核心 term 不在，整个不评估
    #: （老板 2026-11-08 裁定，ADR-013 §5.1 末）；`nothing_computable` 保留给「分母为 0」这一格，
    #: 本层已走不到。有分数时恒为 `null`。前端**不按它分句**，它是给我们对账与排障用的。
    #: 有分数时恒为 `null`——不要让前端去猜「0 分是没覆盖还是算不出」。
    note: str | None = None


class MatchResult(BaseModel):
    """一次匹配的完整载荷。

    `profile_id` / `profile_title` 是 §13 复核补的：请求体为空 ⇒ 用哪份档案是隐式选择，
    不告诉用户是按谁的分数，他第一反应是「分数算错了」。

    `score` 可空。现在只有一种来路：**岗位没填任何要求技能 ⇒ 核心 term 不在 ⇒ 整个不评估**
    （老板 2026-11-08 裁定，ADR-013 §5.1 末）。`null` 与 `0.0` 的分工因此变得干净：
    `0.0` 只剩「岗位有要求技能而一条都没对上」，`null` 只剩「没有可比对的东西」。
    把后者压成 0 会让「刚注册、什么都没填」的人稳定看到 0%，而那正是 §5.1 用来否决
    「缺项记 0 分」的现象本身；反过来，让 0.10 的分母给出一个分数，实测能把
    什么都没比对的岗位显示成 100%。
    """

    profile_id: str | None
    profile_title: str | None
    job_id: str
    score: float | None
    weights_version: str
    terms: list[MatchTerm]
    covered: list[MatchCovered]
    partial: list[MatchPartial]
    gaps: list[MatchGap]
    meta: MatchMeta


#: 第 3c 条（ADR-013 §10）的三档状态，名字与 `MatchResult` 的三档清单**同一个意思**：
#: `covered` = 写法命中且经历里有出处；`partial` = 写法命中但找不到出处或证据偏薄；
#: `missing` = 写法没命中。判等本体只有 `match_scoring.score_match` 那份分区，
#: 这里只是把它的三个清单压回成「岗位的一条要求 → 一个状态」，不重新判一遍。
RequirementStatus = Literal["covered", "partial", "missing"]


class RequirementCoverageRow(BaseModel):
    """岗位的一条要求技能，对上「当前用户这条处在哪个状态」。"""

    #: 岗位侧的原写法，逐字回显（不是我们规范化的结果）：卡片与角标都指着它说话。
    name: str
    status: RequirementStatus
    #: 学生技能表里真正命中的那个写法——岗位要求 `Python`、简历写 `Python3` 时这里是 `Python3`。
    #: `missing` 时恒为 `null`。回显对面写法而不是回显规范名，是因为前者是用户改得动的信息
    #: （把两边写成同一个样子，连去重那关都顺了）；判据见 `match_scoring.matched_with_forms`。
    matched_form: str | None = None


class RequirementMatchMeta(BaseModel):
    """这条端点的元信息。

    刻意**没有** `evidence_mode` / `degrade_reason` / 任何计数：这个端点按定义不调 AI，
    把它写成 `keyword_fallback` 会撒谎（那三个字在 §6.5 里的意思是「AI 该参与而没参与成」），
    写成 `ai_scored` 更会撒谎。

    它要的是**两把版本锁**而不是原来那一把：`matcher_version` 锁判据规则，
    `alias_version` 锁词表内容（`ADR-016` §2.5）。这一格必须有，因为别名行会**直接改
    角标的颜色**——`coverage[].status` 从 `missing` 变 `covered` 是用户在屏幕上看得见的变化，
    而它不是代码变更造成的；没有这一格，「昨天灰的今天绿了」在角标这一侧就没有坐标可对账。
    """

    matcher_version: str
    alias_version: str
    #: 与 `MatchMeta.generated_at` / `data_updated_at` 同一对语义、同一批来源行。
    #: `data_updated_at` 在两个端点上必须逐字相同（同一份岗位 × 同一份档案），
    #: 钉它的测试是 `tests/test_job_match.py::test_both_endpoints_report_the_same_data_updated_at`；
    #: `generated_at` 允许不同，因为那是两次请求的两个瞬间。
    generated_at: str
    data_updated_at: str


class RequirementMatchResponse(BaseModel):
    """`GET /jobs/{job_id}/requirement-match` 的载荷（ADR-013 §10 第 3c 步）。

    `profile_id` / `profile_title` 与匹配端点同样回显：角标说的是「按哪份简历标的」。
    没有这两格，用户切了默认简历之后看到角标变化，只会怀疑前端抽风。
    """

    job_id: str
    profile_id: str | None
    profile_title: str | None
    #: 顺序与 `GET /jobs/{job_id}` 的 `skills` 一致、条数一致（老板 2026-11-08 判据 (a) 的基准域 U）。
    coverage: list[RequirementCoverageRow]
    meta: RequirementMatchMeta
