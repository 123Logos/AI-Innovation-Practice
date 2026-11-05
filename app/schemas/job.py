"""岗位库的请求/响应模型（Phase 4 · B1，ADR-011 1.B / 1.D / 2.A / 2.F / 2.G）。

与 `schemas/profile.py`、`schemas/resume.py` 同风格：写路径严格（非法值 422），
读路径给具体值。

三条与 ADR-011 1.F 绑定的口径：

1. **请求体里没有 `status` / `created_by_user_id` / `origin`，也没有 `evidence_text` /
   `confidence`** —— 身份与归属只来自 `get_current_user`，`POST /jobs` 一律落
   `status='draft'`，每条技能的 `origin` 恒为 `unverified`、`evidence_text` 与 `confidence`
   恒为 `NULL`（都由服务端写死；后两者是 AI 提取产物，只能由 C 轮的提取路径写，
   ADR-011 1.F 的 v6 补条）。客户端就算把这些键塞进 JSON，Pydantic 默认
   `extra="ignore"` 会整键丢掉，落不到仓储（先例：
   `tests/test_resumes_generate.py` 的「忽略请求体里的 profile 字段」）。
   这里刻意**不**用 `extra="forbid"`：那是「报错」而不是「忽略」，而旧客户端 /
   手工调试会把整个对象原样 POST 回来，直接 422 会让「忽略」这条安全规则
   变成可用性回归。丢掉危险字段、放过无害字段，才是 1.F 要的效果。
2. **`description` ≤ 8000 字**（2.F 的输入上限），超限 422；
   `title` / `company_name` ≤ 200，与列宽一致。
3. **薪资两端都选填**，只在「都填了」时比较（与 `ck_jobs_salary_range` 同一条规则），
   本轮不做薪资筛选（1.G）。

`salary_min` / `salary_max` 用 `Decimal` 而不是 `float`：库列是 `DECIMAL(12,2)`，
`float` 会把 8000.1 存成 8000.099999…，读回来看起来像数据被写脏了。
Pydantic 在 `model_dump(mode="json")` 下把 `Decimal` 输出为**字符串**（`"8000.00"`），
前端按字符串展示即可，不会有二进制浮点的尾数噪声。
"""

import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.core.datetime_display import format_shanghai_datetime
from app.repositories.job_repository import MAX_SKILLS_PER_JOB

#: 岗位状态（`ck_jobs_status`）。只有两态，审核态在 1.A 里被明确否决。
JobStatus = Literal["draft", "published"]

#: 工作方式（`ck_jobs_employment_type`）。1.G 说明本轮不含 `work_mode`（远程/驻场）。
EmploymentType = Literal["internship", "full_time", "part_time", "contract", "other"]

#: 技能分类（`ck_job_skills_type`）。可空：AI 的分级不可信，宁可为空让用户填。
SkillType = Literal["hard", "soft", "domain"]

#: 技能条目的「能否回溯到 JD 原文」标记（`ck_job_skills_origin`）。**只用于响应**，
#: 请求体里没有这个字段。值域按 ADR-011 1.D v7（方案 a）命名：这一列只证明
#: 「这句话在 JD 原文里出现过」，不证明「这句话是谁写的」，所以值名不带任何作者含义。
SkillOrigin = Literal["jd_verifiable", "unverified"]

#: JD 原文上限（ADR-011 2.F）。超出由 Pydantic 报 422，不发往任何外部服务。
MAX_DESCRIPTION_LENGTH = 8000

#: 列表分页默认值与上限（议题 3.C「分页参数后端一次做对」，前端首屏拉 50 条）。
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

#: 薪资列宽 `DECIMAL(12,2)` 的可表达上界，提前在应用层拦住，
#: 免得超出列宽的输入撞成一条数据库 1264 而不是可读的 422。
MAX_SALARY = Decimal("99999999.99")


class JobSkillInput(BaseModel):
    """一条岗位技能（写入）。

    只有 `name` 与 `type` 两格。**没有 `origin` 字段**：它由服务端派生
    （本轮恒 `unverified`），客户端提交的 `origin` 会被整键丢掉（模块 docstring 第 1 条）。

    **也没有 `evidence_text` / `confidence`**（ADR-011 1.F 的 v6 补条）：这两列语义上是
    AI 提取的产物 —— `evidence_text` 是 C 轮反幻觉核验的锚点（2.D，列宽 60 与 `String(60)`
    一致），`confidence` 是模型自报的置信度（PRD FR-03 L105）。在没有 AI 的写入路径上
    接受客户端填值，只会落下无法核验的数据，还与「`origin` 恒为 `unverified`」自相矛盾
    （未标注出处的条目却带着「原文依据」）。因此这里从请求模型里**删掉**这两个字段：
    它们由仓储写死为 `NULL`，只能在 C 轮由服务端在真正做过核验的提取路径上写入。
    """

    name: str = Field(min_length=1, max_length=100)
    type: SkillType | None = None

    @model_validator(mode="after")
    def normalize_name(self) -> "JobSkillInput":
        """技能名两端空白裁掉：` Python ` 与 `Python` 是同一个技能。

        不去做大小写归一：唯一性由数据库的 `utf8mb4_0900_ai_ci` 保证（1.D），
        应用层再归一会让展示出来的名字失去用户原本的大小写。
        """
        stripped = self.name.strip()
        if not stripped:
            raise ValueError("skill name must not be blank")
        self.name = stripped
        return self

    @property
    def dedup_key(self) -> str:
        """与应用层重名判断同口径的键：NFKC（全角→半角）+ casefold。

        这是 ADR-011 §2.D.1 归一化函数的**子集**（去掉空白与标点的那两步留给 C 轮的
        evidence 核验）：技能名里 `C++` 的 `+` 是名字的一部分，
        删标点会把 `C++` 与 `C` 混为一谈。数据库那侧由排序规则负责同样的宽容度，
        这里只是把它的报错提前成一条可读的 422。
        """
        return unicodedata.normalize("NFKC", self.name).casefold()


class JobWriteRequest(BaseModel):
    """`POST /jobs` 与 `PUT /jobs/{job_id}` 共用的请求体。

    两者形状一致（ADR-011 2.A 的草图里 PUT 是「改字段与技能」），
    分成两个模型只会让「PUT 少校验了一个字段」这种漂移无处可查。

    必填只有三个：`company_name` / `title` / `description` —— 它们才是
    「这个岗位真的存在」的锚点；其余选填，与 1.G 的「存而不筛」一致。
    """

    company_name: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=160)
    employment_type: EmploymentType | None = None
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
    salary_min: Decimal | None = Field(default=None, ge=0, le=MAX_SALARY)
    salary_max: Decimal | None = Field(default=None, ge=0, le=MAX_SALARY)
    experience_required: str | None = Field(default=None, max_length=100)
    #: 截止时间。允许填**过去**的日期：过期是读侧判定（2.G），不是写入错误，
    #: 拦掉过去日期会让「补录一个已结束的岗位」变成不可能。
    deadline: date | None = None
    skills: list[JobSkillInput] = Field(default_factory=list, max_length=MAX_SKILLS_PER_JOB)

    @model_validator(mode="after")
    def validate_salary_range(self) -> "JobWriteRequest":
        """两端都填了才比较，与 `ck_jobs_salary_range` 同一条规则。

        放在应用层而不是只靠数据库：数据库报错是一条 500/1264，
        而用户在表单里填反了区间，要的是可读的 422。
        """
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min must not be greater than salary_max")
        return self

    @model_validator(mode="after")
    def validate_unique_skill_names(self) -> "JobWriteRequest":
        """一次提交里的重名技能先报 422。

        数据库的 `UNIQUE (job_id, name)` 也会拦，但那是一条 500（1062）：
        用户在表单里多打了一行同名技能，要的是「技能名称重复」而不是服务异常。

        **比较口径必须和列的排序规则一致**（`utf8mb4_0900_ai_ci`：大小写、重音、
        全半角都不敏感），所以这里先 NFKC + casefold 再比。若用裸字符串比较，
        `Python` / `python` / `Ｐython` 会被应用层判成三个不同技能放进 payload，
        数据库却在第二行报错 —— 应用层与数据库对「同一个技能」的判断不一致，
        用户看到的就是一条他无法理解的失败。
        """
        seen: set[str] = set()
        for skill in self.skills:
            key = skill.dedup_key
            if key in seen:
                raise ValueError("skill names must be unique")
            seen.add(key)
        return self


class JobSkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: str | None = None
    #: 读侧给原始字符串（`jd_verifiable` / `unverified`），前端按两态渲染徽标
    #: （议题 3.B：徽标文案是「可回溯原文」/「未标注出处」，**不得出现「AI 提取」字样**）。
    origin: SkillOrigin
    #: 本轮恒为 `NULL`：写侧不接受这两个字段（见 `JobSkillInput`），
    #: 读接口先给出来，前端不需要为「字段将来才有值」写两套代码。
    confidence: Decimal | None = None
    evidence_text: str | None = None


class JobSummary(BaseModel):
    """列表项（议题 3.B 的卡片：企业名 + 岗位名 + 类型 + 截止时间 + 技能前 6 条）。

    刻意不含 `description`：JD 原文最长 8000 字，列表带它就等于把广场首页变成
    一次全量文本传输。
    """

    id: str
    company_name: str
    title: str
    location: str | None = None
    employment_type: str | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    deadline: date | None = None
    status: JobStatus
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    skills: list[JobSkillRead] = Field(default_factory=list, max_length=6)
    #: 服务层用应用时钟算出的过期结论（2.G 与详情页同一判据）。
    #: 前端因此不需要再实现一遍「deadline < 今天」——两处各写一份必然漂移。
    is_expired: bool

    @field_serializer("created_at", "updated_at")
    def serialize_shanghai(self, value: datetime) -> str:
        return format_shanghai_datetime(value)

    @field_serializer("deleted_at")
    def serialize_deleted_at(self, value: datetime | None) -> str | None:
        return None if value is None else format_shanghai_datetime(value)


class JobRead(BaseModel):
    """岗位详情：全字段 + 全部技能。"""

    id: str
    company_name: str
    title: str
    location: str | None = None
    employment_type: str | None = None
    description: str
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    experience_required: str | None = None
    deadline: date | None = None
    status: JobStatus
    created_by_user_id: str
    #: 本轮恒为 `null`：写入方是 C 轮的提取端点（PRD FR-03 L105）。
    #: 读接口先给出来，是为了让前端不需要为「字段将来才存在」写两套代码。
    extract_model: str | None = None
    extract_prompt_version: str | None = None
    extracted_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    skills: list[JobSkillRead] = Field(default_factory=list)
    #: 议题 3.B：详情页「凡 `deadline < 今天` 一律显示已过期」，
    #: 不绑定列表参数、也不区分查看者身份——判据和列表是同一个。
    is_expired: bool

    @field_serializer("created_at", "updated_at")
    def serialize_shanghai(self, value: datetime) -> str:
        return format_shanghai_datetime(value)

    @field_serializer("extracted_at")
    def serialize_extracted_at(self, value: datetime | None) -> str | None:
        return None if value is None else format_shanghai_datetime(value)

    @field_serializer("deleted_at")
    def serialize_deleted_at(self, value: datetime | None) -> str | None:
        return None if value is None else format_shanghai_datetime(value)
