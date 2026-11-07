"""学生档案的请求/响应模型。

写入沿用 T5 已定的边界：技能、经历、教育经历都只限制**上限**，不设下限——
用户反馈要求「保存不拦必填」（草稿式保存），完整性校验挪到「生成简历」时做。

技能名在同一份档案内唯一，评分 1–5，经历/教育的结束日期不得早于开始日期。

T7.5 起教育经历是一对多子表（`educations`）：
学校 / 专业 / 学历 / 起止时间，字段对齐学信网备案表。
`grade`（年级）在库中保留，但已从产品表单下架，请求体里可选、后端忽略。
T7.6 起主表不再有 `major`：专业只由每段教育经历各自携带。
用户反馈轮：新增 `email`（简历上展示的邮箱，与 `users.email` 登录邮箱无关）。

真实用户反馈：
- `contact` 收紧为中国大陆手机号（`^1[3-9]\\d{9}$`），空串仍然放行以便先存草稿；
- 经历的**开始**时间不得晚于「今天」，否则 422。

体验修复（本轮）：**结束**时间放开未来限制。
应届生要写「预计 2027-06 毕业」、在职项目要写「预计 2027-03 结束」，
把结束时间卡在「过去」会直接堵死这两类真实场景。开始时间仍然不许选未来
（没开始的事情不该出现在简历里），结束时间只需不早于开始时间。

保存 422 修复轮：`full_name` 去掉 `min_length=1`（保存是草稿式覆盖，允许空），
完整性改由「生成简历」把关；其余必填日期（`edu_start` / `start_date`）保持
`date` 必填——库里是 `NOT NULL`，前端必须在提交前保证不为空。
"""

import re
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
from app.core.document_style import (
    DEFAULT_THEME,
    LAYOUT_TITLE_MAX_LEN,
    LAYOUT_VERSION,
    SECTION_IDS,
    Section,
    SectionId,
    ThemeId,
)
from app.core.skill_names import skill_name_key
from app.schemas.resume import ExperienceInput, SkillInput

#: 学历枚举，与 `ck_educations_degree` 约束保持一致。
Degree = Literal["专科", "本科", "硕士", "博士"]

#: 纸张尺寸（D5.1）。只做两档：A4 是国内招聘事实标准，Letter 覆盖北美投递。
#: 加第三种纸必须同时改前端 `PAPER_SPECS` 与 `@page` 注入，否则「能选但不能印」。
PaperSize = Literal["A4", "Letter"]

#: 缺省纸张：与迁移里的 `server_default` 和前端 `createEmptyDraft()` 同源。
DEFAULT_PAPER_SIZE: PaperSize = "A4"

#: 配色主题（D5.2）。枚举本体在 `app/core/document_style.py`（含 8 套色值与
#: 实测对比度），这里只做类型引用——色板是「文档样式」的职责，不是 schema 的。
#: 存 id 而不是 hex：任意颜色无法保证对比度，且双端派生必然漂移（ADR-007 §5.2）。
Theme = ThemeId


def normalize_paper_size(value: object) -> PaperSize:
    """把库里的字符串收敛到两档枚举，未知值一律回落 A4。

    读路径**永不因为装饰性字段失败**（ADR-007 §10.4）：手工改库、或将来从
    「支持 Legal」的版本降级回滚，残留的 `Legal` 不该让整份简历 500。
    写路径仍由 pydantic 严格拦截（非法值 422），这个不对称是刻意的。
    """
    return "Letter" if value == "Letter" else DEFAULT_PAPER_SIZE


#: 中国大陆手机号；前端 `resume.ts` 里有一份同源正则，两边必须同步。
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


class LayoutSectionInput(BaseModel):
    """一个模块的排版元信息：id + 标题。**不含内容**（内容永远在子表里）。

    写路径刻意严格（ADR-007 §10.4「写入严格、读取宽松」）：

    - `id` 由 `Literal` 白名单拦截，未知模块 422；
    - `title` 裁掉首尾空白后必须非空，长度 ≤ 12（`LAYOUT_TITLE_MAX_LEN`）——
      空标题会在纸面上留一个空 `h2`，超长标题在 360px 视口会折行把版式撑开；
    - `extra="forbid"`：前端若误传了内容字段（`items` / `school` …），
      宁可 422 也不悄悄忽略——那会让「我明明调了顺序为什么没生效」无从排查。
    """

    model_config = ConfigDict(extra="forbid")

    id: SectionId
    title: str = Field(max_length=LAYOUT_TITLE_MAX_LEN)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, title: str) -> str:
        """压平空白并拒绝空标题；不截断——超长由 `max_length` 报 422 而不是静默改字。"""
        normalized = " ".join(title.split())
        if not normalized:
            raise ValueError("layout section title must not be blank")
        return normalized


class LayoutConfigInput(BaseModel):
    """`PUT` 里的 `layout_config`（完整快照，不是差异）。

    - `version` 只认 `LAYOUT_VERSION`：将来升到 v2 时，旧客户端写 v1 会被拒，
      避免「用旧语义覆盖新结构」；
    - `sections` 允许**少于** 3 段：缺的模块由读侧的 `resolve_layout()` 追加到末尾
      （老客户端不认识新模块时，不该因为少传一项就 422）；
    - 重复 id 直接 422（ADR-007 §6.8(3)）：重复会让前端 `v-for` 的 key 冲突，
      属于用户输入错误，应当报错而不是猜他想表达什么。
    """

    model_config = ConfigDict(extra="forbid")

    version: int
    sections: list[LayoutSectionInput] = Field(min_length=1, max_length=len(SECTION_IDS))

    @field_validator("version")
    @classmethod
    def validate_version(cls, version: int) -> int:
        if version != LAYOUT_VERSION:
            raise ValueError(f"layout_config.version must be {LAYOUT_VERSION}")
        return version

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "LayoutConfigInput":
        ids = [section.id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("layout_config.sections must not contain duplicate ids")
        return self


class LayoutSectionRead(BaseModel):
    id: SectionId
    title: str


class LayoutConfigRead(BaseModel):
    """读侧的 `layout_config`：**恒为解析后的完整配置**，永远不会是 `null`。

    与 `paper_size` / `theme` 的「读侧恒为具体值」同一条口径（ADR-007 §10.2）：
    三态只存在于写侧。这样前端不需要把降级矩阵再实现一遍——
    它拿到的顺序就是生成器会用的顺序。
    """

    version: int = LAYOUT_VERSION
    sections: list[LayoutSectionRead]

    @classmethod
    def from_sections(cls, sections: list[Section]) -> "LayoutConfigRead":
        return cls(
            version=LAYOUT_VERSION,
            sections=[LayoutSectionRead(id=item.id, title=item.title) for item in sections],
        )


class SkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    self_rating: int


class ExperienceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    start_date: date
    end_date: date | None = None
    description: str
    responsibility: str


class EducationInput(BaseModel):
    """一段教育经历；`edu_end` 为 None 表示「至今」（在读）。

    只有 `school` 是「这段经历真的存在」的锚点，必须非空；
    其余字段允许留空以便先存草稿，完整性由前端在生成时统一拦截。

    `edu_end` 允许是未来（应届生填「预计毕业时间」），`edu_start` 不允许。
    """

    school: str = Field(min_length=1, max_length=200)
    major: str = Field(default="", max_length=100)
    degree: Degree
    edu_start: date
    edu_end: date | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> "EducationInput":
        if self.edu_end is not None and self.edu_end < self.edu_start:
            raise ValueError("edu_end must be on or after edu_start")
        if self.edu_start > shanghai_today():
            raise ValueError("edu_start must not be in the future")
        return self


class EducationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    school: str
    major: str
    degree: str
    edu_start: date
    edu_end: date | None = None


class ProfileUpsertRequest(BaseModel):
    """写入档案的请求体。

    T7.6：不再有主表 `major`——专业由 `educations[].major` 表达，
    旧客户端若继续传 `major`，Pydantic 会按默认 `extra="ignore"` 丢弃。

    用户反馈轮：去掉技能/经历的**下限**（原先 3 条 / 1 条），允许先存草稿；
    上限仍然保留，避免一次提交超出简历版式容量。

    保存 422 修复轮：`full_name` 不再是必填。前端把「保存」定义成草稿式整体覆盖
    （见 `frontend/src/stores/resume.ts` 的 `toUpsertPayload()`：空行整行丢弃、
    全空就是一份空档案），后端却要求至少 1 个字符，点「保存修改」直接 422。
    姓名是否填写改为在「生成简历」时判断（`ProfileInput` 仍是 `min_length=1`，
    由 `ResumeService` 提前拦成 400），保存阶段只限制长度。

    D5.1：新增 `paper_size`，**三态语义**（见 ADR-007 §10.2）——

    | 请求体 | 语义 |
    |---|---|
    | 不含该键 | 保留库里原值（旧客户端安全） |
    | `"paper_size": null` | 恢复默认 A4 |
    | `"paper_size": "Letter"` | 设为 Letter |

    实现靠 `model_fields_set`：只有「请求体真的出现过这个键」才算用户意图。
    若按「None 即默认」处理，任何缓存了旧版 JS 的浏览器点一次保存
    就会把用户选的纸张悄悄清回 A4。

    D5.2 的 `theme` 沿用同一套三态语义（不传保留 / null 回 `forest` / 传 id 设置），
    理由完全相同：装饰性字段最容易被旧客户端「顺手清空」。

    D5.3 的 `layout_config` 同样是三态（不传保留 / `null` 恢复默认顺序 / 传快照设置）。
    这里「保留」的意义比前两者更重：旧前端每次保存都会整体覆盖内容，
    若把「没传」当成「重置」，用户调过的顺序与自定义标题会被**静默清掉**。
    """

    full_name: str = Field(default="", max_length=100)
    #: 简历上展示的邮箱；登录邮箱见 `users.email`，两者互不影响。
    email: str | None = Field(default=None, max_length=254)
    #: 年级已下架：允许缺省，服务层不再把它写进库（列保留）。
    grade: str | None = Field(default=None, max_length=50)
    #: 手机号。允许空串（先存草稿），一旦填了就必须是大陆 11 位手机号。
    contact: str = Field(default="", max_length=255)
    #: 纸张尺寸；缺省 = 不改动（不是重置），显式 null = 回到 A4。
    paper_size: PaperSize | None = None
    #: 配色主题（8 套预设之一）；缺省 = 不改动，显式 null = 回到 `forest`。
    theme: Theme | None = None
    #: 排版（顺序 + 自定义标题）；缺省 = 不改动，显式 null = 恢复默认顺序。
    #: 只存元信息：内容仍在 `skills` / `experiences` / `educations` 子表里。
    layout_config: LayoutConfigInput | None = None
    skills: list[SkillInput] = Field(default_factory=list, max_length=8)
    experiences: list[ExperienceInput] = Field(default_factory=list, max_length=3)
    #: 教育经历 0–5 段；为空时不渲染教育区块，缺省值兼容尚未升级的旧客户端。
    educations: list[EducationInput] = Field(default_factory=list, max_length=5)

    @field_validator("contact")
    @classmethod
    def validate_contact(cls, contact: str) -> str:
        """空串合法（草稿式保存）；非空必须是 `^1[3-9]\\d{9}$`。

        为什么不用 `Field(pattern=...)`：pattern 会把空串也判成不合法，
        而产品明确要求「保存档案不拦必填」，只能把格式校验放进校验器里。
        """
        trimmed = contact.strip()
        if not trimmed:
            return ""
        if not PHONE_PATTERN.fullmatch(trimmed):
            raise ValueError("contact must be a mainland China mobile number")
        return trimmed

    @field_validator("skills")
    @classmethod
    def validate_unique_skill_names(cls, skills: list[SkillInput]) -> list[SkillInput]:
        """判定键必须是 `skill_name_key`，不是裸相等。

        裸相等放得过 `Python` / `python`：这一对能过校验、走到 INSERT，然后由
        `uq_skills_profile_name`（建在大小写不敏感的 `utf8mb4_0900_ai_ci` 上）抛
        `IntegrityError 1062`——用户拿到的是内部错误，而正确答案是一句「技能名称重复」。
        同一个问题在写侧与提取侧已经是同一个函数（ADR-011 §2.D.2），这里是漏掉的一处。
        """
        keys = [skill_name_key(skill.name) for skill in skills]
        if len(keys) != len(set(keys)):
            raise ValueError("skill names must be unique")
        return skills

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str | None) -> str | None:
        """空串等同「没填」，避免库里出现半空不空的邮箱。"""
        if email is None:
            return None
        trimmed = email.strip()
        return trimmed or None


class ProfileCreateRequest(BaseModel):
    """新建一份简历（D4.1 · 多份简历）。

    - `title` 省略或全空白时由服务层递增生成（「我的简历」「我的简历 2」…）；
    - `copy_from_id` 非空时做**服务端深拷贝**（基本信息 + 全部技能/经历/教育），
      不走「前端 GET 再 PUT」——那会在两次请求之间留下源简历被改的竞态；
    - `is_default` 为真时把新建的这份设为默认，其余未删除简历自动取消默认。
    """

    title: str | None = Field(default=None, max_length=200)
    copy_from_id: str | None = Field(default=None, max_length=36)
    is_default: bool = False


class ProfileTitleUpdate(BaseModel):
    """重命名请求（D4.3）。

    - 两端空白在服务端裁掉（前端也裁，但不能只依赖前端）；
    - 裁完为空按 422 拒绝：空标题会让下拉列表认不出这是哪一份；
    - 重名判断不在这里做——需要查库，属于服务层，冲突返回 400。
    """

    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, title: str) -> str:
        trimmed = title.strip()
        if not trimmed:
            raise ValueError("title must not be blank")
        return trimmed


class ProfileSummary(BaseModel):
    """列表 / 回收站用的轻量结构。

    刻意不带 `skills` / `experiences` / `educations`：列表只需要区分「哪一份」，
    把整份简历搬进列表会让「打开档案库」变成一次全量读取。
    """

    id: str
    title: str
    is_default: bool
    full_name: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @field_serializer("created_at", "updated_at")
    def serialize_shanghai(self, value: datetime) -> str:
        return format_shanghai_datetime(value)

    @field_serializer("deleted_at")
    def serialize_deleted_at(self, value: datetime | None) -> str | None:
        """回收站里的删除时间同样按东八区返回；未删除返回 `null`。"""
        return None if value is None else format_shanghai_datetime(value)


class ProfileRead(BaseModel):
    id: str
    user_id: str
    #: D4.1：这份简历的名字与默认标记（列表里区分「投后端」「投算法」）。
    title: str
    is_default: bool
    #: 非空 = 在回收站里；详情接口查询时已排除，留着是为了回收站能如实回显。
    deleted_at: datetime | None = None
    #: D5.1：纸张尺寸。**读侧恒为具体值**（库里 NOT NULL + server_default）——
    #: 三态只存在于写侧，读接口给 `null` 只会让前端多一层兜底，没有任何收益。
    paper_size: PaperSize = DEFAULT_PAPER_SIZE
    #: D5.2：配色主题，同上，读侧恒为 8 套之一（库里非法值已在服务层收敛）。
    theme: Theme = DEFAULT_THEME
    #: D5.3：排版。读侧**恒为解析后的完整配置**（永不 null），
    #: 脏数据在 `resolve_layout()` 里已退化为默认顺序，前端拿到即可直接渲染。
    layout_config: LayoutConfigRead
    full_name: str
    email: str | None = None
    grade: str
    contact: str
    created_at: datetime
    updated_at: datetime
    skills: list[SkillRead]
    experiences: list[ExperienceRead]
    educations: list[EducationRead] = Field(default_factory=list)

    @field_serializer("created_at", "updated_at")
    def serialize_shanghai(self, value: datetime) -> str:
        """库里是 naive UTC，返回前统一转东八区（见 `app/core/datetime_display.py`）。"""
        return format_shanghai_datetime(value)

    @field_serializer("deleted_at")
    def serialize_deleted_at(self, value: datetime | None) -> str | None:
        return None if value is None else format_shanghai_datetime(value)
