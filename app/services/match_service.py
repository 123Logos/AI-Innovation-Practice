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
from datetime import date

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
from app.repositories.job_repository import JobRepository
from app.repositories.student_profile_repository import StudentProfileRepository
from app.schemas.match import (
    MatchCovered,
    MatchEvidence,
    MatchGap,
    MatchMeta,
    MatchPartial,
    MatchResult,
    MatchTerm,
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


class MatchService:
    """一条岗位 × 当前用户的默认档案 = 一次匹配。"""

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

        facts = MatchFacts(
            job_title=job.title,
            required_skills=required,
            student_skills=student_skills,
            experiences=experiences,
            target_role=target_role,
            deadline=job.deadline,
            today=self._today(),
        )
        evidence = build_evidence(
            facts, matched_required_skills(unique_required(required), student_skills)
        )
        attempt = self._score_with_ai(evidence.candidates)
        scored = score_match(facts, evidence, attempt.scores, attempt.degrade_reason)
        self._log(user_id, job.id, scored, attempt)
        return self._to_result(
            profile_id=profile.id if profile is not None else None,
            profile_title=profile.title if profile is not None else None,
            job_id=job.id,
            scored=scored,
            finish_reason=attempt.finish_reason,
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
            ),
        )
