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
from app.schemas.resume import ExperienceInput, SkillInput

#: 学历枚举，与 `ck_educations_degree` 约束保持一致。
Degree = Literal["专科", "本科", "硕士", "博士"]

#: 中国大陆手机号；前端 `resume.ts` 里有一份同源正则，两边必须同步。
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


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
    """

    full_name: str = Field(default="", max_length=100)
    #: 简历上展示的邮箱；登录邮箱见 `users.email`，两者互不影响。
    email: str | None = Field(default=None, max_length=254)
    #: 年级已下架：允许缺省，服务层不再把它写进库（列保留）。
    grade: str | None = Field(default=None, max_length=50)
    #: 手机号。允许空串（先存草稿），一旦填了就必须是大陆 11 位手机号。
    contact: str = Field(default="", max_length=255)
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
        names = [skill.name for skill in skills]
        if len(names) != len(set(names)):
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
