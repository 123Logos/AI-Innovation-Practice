"""画像摘要的 AI 适配器（B9 / `ADR-028` §2.5）。

职责边界：本模块**只把服务端已经拼好的 system / user 文本交给模型、把它的回复发回来**，
不读库、不判权限、不决定「标签从哪来」（那是 `app/services/assessment_service.py` 的业务规则）。

**形状逐条继承 `interview_coach.py`**：ABC + 具体实现、可注入 `httpx2.Client`（测试换
`MockTransport`）、BYOK（Key 只来自请求头 `X-DeepSeek-Key`）、`deepseek-flash`、`stream=False`、
`extra_body={"thinking": {"type": "disabled"}}`、`max_retries=0`、`REQUEST_TIMEOUT_SECONDS`；
Key 不落库、不写日志、不进异常消息（`ADR-009`）。

**失败语义与 `evidence_scorer` 完全一致**：异常层级直接复用它的 `EvidenceScore*` 族，
因为「模型说不出话」在任何端点都是同一种上游故障，服务层用同一套 `degrade_reason`
翻译（key_missing / key_invalid / timeout / upstream / bad_output）。本模块不知道降级这回事，
它只在失败时抛异常，由服务层就地吞掉并换成模板文案——画像的事实（标签）不因此消失。

本模块**不 import `app.services`**：分层方向是 services → integrations，反向会被
import-linter 的 “Application layers” 契约挡住。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx2
from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI

from app.integrations.copy_writer import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    REQUEST_TIMEOUT_SECONDS,
)
from app.integrations.evidence_scorer import (
    EvidenceScoreAuthError,
    EvidenceScoreEmptyContentError,
    EvidenceScoreTimeoutError,
    EvidenceScoreTruncatedError,
    EvidenceScoreUnavailableError,
)

logger = logging.getLogger(__name__)

#: 输出 token 硬上限。摘要要求 120 字以内，中文约 200 token，取 600 留足余量；
#: 真被切断走 `EvidenceScoreTruncatedError`（finish_reason=length），由服务层降级。
MAX_OUTPUT_TOKENS = 600


class ProfileSummarizer(ABC):
    """画像摘要生成器接口：服务层只依赖这个抽象，测试用假实现替换，
    真实实现只在 `app/services/dependencies.py` 里装配——与 `InterviewCoach` 完全同构。
    """

    @abstractmethod
    def summarize(self, system_prompt: str, user_prompt: str) -> str:
        """把标签交给模型，带回一段人话；失败时抛 `EvidenceScoreError` 子类。"""


class DeepSeekProfileSummarizer(ProfileSummarizer):
    """用 DeepSeek `deepseek-flash` 把标签写成一段概述。

    入参只有 Key：BYOK 架构下 Key 来自请求头，服务端配置里不留任何默认值，
    Key 的寿命止于本次调用。
    """

    def __init__(self, api_key: str, http_client: httpx2.Client | None = None) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeekProfileSummarizer requires a non-empty api_key")
        self._client = OpenAI(
            api_key=api_key.strip(),
            base_url=DEEPSEEK_BASE_URL,
            http_client=http_client if http_client is not None else _build_http_client(),
            max_retries=0,
        )

    def summarize(self, system_prompt: str, user_prompt: str) -> str:
        try:
            completion = self._client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                stream=False,
                max_tokens=MAX_OUTPUT_TOKENS,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                extra_body={"thinking": {"type": "disabled"}},
            )
        except AuthenticationError as error:
            logger.info("DeepSeek 拒绝该 Key（status=%s）", error.status_code)
            raise EvidenceScoreAuthError("API Key 无效或已欠费。") from error
        except APIConnectionError as error:
            logger.warning("DeepSeek 画像摘要连不上（type=%s）", type(error).__name__)
            raise EvidenceScoreTimeoutError("AI 服务连接失败或超时。") from error
        except APIStatusError as error:
            status = getattr(error, "status_code", None)
            logger.warning(
                "DeepSeek 画像摘要调用失败（status=%s, type=%s）", status, type(error).__name__
            )
            raise EvidenceScoreUnavailableError("AI 服务暂时不可用。") from error

        choice = completion.choices[0] if completion.choices else None
        finish_reason = choice.finish_reason if choice is not None else None
        content = ((choice.message.content if choice is not None else "") or "").strip()

        if finish_reason == "length":
            logger.warning("DeepSeek 画像摘要输出被 max_tokens 切断（length）")
            raise EvidenceScoreTruncatedError("AI 输出被 token 上限切断。", finish_reason="length")
        if not content:
            logger.warning("DeepSeek 画像摘要返回空内容")
            raise EvidenceScoreEmptyContentError("AI 没有返回任何内容。")

        return content


def _build_http_client() -> httpx2.Client:
    """默认的出网客户端（与 `copy_writer` / `evidence_scorer` 同一个理由：测试换 MockTransport）。

    没有复用 `interview_coach._build_http_client`：那是私有名，跨模块 import 一个下划线函数
    等于把「它随时可以改签名」这件事写进本模块。三行重复换一条清晰的依赖边界，值得。
    """
    return httpx2.Client(timeout=REQUEST_TIMEOUT_SECONDS)
