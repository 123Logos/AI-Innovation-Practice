"""文本模拟面试的编排层（FR-07 / B11a，ADR-022）。

分工：读库与调模型在这里，**面试题从哪来、上下文怎么截、AI 失败怎么降级**都是业务规则，
HTTP 形状在 `app/schemas/interview.py`、路由在 `app/api/v1/interviews.py`。

两条结构约束（与 `match_service.py` 同族）：

1. **一个 session、三个仓储**：会话、岗位、档案在同一次 `session_scope()` 里读，
   保证「这条面试基于的简历/岗位是同一时刻的状态」可被复现。
2. **AI 失败在这层就地吞掉**，翻译成 `ai_degrade_reason` 后继续面试（ADR-022 (b)）。
   因此本端点不会因为 AI 失败返回 4xx/5xx，用户在 UI 上看到的是**显式降级标记**而不是中断——
   这条由 `tests/test_interviews.py` 钉住（「AI 失败时用户看到什么」用例）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.integrations.evidence_scorer import (
    EvidenceScoreAuthError,
    EvidenceScoreBadOutputError,
    EvidenceScoreEmptyContentError,
    EvidenceScoreError,
    EvidenceScoreTimeoutError,
    EvidenceScoreTruncatedError,
    EvidenceScoreUnavailableError,
)
from app.integrations.interview_coach import (
    InterviewCoach,
    build_interview_system_prompt,
)
from app.repositories.interview_repository import (
    MAX_CONTEXT_TURNS,
    SPEAKER_CANDIDATE,
    SPEAKER_INTERVIEWER,
    STATUS_ENDED,
    InterviewRepository,
    InterviewSession,
    InterviewTurn,
)
from app.repositories.job_repository import JobRepository
from app.repositories.student_profile_repository import StudentProfileRepository

logger = logging.getLogger(__name__)


class InterviewNotFoundError(Exception):
    """会话/岗位不存在或不可见；对外一律 404（不是 403，见 ADR-022）。"""


class InterviewSessionEndedError(Exception):
    """向已结束的会话发消息；对外 400。"""


#: 离线降级时回显给用户的可见原因（与 `ai_degrade_reason` 列同文）。
_DEGRADE_TEXT = {
    "key_missing": "未配置 AI 密钥，已切换到离线模拟。",
    "key_invalid": "AI 密钥无效，已切换到离线模拟。",
    "timeout": "AI 服务超时，已切换到离线模拟。",
    "upstream": "AI 服务暂时不可用，已切换到离线模拟。",
    "bad_output": "AI 返回异常，已切换到离线模拟。",
}

#: 离线模式的追问题库（无 AI 时按顺序循环）。不依赖简历/岗位也能给出像样的模拟面试。
_OFFLINE_QUESTIONS = [
    "能谈谈你简历中最有成就感的一个项目吗？你具体负责了哪部分？",
    "你在这段经历里遇到的最大挑战是什么，又是怎么解决的？",
    "你觉得自己相比同届同学，最突出的优势是什么？",
    "如果让你重新做一次这个项目，你会在哪里做得不一样？",
    "你未来三到五年的职业规划是怎样的？",
    "你为什么想申请这个方向？你为此做过哪些准备？",
]


@dataclass(frozen=True)
class _InterviewContext:
    """一次面试可用来出题/追问的事实：方向、岗位标题、简历摘要。"""

    target_role: str | None
    job_title: str | None
    resume_summary: str


class InterviewService:
    def __init__(
        self,
        *,
        session_repository: InterviewRepository,
        job_repository: JobRepository,
        profile_repository: StudentProfileRepository,
        coach: InterviewCoach | None = None,
    ) -> None:
        self._sessions = session_repository
        self._jobs = job_repository
        self._profiles = profile_repository
        #: 没有 Key 时装配成 None，由 `_generate()` 翻译成 `key_missing` 降级；
        #: 绝不让「忘传 Key」变成静默失败（ADR-022 (b)）。
        self._coach = coach

    # ------------------------------------------------------------------ 公开

    def create_session(
        self, user_id: str, job_id: str | None, target_role: str | None
    ) -> tuple[InterviewSession, InterviewTurn, str | None]:
        """创建一场进行中的会话，并立即生成面试官的开场问题（sequence=1）。

        AI 在开场就失败时也不中断：开场问题走本地降级模板，并打 `ai_degrade_reason`。
        `job_id` 若提供但不可见（不存在/未发布/回收站），按 404 处理。
        """
        if job_id is not None:
            job = self._jobs.get_readable(job_id, user_id)
            if job is None:
                raise InterviewNotFoundError(job_id)

        session = self._sessions.create_session(user_id, job_id, target_role)
        context = self._build_context(user_id, session)
        opening_text, degrade_reason = self._generate(session, [], context)
        opening = self._sessions.add_turn(
            session.id,
            sequence=1,
            speaker=SPEAKER_INTERVIEWER,
            content=opening_text,
            ai_degrade_reason=degrade_reason,
        )
        self._sessions.touch(session.id)
        self._sessions._session.commit()
        self._sessions._session.refresh(session)
        return session, opening, degrade_reason

    def list_sessions(
        self, user_id: str, page: int, page_size: int
    ) -> tuple[list[InterviewSession], int]:
        """当前用户自己的会话列表（带总数，供分页 meta）。"""
        offset = (page - 1) * page_size
        sessions = self._sessions.list_owned(user_id, offset, page_size)
        total = self._sessions.count_owned(user_id)
        return sessions, total

    def get_session(
        self, user_id: str, session_id: str
    ) -> tuple[InterviewSession, list[InterviewTurn]]:
        """详情：会话 + 全部轮次（按 sequence 顺序）。非所有者或不存在 → 404。"""
        session = self._sessions.get_owned(session_id, user_id)
        if session is None:
            raise InterviewNotFoundError(session_id)
        turns = self._sessions.list_turns(session_id)
        return session, turns

    def send_message(
        self, user_id: str, session_id: str, content: str
    ) -> tuple[InterviewTurn, InterviewTurn, str | None]:
        """发送一条候选人作答，并拿到面试官的下一段（真实或降级）。

        返回（候选人的轮次, 面试官的新轮次, 本轮降级原因）。会话不存在/非所有者 → 404，
        已结束 → 400。
        """
        session = self._sessions.get_owned(session_id, user_id)
        if session is None:
            raise InterviewNotFoundError(session_id)
        if session.status == STATUS_ENDED:
            raise InterviewSessionEndedError(session_id)

        candidate_seq = self._sessions.next_sequence(session_id)
        candidate_turn = self._sessions.add_turn(
            session_id, candidate_seq, SPEAKER_CANDIDATE, content
        )

        prior_turns = self._sessions.list_turns_recent(session_id, MAX_CONTEXT_TURNS)
        context = self._build_context(user_id, session)
        interviewer_text, degrade_reason = self._generate(session, prior_turns, context)

        interviewer_seq = self._sessions.next_sequence(session_id)
        interviewer_turn = self._sessions.add_turn(
            session_id,
            interviewer_seq,
            SPEAKER_INTERVIEWER,
            interviewer_text,
            ai_degrade_reason=degrade_reason,
        )
        self._sessions.touch(session_id)
        self._sessions._session.commit()
        return candidate_turn, interviewer_turn, degrade_reason

    def end_session(self, user_id: str, session_id: str) -> InterviewSession:
        """结束会话（status=ended, ended_at=now）；非所有者 → 404。"""
        session = self._sessions.get_owned(session_id, user_id)
        if session is None:
            raise InterviewNotFoundError(session_id)
        self._sessions.end_session(session_id, user_id)
        self._sessions._session.commit()
        self._sessions._session.refresh(session)
        return session

    # ------------------------------------------------------------------ 内部

    def _build_context(self, user_id: str, session: InterviewSession) -> _InterviewContext:
        """装配出题上下文：方向（target_role 优先，其次岗位标题）、简历摘要。"""
        target_role = session.target_role
        job_title: str | None = None
        if session.job_id is not None:
            job = self._jobs.get_readable(session.job_id, user_id)
            if job is not None:
                job_title = job.title

        resume_summary = self._resume_summary(user_id)
        if not target_role and job_title:
            target_role = job_title
        return _InterviewContext(
            target_role=target_role, job_title=job_title, resume_summary=resume_summary
        )

    def _resume_summary(self, user_id: str) -> str:
        """把默认档案压成一段给模型的简历摘要（有界长度）。"""
        profile = self._profiles.get_by_user_id(user_id)
        if profile is None:
            return ""
        parts: list[str] = []
        if profile.target_role:
            parts.append(f"目标岗位：{profile.target_role}")
        skills = [skill.name for skill in self._profiles.list_skills(profile.id)]
        if skills:
            parts.append("技能：" + "、".join(skills[:20]))
        for experience in self._profiles.list_experiences(profile.id)[:5]:
            parts.append(f"项目：{experience.title} —— {experience.description}")
        summary = "\n".join(parts)
        return summary[:2000]

    def _generate(
        self,
        session: InterviewSession,
        prior_turns: list[InterviewTurn],
        context: _InterviewContext,
    ) -> tuple[str, str | None]:
        """产出面试官本轮发言；AI 不可用时返回（离线文本, 降级原因），否则（AI文本, None）。

        与 `match_service._score_with_ai` 同一套异常→`degrade_reason` 映射；次序有讲究：
        `EvidenceScoreTimeoutError` 是 `…UnavailableError` 子类，必须先判子类，
        否则超时会被 `upstream` 吃掉、`degrade_reason` 写成错的原因。
        """
        if self._coach is None:
            return self._fallback(context, prior_turns), "key_missing"

        system_prompt = build_interview_system_prompt(
            target_role=context.target_role,
            job_title=context.job_title,
            resume_summary=context.resume_summary,
        )
        user_prompt = self._user_prompt(prior_turns, context)

        try:
            text = self._coach.respond(system_prompt, user_prompt)
        except EvidenceScoreAuthError:
            return self._fallback(context, prior_turns), "key_invalid"
        except EvidenceScoreTruncatedError:
            # 必须与 Unavailable 同组但在其前；bad_output 语义。
            return self._fallback(context, prior_turns), "bad_output"
        except (EvidenceScoreEmptyContentError, EvidenceScoreBadOutputError):
            # 同样必须在 Unavailable 之前：这两个都是它的子类。
            return self._fallback(context, prior_turns), "bad_output"
        except EvidenceScoreTimeoutError:
            return self._fallback(context, prior_turns), "timeout"
        except EvidenceScoreUnavailableError:
            return self._fallback(context, prior_turns), "upstream"
        except EvidenceScoreError:
            return self._fallback(context, prior_turns), "bad_output"

        return text, None

    def _user_prompt(self, prior_turns: list[InterviewTurn], context: _InterviewContext) -> str:
        """把最近 N 轮拼成 user 文本，并附上「候选人的下一句作答」占位说明。

        上下文截断：只取最近 `MAX_CONTEXT_TURNS` 轮（ADR-022 (d)）；首轮系统背景不计入轮数上限。
        """
        lines: list[str] = []
        if prior_turns:
            for turn in prior_turns:
                role = "面试官" if turn.speaker == SPEAKER_INTERVIEWER else "候选人"
                lines.append(f"{role}：{turn.content}")
        else:
            lines.append("（面试刚开始，还没有历史对话。）")
        lines.append(
            "请根据上面的对话，作为面试官对候选人上一轮的回答做简短反馈，"
            "然后提出下一个问题。如果这是开场，请直接做自我介绍式的暖场并抛出第一个问题。"
        )
        return "\n".join(lines)

    def _fallback(self, context: _InterviewContext, prior_turns: list[InterviewTurn]) -> str:
        """离线降级题目：不调 AI，也能给出贴合方向的模拟面试问题。

        `prior_turns` 用于判断是开场还是追问，并从题库里按顺序取下一题，
        保证离线模式下多轮对话不重复。
        """
        candidate_answers = [t for t in prior_turns if t.speaker == SPEAKER_CANDIDATE]
        if not candidate_answers:
            if context.target_role:
                return (
                    f"你好，欢迎参加{context.target_role}方向的模拟面试。"
                    "请先做一个简短的自我介绍，包括你的背景，以及为什么对这个方向感兴趣。"
                )
            if context.job_title:
                return (
                    f"你好，欢迎参加「{context.job_title}」岗位的模拟面试。"
                    "请先做一个简短的自我介绍，让我们先了解一下你的背景。"
                )
            return "你好，欢迎参加模拟面试。请先做一个简短的自我介绍，包括你的背景和求职方向。"
        index = (len(candidate_answers) - 1) % len(_OFFLINE_QUESTIONS)
        return _OFFLINE_QUESTIONS[index]
