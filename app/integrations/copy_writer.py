"""DeepSeek 文案润色适配器（ADR-009：BYOK + 非流式 + 关闭思考模式）。

职责边界：本模块**只产出文案**，不产出 HTML。排版仍由
`app/integrations/resume_generator.py` 的模板负责，原因是生成物必须同时满足
三条既有契约（ADR-006 / ADR-007）：

1. DOM 与 `ResumePreview.vue` 一一对应，否则在线编辑与导出会错位；
2. 主题变量与模块顺序跟着库里的 `student_profiles` 走，模型不知道也不该知道；
3. **每个用户字段都要经 `escape()`**：`ResumePreview.vue` 用 `v-html` 注入生成物，
   让模型直接吐 HTML 等于开一条存储型 XSS 通道。

Key 只在本次调用的内存里存在：不落库、不写日志、不进异常消息（ADR-009 后果 §1）。
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence

import httpx2
from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI

from app.schemas.resume import (
    ExperienceInput,
    ProfileInput,
    ResumeCopy,
    SkillInput,
)

logger = logging.getLogger(__name__)

#: DeepSeek 的 OpenAI 兼容入口（官方推荐用 OpenAI SDK 调用）。
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
#: 固定模型：`deepseek-chat` / `deepseek-reasoner` 已于 2026-07-24 停用。
DEEPSEEK_MODEL = "deepseek-flash"
#: 非流式单次调用的硬超时。思考模式关掉后正常在数秒内返回，30 秒是网络抖动的余量，
#: 再长就会撞浏览器/代理的超时线，用户无法归因。
REQUEST_TIMEOUT_SECONDS = 30.0

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class CopyWriterError(Exception):
    """文案适配器的错误基类；消息里**绝不允许**出现 API Key。"""


class CopyAuthError(CopyWriterError):
    """DeepSeek 判定 Key 无效/欠费：必须透传给客户端去修 Key，不能降级。"""


class CopyUnavailableError(CopyWriterError):
    """网络超时、上游限流/5xx，或模型输出不符合契约：可重试，前端可提议模板降级。"""


def _build_http_client() -> httpx2.Client:
    """默认的出网客户端。

    单独抽成函数是因为测试要把它换成 `httpx2.MockTransport`：`openai` 3.x 走的是
    `httpx2`，而 pytest-httpx / respx 只给 `httpx` 打补丁——实测会让"Mock 测试"
    真的往 DeepSeek 发请求。换成自己构造 client 注入，拦截发生在传输层，不依赖第三方。
    """
    return httpx2.Client(timeout=REQUEST_TIMEOUT_SECONDS)


class ResumeCopyWriter(ABC):
    """文案写手接口。"""

    @abstractmethod
    def write(
        self,
        profile: ProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[ExperienceInput],
    ) -> ResumeCopy:
        """按档案事实产出润色后的文案；失败时抛 `CopyWriterError` 子类。"""


class DeepSeekCopyWriter(ResumeCopyWriter):
    """用 DeepSeek `deepseek-flash` 润色项目经历文案。

    入参只有 Key：BYOK 架构下 Key 来自请求头，服务端配置里不留任何默认值
    （`DEEPSEEK_API_KEY` 已经不再被读取，见 `.env.example` 的注释）。
    """

    def __init__(self, api_key: str, http_client: httpx2.Client | None = None) -> None:
        # Key 为空是装配错误，不该走到这里；抛在这里也不会泄漏内容，因为没有内容。
        if not api_key.strip():
            raise ValueError("DeepSeekCopyWriter requires a non-empty api_key")
        self._client = OpenAI(
            api_key=api_key.strip(),
            base_url=DEEPSEEK_BASE_URL,
            http_client=http_client if http_client is not None else _build_http_client(),
            # SDK 默认会重试 2 次。同步接口上重试 2 次就是 3 × 30s = 90 秒的等待，
            # 而 30 秒超时是给用户的硬预算；重试的活儿交给前端按钮（或模板降级）做，
            # 不要藏在服务端里。
            max_retries=0,
        )

    def write(
        self,
        profile: ProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[ExperienceInput],
    ) -> ResumeCopy:
        payload = {
            "experiences": [
                {
                    "index": position,
                    "title": experience.title,
                    "description": experience.description,
                    "responsibility": experience.responsibility,
                }
                for position, experience in enumerate(experiences)
            ],
            "skills": [skill.name for skill in skills],
        }
        try:
            completion = self._client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                # 非流式：Phase 3 只做「整段返回后一次性渲染」，不做打字机效果。
                stream=False,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                # `deepseek-flash` 默认开启思考模式（官方文档）。简历润色不需要长推理，
                # 关掉能同时省掉延迟和大段思维链 token；extra_body 会被 SDK 并进请求体根节点。
                extra_body={"thinking": {"type": "disabled"}},
            )
        except AuthenticationError as error:
            # SDK 自己就把 Key 打码成 `****xxx`，异常消息里没有明文 Key；
            # 这里仍然只记状态码，不把 str(error) 原样往外抛。
            logger.info("DeepSeek 拒绝该 Key（status=%s）", error.status_code)
            raise CopyAuthError("API Key 无效或已欠费。") from error
        except (APIConnectionError, APIStatusError) as error:
            status = getattr(error, "status_code", None)
            # 401 不是 APIStatusError 而是 AuthenticationError（已单独处理）；
            # 这里剩下的都是「上游暂时不可用」：超时、429、5xx、4xx 参数错误。
            logger.warning("DeepSeek 调用失败（status=%s, type=%s）", status, type(error).__name__)
            raise CopyUnavailableError("AI 服务暂时不可用。") from error

        content = (completion.choices[0].message.content or "").strip()
        return self._parse_copy(content)

    def _parse_copy(self, content: str) -> ResumeCopy:
        """把模型输出转成 `ResumeCopy`。

        模型经常把 JSON 包在 ``` 围栏里，或前后带一句废话，所以先剥围栏、再取第一个
        平衡的 `{...}` 片段；仍解析失败就是契约问题，按「上游不可用」处理，
        让前端有机会提议模板降级，而不是落一份半成品进库。
        """
        stripped = _FENCE.sub("", content).strip()
        candidate = stripped if stripped.startswith("{") else _first_object(stripped)
        if candidate is None:
            logger.warning("DeepSeek 返回内容不是 JSON 对象（长度=%s）", len(content))
            raise CopyUnavailableError("AI 返回的内容无法解析。")
        try:
            return ResumeCopy.model_validate_json(candidate)
        except ValueError as error:
            logger.warning("DeepSeek 返回的 JSON 不符合文案契约：%s", type(error).__name__)
            raise CopyUnavailableError("AI 返回的内容不符合约定。") from error


def _first_object(text: str) -> str | None:
    """取第一个括号平衡的 JSON 对象；模型在 JSON 前后加解释时靠它兜住。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : position + 1]
    return None


_SYSTEM_PROMPT = (
    "你是资深技术岗位简历顾问。用户会给出一份中国高校 AI 专业学生的项目经历与技能清单，"
    "JSON 字段：experiences[].index/title/description/responsibility、skills[]。\n"
    "你的任务是把每段经历的 description 与 responsibility 改写成动词开头、结果导向的中文表述，"
    "并对技能做去重与排序。\n"
    "硬性约束：\n"
    "1. 只能使用用户给定的事实，禁止新增公司名、数字、奖项、技术栈等任何未提供的信息；\n"
    "2. 不得删除任何一段经历，experiences 必须与输入等长、index 与输入一一对应；\n"
    "3. skills 只能从给定技能里挑选并排序，名字必须与输入完全一致，不得新增或改写；\n"
    "4. description 不超过 120 个汉字，responsibility 不超过 60 个汉字；\n"
    '5. 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块，形如：'
    '{"skills": ["Python"], "experiences": [{"index": 0, "description": "…", "responsibility": "…"}]}'
)
