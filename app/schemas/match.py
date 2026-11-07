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


class MatchResult(BaseModel):
    """一次匹配的完整载荷。

    `profile_id` / `profile_title` 是 §13 复核补的：请求体为空 ⇒ 用哪份档案是隐式选择，
    不告诉用户是按谁的分数，他第一反应是「分数算错了」。
    """

    profile_id: str | None
    profile_title: str | None
    job_id: str
    score: float
    weights_version: str
    terms: list[MatchTerm]
    covered: list[MatchCovered]
    partial: list[MatchPartial]
    gaps: list[MatchGap]
    meta: MatchMeta
