"""人岗匹配的纯打分层（ADR-013 §7/§8）。

这一层只吃值、只吐分数：不 import 仓储、不 import `integrations`、不 import FastAPI。
读库与调模型留在 `app/services/match_service.py`；分层由 import-linter 的契约守着，
不是靠这段注释。

为什么片段由我们自己切、不让模型复述：§3 的反幻觉闸门要求
`covered[].evidence[].snippet` 是经历原文的连续子串。子串是我们从原文切出来的，
这条性质由构造保证；模型只负责给已经定位好的片段打分，所以它既发明不出覆盖，
也编不出证据。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.core.deadline import is_expired
from app.core.skill_names import skill_forms_match, skill_name_key, skill_stem

#: 五项权重，沿用 `docs/PRD.md:119`。键名直接进 `data.terms[].name`，属于对外契约。
MATCH_WEIGHTS: dict[str, float] = {
    "coverage": 0.45,
    "evidence": 0.20,
    "relevance": 0.15,
    "preference": 0.10,
    "remaining_time": 0.10,
}
MATCH_WEIGHTS_VERSION = "v1"

#: 一个片段 = 命中位置前后各扩这么多**原文**字符（§6 第 2 点的输入预算）。
#: 有了它，送进模型的字数与用户写的经历长度解耦：库里 `description` 是无长度约束的 `Text`，
#: 整段送的话 prompt 大小就由用户自由文本决定（实测 dev 库最长一条 615 字）。
MATCH_SNIPPET_RADIUS = 80

#: 一个技能最多回几个片段。分数不受它影响（强度仍按真实命中经历条数算），
#: 它只限制卡片长度：堆二十条片段没人会读完，只会看不懂「差在哪」。
MAX_SNIPPETS_PER_SKILL = 3

#: 关键词兜底里「命中几条经历算满强度」（§7.2）。计的是经历条数，不是出现次数——
#: 同一句话里写三遍 Python 不该刷分。
KEYWORD_FALLBACK_HITS_CAP = 3

#: 强度低于此值的命中技能进 `partial` 而不是 `covered`。
#:
#: 曾经是 0.4，被集成测试改掉了。0.4 落在关键词档位 0.333 与 0.667 之间，于是
#: 「只有一段项目经历」这个本产品里最常见的形状，`已满足` 一栏会整个空掉——
#: 那不是证据弱，那是代理指标在替它没资格做的判断下结论：`min(hits,3)/3` 测的是
#: **出现了几条经历**，而「提到名字但没做过」只有 AI 模式分得出来。
#: 0.3 把边界放回两种模式各自真正在测的东西：
#: 关键词模式 = 有没有一条真实出处；AI 模式 = 0.3 那一档「只是提到了名字」（见 Prompt 的档位定义）。
#: 零出处的命中技能不走这个阈值，它由「命中但找不到片段」那条规则直接进 `partial`。
#: 两种含义仍然不同，所以必须连着 `evidence_mode` 一起看，`partial` 的文案不许假装解释了是哪一种。
PARTIAL_STRENGTH_THRESHOLD = 0.3

EVIDENCE_MODE_AI = "ai_scored"
EVIDENCE_MODE_KEYWORD = "keyword_fallback"

#: §7.4 的三档。0.2 而不是 0：填了偏好但不相干本身是有效信息（说明这个人知道自己要什么，
#: 而这个岗位不是），归零会让「填偏好」变成纯亏。
PREFERENCE_EXACT = 1.0
PREFERENCE_PARTIAL = 0.6
PREFERENCE_UNRELATED = 0.2
PREFERENCE_TOKEN_RATIO = 0.5

#: §7.5 的四档剩余投递时间（天）。
REMAINING_TIME_NEAR_DAYS = 30
REMAINING_TIME_URGENT_DAYS = 7
REMAINING_TIME_URGENT = 0.4
REMAINING_TIME_NEAR = 0.7
REMAINING_TIME_OPEN = 1.0


@dataclass(frozen=True)
class ExperienceText:
    """一条经历里**参与证据定位**的三个字段。

    刻意不含 `start_date`/`end_date`/`school`/`full_name`：§6 的「不发清单」要写成测试，
    而输入类型里根本没有这些字段，比在注释里叮嘱「别发」可靠得多。
    """

    id: str
    title: str
    description: str
    responsibility: str

    def fields(self) -> tuple[tuple[str, str], ...]:
        """片段检索顺序：`title` → `description` → `responsibility`。

        顺序是契约的一部分（`covered[].evidence` 的排列与被 `MAX_SNIPPETS_PER_SKILL`
        截断的是哪几条都依赖它），所以定在这里，不让调用方各排一次。
        """
        return (
            ("title", self.title),
            ("description", self.description),
            ("responsibility", self.responsibility),
        )


@dataclass(frozen=True)
class RequiredSkill:
    """岗位的一条要求技能。`type` 是 `hard`/`soft`/`domain` 或 `None`（AI 的分级不可信）。"""

    name: str
    type: str | None = None


@dataclass(frozen=True)
class MatchFacts:
    """一次匹配需要的全部事实，全是值，不是 ORM 对象。"""

    job_title: str
    required_skills: Sequence[RequiredSkill]
    student_skills: Sequence[str]
    experiences: Sequence[ExperienceText]
    target_role: str | None
    deadline: date | None
    today: date


@dataclass(frozen=True)
class Candidate:
    """交给模型打分的候选对：一个 (技能, 片段)。

    `fragment_id` 是我们自己编号的，模型只能引用它——§6 第 3 点那条
    「引用了我们没给过的编号就丢弃」能成立，前提就是编号由我们发。
    """

    fragment_id: int
    skill: str
    experience_id: str
    field: str
    snippet: str


@dataclass(frozen=True)
class EvidenceBundle:
    """确定性层的产物：候选片段 + 每个命中技能命中了几条经历 + 被窗口切过的片段数。"""

    candidates: tuple[Candidate, ...]
    experience_hits: Mapping[str, int]
    fragments_truncated: int

    @property
    def matched_skills(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for candidate in self.candidates:
            seen.setdefault(candidate.skill, None)
        return tuple(seen)


@dataclass(frozen=True)
class ModelOutcome:
    """§8 两个分区核的结果。

    **候选侧**（以我们发出去的候选对为单位，恒精确闭合）：
    `candidate_pairs == verified_pairs + dropped_missing_output`
    **条目侧**（以模型返回的条目为单位，也恒精确闭合）：
    `len(raw_items) == verified_pairs + dropped_unreferenced + dropped_unknown_skill + dropped_unusable_strength`

    两个等式的单位不同，所以候选侧闭合**不要求**条目侧的 junk 数为 0——
    模型多编几条不破坏第一个等式。原稿写的「四桶互斥且穷尽 = candidate_pairs」把两个单位混了
    一个式子，实测一跑就红（模型返回一条编号 99 的假条目时，等式两边同时多出 1）。
    """

    strengths: Mapping[int, float]
    verified_pairs: int
    dropped_unreferenced: int
    dropped_unknown_skill: int
    dropped_unusable_strength: int
    dropped_missing_output: int
    clamped_strength: int


@dataclass(frozen=True)
class AiScoring:
    """模型这一路的全部输入：没参与时为 `None`。"""

    model: str
    prompt_version: str
    outcome: ModelOutcome


@dataclass(frozen=True)
class Evidence:
    experience_id: str
    field: str
    snippet: str
    strength: float


@dataclass(frozen=True)
class Covered:
    skill: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Partial:
    skill: str
    reason: str


@dataclass(frozen=True)
class Gap:
    skill: str
    action: str
    verify: str


@dataclass(frozen=True)
class Term:
    name: str
    weight: float
    #: `computed=False` 时是 `None`：「没算出来」与「算出来是 0」是两件事。
    value: float | None
    computed: bool
    note: str | None = None


@dataclass(frozen=True)
class MatchMeta:
    model: str | None
    prompt_version: str | None
    evidence_mode: str
    degrade_reason: str | None
    candidate_pairs: int
    verified_pairs: int
    dropped_unreferenced: int
    dropped_unknown_skill: int
    dropped_unusable_strength: int
    dropped_missing_output: int
    clamped_strength: int
    fragments_truncated: int
    weight_sum_used: float
    no_required_skills: bool


@dataclass(frozen=True)
class ScoredMatch:
    score: float
    weights_version: str
    terms: tuple[Term, ...]
    covered: tuple[Covered, ...]
    partial: tuple[Partial, ...]
    gaps: tuple[Gap, ...]
    meta: MatchMeta


def fold_with_positions(text: str) -> tuple[str, list[int]]:
    """逐字符 NFKC + casefold，并记住「折叠后第 i 个字符来自原文第几个字符」。

    为什么要位置表：命中判定要在折叠空间里做（大小写、全半角都算同一个技能），
    而片段必须是**原文**的连续子串。两边要在同一个索引上会合。

    逐字符归一与整串归一仅在一类输入上有差别：谚文字母（Jamo）需要整串合成音节。
    那种输入下本函数会**找不到**命中而不是找错——方向是保守的：
    宁可少给一条证据，也不给一条假证据。
    """
    chars: list[str] = []
    positions: list[int] = []
    for index, ch in enumerate(text):
        for produced in unicodedata.normalize("NFKC", ch).casefold():
            chars.append(produced)
            positions.append(index)
    return "".join(chars), positions


#: 命中区间两侧不许紧贴的字符（折叠空间）。字母与数字挡掉 `Java` 命中 `JavaScript`、
#: `Go` 命中 `Golang`；`+` / `#` 挡掉 `C` 命中 `C++` 与 `C#`；`-` 挡掉 `C` 命中 `C-Sharp`。
#: **不含** `.`（`Vue` 该命中 `Vue.js`）也不含中日韩（`C语言`、`Python编程` 都该命中）。
_BOUNDARY_BLOCKED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_+-#")

#: 紧跟在词干后面的版本号，用于并进命中区间：`Python 3.13`、`Python3`、`JDK-1.8`。
_TRAILING_VERSION = re.compile(r"[ \t._/-]*\d+(?:[./]\d+)*")


def locate_snippets(text: str, skill: str) -> tuple[tuple[str, bool], ...]:
    """在 `text` 里定位 `skill`，返回 `(原文连续片段, 是否被窗口切过端)`。

    三步，缺一不可（老板 2026-11-08 实测出的两个坑）：

    1. **按词干找**：岗位要求 `Python3` 时，正文里的写法是 `Python 3.13`，
       拿整串 `python3` 去子串搜会得到 0 处命中——覆盖判据修好了也仍然「已具备但零片段」，
       于是分数走的是证据 0 分那一支。所以搜索词是 `skill_stem` 剥完版本号的那一截。
    2. **把版本号并进命中区间，再拿区间原文复核一次判据**：要求 `Python3` 时正文写
       `Python2` 不算证据（两侧都带版本号、主号不同 → `skill_forms_match` 判否）。
       这一步同时取代了旧版那句「片段里还得含技能键」的自检。
    3. **词边界检查**（见 `_BOUNDARY_BLOCKED`）：没有它，`C` 会在 `C++` 里被找到、
       `Java` 会在 `JavaScript` 里被找到，而这两条正是「不同技能」的教科书反例。

    方向仍然是保守的：宁可少给一条证据，也不给一条假证据（谚文整串合成那类边角
    在这里表现为「找不到」而不是「找错」）。
    """
    if not text or not skill:
        return ()
    stem, _ = skill_stem(skill)
    if not stem:
        return ()
    folded_text, positions = fold_with_positions(text)
    found: list[tuple[str, bool]] = []
    at = folded_text.find(stem)
    while at >= 0:
        end = at + len(stem)
        tail = _TRAILING_VERSION.match(folded_text, end)
        if tail is not None:
            end = tail.end()
        before = folded_text[at - 1] if at > 0 else ""
        after = folded_text[end] if end < len(folded_text) else ""
        if before not in _BOUNDARY_BLOCKED and after not in _BOUNDARY_BLOCKED:
            start = positions[at]
            stop = positions[end - 1] + 1
            surface = text[start:stop]
            if skill_forms_match(skill, surface):
                window_start = max(0, start - MATCH_SNIPPET_RADIUS)
                window_end = min(len(text), stop + MATCH_SNIPPET_RADIUS)
                found.append(
                    (text[window_start:window_end], window_start > 0 or window_end < len(text))
                )
        at = folded_text.find(stem, at + len(stem))
    return tuple(found)


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF  # 平假名/片假名
    )


def tokens(text: str) -> frozenset[str]:
    """词面切分：连续的拉丁字母/数字算一个 token，中日韩按**二元组**切。

    不照 ADR-013 §7.3 原稿那样「中文整串当一个 token」，因为那条被实测否定了：
    dev 库 5 个已发布岗位标题 × 8 条经历标题，整串口径下重合数是 **0/40**。
    恒为 0 的项和恒为 3 的 `self_rating` 是同一种病（§1.2），只是这次它进了公式。
    二元组至少让「大模型算法实习生」与「大模型算法」这类真实重合能得分。
    """
    folded = skill_name_key(text)
    out: set[str] = set()
    run: list[str] = []
    for ch in folded:
        if ch.isalnum() and not _is_cjk(ch):
            run.append(ch)
            continue
        if run:
            out.add("".join(run))
            run = []
    if run:
        out.add("".join(run))
    out |= {
        folded[i : i + 2]
        for i in range(len(folded) - 1)
        if _is_cjk(folded[i]) and _is_cjk(folded[i + 1])
    }
    return frozenset(item for item in out if item)


def overlap_ratio(left: str, right: str) -> float:
    """两个字符串的词面重合比例，分母取**较短**的一侧。

    分母取较短侧是有意的：目标偏好通常比岗位标题短（「做大模型算法」 vs
    「大模型算法实习生（2026 届，北上深）」），用长的一侧做分母会系统性压低偏好项。
    """
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def unique_required(skills: Sequence[RequiredSkill]) -> tuple[RequiredSkill, ...]:
    """按**技能键**去重的岗位要求，保留第一次出现的写法。

    分子与分母都必须走这里（§7.1）：岗位里同时写「Python」和「python」是同一个要求，
    按字面去重的话分母会被抬到 2，而学生无论怎么填都只能拿到 0.5——
    这类「键与字面不一致」的偏差正是 §1.3 那一屏自相矛盾的成因。
    """
    seen: dict[str, RequiredSkill] = {}
    for item in skills:
        seen.setdefault(skill_name_key(item.name), item)
    return tuple(item for key, item in seen.items() if key)


def matched_required_skills(
    required: Sequence[RequiredSkill], student: Sequence[str]
) -> tuple[RequiredSkill, ...]:
    """岗位要求里学生技能命中的那些（§7.1 的分子集合）。入参应已按 `unique_required` 去重。

    判据是 `skill_forms_match`（全等 + 受控版本号剥离），不是裸的键全等：
    学生填 `Python3`、岗位要求 `Python` 今天会被判「还缺」，而这条差距纯粹是写法。
    为什么不用子串/前缀推断，见 §7.1 与 `skill_forms_match` 的 docstring。
    """
    filled = [name for name in student if name.strip()]
    return tuple(
        item
        for item in required
        if skill_name_key(item.name)
        and any(skill_forms_match(item.name, candidate) for candidate in filled)
    )


def build_evidence(facts: MatchFacts, matched: Sequence[RequiredSkill]) -> EvidenceBundle:
    """为每个命中的岗位技能找出它在学生经历里的位置。"""
    candidates: list[Candidate] = []
    truncated = 0
    experience_hits: dict[str, int] = {}
    for item in matched:
        hit_experiences: set[str] = set()
        for experience in facts.experiences:
            hit_in_this_experience = False
            for field_name, field_text in experience.fields():
                snippets = locate_snippets(field_text, item.name)
                if not snippets:
                    continue
                hit_in_this_experience = True
                snippet, was_cut = snippets[0]  # 一条 (技能, 经历, 字段) 最多一个窗口
                if was_cut:
                    truncated += 1
                candidates.append(
                    Candidate(
                        fragment_id=len(candidates),
                        skill=item.name,
                        experience_id=experience.id,
                        field=field_name,
                        snippet=snippet,
                    )
                )
            if hit_in_this_experience:
                hit_experiences.add(experience.id)
        experience_hits[item.name] = len(hit_experiences)
    return EvidenceBundle(
        candidates=tuple(candidates),
        experience_hits=experience_hits,
        fragments_truncated=truncated,
    )


def apply_model_scores(
    candidates: Sequence[Candidate], raw_items: Sequence[Mapping[str, object]]
) -> ModelOutcome:
    """把模型返回的条目核到候选对上，按 §8 的固定次序分派，并给出两个分区的计数。

    次序本身就是契约：先技能名、再片段编号、再评分。换一种次序，两个 `dropped_*`
    的值会变而总分不变——那种「只有对账数字漂移」的差异最难查，所以写死在这。
    """
    known_skills = {candidate.skill for candidate in candidates}
    by_id = {candidate.fragment_id: candidate for candidate in candidates}
    strengths: dict[int, float] = {}
    dropped_unknown_skill = 0
    dropped_unreferenced = 0
    dropped_unusable_strength = 0
    clamped = 0
    for item in raw_items:
        skill = item.get("skill")
        if not isinstance(skill, str) or skill not in known_skills:
            dropped_unknown_skill += 1
            continue
        raw_id = item.get("fragment_id")
        candidate = (
            by_id.get(raw_id) if isinstance(raw_id, int) and not isinstance(raw_id, bool) else None
        )
        if candidate is None or candidate.skill != skill:
            dropped_unreferenced += 1
            continue
        raw_strength = item.get("strength")
        if not isinstance(raw_strength, (int, float)) or isinstance(raw_strength, bool):
            dropped_unusable_strength += 1
            continue
        value = float(raw_strength)
        if value < 0.0 or value > 1.0:
            clamped += 1
            value = min(1.0, max(0.0, value))
        strengths[candidate.fragment_id] = value
    dropped_missing_output = sum(
        1 for candidate in candidates if candidate.fragment_id not in strengths
    )
    return ModelOutcome(
        strengths=strengths,
        verified_pairs=len(strengths),
        dropped_unreferenced=dropped_unreferenced,
        dropped_unknown_skill=dropped_unknown_skill,
        dropped_unusable_strength=dropped_unusable_strength,
        dropped_missing_output=dropped_missing_output,
        clamped_strength=clamped,
    )


def keyword_strength(experience_hits: int) -> float:
    """关键词兜底强度：命中该技能的**经历条数**，`min(n, 3) / 3`（§7.2）。"""
    return min(experience_hits, KEYWORD_FALLBACK_HITS_CAP) / KEYWORD_FALLBACK_HITS_CAP


def _skill_strengths(
    evidence: EvidenceBundle, ai: AiScoring | None
) -> tuple[dict[str, list[tuple[Candidate, float]]], str]:
    """每个命中技能的片段与强度。`ai` 为 `None` 时整项走关键词代理值。"""
    grouped: dict[str, list[Candidate]] = {}
    for candidate in evidence.candidates:
        grouped.setdefault(candidate.skill, []).append(candidate)
    scored: dict[str, list[tuple[Candidate, float]]] = {}
    if ai is None:
        for skill, candidates in grouped.items():
            strength = keyword_strength(evidence.experience_hits.get(skill, 0))
            scored[skill] = [(candidate, strength) for candidate in candidates]
        return scored, EVIDENCE_MODE_KEYWORD
    for skill, candidates in grouped.items():
        scored[skill] = [
            (candidate, ai.outcome.strengths[candidate.fragment_id])
            for candidate in candidates
            if candidate.fragment_id in ai.outcome.strengths
        ]
    return scored, EVIDENCE_MODE_AI


def remaining_time_value(
    deadline: date | None, today: date
) -> tuple[float | None, bool, str | None]:
    """§7.5 的「剩余投递时间」。返回 `(值, 是否参与加权, note)`。

    `deadline IS NULL` → **不参与加权**（ADR-013 §7.5 的更正：原稿记 0.5 进分母，
    与 §5.1「算不出来的项不进分母」自相矛盾；实测真实可点的 5 个种子里 1 个走这一支）。
    """
    if deadline is None:
        return None, False, "no_deadline"
    if is_expired(deadline, today):
        return 0.0, True, "expired"
    days = (deadline - today).days
    if days <= REMAINING_TIME_URGENT_DAYS:
        return REMAINING_TIME_URGENT, True, "urgent"
    if days <= REMAINING_TIME_NEAR_DAYS:
        return REMAINING_TIME_NEAR, True, "near"
    return REMAINING_TIME_OPEN, True, "open"


def normalize_score(terms: Sequence[Term]) -> tuple[float, float]:
    """§5.1 归一：只把算得出来的项放进分母。返回 `(总分, 实际用到的权重之和)`。"""
    usable = [
        (term.weight, term.value) for term in terms if term.computed and term.value is not None
    ]
    used = sum(weight for weight, _ in usable)
    if used == 0:
        return 0.0, 0.0
    total = sum(weight * value for weight, value in usable)
    return round(total / used, 3), round(used, 4)


def score_match(
    facts: MatchFacts,
    evidence: EvidenceBundle | None = None,
    ai: AiScoring | None = None,
    degrade_reason: str | None = None,
) -> ScoredMatch:
    """把五项分项装配成总分与三档清单（§3 的响应结构，纯函数版）。

    `evidence` 省略时在这里现算——编排层会先算好交给模型打分再传回来，
    而单测与「不走 AI 的调用方」不需要多写一遍。

    `ai is None` 就是关键词模式；调用方必须给 `degrade_reason` 说明为什么没有 AI（§6.5）。
    """
    required = unique_required(facts.required_skills)
    matched = matched_required_skills(required, facts.student_skills)
    if evidence is None:
        evidence = build_evidence(facts, matched)
    matched_names = {item.name for item in matched}

    if not required:
        coverage = Term("coverage", MATCH_WEIGHTS["coverage"], None, False, "no_required_skills")
        no_required_skills = True
    elif not matched_names:
        coverage = Term("coverage", MATCH_WEIGHTS["coverage"], 0.0, True, None)
        no_required_skills = False
    else:
        coverage = Term(
            "coverage",
            MATCH_WEIGHTS["coverage"],
            round(len(matched_names) / len(required), 3),
            True,
            None,
        )
        no_required_skills = False

    strengths_by_skill, mode = _skill_strengths(evidence, ai)
    if not matched_names:
        evidence_term = Term(
            "evidence", MATCH_WEIGHTS["evidence"], None, False, "no_matched_skills"
        )
    elif not strengths_by_skill:
        # 技能名命中了，但一条经历里都找不到出处：这是真话，不是「算不出来」。
        evidence_term = Term(
            "evidence",
            MATCH_WEIGHTS["evidence"],
            0.0,
            True,
            "matched_without_evidence",
        )
    else:
        means = [
            sum(strength for _, strength in items) / len(items) if items else 0.0
            for items in strengths_by_skill.values()
        ]
        evidence_term = Term(
            "evidence",
            MATCH_WEIGHTS["evidence"],
            round(sum(means) / len(means), 3),
            True,
            mode,
        )

    # §7.3 经验相关度：v1 恒不参与。理由是被测出来的，不是猜的——见 tokens() 的 0/40。
    # 保留这一项而不是删掉，是为了让「PRD 的五项里有一项我们目前测不了」这件事在响应里可见。
    relevance = Term("relevance", MATCH_WEIGHTS["relevance"], None, False, "not_measurable_v1")

    if facts.target_role is None:
        preference = Term("preference", MATCH_WEIGHTS["preference"], None, False, "no_target_role")
    else:
        role = facts.target_role
        folded_role, folded_title = skill_name_key(role), skill_name_key(facts.job_title)
        if folded_role and (folded_role in folded_title or folded_title in folded_role):
            value, note = PREFERENCE_EXACT, "contains"
        elif overlap_ratio(role, facts.job_title) >= PREFERENCE_TOKEN_RATIO:
            value, note = PREFERENCE_PARTIAL, "token_overlap"
        else:
            value, note = PREFERENCE_UNRELATED, "unrelated"
        preference = Term("preference", MATCH_WEIGHTS["preference"], value, True, note)

    remaining_value, remaining_computed, remaining_note = remaining_time_value(
        facts.deadline, facts.today
    )
    remaining = Term(
        "remaining_time",
        MATCH_WEIGHTS["remaining_time"],
        remaining_value,
        remaining_computed,
        remaining_note,
    )

    terms = (coverage, evidence_term, relevance, preference, remaining)
    score, weight_sum_used = normalize_score(terms)

    covered: list[Covered] = []
    partial: list[Partial] = []
    for skill, items in strengths_by_skill.items():
        if not items:
            partial.append(Partial(skill=skill, reason="技能名命中，但经历里找不到出处"))
            continue
        mean_strength = sum(strength for _, strength in items) / len(items)
        shown = sorted(items, key=lambda pair: pair[0].fragment_id)[:MAX_SNIPPETS_PER_SKILL]
        evidence_out = tuple(
            Evidence(
                experience_id=candidate.experience_id,
                field=candidate.field,
                snippet=candidate.snippet,
                strength=round(strength, 3),
            )
            for candidate, strength in shown
        )
        if mean_strength < PARTIAL_STRENGTH_THRESHOLD:
            partial.append(Partial(skill=skill, reason=f"只在 {len(items)} 处经历里出现，证据偏薄"))
        else:
            covered.append(Covered(skill=skill, evidence=evidence_out))

    # 命中了技能名、但一条经历里都找不到出处的条目必须进 `partial`，不能三个清单里都没有它。
    # 这不是补漏：`strengths_by_skill` 是从**候选对**分组的，零候选的命中技能天生不在里面，
    # 于是它会从三档展示里彻底消失——用户看不到「你会 Docker 但没写过」，
    # 而 §3 要求「至少三条可验证证据或明确缺口」，这种消失两头都不算。
    # （这条是被集成测试逼出来的：`test_job_match.py` 里 Docker 命中却零证据，当时三档全空。）
    for skill in sorted(matched_names - set(strengths_by_skill)):
        partial.append(Partial(skill=skill, reason="技能名命中，但经历里找不到出处"))

    gaps = tuple(_gap_for(item) for item in required if item.name not in matched_names)

    outcome = ai.outcome if ai is not None else None
    meta = MatchMeta(
        model=ai.model if ai is not None else None,
        prompt_version=ai.prompt_version if ai is not None else None,
        evidence_mode=mode,
        degrade_reason=None if ai is not None else degrade_reason,
        candidate_pairs=len(evidence.candidates),
        verified_pairs=outcome.verified_pairs if outcome else len(evidence.candidates),
        dropped_unreferenced=outcome.dropped_unreferenced if outcome else 0,
        dropped_unknown_skill=outcome.dropped_unknown_skill if outcome else 0,
        dropped_unusable_strength=outcome.dropped_unusable_strength if outcome else 0,
        dropped_missing_output=outcome.dropped_missing_output if outcome else 0,
        clamped_strength=outcome.clamped_strength if outcome else 0,
        fragments_truncated=evidence.fragments_truncated,
        weight_sum_used=weight_sum_used,
        no_required_skills=no_required_skills,
    )
    return ScoredMatch(
        score=score,
        weights_version=MATCH_WEIGHTS_VERSION,
        terms=terms,
        covered=tuple(covered),
        partial=tuple(partial),
        gaps=gaps,
        meta=meta,
    )


def _gap_for(skill: RequiredSkill) -> Gap:
    """缺口卡的两行文案，由模板给出、不经模型。

    `action`/`verify` 是给用户看的建议，模型写得好看不等于写得对，而这类建议错了用户
    没法自检。`type` 缺失（AI 分级不可信那条线）走通用模板。
    """
    if skill.type == "soft":
        return Gap(
            skill=skill.name,
            action=f"把「{skill.name}」放进具体情境：哪一次协作、你做了什么、对方因此改变了什么。",
            verify="经历里能定位到具体的人和事，而不是只有形容词。",
        )
    if skill.type == "domain":
        return Gap(
            skill=skill.name,
            action=f"在一段真实经历里点明「{skill.name}」这个领域：做了什么业务、面向谁、有什么约束。",
            verify="能说出该领域的一个具体取舍，而不是复述名词。",
        )
    return Gap(
        skill=skill.name,
        action=f"补一条真的用 {skill.name} 做过的项目经历：做了什么 → 怎么做的 → 结果如何。",
        verify="按你写的过程细节追问，能答出为什么这样选，就算验证通过。",
    )
