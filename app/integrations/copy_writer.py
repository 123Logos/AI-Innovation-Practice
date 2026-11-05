"""DeepSeek 文案润色适配器（ADR-009 的 BYOK 通道 + ADR-010 的四档润色）。

职责边界：本模块**只产出文案**，不产出 HTML，也不写库。排版仍由
`app/integrations/resume_generator.py` 的模板负责，原因有三条可验证的既有契约：

1. 生成物必须与 `ResumePreview.vue` 的 DOM 类名一一对应，否则在线编辑与导出错位；
2. `ResumePreview.vue:23` 用 `DOMParser().body.innerHTML` + `v-html` 注入生成物，
   让模型直接吐 HTML 等于开一条**存储型 XSS** 通道（`<img onerror>` 会执行）；
3. 主题变量与模块顺序存在 `student_profiles` 里，模型不知道也不该知道。

ADR-010 起本模块只服务 `POST /resumes/polish`：润色结果回到前端草稿态，
由用户确认后才写入表单，再由用户手动「保存修改」才落库。
Key 的寿命止于本次调用：不落库、不写日志、不进异常消息。
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
    PolishExperienceInput,
    PolishMode,
    PolishProfileInput,
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
    """DeepSeek 判定 Key 无效/欠费：必须透传给客户端去修 Key。"""


class CopyUnavailableError(CopyWriterError):
    """网络超时、上游限流/5xx，或模型输出不符合契约：可重试。"""


#: 四档润色的**共同**硬约束。顺序有意义：先说输出格式，再说不可碰的事实边界。
#: 第 2 条是产品诚信线，不是风格偏好——简历造假会直接伤害真实用户，
#: 所以连 `heavy`（要求写「结果与价值」）也不许编数字，只能引用输入里已有的。
_HARD_RULES = """硬性约束（任何档位都不得违反）：
1. 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块，形如：
   {"skills": ["Python"], "experiences": [{"index": 0, "description": "…", "responsibility": "…"}]}
2. 只能使用用户给定的事实。禁止新增公司名、技术栈、数字、百分比、奖项、排名等任何未提供的信息；
   需要体现成果但输入里没有数字时，一律用定性表述（如「显著缩短了」），不得虚构量化指标。
3. 不得删除任何一段经历：experiences 必须与输入等长，index 与输入一一对应。
4. skills 只能对给定技能做去重与排序，名字必须与输入完全一致，不得新增、改写或翻译。
5. 每段只改写 description 与 responsibility 两个字段；标题与时间是既有事实，不要复述也不要改动。"""

#: 四档的差异化指令（ADR-010 §3）。`expand` 需要把 `max_words` 注入进去。
_MODE_DIRECTIVES: dict[PolishMode, str] = {
    "detail": (
        "档位=细节：只做最小干预。修正错别字、语病、标点与明显的用词不当，"
        "保持原意、原句结构与原文长度（每段字数变化不超过 ±20%）。"
    ),
    "medium": (
        "档位=中幅：提升专业度。换用更准确的动词与技术表述，删去口头禅与冗余修饰，"
        "合并同义表述，长度与原文相近（±30%）。"
    ),
    "heavy": (
        "档位=大幅：按 STAR（情境-任务-行动-结果）重写每段经历，让「做了什么、"
        "怎么做的、带来了什么」一目了然；行动用动词开头，结果只允许引用输入中已出现的数字。"
        "可以比原文更长，但每段 description 不超过 120 个汉字、responsibility 不超过 60 个汉字。"
    ),
    "expand": (
        "档位=扩写：在不偏离事实的前提下丰富细节，把原文已经提到的动作拆成可核验的表述、"
        "补足上下文与职责边界。绝对不要为了凑字数引入新事实。"
        "每段（description 与 responsibility 之和）严格不超过 {max_words} 个汉字。"
    ),
}


def build_system_prompt(mode: PolishMode, max_words: int | None = None) -> str:
    """按档位拼 System Prompt。

    公开给服务层与测试：档位差异必须能从函数签名上验证，
    不然「发了哪一档的指令」只能靠断网抓包才知道。
    """
    directive = _MODE_DIRECTIVES[mode]
    if mode == "expand":
        directive = directive.format(max_words=max_words)
    return (
        "你是资深技术岗位简历顾问，服务对象是中国高校 AI 专业的在校生。"
        "用户会发来一份 JSON，字段为 profile、skills、"
        "experiences[].index/title/description/responsibility。\n" + directive + "\n" + _HARD_RULES
    )


def _build_http_client() -> httpx2.Client:
    """默认的出网客户端。

    单独抽成函数是因为测试要把它换成 `httpx2.MockTransport`：`openai` 3.x 走的是
    `httpx2`，而 pytest-httpx / respx 只给 `httpx` 打补丁——实测那种「Mock」会把请求真的
    发出去。换成自己构造 client 注入，拦截发生在传输层，不依赖第三方。
    """
    return httpx2.Client(timeout=REQUEST_TIMEOUT_SECONDS)


class ResumeCopyWriter(ABC):
    """文案写手接口（ADR-010：润色与生成解耦后，它是唯一的外部 AI 出口）。"""

    @abstractmethod
    def write(
        self,
        profile: PolishProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[PolishExperienceInput],
        mode: PolishMode = "medium",
        max_words: int | None = None,
    ) -> ResumeCopy:
        """按给定事实产出润色文案；失败时抛 `CopyWriterError` 子类。"""


class DeepSeekCopyWriter(ResumeCopyWriter):
    """用 DeepSeek `deepseek-flash` 按档位润色经历文案。

    入参只有 Key：BYOK 架构下 Key 来自请求头，服务端配置里不留任何默认值
    （`DEEPSEEK_API_KEY` 已不再被读取，见 `.env.example` 的注释）。
    """

    def __init__(self, api_key: str, http_client: httpx2.Client | None = None) -> None:
        # Key 为空是装配错误，不该走到这里；抛在这里也不会泄漏内容，因为没有内容。
        if not api_key.strip():
            raise ValueError("DeepSeekCopyWriter requires a non-empty api_key")
        self._client = OpenAI(
            api_key=api_key.strip(),
            base_url=DEEPSEEK_BASE_URL,
            http_client=http_client if http_client is not None else _build_http_client(),
            # SDK 默认重试 2 次，同步接口上就是最坏 3 × 30s = 90 秒，而 30 秒是给用户的硬预算；
            # 重试交给用户点「再试一次」，不藏在服务端里。
            max_retries=0,
        )

    def write(
        self,
        profile: PolishProfileInput,
        skills: Sequence[SkillInput],
        experiences: Sequence[PolishExperienceInput],
        mode: PolishMode = "medium",
        max_words: int | None = None,
    ) -> ResumeCopy:
        payload = {
            "profile": {"full_name": profile.full_name},
            "skills": [skill.name for skill in skills],
            "experiences": [
                {
                    "index": position,
                    "title": experience.title,
                    "description": experience.description,
                    "responsibility": experience.responsibility,
                }
                for position, experience in enumerate(experiences)
            ],
        }
        try:
            completion = self._client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                # 非流式：润色结果整段返回后由用户逐段对照，不做打字机效果。
                stream=False,
                messages=[
                    {"role": "system", "content": build_system_prompt(mode, max_words)},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                # `deepseek-flash` 默认开启思考模式（官方文档）。润色不需要长推理，
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
            # 401 已由上面的 AuthenticationError 单独处理；剩下的都是「上游不可用」。
            logger.warning("DeepSeek 调用失败（status=%s, type=%s）", status, type(error).__name__)
            raise CopyUnavailableError("AI 服务暂时不可用。") from error

        content = (completion.choices[0].message.content or "").strip()
        return self._parse_copy(content)

    def _parse_copy(self, content: str) -> ResumeCopy:
        """把模型输出转成 `ResumeCopy`。

        模型经常把 JSON 包在 ``` 围栏里，或前后带一句废话，所以先剥围栏、再取第一个
        平衡的 `{...}` 片段；仍解析失败就是契约问题，按「上游不可用」处理，
        而不是把半截结果交给用户确认。
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
