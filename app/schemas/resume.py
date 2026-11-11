"""简历相关的 Pydantic 模型。

`ProfileInput` / `SkillInput` / `ExperienceInput` 是**生成器契约**：
`app/integrations/resume_generator.py` 直接消费它们，因此保留在原位置，
不再作为生成接口的请求体（T7.2 起 `/resumes/generate` 只收 title，数据一律来自数据库）。
ADR-010 新增的 `ResumePolishRequest` 是例外：润色端点收的是**前端草稿态数据**，
纯计算、不落库、不采信它做任何持久化或权限判断。
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, model_validator

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
    """

    name: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def normalize_name(self) -> "SkillInput":
        stripped = self.name.strip()
        if not stripped:
            raise ValueError("skill name must not be blank")
        self.name = stripped
        return self


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
