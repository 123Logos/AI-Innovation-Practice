"""DeepSeek 简历结构化适配器（`ADR-031` 2.4）。

职责边界：本模块**只把 PDF 抽出的文本交给模型、把模型给的对象原样带回**，不写库、
不做权限判断，也不做字段归一与裁剪（那是业务规则，留在 `app/services/resume_import.py`
——与润色的 `_sanitize_copy`、提取的 `job_service` 核验同一个分层理由）。

**形状逐条继承 `skill_extractor.py`，不发明新的约定**：ABC + 具体实现、可注入
`httpx2.Client`（测试换 `MockTransport`）、BYOK（Key 只来自请求头 `X-DeepSeek-Key`，
见 `app/services/dependencies.py` 的 `DEEPSEEK_KEY_HEADER`）、`deepseek-flash`、
`stream=False`、`extra_body={"thinking": {"type": "disabled"}}`、`max_retries=0`、
`REQUEST_TIMEOUT_SECONDS`；Key 不落库、不写日志、不进异常消息；
JSON 解析复用 `copy_writer.py` 的剥围栏 `_FENCE` 与括号平衡 `_first_object`。

本模块**不 import `app.schemas.profile`**：那张 schema 已经 import
`app.repositories.student_profile_repository`，而 `repositories/` 位于 `integrations/`
之上（`AGENTS.md` §4.1 + import-linter 的 "Application layers" 契约）。
所以这里的输出契约故意比前端宽松——日期一律 `Any`，「解析不出年月就丢这条」
是服务层的规则，不是适配器的。

**为什么不启用 `response_format={"type":"json_object"}`**：与 `ADR-011` 2.B 同一组理由
（无凭据环境下无法自证该组合可用，且它一旦生效会把不合法输出替换成空白字符串，
反而观测不到原始输出）。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx2
from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.integrations.copy_writer import (
    _FENCE,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    REQUEST_TIMEOUT_SECONDS,
    _first_object,
)

logger = logging.getLogger(__name__)

#: Prompt 的版本号。**Prompt 每改一次这里 +1**，不许原地改写不留版本。
#: 它跟着响应回到前端（`meta.prompt_version`），是「这次导入是哪一版指令产出的」
#: 唯一的回答路径——本端点不落库，所以它只能活在响应里，不能靠数据库列。
STRUCTURE_PROMPT_VERSION = "v1"

#: 输出 token 硬上限（2.3 的第三条封顶）。8 条技能 + 3 段经历 + 5 段教育的骨架
#: 约 900 token，留一倍余量给描述文字；再大就会把 30 秒预算挤掉。
MAX_OUTPUT_TOKENS = 2000

#: 切断情形的 `finish_reason`：与提取侧同一个判据，且同样必须在解析**之前**判。
TRUNCATED_FINISH_REASON = "length"


class ResumeStructureError(Exception):
    """结构化适配器的错误基类；消息里**绝不允许**出现 API Key 或 PDF 正文。"""


class StructureAuthError(ResumeStructureError):
    """DeepSeek 判定 Key 无效/欠费。服务层把它翻译成 `AI_KEY_MISSING`（2.5 第 2 条）。"""


class StructureUnavailableError(ResumeStructureError):
    """超时 / 上游 4xx-5xx / 空返回 → `AI_SERVICE_UNAVAILABLE`。"""


class StructureOutputError(ResumeStructureError):
    """输出不是合法 JSON、结构不符，或被我们自己的 `max_tokens` 切断 → `AI_OUTPUT_INVALID`。

    与 `SkillExtractBadOutputError` 刻意**不同谱系**：提取侧把「没守格式」也归进
    「上游不可用」，而本端点这两类的用户动作不一样（一个该稍后再试，一个该重试或手动填），
    所以必须是兄弟类，好让服务层各映射一个码（2.5 的五个码）。
    """


class StructuredSkill(BaseModel):
    """模型给的一条技能。宽松到只剩一个可缺省的名字：空格子由服务层丢弃并计数。"""

    name: str = ""


class StructuredExperience(BaseModel):
    """一段经历或项目。`start_date` / `end_date` 故意是 `Any`（见模块 docstring）。"""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    start_date: Any = None
    end_date: Any = None
    description: str = ""
    responsibility: str = ""


class StructuredEducation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    school: str = ""
    major: str = ""
    degree: str = ""
    edu_start: Any = None
    edu_end: Any = None


class StructuredProfile(BaseModel):
    """模型返回的那个 JSON 对象本体（2.4 的输出契约）。

    每一格都能缺省：一条坏字段不该让整份导入作废，服务层按格丢弃并在
    `meta.dropped` 里把丢弃条数说出来。
    """

    model_config = ConfigDict(extra="ignore")

    full_name: str = ""
    contact: str = ""
    target_role: str = ""
    skills: list[StructuredSkill] = Field(default_factory=list)
    experiences: list[StructuredExperience] = Field(default_factory=list)
    projects: list[StructuredExperience] = Field(default_factory=list)
    educations: list[StructuredEducation] = Field(default_factory=list)


@dataclass(frozen=True)
class StructureLimits:
    """写进 Prompt 的条数上限。

    **数字的权威在服务层**（它才知道前端与 `ProfileUpsertRequest` 的上限），
    这里只是把同一组数回声给模型，好让它少产几条注定被裁掉的条目。
    """

    skills: int
    experiences: int
    educations: int


def build_structure_prompt(limits: StructureLimits) -> str:
    """拼结构化用的 System Prompt。

    公开给服务层与测试：「发了哪一版指令、给了哪几个上限」必须能从函数签名上验证，
    否则 2.4 的字段契约与 2.3 的三封顶有没有落到 Prompt 里，只能靠抓包才知道。
    """
    return (
        "你是简历解析助手，服务对象是中国高校 AI 专业的在校生。"
        "用户会发来一份 PDF 简历抽出的纯文本，你要把它整理成结构化档案。\n"
        "硬性约束（任何简历都不得违反）：\n"
        "1. 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块，形如：\n"
        '   {"full_name": "张三", "contact": "13800138000", "target_role": "算法工程师", '
        '"skills": [{"name": "Python"}], '
        '"experiences": [{"title": "xx 公司 后端实习", "start_date": "2024-06", '
        '"end_date": "2024-09", "description": "……", "responsibility": "……"}], '
        '"projects": [{"title": "xx 系统", "start_date": "2023-09", "end_date": null, '
        '"description": "……", "responsibility": "……"}], '
        '"educations": [{"school": "中国计量大学", "major": "人工智能", "degree": "本科", '
        '"edu_start": "2021-09", "edu_end": "2025-06"}]}\n'
        "2. 只写 PDF 里读得到的内容。读不到的字符串填 null 或空串、数组填 []，"
        "宁少勿造：不得根据常识补公司、补学校、补时间。\n"
        "3. 日期一律写成 YYYY-MM（只有年份时可以只写 YYYY）；仍在进行或预计完成的填 null。\n"
        "4. contact 只填中国大陆 11 位手机号，没有手机号就填 null；"
        "不要把邮箱、地址写进 contact。\n"
        f"5. skills 最多 {limits.skills} 条，experiences 与 projects 各最多 "
        f"{limits.experiences} 条，educations 最多 {limits.educations} 条；"
        "超出的优先留下最能说明本人能力的。\n"
        "6. 实习、工作经历放 experiences；在校做的系统、比赛、开源放 projects。"
        "分不清就放 experiences，不要重复写两遍。\n"
        "7. degree 只能是「专科」「本科」「硕士」「博士」四个词之一，判不准就填空串。\n"
        "8. description 与 responsibility 各不超过 200 字，用原文里最能说明"
        "「做了什么、结果如何」的句子，不要抄整段。"
    )


def _build_http_client() -> httpx2.Client:
    """默认的出网客户端。

    单独抽成函数是为了测试把它换成 `httpx2.MockTransport`：`openai` 3.x 走的是 `httpx2`，
    而 pytest-httpx / respx 只给 `httpx` 打补丁。与 `copy_writer` / `skill_extractor`
    同一个理由、同一个超时口径。
    """
    return httpx2.Client(timeout=REQUEST_TIMEOUT_SECONDS)


class ResumeStructurer(ABC):
    """PDF 文本 → 结构化档案的抽象（`ADR-031` 2.1：纯计算出口，不写任何表）。"""

    #: 响应里的 `meta.prompt_version` 从这里取：真实实现报 `v1`，
    #: 假实现报自己的版本，测试因此能钉住「回显的版本号就是发指令那一版」。
    prompt_version: str = "unknown"

    @abstractmethod
    def structure(self, text: str, limits: StructureLimits) -> StructuredProfile:
        """把抽取出的文本交给模型，带回它给的对象；失败时抛 `ResumeStructureError` 子类。"""


class DeepSeekResumeStructurer(ResumeStructurer):
    """用 DeepSeek `deepseek-flash` 把 PDF 文本整理成结构化档案。

    入参只有 Key：BYOK 架构下 Key 来自请求头，服务端配置里不留任何默认值，
    Key 的寿命止于本次调用（不落库、不写日志、不进异常消息）。
    """

    prompt_version = STRUCTURE_PROMPT_VERSION

    def __init__(self, api_key: str, http_client: httpx2.Client | None = None) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeekResumeStructurer requires a non-empty api_key")
        self._client = OpenAI(
            api_key=api_key.strip(),
            base_url=DEEPSEEK_BASE_URL,
            http_client=http_client if http_client is not None else _build_http_client(),
            # 同步接口上 SDK 默认重试 2 次就是最坏 3 × 30s = 90 秒，而 30 秒是给用户的
            # 硬预算；重试交给用户点「再试一次」，不藏在服务端里（与润色/提取同口径）。
            max_retries=0,
        )

    def structure(self, text: str, limits: StructureLimits) -> StructuredProfile:
        try:
            completion = self._client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                stream=False,
                max_tokens=MAX_OUTPUT_TOKENS,
                messages=[
                    {"role": "system", "content": build_structure_prompt(limits)},
                    {"role": "user", "content": text},
                ],
                # `deepseek-flash` 默认开启思考模式；抽取不需要长推理。
                extra_body={"thinking": {"type": "disabled"}},
            )
        except AuthenticationError as error:
            # SDK 自己就把 Key 打码成 `****xxx`，异常消息里没有明文 Key；
            # 这里仍然只记状态码，不把 str(error) 原样往外抛。
            logger.info("DeepSeek 拒绝该 Key（status=%s）", error.status_code)
            raise StructureAuthError("API Key 无效或已欠费。") from error
        except (APIConnectionError, APIStatusError) as error:
            status = getattr(error, "status_code", None)
            logger.warning(
                "DeepSeek 结构化调用失败（status=%s, type=%s）", status, type(error).__name__
            )
            raise StructureUnavailableError("AI 服务暂时不可用。") from error

        choice = completion.choices[0] if completion.choices else None
        finish_reason = choice.finish_reason if choice is not None else None
        content = ((choice.message.content if choice is not None else "") or "").strip()

        # 切断与空返回都在解析**之前**判：半截 JSON 先解析会被误诊成「模型不守格式」，
        # 而这两种情形对应的用户动作不同（2.5 的码表就是这么分出来的）。
        if finish_reason == TRUNCATED_FINISH_REASON:
            logger.warning("DeepSeek 输出被 max_tokens 切断（content_length=%s）", len(content))
            raise StructureOutputError("AI 输出被 token 上限切断。")
        if not content:
            logger.warning("DeepSeek 返回空内容（finish_reason=%s）", finish_reason)
            raise StructureUnavailableError("AI 没有返回任何内容。")

        return self._parse_profile(content)

    def _parse_profile(self, content: str) -> StructuredProfile:
        """把模型输出转成对象，复用 `copy_writer` 的剥围栏 + 括号平衡策略。"""
        stripped = _FENCE.sub("", content).strip()
        candidate = stripped if stripped.startswith("{") else _first_object(stripped)
        if candidate is None:
            logger.warning("DeepSeek 返回内容不是 JSON 对象（长度=%s）", len(content))
            raise StructureOutputError("AI 返回的内容无法解析。")
        try:
            return StructuredProfile.model_validate_json(candidate)
        except ValueError as error:
            logger.warning("DeepSeek 返回的 JSON 不符合结构化契约：%s", type(error).__name__)
            raise StructureOutputError("AI 返回的内容不符合约定。") from error
