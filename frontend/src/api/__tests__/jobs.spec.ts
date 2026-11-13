/**
 * 岗位库端点封装的单测（Phase 4 · B2 + C2，ADR-011 议题 3.C / 1.F / 2.A / 2.D）。
 *
 * 钉住三条「只有前端能发现、坏了又最难解释」的链路：
 *
 * 1. **路径与方法名**。`/jobs/trash` 必须是 GET 到 `/jobs/trash`，而不是被拼成
 *    `/jobs/{id}`；`restore` 是 **PUT** `/jobs/{id}/restore`（与 `profiles.py:221` 同形）。
 *    写错的表现为「回收站打不开，404」，而后端一切正常——只有看请求的人才发现得了。
 * 2. **查询串的拼装**。`status=draft`、`include_expired`、`page`/`page_size` 全部是
 *    后端的参数名；前端 store 用的是 camelCase，翻译只发生在 `api/jobs.ts` 一处。
 *    漏翻译的后果不是报错，而是「筛了个寂寞」：后端按默认值返回广场，用户以为草稿没了。
 * 3. **写侧请求体的键集**。后端 `JobWriteRequest` 没有 `status` / `created_by_user_id`，
 *    技能条目没有 `origin`，而且 Pydantic 是 `extra="ignore"`——多传了**不报错，直接丢**。
 *    所以反向断言（这些键不许出现）比正向断言更重要：它防的是「前端以为自己存进去了」。
 *    `evidence_text` / `confidence` 自 ADR-011 v9 起是**合法键**，改为正向断言「带得过去」。
 * 4. **提取响应的层级**：七个计数在 `data.meta`，外层 `meta` 只有 `request_id`——
 *    读错层不报错、只是全是 `undefined`，所以这里同时验「取到内层」与「取不到外层」。
 *
 * 做法与 `byok.spec.ts` 一致：把 axios 的 adapter 换成假的，让**真实拦截器链**跑一遍
 * 但不碰网络；断言看的是最终发出去的 URL、查询串与请求体，不是内部实现细节。
 */

import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ApiError, http } from '@/api/client'
import { jobsApi } from '@/api'
import type { jobsApi as JobsContract } from '@/api'

type JobSummary = JobsContract.JobSummary
type JobRead = JobsContract.JobRead

/** 一次请求被 fakeAdapter 捕获后的可读形状。 */
interface Captured {
  method: string
  url: string
  /** 真实发出去的完整 URL（含 baseURL 与查询串），由 axios 自己的 `getUri` 算出。 */
  fullUrl: string
  query: Record<string, string>
  /** 请求体：`transformRequest` 已把对象 JSON 化，这里还原成对象再断言。 */
  body: unknown
  /** 本次请求生效的超时（毫秒）：提取必须放宽到 45 秒，见 `extractJobSkills` 用例。 */
  timeout: number | undefined
}

let captured: Captured[] = []
type SavedAdapter = typeof http.defaults.adapter
let savedAdapter: SavedAdapter

function skillFixture(overrides: Partial<JobsContract.JobSkillRead> = {}): JobsContract.JobSkillRead {
  return {
    id: 'skill-1',
    name: 'Python',
    type: 'hard',
    origin: 'unverified',
    confidence: null,
    evidence_text: null,
    ...overrides,
  }
}

function summaryFixture(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: 'job-1',
    company_name: '示例科技',
    title: '后端实习（AI 方向）',
    location: '上海',
    employment_type: 'internship',
    salary_min: '3000.00',
    salary_max: '6000.00',
    deadline: '2099-12-31',
    status: 'published',
    created_by_user_id: 'user-1',
    created_at: '2026-11-05T10:00:00+08:00',
    updated_at: '2026-11-05T10:00:00+08:00',
    deleted_at: null,
    skills: [skillFixture()],
    is_expired: false,
    ...overrides,
  }
}

function detailFixture(overrides: Partial<JobRead> = {}): JobRead {
  return {
    ...summaryFixture(),
    description: '负责后端接口开发，熟练掌握 Python 与 FastAPI。',
    experience_required: '3 年以上后端经验',
    // `ADR-024` §2.6：驳回理由，只有 `rejected` 时有值。
    review_note: null,
    extract_model: null,
    extract_prompt_version: null,
    extracted_at: null,
    ...overrides,
  }
}

function envelope<T>(config: InternalAxiosRequestConfig, data: T, status = 200): AxiosResponse {
  return {
    data,
    status,
    statusText: 'OK',
    headers: {},
    config,
  } as AxiosResponse
}

/** `data.meta` 的七个字段（后端 `ExtractSkillsMeta`，`app/schemas/job.py:351-380`）。 */
function extractMetaFixture(
  overrides: Partial<JobsContract.ExtractSkillsMeta> = {},
): JobsContract.ExtractSkillsMeta {
  return {
    model: 'deepseek-flash',
    prompt_version: 'v1',
    received_count: 20,
    dropped_count: 4,
    deduped_count: 1,
    truncated: true,
    finish_reason: 'stop',
    ...overrides,
  }
}

/** 一份「按实现来」的成功信封：计数在 `data.meta`，外层 `meta` 只有 `request_id`。 */
function defaultExtractEnvelope(): unknown {
  return {
    data: {
      skills: [
        { name: 'Python', type: 'hard', evidence: '熟练掌握 Python', confidence: 0.9 },
        { name: '沟通能力强', type: null, evidence: '良好的沟通表达', confidence: null },
      ],
      meta: extractMetaFixture(),
    },
    meta: { request_id: 'req-test' },
  }
}

/**
 * 提取端点要同时验三种信封形状（七个计数在 `data.meta` / 只在外层 `meta` / 两层都有但值不同），
 * 所以把这一支的响应体留给用例临时指定；`null` = 用下面的默认那份。
 * 每次 `beforeEach` 复位，避免一份假信封漏到下一个用例。
 */
let extractEnvelope: unknown = null

/** 按「方法 + 路径」回一盘假的响应；只关心调用方拿没拿对，不模拟数据库。 */
function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  // axios 的 `buildURL` 就是把标量原样转成字符串再拼 `&`，这里用同一口径读回键值。
  const rawParams = (config.params ?? {}) as Record<string, unknown>
  const query = Object.fromEntries(
    Object.entries(rawParams).map(([key, value]) => [key, String(value)]),
  )

  captured.push({
    method,
    url,
    fullUrl: http.getUri(config),
    query,
    body: typeof config.data === 'string' && config.data ? JSON.parse(config.data) : config.data,
    timeout: config.timeout,
  })

  if (method === 'delete') {
    return Promise.resolve(
      envelope(config, { data: { job_id: 'job-1', deleted: true }, meta: { request_id: 'req-test' } }),
    )
  }

  if (method === 'post' && url === '/jobs/extract-skills') {
    return Promise.resolve(envelope(config, extractEnvelope ?? defaultExtractEnvelope()))
  }

  if (method === 'get' && url === '/jobs/trash') {
    return Promise.resolve(
      envelope(config, {
        data: { jobs: [summaryFixture({ id: 'job-deleted', deleted_at: '2026-11-05T12:00:00+08:00' })] },
        meta: { request_id: 'req-test' },
      }),
    )
  }

  if (method === 'get' && url === '/jobs/review-queue') {
    // 待审队列的形状与广场列表一致（`{ jobs } + 分页 meta`），只是能进来的行不同。
    return Promise.resolve(
      envelope(config, {
        data: { jobs: [summaryFixture({ id: 'job-pending', status: 'pending_review' })] },
        meta: {
          request_id: 'req-test',
          page: Number(query.page ?? 1),
          page_size: Number(query.page_size ?? 20),
          total: 51,
        },
      }),
    )
  }

  if (method === 'get' && url === '/jobs') {
    // `page` / `page_size` 如实回显请求值（与后端 `success_response(..., page=page)` 一致），
    // `total` 是「符合筛选的总数」而不是当页条数——前端两个来源搞混就会算错页码。
    return Promise.resolve(
      envelope(config, {
        data: { jobs: [summaryFixture()] },
        meta: {
          request_id: 'req-test',
          page: Number(query.page ?? 1),
          page_size: Number(query.page_size ?? 20),
          total: 51,
        },
      }),
    )
  }

  // 其余（POST /jobs、GET|PUT /jobs/{id}、POST .../submit-review|review、
  // PUT .../unpublish|restore）都回一条详情。状态按动作给，免得「前端拿到什么就信什么」
  // 的用例在错误的初始态上通过。
  const actionStatus = url.endsWith('/submit-review')
    ? 'pending_review'
    : url.endsWith('/review')
      ? 'published'
      : method === 'post'
        ? 'draft'
        : 'published'
  return Promise.resolve(
    envelope(
      config,
      { data: { job: detailFixture({ status: actionStatus }) }, meta: { request_id: 'req-test' } },
      method === 'post' ? 201 : 200,
    ),
  )
}

beforeEach(() => {
  captured = []
  extractEnvelope = null
  savedAdapter = http.defaults.adapter
  http.defaults.adapter = fakeAdapter
})

afterEach(() => {
  http.defaults.adapter = savedAdapter
})

/** 最近一次（或唯一一次）发出去的请求。 */
function lastRequest(): Captured {
  const request = captured[captured.length - 1]
  if (!request) throw new Error('没有任何请求发出')
  return request
}

describe('listJobs 的查询串', () => {
  it('默认发广场第一页：status=published、page_size=50、include_expired=false', async () => {
    const result = await jobsApi.listJobs()

    expect(lastRequest().method).toBe('get')
    expect(lastRequest().url).toBe('/jobs')
    // 首屏 50 条是议题 3.C 写死的口径，不是后端默认的 20。
    expect(lastRequest().fullUrl).toBe(
      '/api/v1/jobs?status=published&page=1&page_size=50&include_expired=false',
    )
    // 没传关键词时**不下发这个键**：`keyword=` 在后端是「搜空串」，不是「不搜」。
    expect(lastRequest().query.keyword).toBeUndefined()

    // `total` 来自 `meta`（符合筛选的总数），`page`/`page_size` 也来自 `meta` 的回显。
    expect(result.total).toBe(51)
    expect(result.page).toBe(1)
    expect(result.pageSize).toBe(50)
    expect(result.jobs).toHaveLength(1)
  })

  it('自己的草稿要显式传 status=draft，翻页与含过期一起拼进同一个查询串', async () => {
    await jobsApi.listJobs({
      status: 'draft',
      keyword: '字节',
      includeExpired: true,
      page: 3,
      pageSize: 50,
    })

    expect(lastRequest().fullUrl).toBe(
      '/api/v1/jobs?status=draft&page=3&page_size=50&include_expired=true&keyword=%E5%AD%97%E8%8A%82',
    )
    // 键名必须是后端的 snake_case：store 里的 `includeExpired` 不能原样发出去。
    expect(lastRequest().query).toHaveProperty('include_expired', 'true')
    expect(lastRequest().query).not.toHaveProperty('includeExpired')
    expect(lastRequest().query).toHaveProperty('keyword', '字节')
  })

  it('纯空白的关键词等同没填，不会发出 keyword=', async () => {
    await jobsApi.listJobs({ keyword: '   ' })

    expect(lastRequest().query.keyword).toBeUndefined()
    expect(lastRequest().fullUrl).not.toContain('keyword')
  })
})

describe('单条与写入的路径', () => {
  it('getJob 打 GET /jobs/{id}，并拆掉 { job } 外壳', async () => {
    const job = await jobsApi.getJob('job-42')

    expect(lastRequest().method).toBe('get')
    expect(lastRequest().url).toBe('/jobs/job-42')
    expect(job.id).toBe('job-1')
    expect(job.description).toContain('Python')
  })

  it('createJob 打 POST /jobs，请求体只有后端接受的键', async () => {
    await jobsApi.createJob({
      company_name: '示例科技',
      title: '后端实习',
      description: '熟练掌握 Python',
      location: '上海',
      employment_type: 'internship',
      salary_min: '3000',
      salary_max: '6000',
      experience_required: null,
      deadline: null,
      skills: [{ name: 'Python', type: null }],
    })

    expect(lastRequest().method).toBe('post')
    expect(lastRequest().url).toBe('/jobs')
    // 白名单 = 后端 `JobWriteRequest` 的字段集，多一个都算越界。
    expect(Object.keys((lastRequest().body ?? {}) as Record<string, unknown>).sort()).toEqual([
      'company_name',
      'deadline',
      'description',
      'employment_type',
      'experience_required',
      'location',
      'salary_max',
      'salary_min',
      'skills',
      'title',
    ])
  })

  it('updateJob 打 PUT /jobs/{id}；只传 name/type 时不凭空补出 evidence / confidence', async () => {
    await jobsApi.updateJob('job-42', {
      company_name: '示例科技',
      title: '后端实习',
      description: '熟练掌握 Python',
      skills: [{ name: 'Python', type: 'hard' }],
    })

    expect(lastRequest().method).toBe('put')
    expect(lastRequest().url).toBe('/jobs/job-42')

    // 「没有依据」的手动行必须发成两个键，而不是 `evidence_text: null`：
    // 库里 `NULL` 只该有一种到达方式，多一种写法就多一处「前端以为清了其实没清」。
    const body = lastRequest().body as { skills: Record<string, unknown>[] }
    expect(Object.keys(body.skills[0] as Record<string, unknown>).sort()).toEqual(['name', 'type'])
  })
})

describe('状态与回收站的路径', () => {
  it('submit-review / review 是 POST 且挂在 {id} 之后；unpublish / restore 仍是 PUT', async () => {
    await jobsApi.submitJobForReview('job-42')
    expect(lastRequest().method).toBe('post')
    expect(lastRequest().url).toBe('/jobs/job-42/submit-review')

    await jobsApi.reviewJob('job-42', 'approve')
    expect(lastRequest().method).toBe('post')
    expect(lastRequest().url).toBe('/jobs/job-42/review')
    // 通过时不带 `note` 键：空串会被后端存成「写过理由但没内容」。
    expect(lastRequest().body).toEqual({ decision: 'approve' })

    await jobsApi.reviewJob('job-42', 'reject', '  缺少薪资范围  ')
    expect(lastRequest().url).toBe('/jobs/job-42/review')
    // 理由先 trim 再发：前后空格留着，驳回理由在详情页就带一段莫名其妙的缩进。
    expect(lastRequest().body).toEqual({ decision: 'reject', note: '缺少薪资范围' })

    await jobsApi.unpublishJob('job-42')
    expect(lastRequest().method).toBe('put')
    expect(lastRequest().url).toBe('/jobs/job-42/unpublish')

    await jobsApi.restoreJob('job-42')
    expect(lastRequest().method).toBe('put')
    expect(lastRequest().url).toBe('/jobs/job-42/restore')
  })

  it('listReviewQueue 打 GET /jobs/review-queue —— 静态段不能被当成岗位 id 发成 /jobs/{id}', async () => {
    const result = await jobsApi.listReviewQueue({ keyword: '后端' })

    expect(lastRequest().method).toBe('get')
    expect(lastRequest().url).toBe('/jobs/review-queue')
    expect(lastRequest().query).toMatchObject({ keyword: '后端', page: '1', page_size: '50' })
    // `total` 取的是 `meta`（符合筛选的总数），不是当页条数。
    expect(result.total).toBe(51)
  })

  it('deleteJob 打 DELETE /jobs/{id}，且不把响应体当数据用', async () => {
    await expect(jobsApi.deleteJob('job-42')).resolves.toBeUndefined()

    expect(lastRequest().method).toBe('delete')
    expect(lastRequest().url).toBe('/jobs/job-42')
  })

  it('listJobTrash 打 GET /jobs/trash —— 静态段不能被当成岗位 id 发成 /jobs/{id}', async () => {
    const rows = await jobsApi.listJobTrash()

    expect(lastRequest().method).toBe('get')
    expect(lastRequest().url).toBe('/jobs/trash')
    // 反向确认：路径里没有多出来的查询串，也没有 `/jobs/trash/` 这种尾斜杠。
    expect(lastRequest().fullUrl).toBe('/api/v1/jobs/trash')
    expect(rows).toHaveLength(1)
    expect(rows[0]?.deleted_at).not.toBeNull()
  })
})

describe('extractJobSkills 的响应层级与超时', () => {
  /**
   * 本用例的存在理由：统一信封（`app/core/responses.py:10-11`）把结果包进 `data`，
   * 于是成功响应有**两个 `meta`**——外层只有 `request_id`，七个计数在 `data.meta`。
   * 读错那一份不会报错，只会让 `meta.received_count` 变成 `undefined`，
   * 屏幕上那三句核验计数一起消失（或显示成「AI 给出 undefined 条」）。
   * 这属于「不看代码推不出来」的坑，ADR-011 2.A（v10）与 `docs/API.md` §4.8 都写了它，
   * 但文档只能说明坑存在，只有断言能防住它。
   */
  it('七个计数字段逐个从 `data.meta` 取到值（外层 `meta` 只有 request_id）', async () => {
    const result = await jobsApi.extractJobSkills({
      description: '负责后端接口开发，熟练掌握 Python 与 FastAPI。',
      title: '后端实习',
    })

    expect(lastRequest().method).toBe('post')
    expect(lastRequest().url).toBe('/jobs/extract-skills')

    // 七个字段逐个断言：只断言「有一个 meta 对象」的话，字段改名会假绿。
    expect(result.meta.model).toBe('deepseek-flash')
    expect(result.meta.prompt_version).toBe('v1')
    expect(result.meta.received_count).toBe(20)
    expect(result.meta.dropped_count).toBe(4)
    expect(result.meta.deduped_count).toBe(1)
    expect(result.meta.truncated).toBe(true)
    expect(result.meta.finish_reason).toBe('stop')

    // 技能四格：`evidence`（不是 `evidence_text`）、`confidence` 是裸数字且可为 null、
    // `type` 可为 null（枚举外值保留该条、把 type 置 NULL，2.D 第 5 条）。
    expect(result.skills[0]).toEqual({
      name: 'Python',
      type: 'hard',
      evidence: '熟练掌握 Python',
      confidence: 0.9,
    })
    expect(result.skills[1]?.type).toBeNull()
    expect(result.skills[1]?.confidence).toBeNull()
    // 请求体只有两格（后端 `ExtractSkillsRequest`），且原样带过去。
    expect(lastRequest().body).toEqual({
      description: '负责后端接口开发，熟练掌握 Python 与 FastAPI。',
      title: '后端实习',
    })
  })

  it('计数只放在**外层** `meta` 时取不到值（读错层的反向护栏）', async () => {
    // 一份「把七个字段错放到外层」的响应：后端不是这么发的，所以封装也绝不该从这里读。
    extractEnvelope = {
      data: { skills: [{ name: 'Python', type: 'hard', evidence: '熟练掌握 Python', confidence: 0.9 }] },
      meta: {
        request_id: 'req-test',
        model: 'deepseek-flash',
        prompt_version: 'v1',
        received_count: 20,
        dropped_count: 4,
        deduped_count: 1,
        truncated: true,
        finish_reason: 'stop',
      },
    }

    const result = await jobsApi.extractJobSkills({ description: '熟练掌握 Python' })

    // `data.meta` 缺席 → 封装拿回的就是 `undefined`：任何去读 `received_count` 的代码
    // 都会在这一行当场炸掉，而不是安静地显示「AI 给出 undefined 条」。
    expect(result.meta).toBeUndefined()
    // 反向确认外层那几个数**没有**被偷偷塞进返回值里（那才说明封装真的在读外层）。
    expect(result as unknown as Record<string, unknown>).not.toHaveProperty('received_count')
    expect(result.skills).toHaveLength(1)
  })

  it('两层都有计数时，取的是 `data.meta` 那一份（不是外层）', async () => {
    // 两层的数字**故意不同**：这样「读错层」一定变红，而不是恰好取到一个看起来对的值。
    extractEnvelope = {
      data: {
        skills: [{ name: 'Python', type: 'hard', evidence: '熟练掌握 Python', confidence: 0.9 }],
        meta: extractMetaFixture({ received_count: 20, dropped_count: 4, deduped_count: 1 }),
      },
      meta: {
        request_id: 'req-test',
        received_count: 999,
        dropped_count: 999,
        deduped_count: 999,
        truncated: false,
        finish_reason: 'WRONG_LAYER',
      },
    }

    const result = await jobsApi.extractJobSkills({ description: '熟练掌握 Python' })

    expect(result.meta.received_count).toBe(20)
    expect(result.meta.dropped_count).toBe(4)
    expect(result.meta.deduped_count).toBe(1)
    expect(result.meta.truncated).toBe(true)
    expect(result.meta.finish_reason).toBe('stop')
  })

  it('超时被放宽到 45 秒（`http` 默认 15 秒会在后端还在等 DeepSeek 时先掐断）', async () => {
    await jobsApi.extractJobSkills({ description: '熟练掌握 Python' })

    expect(lastRequest().timeout).toBe(45_000)
    expect(lastRequest().timeout).toBe(jobsApi.EXTRACT_TIMEOUT_MS)
  })

  it('对照组：普通岗位请求仍用实例默认的 15 秒（45 秒只属于两个 AI 端点）', async () => {
    await jobsApi.getJob('job-42')

    // 若有人把 45 秒做成实例级默认值，这条会红：那等于让所有列表/详情都肯等 45 秒。
    expect(lastRequest().timeout).toBe(15_000)
  })

  it('第 7 情形的 `finish_reason` 由 client 挂在 `ApiError` 上（判据不是文案）', async () => {
    // 真实错误形状：502 + `error.code = AI_SERVICE_UNAVAILABLE` + **外层** `meta.finish_reason`
    // （`app/api/v1/jobs.py:144-153` 就地组装；错误体里没有 `data` 这一层）。
    http.defaults.adapter = async (config) => {
      const body = {
        error: {
          code: 'AI_SERVICE_UNAVAILABLE',
          message: 'AI 输出的技能条目过多，被服务端的 token 上限切断了。',
          details: {},
          retryable: true,
        },
        meta: { request_id: 'req-7', finish_reason: 'length' },
      }
      return Promise.reject(
        new AxiosError('bad gateway', 'ERR_BAD_RESPONSE', config, {}, envelope(config, body, 502)),
      )
    }

    const error = await jobsApi
      .extractJobSkills({ description: '熟练掌握 Python' })
      .catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(502)
    expect((error as ApiError).code).toBe('AI_SERVICE_UNAVAILABLE')
    // 这一条是分流的全部依据：视图拿它区分「被切断」与「稍后重试」。
    expect((error as ApiError).finishReason).toBe('length')
  })

  it('对照组：第 3/4/5/6 情形的 502 没有 `finish_reason`，`ApiError.finishReason` 是 null', async () => {
    http.defaults.adapter = async (config) => {
      const body = {
        error: { code: 'AI_SERVICE_UNAVAILABLE', message: 'AI 暂时不可用。', details: {}, retryable: true },
        meta: { request_id: 'req-4' },
      }
      return Promise.reject(
        new AxiosError('bad gateway', 'ERR_BAD_RESPONSE', config, {}, envelope(config, body, 502)),
      )
    }

    const error = await jobsApi
      .extractJobSkills({ description: '熟练掌握 Python' })
      .catch((caught: unknown) => caught)

    expect((error as ApiError).code).toBe('AI_SERVICE_UNAVAILABLE')
    // 反向断言防的是「把 null 当成 length」这类分流错误——两者用户动作完全不同。
    expect((error as ApiError).finishReason).toBeNull()
    expect((error as ApiError).finishReason).not.toBe('length')
  })

  it('请求头里没有 Key 时是 400 `AI_KEY_MISSING`，`status` 与 `code` 原样递给视图', async () => {
    http.defaults.adapter = async (config) => {
      const body = {
        error: { code: 'AI_KEY_MISSING', message: '缺少 DeepSeek API Key。', details: {}, retryable: false },
        meta: { request_id: 'req-1' },
      }
      return Promise.reject(
        new AxiosError('bad request', 'ERR_BAD_REQUEST', config, {}, envelope(config, body, 400)),
      )
    }

    const error = await jobsApi
      .extractJobSkills({ description: '熟练掌握 Python' })
      .catch((caught: unknown) => caught)

    expect((error as ApiError).status).toBe(400)
    expect((error as ApiError).code).toBe('AI_KEY_MISSING')
    expect((error as ApiError).finishReason).toBeNull()
  })
})

describe('写侧请求体的反向断言', () => {
  /**
   * 后端 Pydantic 是 `extra="ignore"`：这些键被整丢掉且不报错，所以「没报错」不等于「存进去了」。
   *
   * **C2 改过一次**（监督者裁定 + ADR-011 1.F v9 放开）：`evidence_text` 与 `confidence`
   * 从「禁止出现」移到「允许并且必须能带上」——B2 那一版把它们禁掉的理由是
   * 「没有 AI 的路径上接受填值只会落下无法核验的数据」，而 C1 之后提取路径已经成立，
   * 存不进这两格等于 1.D 的派生与 §2.D.1 的核验无处落地。
   * 因此这里删掉的是**过期的那一半断言**，不是断言本身：`origin` / `id` / `self_rating`
   * 依然禁止（`origin` 由服务端派生、`id` 是读模型的键），并新增一条正向断言盯住「带得过去」。
   */
  const FORBIDDEN_TOP_LEVEL = [
    'status',
    'created_by_user_id',
    'origin',
    'extract_model',
    'extract_prompt_version',
    'extracted_at',
  ]
  const FORBIDDEN_SKILL_KEYS = ['origin', 'id', 'self_rating', 'student_profile_id']

  it('POST 与 PUT 的请求体里都不出现 status / origin / 三列溯源戳，技能条目不出现 id', async () => {
    const payload: JobsContract.JobWriteRequest = {
      company_name: '示例科技',
      title: '后端实习',
      description: '熟练掌握 Python',
      skills: [{ name: 'Python', type: 'hard', evidence_text: '熟练掌握 Python', confidence: '0.9000' }],
    }

    await jobsApi.createJob(payload)
    const created = lastRequest().body as Record<string, unknown>
    const createdSkills = (created.skills ?? []) as Record<string, unknown>[]
    for (const key of FORBIDDEN_TOP_LEVEL) {
      expect(created, `创建请求体不该带 ${key}`).not.toHaveProperty(key)
    }
    for (const skill of createdSkills) {
      for (const key of FORBIDDEN_SKILL_KEYS) {
        expect(skill, `技能条目不该带 ${key}`).not.toHaveProperty(key)
      }
    }

    await jobsApi.updateJob('job-42', payload)
    const updated = lastRequest().body as Record<string, unknown>
    for (const key of FORBIDDEN_TOP_LEVEL) {
      expect(updated, `更新请求体不该带 ${key}`).not.toHaveProperty(key)
    }
  })

  it('`evidence_text` / `confidence` 有值时原样带上；没值时**不出现这个键**', async () => {
    // 有值必须带得过去（否则用户确认过的核验锚点在保存那一刻蒸发，徽标掉回「未标注出处」）。
    await jobsApi.createJob({
      company_name: '示例科技',
      title: '后端实习',
      description: '熟练掌握 Python 与 FastAPI',
      skills: [
        { name: 'Python', type: 'hard', evidence_text: '熟练掌握 Python', confidence: '0.9000' },
        { name: 'Docker', type: null, evidence_text: '熟悉 Docker 部署', confidence: 0.7 },
        { name: '沟通能力强', type: 'soft' },
      ],
    })

    const skills = (lastRequest().body as { skills: Record<string, unknown>[] }).skills
    expect(skills[0]).toEqual({
      name: 'Python',
      type: 'hard',
      evidence_text: '熟练掌握 Python',
      confidence: '0.9000',
    })
    // 数字形态也要能原样发出去：提取响应里的 `confidence` 是裸 `number`。
    expect(skills[1]).toEqual({
      name: 'Docker',
      type: null,
      evidence_text: '熟悉 Docker 部署',
      confidence: 0.7,
    })
    // 手动行没有依据：不发 `evidence_text: null` / `confidence: null`，
    // 而是**根本没有这两个键**——「没有依据」与「依据是空」在库里是同一件事，
    // 在 payload 上必须只有一种写法，否则测试就无法区分「投影漏了」和「本来就没值」。
    expect(skills[2]).toEqual({ name: '沟通能力强', type: 'soft' })
    expect(Object.keys(skills[2] as Record<string, unknown>).sort()).toEqual(['name', 'type'])
  })
})
