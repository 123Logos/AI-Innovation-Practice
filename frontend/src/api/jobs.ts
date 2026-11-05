/**
 * 岗位库接口：`app/api/v1/jobs.py` 的十条路径（ADR-011 1.F / 2.A / 2.D / 2.F / 议题 3.C）。
 *
 * 命名与写法照 `api/profiles.ts`：复数模块名、`http` 实例、`unwrap()` 拆信封，
 * 错误一律由 `client.ts` 的拦截器归一化成 `ApiError`，这里不自造第二套错误处理。
 *
 * **本文件有一条 AI 调用**：`extractJobSkills()` → `POST /jobs/extract-skills`（C1 已上线）。
 * 它是纯计算端点（ADR-011 2.A：既不读库也不写库），结果只回显成草稿、由用户逐条确认后
 * 再走 `POST/PUT /jobs` 落库。`X-DeepSeek-Key` 的注入范围由 `client.ts` 的白名单单独决定，
 * 这里不手工加头——反过来说，**新增任何 `/jobs*` 端点都不会自动带上 Key**。
 *
 * 写侧的形状是本轮最容易写错的地方，三条口径直接抄后端 `app/schemas/job.py`：
 *
 * 1. `JobWriteRequest` **只有** `company_name` / `title` / `location` / `employment_type` /
 *    `description` / `salary_min` / `salary_max` / `experience_required` / `deadline` / `skills`。
 *    请求模型里没有 `status`、没有 `created_by_user_id`（身份只来自 JWT，`POST /jobs`
 *    一律落 `draft`），技能条目里也**没有** `origin`（服务端按 JD 原文现场派生，1.D）。
 *    技能的 `evidence_text` / `confidence` **自 ADR-011 v9 起回到请求体**：用户是在草稿态
 *    逐条确认过 AI 结果才点保存的，这两格存不进去，1.D 的派生与 §2.D.1 的核验就无处落地。
 *    后端用 Pydantic 默认 `extra="ignore"` 把不认识的键整丢掉，所以「多传了」不会报错——
 *    **不报错正是风险所在**：前端如果图省事把读模型原样 PUT 回去，`origin` 与三列溯源戳
 *    就静静地被丢弃，用户以为存进去了。因此这里把写侧技能类型收成 `JobSkillInput`，
 *    并由 `stores/job.ts` 显式投影，`api/__tests__/jobs.spec.ts` 钉正反两条断言。
 * 2. `salary_min` / `salary_max` 是 `DECIMAL(12,2)`：后端读出来是**字符串**（`"8000.00"`），
 *    写进去也按字符串发（Pydantic 按字符串构造 Decimal，精确；发 number 会引入
 *    `8000.1 → 8000.09999…` 这类尾数噪声）。所以这里两端都是 `string`。
 *    `confidence` 同理（列是 `DECIMAL(5,4)`）：读侧是字符串 `"0.9000"`，写侧按字符串回传；
 *    **提取响应例外**——那里的 `confidence` 是模型自报的裸 `number`（`float | null`，
 *    未经列宽约束），见 `ExtractedSkill`。
 * 3. `deadline` / 日期类字段是 `YYYY-MM-DD` 纯日期串，不带时刻与时区。
 *
 * 列表的 `skills` 由后端截到前 6 条（`JobSummary` 的 `max_length=6`），
 * 且**不含 `description`**（JD 最长 8000 字）——卡片要显示的就这些，详情另发一次 GET。
 */

import { http, unwrap } from './client'
import type { ApiMeta, ApiSuccess } from './types'

/* -------------------------------- 枚举与常量 ------------------------------- */

/** 岗位状态（`ck_jobs_status`，后端 `JobStatus`）。只有两态，没有审核态。 */
export type JobStatus = 'draft' | 'published'

/** 工作方式（`ck_jobs_employment_type`，后端 `EmploymentType`）。本轮不含远程/驻场。 */
export type EmploymentType = 'internship' | 'full_time' | 'part_time' | 'contract' | 'other'

/** 技能分类（`ck_job_skills_type`，后端 `SkillType`）。可空：分类是可选填写项。 */
export type JobSkillType = 'hard' | 'soft' | 'domain'

/**
 * 技能条目的「能否回溯到 JD 原文」标记（`ck_job_skills_origin`，后端 `SkillOrigin`）。
 *
 * **只出现在读模型里**：请求体里没有这个键，客户端提交任何值都会被整丢掉（1.D）。
 * 值由服务端现场派生——该条 `evidence_text` 与岗位 `description` 各过四步归一化
 * （NFKC → casefold → 去空白 → 去标点）后，前者是后者的子串 → `jd_verifiable`，否则 `unverified`。
 * 所以「手填且没写依据」的条目是 `unverified`，而**用户从 AI 结果里确认保存**下来的条目
 * 才可能拿到 `jd_verifiable`；徽标文案也因此刻意不提「谁写的」。
 */
export type JobSkillOrigin = 'jd_verifiable' | 'unverified'

/** 后端 `MAX_DESCRIPTION_LENGTH`（ADR-011 2.F 的输入上限），与录入页的计数器同源。 */
export const MAX_JOB_DESCRIPTION_LENGTH = 8000

/** 后端 `MAX_SKILLS_PER_JOB`（`app/repositories/job_repository.py`），超出 422。 */
export const MAX_JOB_SKILLS = 15

/** 后端 `JobWriteRequest` 的列宽上限，表单的 `maxlength` 用同一组数字。 */
export const JOB_TEXT_LIMIT = {
  company: 200,
  title: 200,
  location: 160,
  experience: 100,
  skillName: 100,
} as const

/**
 * 前端首屏一次拉 50 条（议题 3.C）。
 *
 * 后端默认 20、上限 100（`MAX_PAGE_SIZE`）；50 是「一轮翻不到两页、
 * 又不至于把 8000 字段文的列表整表搬回来」的折中，且明确落在后端允许区间内。
 */
export const JOB_LIST_PAGE_SIZE = 50

/* --------------------------------- 读模型 --------------------------------- */

/** 后端 `JobSkillRead`：比写侧多 `id` / `origin`；`evidence_text` / `confidence` 两侧都有。 */
export interface JobSkillRead {
  id: string
  name: string
  /** 后端 `type: str | None`；读侧给原始字符串，不在这里做枚举收敛。 */
  type: string | null
  origin: JobSkillOrigin
  /**
   * 模型自报的把握（PRD FR-03 L105）。列是 `DECIMAL(5,4)`，所以 JSON 里是**字符串**
   * （`"0.9000"`）；没带 `confidence` 的手动条目是 `null`。
   */
  confidence: string | null
  /**
   * JD 原文片段（§2.D.1 的核验锚点，列宽 `String(60)`）。`null` = 没有依据，
   * 详情页此时**整块不渲染**展开区，而不是渲染一句「无依据」的占位。
   */
  evidence_text: string | null
}

/** 后端 `JobSummary`（列表项 / 回收站行）：刻意没有 `description`。 */
export interface JobSummary {
  id: string
  company_name: string
  title: string
  location: string | null
  employment_type: string | null
  salary_min: string | null
  salary_max: string | null
  /** `YYYY-MM-DD`；`null` = 长期有效。 */
  deadline: string | null
  status: JobStatus
  created_by_user_id: string
  /** 东八区 ISO 串（后端 `format_shanghai_datetime`）。 */
  created_at: string
  updated_at: string
  /** 非空 = 在回收站里。 */
  deleted_at: string | null
  /** 后端已截到前 6 条。 */
  skills: JobSkillRead[]
  /** 服务端用应用时钟算好的过期结论（2.G），前端不再自己比一次「deadline < 今天」。 */
  is_expired: boolean
}

/** 后端 `JobRead`（详情）：全字段 + 全部技能。 */
export interface JobRead {
  id: string
  company_name: string
  title: string
  location: string | null
  employment_type: string | null
  /** JD 原文，最长 8000 字。 */
  description: string
  salary_min: string | null
  salary_max: string | null
  experience_required: string | null
  deadline: string | null
  status: JobStatus
  created_by_user_id: string
  /**
   * 三列提取溯源戳：**由服务端盖，不采信客户端**（ADR-011 1.F v9 第 ② 条）。
   * 本次保存的技能里只要有任何一条被判为 `jd_verifiable`，三列一起写入
   * （`"deepseek-flash"` / `"v1"` / 应用时钟 UTC）；一条都没有则三列都是 `null`。
   * `PUT` 会整批重算，所以「读回来再 PUT 回去」并不会保留原值——别把它当只读缓存用。
   */
  extract_model: string | null
  extract_prompt_version: string | null
  extracted_at: string | null
  deleted_at: string | null
  created_at: string
  updated_at: string
  skills: JobSkillRead[]
  is_expired: boolean
}

/* --------------------------------- 写模型 --------------------------------- */

/**
 * 一条岗位技能（写入）：后端 `JobSkillInput` 的镜像，四格 `name` / `type` /
 * `evidence_text` / `confidence`。
 *
 * **依然没有 `origin`**：它由服务端按 1.D 现场派生，客户端提交什么都被丢掉；
 * 把 `origin` 加进来不会「多存一个字段」，只会让前端以为用户的声称有分量。
 * 后两格自 ADR-011 v9 起进入请求体（v6 曾整格禁掉）：草稿态确认过的 AI 结果必须存得进去，
 * 否则徽标与核验计数无从对上这条技能。
 *
 * 三条越界即 422 的宽度（在这里就写成类型注释，免得视图各自记一份）：
 * `confidence` ∈ [0, 1]、`evidence_text` ≤ 60 字、`name` ≤ 100 字。
 */
export interface JobSkillInput {
  name: string
  type?: JobSkillType | null
  /** JD 原文片段；`null` / 缺省 = 没有依据，该条会被派生成 `unverified`。 */
  evidence_text?: string | null
  /**
   * 模型自报的把握。读回来是字符串（`"0.9000"`）、提取响应里是数字（`0.9`），
   * 两种都要能原样发回去：后端 `Decimal` 在宽松模式下两者都收，
   * 但把字符串转成 `Number` 会引入 `0.9000 → 0.9` 的显示漂移，没必要。
   */
  confidence?: string | number | null
}

/** 后端 `JobWriteRequest`：`POST /jobs` 与 `PUT /jobs/{id}` 共用同一个形状。 */
export interface JobWriteRequest {
  company_name: string
  title: string
  location?: string | null
  employment_type?: EmploymentType | null
  description: string
  salary_min?: string | null
  salary_max?: string | null
  experience_required?: string | null
  /** `YYYY-MM-DD`；允许填过去的日期（过期是读侧判定，不是写入错误）。 */
  deadline?: string | null
  skills: JobSkillInput[]
}

/* ------------------------------- AI 提取契约 ------------------------------ */

/**
 * `POST /jobs/extract-skills` 的请求体（后端 `ExtractSkillsRequest`）：只有两格。
 *
 * 没有 `skills` / `origin` / `confidence`：本端点纯计算（2.A），
 * 把落库字段放进请求体只会给「提取顺手改库」留下想象空间。
 */
export interface ExtractSkillsRequest {
  /** JD 原文，1–8000 字（2.F）；超 8000 由后端 422，且**不发往 DeepSeek**。 */
  description: string
  /** 岗位名，≤200 字；只进 Prompt 作上下文，后端不拿它做任何判定。 */
  title?: string | null
}

/** 后端 `ExtractedSkillRead`：提取结果里的一条候选技能。 */
export interface ExtractedSkill {
  /** 技能名，服务端已按 ≤40 字筛过（`MAX_SKILL_NAME_CHARS`）。 */
  name: string
  /**
   * 分类。2.D 第 5 条：模型给的值不在 `hard/soft/domain` 内时**保留该条**、把 type 置
   * `null`（宁可为空让用户填，也不悄悄丢掉一条真实要求），所以这里可空。
   */
  type: JobSkillType | null
  /**
   * JD 原文片段（≤60 字，§2.D.1 的核验锚点）。
   *
   * 字段名是 `evidence` 而**不是** `evidence_text`：这里是「模型给的原话」，
   * 还没经过落库判定；用户确认后保存时，同一串文字走 `JobSkillInput.evidence_text` 进列。
   * 两个名字都出自 ADR-011（2.C 与修订清单 #2），搞混的表现为「保存后依据凭空消失」。
   */
  evidence: string
  /**
   * 模型自报的把握：**裸数字**且可能为 `null`。
   *
   * 与读模型的 `JobSkillRead.confidence`（字符串 `"0.9000"`）不同——那一列是
   * `DECIMAL(5,4)`、经 `model_dump(mode="json")` 输出成字符串避免浮点尾数噪声；
   * 这一格是模型输出的原始值、还没过列宽约束（`app/schemas/job.py:348`）。
   */
  confidence: number | null
}

/**
 * `data.meta`：核验计数与溯源信息（ADR-011 2.D「核验计数必须可见」）。
 *
 * **层级要说死**：这七个字段在响应的 `data.meta` 里，不在外层 `meta`——
 * 统一信封（`app/core/responses.py:10-11`）把结果包进 `data`，于是成功响应有**两个** `meta`，
 * 外层那一个只有 `request_id`。读错层的后果不是报错而是全 `undefined`，
 * 三句计数文案一起消失，而这条正是本端点唯一的产品承诺。
 * 由 `api/__tests__/jobs.spec.ts` 用一份「只把计数放在外层」的反向响应钉住。
 */
export interface ExtractSkillsMeta {
  /** 这批结果是哪个模型产出的（常量 `deepseek-flash`，PRD FR-03 L105）。 */
  model: string
  /** 这批结果是哪一版 Prompt 产出的（常量 `"v1"`）；Prompt 每改一次 +1，三个月后只有它对得上账。 */
  prompt_version: string
  /** 模型**一共给了**几条，含后来被剔除、被合并、被切断的。 */
  received_count: number
  /** 其中几条因**无法回溯 JD 原文**（或 name/evidence 不合规）被剔除 → 该怀疑核验规则或换 JD。 */
  dropped_count: number
  /** 其中几条与前面的条目**重名**被合并掉（保留第一条）→ 用户什么都不用做。 */
  deduped_count: number
  /** 15 条封顶**是否真的切掉了东西**；为真时前端补一句「只保留了前 15 条」。 */
  truncated: boolean
  /**
   * 上游给的结束原因（成功响应里常态是 `"stop"`，可能为 `null`）。
   *
   * 注意与第 7 情形的区别：被 token 上限切断时本端点**不会**返回成功体，
   * 而是 502 + **外层** `meta.finish_reason = "length"`（`app/api/v1/jobs.py:151`），
   * 那个值由 `client.ts` 挂在 `ApiError.finishReason` 上。
   */
  finish_reason: string | null
}

/** 后端 `ExtractSkillsResult`：`{ skills, meta }`，即响应的 `data`。 */
export interface ExtractSkillsResult {
  /**
   * 至少一条：一条都不剩时后端不返回空列表而是 502（2.D 第 3 条，禁止静默降级）。
   * 所以前端不需要「AI 没提取到东西」这个分支。
   */
  skills: ExtractedSkill[]
  meta: ExtractSkillsMeta
}

/**
 * 提取请求的超时（与润色同口径，`api/resumes.ts::POLISH_TIMEOUT_MS`）。
 *
 * `http` 实例默认 15 秒，而后端给 DeepSeek 的预算是 30 秒（ADR-009 / 2.B）；
 * 按默认值会先被自己掐断、报成「无法连接到服务」，用户以为后端挂了，其实 AI 还在跑。
 * 45 秒 = 30 秒上游预算 + 余量，也是 `MVP_SCOPE.md` 对 AI 等待的 NFR 口径。
 */
export const EXTRACT_TIMEOUT_MS = 45_000

/** `data.meta.truncated` 为真时补的那句文案（截断必须可见，与「悄悄替换结果」不同类）。 */
export const EXTRACT_TRUNCATED_HINT = '模型给出的条目超过 15 条，只保留了前 15 条'

/* -------------------------------- 列表参数 -------------------------------- */

/** 列表筛选（议题 3.C：本轮只有 `keyword` + `status`，薪资/地点/经验存而不筛）。 */
export interface JobListQuery {
  /** 缺省 `published`（后端 `DEFAULT_LIST_STATUS`）；自己的草稿要显式传 `draft`。 */
  status?: JobStatus
  /** 企业名 / 岗位名的模糊匹配（后端不匹配 JD 正文）。 */
  keyword?: string
  /** 默认 `false`：`deadline < 今天` 的行不出现；要一起看到必须显式传 `true`。 */
  includeExpired?: boolean
  page?: number
  pageSize?: number
}

/** 拆过信封的列表结果：`total` 来自 `meta`（符合筛选的总数，不是当页条数）。 */
export interface JobListResult {
  jobs: JobSummary[]
  page: number
  pageSize: number
  total: number
}

/** 列表 `meta` 在 `request_id` 之外多三个分页字段（`app/core/responses.py::success_response`）。 */
interface JobListMeta extends ApiMeta {
  page: number
  page_size: number
  total: number
}

/* ---------------------------------- 端点 ---------------------------------- */

/** `GET /api/v1/jobs`：广场（`status=published`）或本人的草稿（`status=draft`）。 */
export async function listJobs(query: JobListQuery = {}): Promise<JobListResult> {
  const page = query.page ?? 1
  const pageSize = query.pageSize ?? JOB_LIST_PAGE_SIZE

  // 只拼「这次真的带了」的键：`params` 里留 `undefined` 会被 axios 跳过，
  // 留 `null` 却会序列化成 `keyword=`（空串），后者在后端是「搜空串」而不是「不搜」。
  const params: Record<string, string | number | boolean> = {
    status: query.status ?? 'published',
    page,
    page_size: pageSize,
    include_expired: query.includeExpired ?? false,
  }
  const keyword = query.keyword?.trim()
  if (keyword) params.keyword = keyword

  const response = await http.get<ApiSuccess<{ jobs: JobSummary[] }> & { meta: JobListMeta }>(
    '/jobs',
    { params },
  )
  const meta = response.data.meta
  return {
    jobs: unwrap(response).jobs,
    // `meta` 由后端保证存在；`?? ` 只是防止信封变化时这里静默拿到 undefined 分页号。
    page: meta?.page ?? page,
    pageSize: meta?.page_size ?? pageSize,
    total: meta?.total ?? 0,
  }
}

/**
 * `GET /api/v1/jobs/{jobId}`：详情。
 *
 * 「不存在」「他人草稿」「在回收站里」在后端是同一个 404（`get_readable` 排除了软删行），
 * 所以调用方拿到 404 只需当作「这条读不到」，不必区分三种成因。
 */
export async function getJob(jobId: string): Promise<JobRead> {
  const response = await http.get<ApiSuccess<{ job: JobRead }>>(`/jobs/${jobId}`)
  const job = unwrap(response).job
  if (!job) {
    throw new Error('服务端未返回岗位数据')
  }
  return job
}

/** `POST /api/v1/jobs`（201）：录入一条岗位，后端一律落 `draft`。 */
export async function createJob(payload: JobWriteRequest): Promise<JobRead> {
  const response = await http.post<ApiSuccess<{ job: JobRead }>>('/jobs', payload)
  return unwrap(response).job
}

/** `PUT /api/v1/jobs/{jobId}`：整体覆盖（字段 + 技能子表全量替换），`status` 不变。 */
export async function updateJob(jobId: string, payload: JobWriteRequest): Promise<JobRead> {
  const response = await http.put<ApiSuccess<{ job: JobRead }>>(`/jobs/${jobId}`, payload)
  return unwrap(response).job
}

/** `PUT /api/v1/jobs/{jobId}/publish`：发布进广场（仅创建者；1.F #6 的显式动作）。 */
export async function publishJob(jobId: string): Promise<JobRead> {
  const response = await http.put<ApiSuccess<{ job: JobRead }>>(`/jobs/${jobId}/publish`)
  return unwrap(response).job
}

/** `PUT /api/v1/jobs/{jobId}/unpublish`：下架回草稿（广场立刻读不到）。 */
export async function unpublishJob(jobId: string): Promise<JobRead> {
  const response = await http.put<ApiSuccess<{ job: JobRead }>>(`/jobs/${jobId}/unpublish`)
  return unwrap(response).job
}

/** `DELETE /api/v1/jobs/{jobId}`：软删除（进回收站，`job_skills` 一行不动）。 */
export async function deleteJob(jobId: string): Promise<void> {
  await http.delete(`/jobs/${jobId}`)
}

/** `GET /api/v1/jobs/trash`：只含**自己**软删除的岗位，最近删除的在前。 */
export async function listJobTrash(): Promise<JobSummary[]> {
  const response = await http.get<ApiSuccess<{ jobs: JobSummary[] }>>('/jobs/trash')
  return unwrap(response).jobs
}

/** `PUT /api/v1/jobs/{jobId}/restore`：恢复（`deleted_at` 清回 NULL，`status` 原样保留）。 */
export async function restoreJob(jobId: string): Promise<JobRead> {
  const response = await http.put<ApiSuccess<{ job: JobRead }>>(`/jobs/${jobId}/restore`)
  return unwrap(response).job
}

/* -------------------------------- AI 提取 -------------------------------- */

/**
 * `POST /api/v1/jobs/extract-skills`：AI 岗位 JD 技能提取（ADR-011 2.A / 2.D）。
 *
 * **纯计算、不落库**：这一调用不碰 `jobs` 也不碰 `job_skills`，返回的候选条目只进前端草稿态，
 * 用户逐条删改后点「保存岗位」才由 `createJob` / `updateJob` 写库。所以本函数的返回值
 * 不能被当成「已经存好了」，调用方必须保留一条显式的保存动作（议题 3.B 的确认制）。
 *
 * `X-DeepSeek-Key` 由 `client.ts` 的白名单注入，这里不手工加头。
 * 失败只有三个码（`AI_KEY_MISSING` / `AI_KEY_INVALID` / `AI_SERVICE_UNAVAILABLE`，2.F），
 * 全部走拦截器归一化，本函数不做任何降级——「提取不到就当没有」是被明令禁止的。
 */
export async function extractJobSkills(
  payload: ExtractSkillsRequest,
): Promise<ExtractSkillsResult> {
  // 注意 `unwrap` 之后拿到的是整个 `data`，`meta` 就在它里面：
  // 信封外层那个同名 `meta` 只有 `request_id`，七个计数不在那儿。
  const response = await http.post<ApiSuccess<ExtractSkillsResult>>(
    '/jobs/extract-skills',
    payload,
    { timeout: EXTRACT_TIMEOUT_MS },
  )
  return unwrap(response)
}
