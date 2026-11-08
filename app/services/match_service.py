"""人岗匹配的编排层（ADR-013 §10 第 7c 步）。

分工：读库与调模型在这里，**分数怎么算是纯函数**（`match_scoring`），HTTP 形状在
`app/schemas/match.py`。三层各自能单独测，坏了才知道是谁坏的。

两条结构约束，都是复核时定下的，不是风格偏好：

1. **一个 session、两个仓储**（§13 第 8 条）。不复用 `get_job_service()` + `get_profile_service()`
   拼两个 session：那等于同一次请求占两条连接、读到的还是两个不同快照，
   而「岗位与档案是同一时刻的状态」是分数能被复现的前提。
2. **AI 失败在这层就地吞掉**，翻译成 `degrade_reason` 后继续给分（§6.5）。
   因此本端点不会因为 AI 失败返回 400/401/502，`app/main.py` 的三个 AI 处理器在这里
   一次都不该被命中——这条由 `tests/test_job_match.py` 反向钉住。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.core.skill_names import skill_name_key
from app.integrations.copy_writer import DEEPSEEK_MODEL
from app.integrations.evidence_scorer import (
    EVIDENCE_PROMPT_VERSION,
    EvidenceScoreAuthError,
    EvidenceScoreBadOutputError,
    EvidenceScoreEmptyContentError,
    EvidenceScoreError,
    EvidenceScorer,
    EvidenceScoreTimeoutError,
    EvidenceScoreTruncatedError,
    EvidenceScoreUnavailableError,
    ScoringFragment,
)
from app.repositories.job_repository import Job, JobRepository
from app.repositories.student_profile_repository import StudentProfileRepository
from app.schemas.match import (
    MatchCovered,
    MatchEvidence,
    MatchGap,
    MatchMeta,
    MatchPartial,
    MatchResult,
    MatchTerm,
    RequirementCoverageRow,
    RequirementMatchMeta,
    RequirementMatchResponse,
    RequirementStatus,
)
from app.services.job_service import JobNotFoundError
from app.services.match_scoring import (
    AiScoring,
    Candidate,
    ExperienceText,
    MatchFacts,
    RequiredSkill,
    ScoredMatch,
    apply_model_scores,
    build_evidence,
    matched_required_skills,
    matched_with_forms,
    score_match,
    unique_required,
)

logger = logging.getLogger(__name__)


class _AiAttempt:
    """一次 AI 尝试的结果。`scores is None` 就是关键词模式。"""

    def __init__(
        self,
        *,
        scores: AiScoring | None = None,
        degrade_reason: str | None = None,
        finish_reason: str | None = None,
    ) -> None:
        self.scores = scores
        self.degrade_reason = degrade_reason
        self.finish_reason = finish_reason

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


@dataclass(frozen=True)
class _MatchInputs:
    """`_gather()` 读出来的两侧事实：岗位行、用了哪份档案、以及装配好的 `MatchFacts`。

    之所以带上 `job`：两个公开方法都要回 `job_id`，而档案两格已经摊平成 `profile_id` /
    `profile_title`（`profile` 可能为 `None`，摊平后调用方不必各自判一次空）。
    """

    job: Job
    profile_id: str | None
    profile_title: str | None
    facts: MatchFacts


class MatchService:
    """一条岗位 × 当前用户的默认档案 = 一次匹配（或一次不算分的要求覆盖查询）。"""

    def __init__(
        self,
        *,
        job_repository: JobRepository,
        profile_repository: StudentProfileRepository,
        scorer: EvidenceScorer | None = None,
        today: date | None = None,
    ) -> None:
        self._jobs = job_repository
        self._profiles = profile_repository
        self._scorer = scorer
        #: 测试注入「今天」。生产路径恒为 `None` → 用应用时钟，与 `JobService` 同一口径。
        self._today_override = today

    def match(self, user_id: str, job_id: str) -> MatchResult:
        """算一次匹配。岗位不可读 → `JobNotFoundError`（404，不是 403）。"""
        inputs = self._gather(user_id, job_id)
        facts = inputs.facts
        evidence = build_evidence(
            facts,
            matched_required_skills(unique_required(facts.required_skills), facts.student_skills),
        )
        attempt = self._score_with_ai(evidence.candidates)
        scored = score_match(facts, evidence, attempt.scores, attempt.degrade_reason)
        self._log(user_id, inputs.job.id, scored, attempt)
        return self._to_result(
            profile_id=inputs.profile_id,
            profile_title=inputs.profile_title,
            job_id=inputs.job.id,
            scored=scored,
            finish_reason=attempt.finish_reason,
        )

    def requirement_match(self, user_id: str, job_id: str) -> RequirementMatchResponse:
        """第 3c 条：这条岗位的每条要求技能，当前用户处在什么状态（给「你已具备」角标用）。

        与 `match()` 三处同源，一处都不另起炉灶——这正是老板 2026-11-08「禁止两套判定」那条：

        1. **取数**同一个 `_gather`：同一 session、同一默认档案语义、同一 `get_readable` 可见性；
        2. **分区**同一个 `score_match`：三档（`covered` / `partial` / `gaps`）由它装配，
           本方法只是把它们压回「一条要求 → 一个状态」，不重新判一遍；
        3. **写法命中**同一个 `matched_with_forms`：`matched_form` 就是打分用的那次循环的产物。

        这里**不调 AI**，所以拿到的是关键词模式那份分区：`covered` 与 `partial` 的分界
        可能与匹配卡不同（卡上的分界会被 AI 的强度判断挪动），但**两档的并集恒等于「写法命中」**——
        `match_scoring.py` 在装配三档时把「命中却拿不到强度」的技能一律补进 `partial`，
        所以没有任何一支 AI 判等能让一条命中技能从并集里消失。角标回答的恰好只有「命中没有」这一件，
        老板判据 (b) 断言的也是两边的**并集**相等；这条不只在关键词模式下测过
        （`test_badge_union_still_equals_match_union_in_ai_mode` 用 `strength=0.1` 真的把边界挪开了）。

        为什么值得单开一个端点而不是让前端自己比：前端那套 `trim + NFKC + toLowerCase`
        认不出 `Python3` 与 `Python` 是同一个技能（§1.3 那一屏自相矛盾的第二半），
        而它本来也不该再养一套判据。
        """
        inputs = self._gather(user_id, job_id)
        facts = inputs.facts
        scored = score_match(facts)
        covered_keys = {skill_name_key(item.skill) for item in scored.covered}
        partial_keys = {skill_name_key(item.skill) for item in scored.partial}
        forms = {
            skill_name_key(required.name): matched_form
            for required, matched_form in matched_with_forms(
                unique_required(facts.required_skills), facts.student_skills
            )
        }

        def status_for(key: str) -> RequirementStatus:
            if key in covered_keys:
                return "covered"
            if key in partial_keys:
                return "partial"
            return "missing"

        return RequirementMatchResponse(
            job_id=inputs.job.id,
            profile_id=inputs.profile_id,
            profile_title=inputs.profile_title,
            #: 逐条对着岗位自己的行说，顺序与条数都跟 `GET /jobs/{job_id}` 的 `skills` 一致
            #: （判据 (a) 的基准域 U 就是这么定的）。重复写法在这里不会丢行：查表按技能键。
            coverage=[
                RequirementCoverageRow(
                    name=item.name,
                    status=status_for(skill_name_key(item.name)),
                    matched_form=forms.get(skill_name_key(item.name)),
                )
                for item in facts.required_skills
            ],
            meta=RequirementMatchMeta(matcher_version=scored.meta.matcher_version),
        )

    # ------------------------------------------------------------------ 取数

    def _gather(self, user_id: str, job_id: str) -> _MatchInputs:
        """读「这条岗位 + 当前用户的默认档案」，装配成一份事实。两个公开方法共用。

        为什么必须是同一个方法而不是各读各的：分数与角标要能在同一屏上对账，
        前提是两侧读的是**同一时刻的同一份数据**。分两遍读，中间用户改了简历，
        屏幕上就是半个新半个旧——那种不一致没人能复现，也没法测。
        """
        job = self._jobs.get_readable(job_id, user_id)
        if job is None:
            raise JobNotFoundError(job_id)

        profile = self._profiles.get_by_user_id(user_id)
        required = [
            RequiredSkill(name=skill.name, type=skill.type)
            for skill in self._jobs.list_skills(job.id)
        ]
        student_skills: list[str] = []
        experiences: list[ExperienceText] = []
        target_role: str | None = None
        if profile is not None:
            target_role = profile.target_role
            student_skills = [skill.name for skill in self._profiles.list_skills(profile.id)]
            experiences = [
                ExperienceText(
                    id=experience.id,
                    title=experience.title,
                    description=experience.description,
                    responsibility=experience.responsibility,
                )
                for experience in self._profiles.list_experiences(profile.id)
            ]

        return _MatchInputs(
            job=job,
            profile_id=profile.id if profile is not None else None,
            profile_title=profile.title if profile is not None else None,
            facts=MatchFacts(
                job_title=job.title,
                required_skills=required,
                student_skills=student_skills,
                experiences=experiences,
                target_role=target_role,
                deadline=job.deadline,
                today=self._today(),
            ),
        )

    # ------------------------------------------------------------------ AI

    def _score_with_ai(self, candidates: Sequence[Candidate]) -> _AiAttempt:
        """把候选片段交给模型，失败时**在这里**换成退化标记。

        异常到 `degrade_reason` 的映射逐条对应 §6.5 的五种值。次序有讲究：
        `EvidenceScoreTimeoutError` 是 `…UnavailableError` 的子类，必须先判子类，
        否则超时会被上面的分支吃掉、`degrade_reason` 恒写成 `upstream`。
        """
        if not candidates:
            # 没有候选对就不外呼：省一次真实计费，也不给「ai_scored 但 candidate_pairs=0」留可能。
            return _AiAttempt()
        if self._scorer is None:
            return _AiAttempt(degrade_reason="key_missing")

        fragments = [
            ScoringFragment(
                fragment_id=candidate.fragment_id,
                skill=candidate.skill,
                text=candidate.snippet,
            )
            for candidate in candidates
        ]
        try:
            scores = self._scorer.score(fragments)
        except EvidenceScoreAuthError:
            return _AiAttempt(degrade_reason="key_invalid")
        except EvidenceScoreTruncatedError as error:
            return _AiAttempt(degrade_reason="bad_output", finish_reason=error.finish_reason)
        except (EvidenceScoreEmptyContentError, EvidenceScoreBadOutputError):
            # 同样必须排在 Unavailable 之前：这两个都是它的子类。
            # 次序写反的后果很隐蔽——响应仍是 200、仍给分，只有 `degrade_reason`
            # 从「模型没守格式」变成「上游不可用」，而前端按它分句，于是给用户一句假原因。
            return _AiAttempt(degrade_reason="bad_output")
        except EvidenceScoreTimeoutError:
            # 必须排在 Unavailable 之前：它是 Unavailable 的子类，反过来写超时就被上游吃掉。
            return _AiAttempt(degrade_reason="timeout")
        except EvidenceScoreUnavailableError:
            return _AiAttempt(degrade_reason="upstream")
        except EvidenceScoreError:
            return _AiAttempt(degrade_reason="bad_output")

        outcome = apply_model_scores(
            candidates,
            [
                {
                    "fragment_id": item.fragment_id,
                    "skill": item.skill,
                    "strength": item.strength,
                }
                for item in scores.items
            ],
        )
        return _AiAttempt(
            scores=AiScoring(
                model=DEEPSEEK_MODEL,
                prompt_version=EVIDENCE_PROMPT_VERSION,
                outcome=outcome,
            ),
            finish_reason=scores.finish_reason,
        )

    # ------------------------------------------------------------- 装配与日志

    def _today(self) -> date:
        return self._today_override if self._today_override is not None else date.today()

    def _log(self, user_id: str, job_id: str, scored: ScoredMatch, attempt: _AiAttempt) -> None:
        """只记计数与标识：没有 Key、没有片段正文、没有经历原文（AGENTS.md §4.4 + ADR-009）。"""
        logger.info(
            "匹配完成（user=%s job=%s score=%s mode=%s degrade=%s pairs=%s/%s weight_sum=%s finish=%s）",
            user_id,
            job_id,
            scored.score,
            scored.meta.evidence_mode,
            scored.meta.degrade_reason,
            scored.meta.verified_pairs,
            scored.meta.candidate_pairs,
            scored.meta.weight_sum_used,
            attempt.finish_reason,
        )

    def _to_result(
        self,
        *,
        profile_id: str | None,
        profile_title: str | None,
        job_id: str,
        scored: ScoredMatch,
        finish_reason: str | None,
    ) -> MatchResult:
        meta = scored.meta
        return MatchResult(
            profile_id=profile_id,
            profile_title=profile_title,
            job_id=job_id,
            score=scored.score,
            weights_version=scored.weights_version,
            terms=[
                MatchTerm(
                    name=term.name,
                    weight=term.weight,
                    value=term.value,
                    computed=term.computed,
                    note=term.note,
                )
                for term in scored.terms
            ],
            covered=[
                MatchCovered(
                    skill=covered.skill,
                    evidence=[
                        MatchEvidence(
                            experience_id=item.experience_id,
                            field=item.field,
                            snippet=item.snippet,
                            strength=item.strength,
                        )
                        for item in covered.evidence
                    ],
                )
                for covered in scored.covered
            ],
            partial=[MatchPartial(skill=item.skill, reason=item.reason) for item in scored.partial],
            gaps=[
                MatchGap(skill=item.skill, action=item.action, verify=item.verify)
                for item in scored.gaps
            ],
            meta=MatchMeta(
                model=meta.model,
                prompt_version=meta.prompt_version,
                evidence_mode=meta.evidence_mode,  # type: ignore[arg-type]
                degrade_reason=meta.degrade_reason,  # type: ignore[arg-type]
                candidate_pairs=meta.candidate_pairs,
                verified_pairs=meta.verified_pairs,
                dropped_unreferenced=meta.dropped_unreferenced,
                dropped_unknown_skill=meta.dropped_unknown_skill,
                dropped_unusable_strength=meta.dropped_unusable_strength,
                dropped_missing_output=meta.dropped_missing_output,
                clamped_strength=meta.clamped_strength,
                fragments_truncated=meta.fragments_truncated,
                truncated=finish_reason == "length",
                finish_reason=finish_reason,
                weight_sum_used=meta.weight_sum_used,
                no_required_skills=meta.no_required_skills,
                matcher_version=meta.matcher_version,
                note=meta.note,
            ),
        )
