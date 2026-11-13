"""简历相关的 Pydantic 模型。

`ProfileInput` / `SkillInput` / `ExperienceInput` 是**生成器契约**：
`app/integrations/resume_generator.py` 直接消费它们，因此保留在原位置，
不再作为生成接口的请求体（T7.2 起 `/resumes/generate` 只收 title，数据一律来自数据库）。
ADR-010 新增的 `ResumePolishRequest` 是例外：润色端点收的是**前端草稿态数据**，
纯计算、不落库、不采信它做任何持久化或权限判断。
"""

from datetime import date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.datetime_display import format_shanghai_datetime, shanghai_today


class ProfileInput(BaseModel):
    """生成器需要的基本信息。

    T7.5 起不再包含 `grade`（年级已从产品下架），避免调用方被一个
    「库里恒为空串」的字段卡在校验上。
    T7.6 起不再包含 `major`：专业由 `educations` 的每一段各自携带。
    用户反馈轮：新增 `email`（简历上展示的邮箱），可为空。
    """

    full_name: str = Field(min_length=1, max_length=100)
    contact: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=254)


class SkillInput(BaseModel):
    """学生侧的一条技能。

    `name` 两端空白在这里就裁掉，与岗位侧 `JobSkillInput.normalize_name` 同一规矩：
    库里两张技能表必须只有一种规范化程度，否则「后端键判未覆盖、前端角标判已具备」
    会在同一屏上同时出现（ADR-013 §1.3）。大小写/全半角**不在这里折**——展示要保留
    用户自己写的样子，判定交给 `skill_name_key`。

    2026-11-07 起没有 `self_rating`：那一格在 UI 已下线、渲染不使用、库里恒为 3，
    却仍要求每个客户端编一个 1–5 的数。迁移与理由见 ADR-013 §4。

    B7（`ADR-027`）加了两格**元数据**，它们不是自评分的壳：

    - `source`：这条技能**是谁说的**（自评 / 来自项目 / 教师确认）。**省略 = 沿用库里
      同名技能已有的来源**（新技能则落 `self_reported`），不是「清空」。`assessment`
      不在可写集合里——它只能由测评提交时由服务端写入（`ADR-028` §2.6），客户端传它
      等于在没有测评的情况下自称「已被验证」；
    - `last_used_on`：用户声明的最近使用日，**省略即清空**（PUT 整体覆盖语义，
      与 `target_role` 同一条）。它不从 `created_at` 推：全量替换让那列恒等于
      「上次保存时间」。

    两格的省略语义**故意不同**：`last_used_on` 是纯用户数据，「没说就是没有」；
    `source` 有一档是**服务端写的**（`assessment`），若省略也当清空，
    那么用户测完评之后随便改一个字再保存，测评写下的来源就被抹成自评——
    B9 的证据一次保存就作废。客户端的正确做法是：自己没资格表态的那一行**别传**。

    `first_added_at` 不在这里：它由写入历史决定，客户端无权声明。
    """

    name: str = Field(min_length=1, max_length=100)
    source: Literal["self_reported", "project", "teacher_confirmed"] | None = None
    last_used_on: date | None = None

    @model_validator(mode="after")
    def normalize_name(self) -> "SkillInput":
        stripped = self.name.strip()
        if not stripped:
            raise ValueError("skill name must not be blank")
        self.name = stripped
        return self

    @field_validator("source", mode="before")
    @classmethod
    def reject_assessment_source(cls, value: object) -> object:
        """`assessment` 不给客户端选，且**要说清为什么**（`ADR-027` §2.2）。

        不写成 `Literal` 一员然后「先收着」：允许客户端声明「已被测评验证」，等于让人
        在没有测评的情况下伪造一个更强的来源；而「收了却不用」是另一种静默降级。
        """
        if value == "assessment":
            raise ValueError("skill source assessment is written by the server")
        return value

    @field_validator("last_used_on")
    @classmethod
    def validate_last_used_on(cls, value: date | None) -> date | None:
        """「上次用这个技术」不可能是明天。

        与经历的「开始时间不得晚于今天」同一口径（东八区）；结束时间那格反而允许未来
        （「预计结束」），因为那是计划，而「最近使用」是已发生的事。
        """
        if value is not None and value > shanghai_today():
            raise ValueError("last_used_on must not be in the future")
        return value


class ExperienceInput(BaseModel):
    """一段项目经历。

    「这段经历存在」的锚点是 `title`，必须非空；描述/职责允许留空——
    保存接口不再拦必填，只有「生成简历」时前端才做完整性校验。
    开始时间不得晚于今天（东八区），未来开始一律 422；
    结束时间允许未来（「预计结束」），只需不早于开始时间。
    """

    title: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date | None = None
    description: str = ""
    responsibility: str = ""

    @model_validator(mode="after")
    def validate_date_range(self) -> "ExperienceInput":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.start_date > shanghai_today():
            raise ValueError("start_date must not be in the future")
        return self


class ResumeGenerateRequest(BaseModel):
    """生成请求体只剩标题 + 可选的目标简历；档案内容由服务层按登录用户从库中读取。

    D4.1 起一个用户有多份简历，`profile_id` 指定用哪一份生成；
    不传则用默认简历 —— 旧前端（D4.2 之前）只发 `title`，必须继续可用。

    ADR-010 起本接口是**纯模板**：不再调用 AI，因此也不需要 `X-DeepSeek-Key`。
    曾经存在的 `use_fallback` 字段随「润色与生成解耦」一并删除——模板生成不再依赖
    任何 AI 失败事件才可达。
    """

    title: str = Field(min_length=1, max_length=200)
    profile_id: str | None = Field(default=None, max_length=36)


PolishMode = Literal["detail", "medium", "heavy", "expand"]
POLISH_MODES: tuple[PolishMode, ...] = ("detail", "medium", "heavy", "expand")

#: `expand` 未指定 `max_words` 时的默认上限，与前端输入框默认值一致。
DEFAULT_MAX_WORDS = 200


class PolishProfileInput(BaseModel):
    """润色用的基本信息：只做**宽松校验**。

    `/resumes/polish` 是纯计算端点，不落库、不采信这些值做任何权限或持久化判断，
    所以没必要拿生成接口的必填标准卡住「姓名还没填就想润色经历」的用户。
    上限保留，是为了给 Prompt 的体积兜底。
    """

    full_name: str = Field(default="", max_length=100)
    contact: str = Field(default="", max_length=255)
    email: str | None = Field(default=None, max_length=254)


class PolishExperienceInput(BaseModel):
    """参与润色的一段经历。

    只有 `description` / `responsibility` 会被改写；`title` 仅用于让模型知道这段经历在做什么，
    因此**不校验日期**——草稿里一段还没填时间的经历同样值得润色，
    而拿 `ExperienceInput` 的「开始时间不得早于今天」去卡它会变成莫名的 422。
    """

    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)
    responsibility: str = Field(default="", max_length=2000)


class ResumePolishRequest(BaseModel):
    """`POST /resumes/polish` 的请求体（ADR-010）。

    数据来自前端 Pinia 草稿态，允许尚未保存；服务端只回一份润色结果，
    **不写任何表**。技能与经历的数量上限既是 Prompt 体积的兜底，
    也是防止本端点被当成开放 AI 代理刷用户 Token 的第一道闸（第二道是限流）。
    """

    profile: PolishProfileInput = Field(default_factory=PolishProfileInput)
    skills: list[SkillInput] = Field(min_length=1, max_length=50)
    experiences: list[PolishExperienceInput] = Field(min_length=1, max_length=20)
    mode: PolishMode = "medium"
    max_words: int | None = Field(default=None, ge=40, le=800)

    @model_validator(mode="after")
    def normalize_max_words(self) -> "ResumePolishRequest":
        """`max_words` 只对 `expand` 有意义：缺省补 200，其它档位一律置空。

        放在校验层而不是散在 Prompt 构造函数里，是为了让「服务层拿到的值」和
        「最终生效的值」只有一个版本——否则测试里断言的默认值和实际发出去的值可能不同源。
        """
        if self.mode == "expand":
            if self.max_words is None:
                self.max_words = DEFAULT_MAX_WORDS
        else:
            self.max_words = None
        return self


#: 响应契约的最后一道硬边（ADR-018 §2.3 / §6）：`description ≤ 1200`、`responsibility ≤ 600`。
#: 放宽自 600/300——`max_words` 上限 800 是两字段合计，放宽后单字段必然容得下合法输出。
#: 模型写飞超出这道边时**段级**截断并标 `truncated=true`，不再整单 502；
#: 常量单源在此，适配器（`copy_writer`）与服务层（`resume_service`）都从这里取。
POLISHED_DESCRIPTION_MAX_LEN = 1200
POLISHED_RESPONSIBILITY_MAX_LEN = 600


def clamp_to_polish_contract(description: str, responsibility: str) -> tuple[str, str, bool]:
    """把一对文案截到响应契约上限，返回 `(description, responsibility, truncated)`。

    ADR-018 §2.4：超出放宽后的契约时不再整单 502——该段截到上限并标 `truncated=true`，
    其余段照常返回。这是「出现但标注」：不承诺报出截断前少了多少字
    （`truncated_from_words` 之类字段被老板明确否掉，ADR-018 §6 末）。
    """
    truncated = False
    if len(description) > POLISHED_DESCRIPTION_MAX_LEN:
        description = description[:POLISHED_DESCRIPTION_MAX_LEN]
        truncated = True
    if len(responsibility) > POLISHED_RESPONSIBILITY_MAX_LEN:
        responsibility = responsibility[:POLISHED_RESPONSIBILITY_MAX_LEN]
        truncated = True
    return description, responsibility, truncated


class PolishedExperience(BaseModel):
    """模型对**一段**既有经历的改写结果。

    `index` 是输入经历列表里的下标，不是数据库 id：让模型复述 id 既没必要也容易出错，
    而下标由我们在 Prompt 里编号，回贴时不会错位。标题与起止时间属于既有事实，
    模型无权改动，因此这里刻意不接受。

    ADR-018 起新增三格（加字段，不改既有语义）：

    - `target_words`：本次请求生效的目标字数，**只有 `expand` 有**，其它档 `None`；
    - `over_limit_by`：返回文字（description + responsibility 合计）超出目标多少，
      不超为 0。它必须有数：`len(返回文字) - target_words`，用户据此自己减字；
    - `truncated`：是否撞到响应契约（1200/600）被段级截断。超**目标**不截断只标注，
      超**契约**才截断——两道边的职责分开（ADR-018 §6）。
    """

    index: int = Field(ge=0)
    description: str = Field(min_length=1, max_length=POLISHED_DESCRIPTION_MAX_LEN)
    responsibility: str = Field(min_length=1, max_length=POLISHED_RESPONSIBILITY_MAX_LEN)
    target_words: int | None = None
    over_limit_by: int = Field(default=0, ge=0)
    truncated: bool = False


class ResumeCopy(BaseModel):
    """AI 产出的文案契约，同时是 `POST /resumes/polish` 的响应体（ADR-009 / ADR-010）。

    只有经历文案与技能顺序两项：主题、模块顺序、区块标题都由 `student_profiles` 决定，
    姓名/联系方式/教育经历是原始事实——这些都不经过模型，
    模型也就无法把它们改坏或塞进 HTML。
    """

    skills: list[str] = Field(default_factory=list, max_length=50)
    experiences: list[PolishedExperience] = Field(default_factory=list, max_length=20)


class ResumeRead(BaseModel):
    resume_id: str
    title: str
    content_html: str
    created_at: datetime
    #: ADR-015 §2.3（B8）：这份简历在档案内的版本号，从 1 递增。
    #: 生成与「当前简历」读路径共用本模型，读侧恒为具体值。
    version_no: int = 1

    @field_serializer("created_at")
    def serialize_shanghai(self, value: datetime) -> str:
        """与档案接口同口径：naive UTC → 东八区 ISO 串。"""
        return format_shanghai_datetime(value)


# --------------------------------------------------------------------------- #
# 导出前隐私检查（批次 B2 / PRD FR-06「导出前显示隐私检查：联系方式、作品链接、
# 个人信息和授权范围」）。四个类全是**响应模型**：这一格是纯计算，不落库、不加列、
# 不建表——形态与 `POST /jobs/extract-skills`（ADR-011 议题 2.A）同一条路子。
#
# `key` 是给界面与测试用的稳定标识，`label` 才是上屏的那句人话；两者分开写，
# 是为了让「改措辞」不牵动断言，也让界面不必拿英文键去猜中文说法。
# --------------------------------------------------------------------------- #

#: FR-06 点名的四类，顺序即界面顺序（联系方式 / 作品链接 / 个人信息 / 授权范围）。
PrivacyCategoryKey = Literal["contact", "portfolio", "personal", "authorization"]

#: 一格的三种样子：`present` 有内容可核、`missing` 档案里没填、
#: `not_available` 数据模型里根本没有这一栏（今天无数据可查）。
#: 第三种必须存在，否则「还没做这一栏」会被读成「用户什么都没填」——那是假话。
PrivacyCategoryStatus = Literal["present", "missing", "not_available"]


class ResumePrivacyCheckRequest(BaseModel):
    """`POST /resumes/privacy-check` 的请求体：只有可选的 `profile_id`。

    与 `ResumeGenerateRequest` 同一条信任边界（T7.2）：检查读的是**库里已保存的档案**，
    客户端塞进来的任何档案字段都不采信。这里比生成更进一步——连 `title` 都不收，
    因为「会不会被带出去」不取决于简历叫什么名字。
    """

    profile_id: str | None = Field(default=None, max_length=36)


class PrivacyItem(BaseModel):
    """一类里的一格：档案里有没有它、它会不会进到这页简历上。

    `present` 与 `included` 是两个问题，不许合并成一格：邮箱没填时两个都是假，
    而「目标岗位偏好」是 `present=True, included=False`——它确实存在，只是不上简历。
    """

    key: str
    label: str
    present: bool
    included: bool
    #: 只有布尔说不清时才填这一句（例如整节缺失时该怎么补）。界面按「有就说、没有就不说」处理。
    note: str | None = None


class PrivacyCategory(BaseModel):
    """FR-06 四类中的一类。"""

    key: PrivacyCategoryKey
    label: str
    status: PrivacyCategoryStatus
    #: `status="not_available"` 时这一句是**必给**的：不给，用户看见的就是一个没有来由的空壳。
    note: str | None = None
    items: list[PrivacyItem] = Field(default_factory=list)


class ResumePrivacyCheck(BaseModel):
    """`POST /resumes/privacy-check` 的响应体（四类清单）。"""

    profile_id: str
    categories: list[PrivacyCategory]


# --------------------------------------------------------------------------- #
# 版本对比与按岗位定制（批次 B8 剩余，`ADR-026`）。
#
# 三组模型对应三个端点：版本列表、两版差异、定制结果。全部是**响应模型**，
# 唯一的请求模型 `ResumeCustomizeRequest` 只有 `profile_id`（可选）与 `job_id`——
# 「用哪个档案、面向哪条岗位」是调用方的输入，其余（标题、排序、来源标记）
# 一律由服务层算出来，客户端改不到（与 `/generate` 同一条信任边界）。
# --------------------------------------------------------------------------- #

#: 这一行怎么来的（`ADR-026` §2.1）。字面量即 `ck_resumes_origin` 的取值。
ResumeVersionOrigin = Literal["generated", "customized"]


class ResumeVersionSummary(BaseModel):
    """版本列表里的一行：**不含 `content_html`**。

    列表不背正文：一份简历的 HTML 是整页纸（数十 KB），20 个版本就是一次
    几百 KB 的响应，而列表只需要「几号、叫什么、什么时候、是不是链头」。
    要看正文就走 `GET /resumes/versions/diff`（它也只回差异，不回全文）。
    """

    resume_id: str
    version_no: int
    title: str
    origin: ResumeVersionOrigin
    #: 是不是当前链头（`ADR-015` §2.3）。定制版恒为 `False`（`ADR-026` §2.2）。
    is_head: bool
    #: 定制版面向的岗位 id；岗位被删后由 `ON DELETE SET NULL` 置空。
    #: 岗位**标题**不在这里：它已经写进 `title`（快照语义，`ADR-026` §2.1）。
    target_job_id: str | None = None
    created_at: datetime

    @field_serializer("created_at")
    def serialize_shanghai(self, value: datetime) -> str:
        return format_shanghai_datetime(value)


class ResumeVersionList(BaseModel):
    """`GET /resumes/versions` 的 `data`。

    `current_version_id` 单独给一格：前端要能画「链头」徽标，
    而 `versions` 里也带 `is_head`——两处同源，谁都不许自己推。
    """

    profile_id: str
    current_version_id: str | None
    versions: list[ResumeVersionSummary]


class ResumeDiffField(BaseModel):
    """条目里一格字段的前后值（`ADR-026` §2.4 规则 3）。

    `before` / `after` 允许空串：空串表示「这一版里这一格不存在」，
    与「这一格的值是空的」不是一回事——后者在纸面上会显示「待补充：X」。
    """

    field: str
    before: str
    after: str


class ResumeDiffItem(BaseModel):
    """一个条目的差异。`kind` 三态与 `added/removed/changed` 的计数一一对应。"""

    kind: Literal["added", "removed", "changed"]
    #: 锚点文本（项目名 / 学校名 / 证书名 / 技能名）——界面上这一行的标题。
    label: str
    #: 整条的纯文本（新增/删除时展示全文；修改时是修改前后的全文）。
    before: str | None = None
    after: str | None = None
    fields: list[ResumeDiffField] = Field(default_factory=list)


class ResumeDiffSection(BaseModel):
    """一个区块的差异。

    `change=="unchanged"` 的区块**照样返回**：用户需要看到「这一块没动」，
    而不是在列表里找不到它却不知道是没动还是没解析出来。
    """

    key: Literal["educations", "skills", "experiences", "certificates", "portfolio"]
    #: 区块标题（`to` 那一版；区块被删时取 `from` 的）。
    label: str
    change: Literal["added", "removed", "changed", "unchanged"]
    title_before: str | None = None
    title_after: str | None = None
    #: 条目顺序是否变了（`ADR-026` §2.4 规则 4）。按岗位定制的差异**只应**出现在这里。
    order_changed: bool = False
    items: list[ResumeDiffItem] = Field(default_factory=list)


class ResumeDiffSummary(BaseModel):
    """一眼能读完的计数。`sections_changed` 只数 `change != "unchanged"` 的区块。"""

    added: int = 0
    removed: int = 0
    changed: int = 0
    sections_changed: int = 0
    section_order_changed: bool = False
    item_order_changed: bool = False


class ResumeDiff(BaseModel):
    """`GET /resumes/versions/diff` 的 `data`。

    `comparable=False` 时 `sections` 为空且 `note` 必给：**绝不能**用一份空差异
    表示「两版一样」（`ADR-026` §1.1 末）。
    """

    from_version: ResumeVersionSummary
    to_version: ResumeVersionSummary
    comparable: bool
    note: str | None = None
    summary: ResumeDiffSummary
    sections: list[ResumeDiffSection]


class ResumeCustomizeRequest(BaseModel):
    """`POST /resumes/customize` 的请求体。

    `extra="forbid"`：定制会**落一行新版本**，多塞一个 `title` 或 `skills`
    被静默忽略，会让调用方以为自己的输入生效了。
    """

    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = Field(default=None, max_length=36)
    job_id: str = Field(min_length=1, max_length=36)


class ResumeCustomizeFocus(BaseModel):
    """定制版面向哪条岗位，以及「命中/缺失」的对照（`ADR-026` §2.5）。

    `missing` 是**提示不是结论**：它只说「JD 里点了这几样、你的档案里没有」，
    不评价重不重要，也不写进纸面。
    """

    job_id: str
    job_title: str
    matched: list[str]
    missing: list[str]


class ResumeCustomizeResult(BaseModel):
    """定制结果：新版本 + 它基于哪一版 + 岗位对照。

    `based_on_version_no` 是「定制时链头是第几版」。定制**不移动链头**，
    所以它同时也是「现在点『当前简历』还会拿到哪一版」的答案。
    """

    resume: ResumeRead
    based_on_version_no: int
    focus: ResumeCustomizeFocus
