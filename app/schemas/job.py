"""岗位库的请求/响应模型（Phase 4 · B1 + C1，ADR-011 1.B / 1.D / 2.A / 2.C / 2.D / 2.F / 2.G）。

与 `schemas/profile.py`、`schemas/resume.py` 同风格：写路径严格（非法值 422），
读路径给具体值。

三条与 ADR-011 1.F 绑定的口径：

1. **请求体里没有 `status` / `created_by_user_id` / `origin` / `extract_model` /
   `extract_prompt_version` / `extracted_at`** —— 身份与归属只来自 `get_current_user`，
   `POST /jobs` 一律落 `status='draft'`，每条技能的 `origin` 由服务端按 1.D 派生，
   三列提取溯源戳由服务端盖（v9 的第 ② 条）。客户端就算把这些键塞进 JSON，Pydantic 默认
   `extra="ignore"` 会整键丢掉，落不到仓储（先例：
   `tests/test_resumes_generate.py` 的「忽略请求体里的 profile 字段」）。
   这里刻意**不**用 `extra="forbid"`：那是「报错」而不是「忽略」，而旧客户端 /
   手工调试会把整个对象原样 POST 回来，直接 422 会让「忽略」这条安全规则
   变成可用性回归。丢掉危险字段、放过无害字段，才是 1.F 要的效果。
   **`evidence_text` 与 `confidence` 在 v9 起回到请求体里**（1.F 的 v9 放开条）：
   用户是在草稿态逐条确认过 AI 结果才点保存的，这两格必须存得进去，
   否则 1.D 的派生与 §2.D.1 的核验无处落地。它们不再是「够不到的列」，
   但 `origin` 与三列溯源戳依然够不到。
2. **`description` ≤ 8000 字**（2.F 的输入上限），超限 422；
   `title` / `company_name` ≤ 200，与列宽一致。
3. **薪资两端都选填**，只在「都填了」时比较（与 `ck_jobs_salary_range` 同一条规则），
   本轮不做薪资筛选（1.G）。

`salary_min` / `salary_max` 用 `Decimal` 而不是 `float`：库列是 `DECIMAL(12,2)`，
`float` 会把 8000.1 存成 8000.099999…，读回来看起来像数据被写脏了。
Pydantic 在 `model_dump(mode="json")` 下把 `Decimal` 输出为**字符串**（`"8000.00"`），
前端按字符串展示即可，不会有二进制浮点的尾数噪声。
`job_skills.confidence` 同口径用 `Decimal`（列是 `DECIMAL(5,4)`）。

C1 另加提取端点的四个模型（`ExtractSkillsRequest` / `ExtractedSkillRead` /
`ExtractSkillsMeta` / `ExtractSkillsResult`）：提取是**纯计算**（2.A），
所以它们的字段只描述响应，不参与任何持久化。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.core.datetime_display import format_shanghai_datetime
from app.core.skill_names import skill_name_key
from app.integrations.skill_extractor import MAX_EVIDENCE_CHARS
from app.repositories.job_repository import MAX_SKILLS_PER_JOB

#: 岗位状态（`ck_jobs_status`）。`ADR-011` 1.B 的两态由 `ADR-024` §2.1 扩成四态：
#: `pending_review`（待审核）与 `rejected`（已驳回）是审核流补的，
#: **没有 `approved`**（通过即上架）、**没有 `expired`**（过期是读侧判定）。
JobStatus = Literal["draft", "pending_review", "published", "rejected"]

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

    四格：`name` / `type` / `evidence_text` / `confidence`。**后两格依据 ADR-011 v9 放开**
    （v6 曾把它们从请求体里删掉、由仓储写死 `NULL`，理由是「没有 AI 的路径上接受填值只会
    落下无法核验的数据」；C1 的提取链路接通后，用户确实是在草稿态逐条确认过 AI 结果才保存，
    这两格存不进去就等于 1.D 的派生与 §2.D.1 的核验无处落地）。

    **依然没有 `origin` 字段**：它由服务端按 1.D 现场派生（`evidence_text` 归一化后是否是
    JD 原文的子串），客户端提交的 `origin` 会被整键丢掉（模块 docstring 第 1 条）。
    放开这两格不会削弱「可回溯原文」徽标的可信度，因为派生用的锚点是这条自己带的
    `evidence_text`，而它是否与 `description` 对得上由服务端判定，不由提交者声称。

    `confidence` 越界（<0 或 >1）与 `evidence_text` 超 60 字在这里就是 **422**
    （v9 的第 ③ 条，取代 v6 的「静默丢弃」）：Pydantic 先于数据库 CHECK 拦下来，
    用户拿到的是一条可读的校验错误，而不是一条 500。
    """

    name: str = Field(min_length=1, max_length=100)
    type: SkillType | None = None
    #: 模型给的 JD 原文片段（2.D 的核验锚点）；列宽 `String(60)` 与这里的上限同一个数。
    evidence_text: str | None = Field(default=None, max_length=MAX_EVIDENCE_CHARS)
    #: 模型自报的把握（PRD FR-03 L105）；列是 `DECIMAL(5,4)`，取值 0–1。
    confidence: Decimal | None = Field(default=None, ge=0, le=1)

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

    @model_validator(mode="after")
    def normalize_evidence_text(self) -> "JobSkillInput":
        """纯空白的 `evidence_text` 归一成 `None`，与库里「没有原文依据」同一表示。

        为什么要归一而不是照存：`"   "` 归一化之后是空串，而空串是任何文本的子串，
        照存下去这条就会被 1.D 派生成「可回溯原文」——徽标凭空出现。
        `is_traceable()` 里已经挡掉了空锚点，这里再把它折成 `NULL`，
        是为了让库里根本不存在第二种「没有依据」的写法（`NULL` 与空白串）。
        """
        if self.evidence_text is not None:
            evidence = self.evidence_text.strip()
            self.evidence_text = evidence or None
        return self

    @property
    def dedup_key(self) -> str:
        """与应用层重名判断同口径的键，实现只有一个：`skill_name_key`。

        口径为什么只有两步（NFKC + casefold，不删标点）、以及为什么和 §2.D.1 的
        归一化必须是两个函数，全部写在 `skill_name_key` 上：这里再抄一遍，
        两处就会各漂移各的。
        """
        return skill_name_key(self.name)


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
    #: C1 起有值：写侧依据 ADR-011 v9 放开这两格，用户保存提取结果时它们跟着落库。
    #: 没带 `evidence_text` 的手动条目仍然是 `NULL`，所以类型依旧可空。
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
    #: 驳回理由（`ADR-024` §2.6）。只有 `rejected` 时有值；创建人与审核人看得到，
    #: 其他人连这条岗位都读不到（可见性见 `JobRepository.get_readable`）。
    review_note: str | None = None
    #: 三列提取溯源戳，**由服务端盖，不采信客户端**（ADR-011 v9 第 ② 条）：
    #: 本次保存里只要有任何一条技能被判为 `jd_verifiable`，服务层就写入
    #: `extract_model` = 固定模型、`extract_prompt_version` = `EXTRACT_PROMPT_VERSION`、
    #: `extracted_at` = 应用时钟的当前 UTC 时间；一条都没有则三列保持 `NULL`。
    #: 客户端提交的同名字段在 Pydantic 那一层就整键丢掉（模块 docstring 第 1 条）。
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


# --------------------------------------------------------------------------- #
# C1：`POST /jobs/extract-skills`（ADR-011 2.A / 2.C / 2.D / 2.F）
#
# 这四个模型**只描述一次计算的两端**，不参与任何持久化：岗位此刻还没落库（2.A），
# 用户逐条删改之后走的是上面那套 `JobWriteRequest`。
# --------------------------------------------------------------------------- #


class ExtractSkillsRequest(BaseModel):
    """提取请求体：只有 JD 原文与可选岗位名。

    两个输入上限都是 2.F 的硬规定（`description` ≤ 8000、`title` ≤ 200），
    超限由 Pydantic 报 422 而**不发往任何外部服务**：Prompt 体积要兜住（8000 字已经
    是「整份 JD 粘进来」的量级），而 BYOK 下每个字节都花用户的钱。

    没有 `skills` / `origin` / `confidence` 之类的格子：本端点只读不写，
    把落库字段放进请求体只会给「提取顺手改库」留下想象空间。
    """

    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
    title: str | None = Field(default=None, max_length=200)


class ExtractedSkillRead(BaseModel):
    """提取响应里的一条技能（2.C 的输出契约）。

    字段名是 `evidence` 而不是 `evidence_text`：这里是「模型给的原话」，
    还没有经过服务端的落库判定；用户在草稿态确认后保存时，同一串文字走
    `JobSkillInput.evidence_text` 进列。两个名字都出自 ADR-011（2.C 与修订清单 #2）。

    `type` 可空是 2.D 第 5 条的结果：枚举外的值保留该条、把 `type` 置 NULL，
    让前端显示「未分类」并由用户选，而不是悄悄丢掉一条真实的技能。
    """

    name: str
    type: SkillType | None = None
    evidence: str
    confidence: float | None = None


class ExtractSkillsMeta(BaseModel):
    """核验计数与溯源信息（2.D「核验计数必须可见」——本端点最重要的一条产品承诺）。

    七个字段逐个为什么必须在（`docs/API.md` 修订清单 #3 要求逐字段写进契约，
    缺一份字段契约，将来只剩端点名，语义会丢）：

    - `model` / `prompt_version`：这一批结果是哪一版指令产出的（PRD FR-03 L105）；
    - `received_count`：模型一共给了几条；
    - `dropped_count`：其中几条因**无法回溯 JD 原文**被剔除——前端把它读成
      「AI 给出 20 条，其中 14 条无法在 JD 原文中找到依据，已剔除」，
      也是「子串核验误伤合理归纳」唯一的可读数来源（无凭据环境下测不出来，只能靠它）；
    - `deduped_count`：其中几条与前面的条目重名、被合并掉——前端读成「另有 N 条重名已合并」。
      它**必须与 `dropped_count` 分开**而不是并成一个数：两者是两种不同的失败，用户看到的
      下一步动作也不同——「无法回溯原文」意味着该怀疑核验规则或换一份 JD，
      「重名合并」什么都不用做（模型自己把同一个技能拆成两种写法，合并正是我们想要的）。
      并成一个数会让那句「什么都不用做」被混进那句「该怀疑规则」；
    - `truncated`：条数封顶（15）是否真的切掉了东西。**只在确实切的时候为真**：
      给了 20 条但 12 条没通过核验、剩下 12 条全部返回时，并没有「只保留前 15 条」，
      此时报 `true` 会让前端说出一句假话；
    - `finish_reason`：上游给的原因，用来把第 7 情形（输出被服务端的 token 上限切断）
      与第 4 情形（模型不守格式）分开——两者的用户动作不同。
    """

    model: str
    prompt_version: str
    received_count: int
    dropped_count: int
    deduped_count: int
    truncated: bool
    finish_reason: str | None = None


class ExtractSkillsResult(BaseModel):
    """提取响应体：`{skills, meta}`（2.A）。

    `skills` 至少一条：一条都不剩时服务层不会返回这个模型，而是抛
    `ResumeCopyUnavailableError`（502）——「静默返回空结果」在七条失败语义里被明令禁止。
    """

    skills: list[ExtractedSkillRead] = Field(min_length=1)
    meta: ExtractSkillsMeta


# --------------------------------------------------------------------------- #
# B6 另一半：审核动作的请求体（`ADR-024` §2.6）
#
# 只有两格：审哪条走路径参数，`decision` 决定落到哪个状态，`note` 是驳回理由。
# 刻意不做 `JobReviewPatch` 这种「部分更新」模型：审核是一次完整判定，
# 没有「只改理由」这种动作——要改就重新驳一次，审计里因此永远是一次决定一条记录。
# --------------------------------------------------------------------------- #


class JobReviewRequest(BaseModel):
    """审核请求体：`approve` → `published`，`reject` → `rejected`（带理由）。

    `note` 上限 200 与列宽一致；通过时传了也会被服务层丢掉（`review_note` 只在
    驳回时有意义），所以前端不需要在切换按钮时清空输入框。
    """

    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=200)
