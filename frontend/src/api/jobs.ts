/**
 * 岗位库接口：`app/api/v1/jobs.py` 的九条路径（ADR-011 1.F / 2.A / 议题 3.C）。
 *
 * 命名与写法照 `api/profiles.ts`：复数模块名、`http` 实例、`unwrap()` 拆信封，
 * 错误一律由 `client.ts` 的拦截器归一化成 `ApiError`，这里不自造第二套错误处理。
 *
 * **本轮不含任何 AI 调用**：`POST /jobs/extract-skills` 属于下一轮（C 轮），
 * 这里连封装都没有，也就没有把 `X-DeepSeek-Key` 扩到岗位 URL 的可能
 * （注入范围仍由 `client.ts::isResumePolishRequest` 单独决定，ADR-010）。
 *
 * 写侧的形状是本轮最容易写错的地方，三条口径直接抄后端 `app/schemas/job.py`：
 *
 * 1. `JobWriteRequest` **只有** `company_name` / `title` / `location` / `employment_type` /
 *    `description` / `salary_min` / `salary_max` / `experience_required` / `deadline` / `skills`。
 *    请求模型里没有 `status`、没有 `created_by_user_id`（身份只来自 JWT，`POST /jobs`
 *    一律落 `draft`），技能条目里也**没有** `origin` / `evidence_text` / `confidence`
 *    （ADR-011 1.F 的 v6 补条：后两列是 AI 提取产物，只能由服务端在真做过核验的路径上写）。
 *    后端用 Pydantic 默认 `extra="ignore"` 把这些键整丢掉，所以「多传了」不会报错——
 *    **不报错正是风险所在**：前端如果图省事把读模型原样 PUT 回去，那三个字段就静静地
 *    被丢弃，用户以为存进去了。因此这里把写侧技能类型收成 `JobSkillInput`（只有 name/type），
 *    并由 `stores/job.ts` 显式投影，`api/__tests__/jobs.spec.ts` 再钉一条反向断言。
 * 2. `salary_min` / `salary_max` 是 `DECIMAL(12,2)`：后端读出来是**字符串**（`"8000.00"`），
 *    写进去也按字符串发（Pydantic 按字符串构造 Decimal，精确；发 number 会引入
 *    `8000.1 → 8000.09999…` 这类尾数噪声）。所以这里两端都是 `string`。
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
 * **只出现在读模型里**。本轮所有写入路径都是手填，后端恒落 `unverified`；
 * `jd_verifiable` 要等 C 轮的核验路径才会出现。
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

/** 后端 `JobSkillRead`：比写侧多 `id` / `origin` / `confidence` / `evidence_text`。 */
export interface JobSkillRead {
  id: string
  name: string
  /** 后端 `type: str | None`；读侧给原始字符串，不在这里做枚举收敛。 */
  type: string | null
  origin: JobSkillOrigin
  /** 本轮恒为 `null`（AI 提取产物）；`Decimal` 在 JSON 里是字符串。 */
  confidence: string | null
  /** 本轮恒为 `null`；详情页对 `null` 的处理是**整块不渲染**，不是渲染空展开区。 */
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
  /** 以下三项本轮恒为 `null`：写入方是 C 轮的提取端点。 */
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
 * 一条岗位技能（写入）：后端 `JobSkillInput` 的镜像，**只有 `name` 与 `type`**。
 *
 * 不要在这里加 `origin` / `evidence_text` / `confidence`：那三个键会被后端整丢，
 * 加了只会让前端误以为「我存进去过」。
 */
export interface JobSkillInput {
  name: string
  type?: JobSkillType | null
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
