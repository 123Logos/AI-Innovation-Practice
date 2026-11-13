"""标准化题库测评与画像的编排层（FR-04 / B9，`ADR-028`）。

分工：读库、判分、写标签、调模型都在这里；**分数与三态的口径在
`app/core/assessment_rules.py`**（纯函数），HTTP 形状在 `app/schemas/assessment.py`，
路由在 `app/api/v1/assessments.py`。

三条结构约束：

1. **一个 session、两个仓储**：测评仓储与档案仓储在同一次 `session_scope()` 里装好。
   提交要同时写「会话 + 答案 + 标签」并改「技能来源」，分两次会话就会留下
   「标签写了、来源没升」或反过来的半截状态。
2. **判分完全确定性**：不调 AI、不看时间、不看题库的当前状态——用的是
   `session.question_ids`（这场会话**实际出过**的题）。同一份答案永远同样的分。
3. **AI 失败在本层就地吞掉**，翻译成 `source='fallback'` + 一段模板文案（`ADR-028` §2.5）。
   与 B11a 的区别只在「损失的是什么」：面试丢了内容，画像只丢措辞，
   所以这里必须**降级并继续**，而不是像润色那样 4xx。

`_score_session()` 是 `submit` 与 `summary` 共用的唯一判分入口：摘要里的
「已验证 / 待提升」必须与提交那一刻算出来的完全一致，两处各算一遍迟早会分叉。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.assessment_rules import (
    TAG_VERIFIED,
    SkillScore,
    build_summary_prompts,
    fallback_summary,
    score_percent,
    score_skill,
)
from app.core.datetime_display import utc_now
from app.integrations.evidence_scorer import (
    EvidenceScoreAuthError,
    EvidenceScoreBadOutputError,
    EvidenceScoreEmptyContentError,
    EvidenceScoreError,
    EvidenceScoreTimeoutError,
    EvidenceScoreTruncatedError,
    EvidenceScoreUnavailableError,
)
from app.integrations.profile_summarizer import ProfileSummarizer
from app.repositories.assessment_repository import (
    STATUS_IN_PROGRESS,
    STATUS_SUBMITTED,
    SUMMARY_SOURCE_AI,
    SUMMARY_SOURCE_FALLBACK,
    AssessmentAnswer,
    AssessmentQuestion,
    AssessmentRepository,
    AssessmentSession,
    ProfileTag,
)
from app.repositories.student_profile_repository import StudentProfile, StudentProfileRepository

logger = logging.getLogger(__name__)


class AssessmentNotFoundError(Exception):
    """会话或档案不存在 / 不属于当前用户；对外一律 404（不是 403）。"""


class AssessmentProfileRequiredError(Exception):
    """还没有可用的档案：测评要挂在某一份简历上（画像按档案聚合）。"""


class AssessmentBankEmptyError(Exception):
    """题库为空（没有启用中的题目）：这是部署数据问题，不是用户的错。"""


class AssessmentStateError(Exception):
    """会话已不在 `in_progress`：任何写入都是 409（`ADR-028` §2.2）。"""


class AssessmentAnswerInvalidError(Exception):
    """答案不合法：题目不在本次会话里，或选项 key 不属于该题。"""


@dataclass(frozen=True)
class SummaryResult:
    """`summarize()` 的结果。`note` 只描述**这一次请求**发生了什么，不落库。"""

    summary: str
    source: str
    note: str | None


#: AI 失败原因 → 降级说明。措辞必须让用户知道「事实还在，只是措辞没了」。
_DEGRADE_NOTES = {
    "key_missing": "未配置 AI 密钥，以下为根据测评结果自动整理的概述。",
    "key_invalid": "AI 密钥无效，以下为根据测评结果自动整理的概述。",
    "timeout": "AI 服务超时，以下为根据测评结果自动整理的概述。",
    "upstream": "AI 服务暂时不可用，以下为根据测评结果自动整理的概述。",
    "bad_output": "AI 返回内容异常，以下为根据测评结果自动整理的概述。",
}


@dataclass(frozen=True)
class _Scored:
    """一场会话的判分结果。`tags` 是**本次**会话产出的标签（按技能分组）。"""

    total: int
    correct: int
    score: int
    tags: list[SkillScore]


class AssessmentService:
    def __init__(
        self,
        *,
        repository: AssessmentRepository,
        profile_repository: StudentProfileRepository,
        summarizer: ProfileSummarizer | None = None,
    ) -> None:
        self._repository = repository
        self._profiles = profile_repository
        #: 没有 Key 时装配成 None，由 `summarize()` 翻译成 `key_missing` 降级；
        #: 绝不让「忘传 Key」变成 500（`ADR-028` §2.5）。
        self._summarizer = summarizer

    # ------------------------------------------------------------------ 题库

    def catalog(self) -> tuple[str, list[tuple[str, str, int]]]:
        """题库目录：`(bank_version, [(skill_key, skill_name, 题数)])`。

        **不含题干、不含答案**（`ADR-028` §2.7）：目录只回答「能测什么」。
        """
        version = self._repository.latest_bank_version()
        if version is None:
            return "", []
        counts: dict[str, tuple[str, int]] = {}
        for question in self._repository.list_questions(version):
            name, count = counts.get(question.skill_key, (question.skill_name, 0))
            counts[question.skill_key] = (name, count + 1)
        skills = [(key, name, count) for key, (name, count) in counts.items()]
        return version, skills

    # ------------------------------------------------------------------ 会话

    def start_session(
        self, user_id: str, profile_id: str | None
    ) -> tuple[AssessmentSession, list[AssessmentQuestion]]:
        """开一场测评：钉住题库版本与**这一场实际出过的题**。

        题目 id 落库（`question_ids`）而不是只记数量：题库随时可能改，
        只记数量的话判分时得按「当前题库」重新取题，同一份答案的分数就会取决于
        什么时候点的提交（`ADR-028` §2.2 的实现补记）。
        """
        profile = self._resolve_profile(user_id, profile_id)
        version, _ = self.catalog()
        if not version:
            raise AssessmentBankEmptyError("题库为空")
        questions = self._repository.list_questions(version)
        if not questions:
            raise AssessmentBankEmptyError("题库为空")
        session = self._repository.create_session(
            user_id=user_id,
            student_profile_id=profile.id,
            bank_version=version,
            question_ids=[question.id for question in questions],
        )
        self._repository.commit()
        return session, questions

    def get_session(
        self, user_id: str, session_id: str
    ) -> tuple[AssessmentSession, list[AssessmentQuestion], list[AssessmentAnswer]]:
        """会话详情：题目按**出题顺序**返回（`session.question_ids` 的顺序）。

        不能直接返回 `questions_by_ids()` 的字典或其 `values()`：那是
        `WHERE id IN (...)` 的查询结果，SQL 不保证顺序，刷新一次就可能换一次题序，
        用户会以为自己看错了题。这里按钉住的 id 顺序重排一次。
        """
        session = self._require_session(user_id, session_id)
        return (
            session,
            self._ordered_questions(session, self._questions_of(session)),
            self._repository.list_answers(session.id),
        )

    def save_answers(
        self, user_id: str, session_id: str, answers: Sequence[tuple[str, str]]
    ) -> tuple[AssessmentSession, list[AssessmentAnswer]]:
        """批量覆盖答案；只允许 `in_progress`（提交后 409，`ADR-028` §2.2）。

        两处校验都必须在写库之前：题目必须属于**这一场**会话（否则可以拿别的会话
        的题目 id 来污染本题），选项必须属于那道题（否则判分时 `is_correct` 永远 False，
        用户看到「我明明选了 A」）。
        """
        session = self._require_session(user_id, session_id)
        if session.status != STATUS_IN_PROGRESS:
            raise AssessmentStateError(session.status)
        questions = self._questions_of(session)
        for question_id, chosen_key in answers:
            question = questions.get(question_id)
            if question is None:
                raise AssessmentAnswerInvalidError(question_id)
            if chosen_key not in {option["key"] for option in question.options}:
                raise AssessmentAnswerInvalidError(question_id)
        self._repository.replace_answers(session.id, list(answers))
        self._repository.commit()
        return session, self._repository.list_answers(session.id)

    def abandon(self, user_id: str, session_id: str) -> AssessmentSession:
        """放弃这场测评：**不写标签、不判分**（`ADR-028` §2.2）。"""
        session = self._require_session(user_id, session_id)
        if session.status != STATUS_IN_PROGRESS:
            raise AssessmentStateError(session.status)
        self._repository.mark_abandoned(session)
        self._repository.commit()
        return session

    def submit(self, user_id: str, session_id: str) -> tuple[AssessmentSession, list[SkillScore]]:
        """判分 + 写标签 + 升级技能来源，一次提交里全部完成（`ADR-028` §2.3 / §2.6）。

        重复提交返回 409 而不是「再算一遍」：提交会写画像标签，「重复提交悄悄成功」
        会让用户以为可以靠刷新改分。
        """
        session = self._require_session(user_id, session_id)
        if session.status != STATUS_IN_PROGRESS:
            raise AssessmentStateError(session.status)

        questions = self._questions_of(session)
        answers = self._repository.list_answers(session.id)
        scored = self._score_session(session, questions, answers)

        correct_by_question = {
            answer.question_id: questions[answer.question_id].answer_key == answer.chosen_key
            for answer in answers
            if answer.question_id in questions
        }
        self._repository.grade_answers(session.id, correct_by_question)
        self._repository.upsert_tags(
            student_profile_id=session.student_profile_id,
            tags=[
                {
                    "tag": tag.skill_name,
                    "status": tag.status,
                    "score": tag.score,
                    "evidence_count": tag.question_count,
                }
                for tag in scored.tags
            ],
            session_id=session.id,
        )
        # B7 接线：只为**已验证**的标签升级来源，且只作用于已存在的技能（§2.6）。
        verified = [tag.skill_name for tag in scored.tags if tag.status == TAG_VERIFIED]
        if verified:
            self._profiles.mark_skills_assessed(session.student_profile_id, verified, now=utc_now())
        self._repository.mark_submitted(session, correct_count=scored.correct, score=scored.score)
        self._repository.commit()
        return session, scored.tags

    # ------------------------------------------------------------------ 画像

    def summarize(self, user_id: str, session_id: str) -> SummaryResult:
        """把标签写成一段人话；AI 不可用时降级成模板（`ADR-028` §2.5）。

        三条不能动的边界：

        - 只对 `submitted` 的会话生成（`in_progress` 没有标签，`abandoned` 没有结果）；
        - 输入只有标签、分数、题量与题库版本，**不读简历正文、不读联系方式**；
        - 降级不伪装成 AI：`source='fallback'` 是落库的事实，前端据此换标题。
        """
        session = self._require_session(user_id, session_id)
        if session.status != STATUS_SUBMITTED:
            raise AssessmentStateError(session.status)

        questions = self._questions_of(session)
        scored = self._score_session(session, questions, self._repository.list_answers(session.id))
        text, note = self._summarize_text(session, scored)
        source = SUMMARY_SOURCE_AI if note is None else SUMMARY_SOURCE_FALLBACK
        self._repository.set_summary(session, text=text, source=source)
        self._repository.commit()
        return SummaryResult(summary=text, source=source, note=note)

    def profile(
        self, user_id: str, profile_id: str | None
    ) -> tuple[str, list[ProfileTag], AssessmentSession | None]:
        """我的画像：标签 + 最近一次已提交会话（摘要取它）。"""
        profile = self._resolve_profile(user_id, profile_id)
        return (
            profile.id,
            self._repository.list_tags(profile.id),
            self._repository.latest_submitted(profile.id),
        )

    # ------------------------------------------------------------------ 内部

    def _summarize_text(
        self, session: AssessmentSession, scored: _Scored
    ) -> tuple[str, str | None]:
        """返回 `(文案, 降级原因)`；`降级原因 is None` 表示真的由 AI 生成。

        异常→原因的顺序有讲究：`EvidenceScoreTimeoutError` 是 `…UnavailableError` 的子类，
        必须先判子类，否则超时会被 `upstream` 吃掉、`note` 写成错的原因
        （与 `interview_service._generate` 同一条，不是复制粘贴的巧合）。
        """
        fallback = fallback_summary(total=scored.total, correct=scored.correct, tags=scored.tags)
        if self._summarizer is None:
            return fallback, "key_missing"

        system_prompt, user_prompt = build_summary_prompts(
            total=scored.total,
            correct=scored.correct,
            tags=scored.tags,
            bank_version=session.bank_version,
        )
        try:
            return self._summarizer.summarize(system_prompt, user_prompt), None
        except EvidenceScoreAuthError:
            return fallback, "key_invalid"
        except (
            EvidenceScoreTruncatedError,
            EvidenceScoreEmptyContentError,
            EvidenceScoreBadOutputError,
        ):
            return fallback, "bad_output"
        except EvidenceScoreTimeoutError:
            return fallback, "timeout"
        except EvidenceScoreUnavailableError:
            return fallback, "upstream"
        except EvidenceScoreError:
            return fallback, "bad_output"

    def _score_session(
        self,
        session: AssessmentSession,
        questions: dict[str, AssessmentQuestion],
        answers: Sequence[AssessmentAnswer],
    ) -> _Scored:
        """按 `session.question_ids` 判分（`submit` 与 `summary` 的唯一判分入口）。

        分母是**这场会话实际出过的题数**，不是「答了几题」：没答的题算错，
        否则留白反而能拿高分。题 id 在题库里查不到时（理论上不会发生——
        题目只停用不删除）仍计入分母、不进任何技能分组，并留一条 warning。
        """
        chosen = {answer.question_id: answer.chosen_key for answer in answers}
        correct_total = 0
        per_skill: dict[str, list[int]] = {}
        names: dict[str, str] = {}
        for question_id in session.question_ids:
            question = questions.get(question_id)
            if question is None:
                logger.warning(
                    "测评会话 %s 的题目 %s 在题库里不存在，按未答处理", session.id, question_id
                )
                continue
            is_correct = chosen.get(question_id) == question.answer_key
            correct_total += int(is_correct)
            bucket = per_skill.setdefault(question.skill_key, [0, 0])
            bucket[0] += int(is_correct)
            bucket[1] += 1
            names[question.skill_key] = question.skill_name

        tags = [
            score_skill(names[key], key, counts[0], counts[1]) for key, counts in per_skill.items()
        ]
        # 标签顺序固定成「技能名」：画像页按它排，重考后顺序不会跳动。
        tags.sort(key=lambda tag: tag.skill_name)
        total = session.question_count
        return _Scored(
            total=total,
            correct=correct_total,
            score=score_percent(correct_total, total),
            tags=tags,
        )

    def _questions_of(self, session: AssessmentSession) -> dict[str, AssessmentQuestion]:
        return self._repository.questions_by_ids(list(session.question_ids))

    def _ordered_questions(
        self,
        session: AssessmentSession,
        questions: dict[str, AssessmentQuestion],
    ) -> list[AssessmentQuestion]:
        """按 `session.question_ids` 的顺序排列题目（= 这一场出题的顺序）。

        题库里查不到的 id 直接跳过（题目只停用不删除，理论到不了这里）；
        判分那边遇到同一情况会留一条 warning，因为那里必须计入分母。
        """
        return [
            questions[question_id]
            for question_id in session.question_ids
            if question_id in questions
        ]

    def _require_session(self, user_id: str, session_id: str) -> AssessmentSession:
        session = self._repository.get_session(user_id, session_id)
        if session is None:
            raise AssessmentNotFoundError(session_id)
        return session

    def _resolve_profile(self, user_id: str, profile_id: str | None) -> StudentProfile:
        """解析「这场测评挂在哪份档案上」。

        指定了 id 就必须是自己的（否则 404）；没指定就用默认简历；
        一份都没有时 400——测评要写标签，标签必须挂在某份档案上，
        凭空造一份「测评专用档案」会让画像页与简历页对不上。
        """
        if profile_id is not None:
            profile = self._profiles.get_by_id(user_id, profile_id)
            if profile is None:
                raise AssessmentNotFoundError(profile_id)
            return profile
        profile = self._profiles.get_by_user_id(user_id)
        if profile is None:
            raise AssessmentProfileRequiredError(user_id)
        return profile


#: 供路由层复用的降级说明（`note` 的取值集合）；导出它是为了让 `note` 的措辞只有一份。
def degrade_note(reason: str | None) -> str | None:
    if reason is None:
        return None
    return _DEGRADE_NOTES.get(reason, "AI 概述暂不可用，以下为根据测评结果自动整理的概述。")


__all__ = [
    "AssessmentAnswerInvalidError",
    "AssessmentBankEmptyError",
    "AssessmentNotFoundError",
    "AssessmentProfileRequiredError",
    "AssessmentService",
    "AssessmentStateError",
    "SummaryResult",
    "degrade_note",
]
