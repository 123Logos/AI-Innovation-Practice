"""DeepSeek 岗位 JD 技能提取适配器（ADR-011 议题 2 的 C1 落地）。

职责边界：本模块**只把 JD 原文交给模型、把模型给的条目原样带回**，不写库、不做权限判断，
也不做 §2.D 的核验（核验是业务规则，留在 `app/services/job_service.py`——与润色的
`_sanitize_copy` 同一个分层理由）。提取端点与 `/resumes/polish` 同构（2.A）：结果先回显，
用户在草稿态逐条删改，点「保存岗位」才由 `POST/PUT /jobs` 落库。

**形状逐条继承 `copy_writer.py`，不发明新的约定**：
ABC + 具体实现、可注入 `httpx2.Client`（测试换 `MockTransport`）、BYOK（Key 只来自请求头
`X-DeepSeek-Key`，见 `app/services/dependencies.py` 的 `DEEPSEEK_KEY_HEADER`）、
`deepseek-flash`、`stream=False`、`extra_body={"thinking": {"type": "disabled"}}`、
`max_retries=0`、`REQUEST_TIMEOUT_SECONDS`；Key 不落库、不写日志、不进异常消息；
JSON 解析复用它的剥围栏 `_FENCE` 与括号平衡 `_first_object`，而不是另写一份。

**与润色唯一的参数差异**：`max_tokens=1500`。理由见 ADR-011 2.B——润色的输出长度由
Prompt 的字数约束与「经历 ≤ 20 段」共同兜住，而一个 8000 字的 JD 能让模型自行决定生成
几十条技能；服务端不封顶，「30 秒够用」这条推理在本端点就不成立。
不启用 `response_format={"type":"json_object"}`：`deepseek-flash` 对该组合的支持在无凭据
环境下无法自证，而它一旦生效会把不合法输出替换成空白字符串，反而观测不到原始输出
（2.B「替代方案」的三条理由，本轮照此执行）。

本模块**不 import `app.schemas`**：`app/schemas/job.py` 已经 import
`app.repositories.job_repository`，而 `repositories/` 位于 `integrations/` 之上
（AGENTS.md §4.1 的分层方向 + import-linter 的 “Application layers” 契约）。
所以这里只带 AI 侧的输出契约（`ExtractedSkill` / `ExtractionOutput`），
HTTP 侧的请求/响应模型留在 `app/schemas/job.py`，两个契约由服务层对接。
"""

from __future__ import annotations

import json
import logging
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx2
from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI
from pydantic import BaseModel, Field

from app.integrations.copy_writer import (
    _FENCE,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    REQUEST_TIMEOUT_SECONDS,
    _first_object,
)

logger = logging.getLogger(__name__)

#: 提取 Prompt 的版本号。**Prompt 每改一次这里 +1**，不许原地改写不留版本：
#: 它会被服务端写进 `jobs.extract_prompt_version`（ADR-011 v9 的溯源戳），
#: 是 PRD FR-03「保存提示词版本」唯一的实现路径——否则三个月后没人能回答
#: 「这批技能是哪一版 Prompt 产出的」。
EXTRACT_PROMPT_VERSION = "v1"

#: 输出 token 硬上限（2.B 三封顶之一）。15 条 × (60 字 evidence + 约 30 token 结构开销)
#: = 1350 ≤ 1500；模型把 `name` 写长时会在第 14/15 条处被切断，那是第 7 情形，
#: 由 `finish_reason == "length"` 单独命名，不与「模型不守格式」（第 4 情形）混记。
MAX_OUTPUT_TOKENS = 1500

#: 技能条数上限（2.B 三封顶之一，Prompt 与后端截断用同一个数）。
#: 与 `job_repository.MAX_SKILLS_PER_JOB` 同值是有意的：手动录入与 AI 提取共用一个上限。
MAX_EXTRACTED_SKILLS = 15

#: 单条 `evidence` 的字数上限（2.B 三封顶之一）。主因是误杀率而不是 token：
#: evidence 越长，要求模型逐字复现的连续原文跨度越大，一个全半角差异就作废整条。
#: 同时它必须等于 `job_skills.evidence_text` 的列宽（`String(60)`），
#: 所以写侧的 `JobSkillInput.evidence_text` 也从这里 import 这一个常量。
MAX_EVIDENCE_CHARS = 60

#: 单条 `name` 的字数上限（2.D 第 6 条：超了丢弃该条）。库里 `name` 列宽 100 更宽松，
#: 因为手动录入的技能名允许写长；40 只约束「模型给的技能名」，超长通常是模型在抄整句。
MAX_SKILL_NAME_CHARS = 40

#: 切断情形的错误消息里回显给用户的原因，与第 4 情形的「不合法 JSON」文案刻意不同：
#: 两者的用户动作不一样（第 4 该重试同一请求，第 7 该只粘 JD 的核心段落或减少期望条数）。
TRUNCATED_FINISH_REASON = "length"


class SkillExtractorError(Exception):
    """提取适配器的错误基类；消息里**绝不允许**出现 API Key、JD 正文或 evidence 内容。"""


class SkillExtractAuthError(SkillExtractorError):
    """DeepSeek 判定 Key 无效/欠费（失败语义第 2 情形）：透给用户去修 Key。"""


class SkillExtractUnavailableError(SkillExtractorError):
    """上游不可用或输出不合契约（第 3/4/6/7 情形）：一律映射成 502 `retryable`。"""


class SkillExtractBadOutputError(SkillExtractUnavailableError):
    """第 4 情形：返回内容不是可解析的 JSON 对象——模型自己没守格式。"""


class SkillExtractEmptyContentError(SkillExtractUnavailableError):
    """第 6 情形：`choices[0].message.content` 为空或全空白。

    单独命名是 ADR-011 附录第 6 行的要求：`copy_writer.py` 那种
    `(content or "").strip()` 兜住 `None` 之后仍会走解析路径，最终报的是「不是 JSON」，
    把「模型根本没答」误诊成「模型答得不对」。
    """


class SkillExtractTruncatedError(SkillExtractUnavailableError):
    """第 7 情形：输出被我们自己的 `max_tokens` 切断（`finish_reason == "length"`）。

    携带 `finish_reason` 供服务层回进 `meta.finish_reason`（2.D 的观测点之一），
    也供日志区分「答得太多被切断」与「没答」。
    """

    def __init__(self, message: str, *, finish_reason: str | None) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason


class ExtractedSkill(BaseModel):
    """模型输出的一条技能（**AI 侧契约**，故意比 HTTP 响应宽松）。

    宽松的两处都是 ADR-011 2.D 明文要求的，不是偷懒：

    - `name` / `evidence` 允许缺省成空串：缺锚点的条目由服务层按第 6 条丢弃，
      而不是在这里报错——报错会让一条坏条目拖垮整份响应。
    - `type` 用 `Any`：2.D 第 5 条要求「`type` 不在枚举内 → **保留该条**、`type` 置 NULL」，
      所以这里绝不能把 `"senior"` 或 `123` 折成校验失败，否则第 5 条实现不了。

    `confidence` 保持 `float | None`（严格）：它只有「模型自报的数」这一个来源，
    给不出数就是没守 2.C 的输出契约，按第 4 情形整体作废比悄悄吞掉更诚实。
    """

    name: str = ""
    type: Any = None
    evidence: str = ""
    confidence: float | None = None


class ExtractionOutput(BaseModel):
    """模型返回的那个 JSON 对象本体（2.C 的输出契约）。"""

    skills: list[ExtractedSkill] = Field(default_factory=list)


@dataclass(frozen=True)
class SkillExtraction:
    """适配器的返回值：模型给的条目 + 上游的 `finish_reason`。

    `finish_reason` 必须跟着回来——2.D 第 5 条要求它进 `meta` 与日志，
    而它只存在于 `completion.choices[0]` 上，不在这层带出来服务层就拿不到。
    """

    skills: list[ExtractedSkill] = field(default_factory=list)
    finish_reason: str | None = None


#: 2.D 第 1 条的硬约束。顺序有意义：先说输出格式，再说条数与锚点，最后说「不许编」。
#: `evidence` 要求「最短连续片段」是子串核验能跑通的前提：抄得越长越容易一个字符对不上。
_EXTRACT_RULES = f"""硬性约束（任何 JD 都不得违反）：
1. 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块，形如：
   {{"skills": [{{"name": "Python", "type": "hard", "evidence": "熟悉 Python", "confidence": 0.9}}]}}
2. skills 最多 {MAX_EXTRACTED_SKILLS} 条；JD 里出现的技能多于这个数时，
   保留最能代表该岗位的前 {MAX_EXTRACTED_SKILLS} 条，不要把同义写法拆成多条。
3. 每条的 evidence 必须是 JD 原文里**最短的连续片段**（不超过 {MAX_EVIDENCE_CHARS} 个字），
   逐字复制：不得改写、不得补删标点、不得跨句拼接、不得省略中间内容、不要抄整段。
   找不到可逐字复制的片段，就不要输出这条技能。
4. 不得输出 JD 里读不到的技术栈、工具或能力；JD 没写的技能一律不要输出，宁少勿造。
5. name 是技能本身的名字（如「Python」「跨部门沟通」），不超过 {MAX_SKILL_NAME_CHARS} 个字，
   不要写成整句话，也不要与 evidence 完全相同。
6. type 只能是 "hard"（硬技能）、"soft"（通用能力）、"domain"（领域知识）三者之一，
   判不准就填 null。
7. confidence 是你对「这条技能确实出自该 JD」的把握，0 到 1 之间的小数。"""


def build_extraction_prompt() -> str:
    """拼提取用的 System Prompt。

    公开给服务层与测试：「发了哪一版指令」必须能从函数签名上验证，
    不然三个封顶数字（条数 15 / evidence 60 字 / name 40 字）有没有落到 Prompt 里，
    只能靠断网抓包才知道。
    """
    return (
        "你是企业岗位 JD 的技能拆解助手，服务对象是中国高校 AI 专业的在校生。"
        "用户会发来一段岗位 JD（字段 description，可能带 title），"
        "你要从中提取这个岗位要求的技能条目。\n" + _EXTRACT_RULES
    )


def normalize_for_trace(text: str) -> str:
    """§2.D.1 的四步归一化，核验与 `origin` 派生共用这**同一个**函数。

    四步写成四行、与 ADR 逐字对应，方便对照审查：

    1. NFKC：全角→半角、兼容字符归一（`，`→`,`、`＿`→`_`、`Ｐython`→`python`）；
    2. casefold：大小写折叠（`Python` ≡ `python`）；
    3. 去空白与换行；
    4. **去标点**——这就是「标点是否参与比较」的答案：**不参与**。

    第 4 步覆盖 Unicode 通用类别 `Pc/Pd/Ps/Pe/Po/Pi/Pf`（ASCII 标点、中文全角标点、
    顿号、书名号、引号、省略号一并删掉）。取舍与代价见 §2.D.1 的用例表：
    核验要证明的是「这句话真在 JD 里出现过」，不是「连标点都抄对了」；
    要求后者会把误杀集中到信息量最低的那类差异上（`、` 与 `。` 没有 ASCII 兼容映射，
    NFKC 折不动，只有第 4 步能处理）。

    两个**已知取舍**，不是 bug，别当缺陷修：
    - 符号类 `S` 不删：`+` 的类别是 `Sm`，所以「C++」里的加号留在串里（它是技能名的一部分）；
    - `Pc` 含下划线，于是 `user_id` 与 `userid` 会被判为相同——只影响定位的严格度，
      不影响「是否出自原文」。
    """
    normalized = unicodedata.normalize("NFKC", text)
    folded = normalized.casefold()
    without_space = "".join(char for char in folded if not char.isspace())
    return "".join(char for char in without_space if not unicodedata.category(char).startswith("P"))


def is_traceable(evidence: str, description: str) -> bool:
    """`evidence` 归一化后是否是 `description` 归一化文本的子串（2.D 第 2 条的唯一判据）。

    空锚点必须先挡掉：`"" in 任何串` 恒为真，不挡的话一条空白 evidence 就会被派生成
    「可回溯原文」——那正好是 2.D 想拦住的东西的反面。
    """
    anchor = normalize_for_trace(evidence)
    if not anchor:
        return False
    return anchor in normalize_for_trace(description)


def _build_http_client() -> httpx2.Client:
    """默认的出网客户端。

    单独抽成函数是为了测试把它换成 `httpx2.MockTransport`：`openai` 3.x 走的是 `httpx2`，
    而 pytest-httpx / respx 只给 `httpx` 打补丁（实测那种「Mock」会把请求真的发出去）。
    与 `copy_writer._build_http_client()` 同一个理由、同一个超时口径。
    """
    return httpx2.Client(timeout=REQUEST_TIMEOUT_SECONDS)


class JobSkillExtractor(ABC):
    """JD 技能提取接口（ADR-011 2.A：纯计算出口，不写任何表）。

    形状与 `ResumeCopyWriter` 一致：服务层只依赖这个抽象，测试用假实现替换，
    真实实现只在 `app/services/dependencies.py` 里装配。
    """

    @abstractmethod
    def extract(self, description: str, title: str | None = None) -> SkillExtraction:
        """把 JD 原文交给模型，带回它给的条目；失败时抛 `SkillExtractorError` 子类。"""


class DeepSeekJobSkillExtractor(JobSkillExtractor):
    """用 DeepSeek `deepseek-flash` 从 JD 原文里提取技能条目。

    入参只有 Key：BYOK 架构下 Key 来自请求头，服务端配置里不留任何默认值，
    Key 的寿命止于本次调用（不落库、不写日志、不进异常消息）。
    """

    def __init__(self, api_key: str, http_client: httpx2.Client | None = None) -> None:
        # Key 为空是装配错误，不该走到这里；抛在这里也不会泄漏内容，因为没有内容。
        if not api_key.strip():
            raise ValueError("DeepSeekJobSkillExtractor requires a non-empty api_key")
        self._client = OpenAI(
            api_key=api_key.strip(),
            base_url=DEEPSEEK_BASE_URL,
            http_client=http_client if http_client is not None else _build_http_client(),
            # 与润色同口径：SDK 默认重试 2 次，同步接口上就是最坏 3 × 30s = 90 秒，
            # 而 30 秒是给用户的硬预算；重试交给用户点「再试一次」，不藏在服务端里。
            max_retries=0,
        )

    def extract(self, description: str, title: str | None = None) -> SkillExtraction:
        payload: dict[str, str] = {"description": description}
        if title:
            # 岗位名只是提示词里的上下文，不传就不发这一格：空串对模型没有任何信息量，
            # 却会让「Prompt 里带了什么」这件事在日志与测试里多一个歧义来源。
            payload["title"] = title

        try:
            completion = self._client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                # 非流式：提取结果整段返回后由用户逐条删改，不做打字机效果（2.B 继承项）。
                stream=False,
                # 本轮唯一新增的输出参数，理由见模块 docstring 与 2.B 的三封顶算式。
                max_tokens=MAX_OUTPUT_TOKENS,
                messages=[
                    {"role": "system", "content": build_extraction_prompt()},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                # `deepseek-flash` 默认开启思考模式；提取不需要长推理，
                # 关掉同时省掉延迟和大段思维链 token（与润色同一个 `extra_body`）。
                extra_body={"thinking": {"type": "disabled"}},
            )
        except AuthenticationError as error:
            # SDK 自己就把 Key 打码成 `****xxx`，异常消息里没有明文 Key；
            # 这里仍然只记状态码，不把 str(error) 原样往外抛。
            logger.info("DeepSeek 拒绝该 Key（status=%s）", error.status_code)
            raise SkillExtractAuthError("API Key 无效或已欠费。") from error
        except (APIConnectionError, APIStatusError) as error:
            status = getattr(error, "status_code", None)
            # 401 已由上面的 AuthenticationError 单独处理；剩下的都是「上游不可用」。
            logger.warning(
                "DeepSeek 提取调用失败（status=%s, type=%s）", status, type(error).__name__
            )
            raise SkillExtractUnavailableError("AI 服务暂时不可用。") from error

        choice = completion.choices[0] if completion.choices else None
        finish_reason = choice.finish_reason if choice is not None else None
        content = ((choice.message.content if choice is not None else "") or "").strip()

        # 第 7 情形必须在解析**之前**判：被 `max_tokens` 切断的输出一定是半截 JSON，
        # 先解析就会把它误诊成第 4 情形「模型没守格式」——两个诊断对应的用户动作不同。
        # 这里只记长度，不记 content，更不记 Key 与 JD 正文。
        if finish_reason == TRUNCATED_FINISH_REASON:
            logger.warning(
                "DeepSeek 输出被 max_tokens 切断（finish_reason=length, content_length=%s）",
                len(content),
            )
            raise SkillExtractTruncatedError(
                "AI 输出被 token 上限切断。", finish_reason=finish_reason
            )
        # 第 6 情形同样在解析之前：空内容报「空」，不留给解析器去编一条「不是 JSON」。
        if not content:
            logger.warning("DeepSeek 返回空内容（finish_reason=%s）", finish_reason)
            raise SkillExtractEmptyContentError("AI 没有返回任何内容。")

        return SkillExtraction(skills=self._parse_skills(content), finish_reason=finish_reason)

    def _parse_skills(self, content: str) -> list[ExtractedSkill]:
        """把模型输出转成条目列表，复用 `copy_writer` 的剥围栏 + 括号平衡策略。

        模型经常把 JSON 包在 ``` 围栏里，或前后带一句废话，所以先剥围栏、再取第一个
        平衡的 `{...}` 片段；仍解析失败就是契约问题（第 4 情形），按「上游不可用」处理，
        而不是把半截结果交给用户确认。
        """
        stripped = _FENCE.sub("", content).strip()
        candidate = stripped if stripped.startswith("{") else _first_object(stripped)
        if candidate is None:
            logger.warning("DeepSeek 返回内容不是 JSON 对象（长度=%s）", len(content))
            raise SkillExtractBadOutputError("AI 返回的内容无法解析。")
        try:
            return ExtractionOutput.model_validate_json(candidate).skills
        except ValueError as error:
            logger.warning("DeepSeek 返回的 JSON 不符合提取契约：%s", type(error).__name__)
            raise SkillExtractBadOutputError("AI 返回的内容不符合约定。") from error
