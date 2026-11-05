/**
 * 岗位库端点封装的单测（Phase 4 · B2，ADR-011 议题 3.C / 1.F）。
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
 *    技能条目没有 `origin` / `evidence_text` / `confidence`，而且 Pydantic 是
 *    `extra="ignore"`——多传了**不报错，直接丢**。所以反向断言（这些键不许出现）比
 *    正向断言更重要：它防的是「前端以为自己存进去了」。
 *
 * 做法与 `byok.spec.ts` 一致：把 axios 的 adapter 换成假的，让**真实拦截器链**跑一遍
 * 但不碰网络；断言看的是最终发出去的 URL、查询串与请求体，不是内部实现细节。
 */

import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { http } from '@/api/client'
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
  })

  if (method === 'delete') {
    return Promise.resolve(
      envelope(config, { data: { job_id: 'job-1', deleted: true }, meta: { request_id: 'req-test' } }),
    )
  }

  if (method === 'get' && url === '/jobs/trash') {
    return Promise.resolve(
      envelope(config, {
        data: { jobs: [summaryFixture({ id: 'job-deleted', deleted_at: '2026-11-05T12:00:00+08:00' })] },
        meta: { request_id: 'req-test' },
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

  // 其余（POST /jobs、GET|PUT /jobs/{id}、PUT .../publish|unpublish|restore）都回一条详情。
  return Promise.resolve(
    envelope(
      config,
      { data: { job: detailFixture({ status: method === 'post' ? 'draft' : 'published' }) }, meta: { request_id: 'req-test' } },
      method === 'post' ? 201 : 200,
    ),
  )
}

beforeEach(() => {
  captured = []
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

  it('updateJob 打 PUT /jobs/{id}，技能条目不携带 AI 语义字段', async () => {
    await jobsApi.updateJob('job-42', {
      company_name: '示例科技',
      title: '后端实习',
      description: '熟练掌握 Python',
      skills: [{ name: 'Python', type: 'hard' }],
    })

    expect(lastRequest().method).toBe('put')
    expect(lastRequest().url).toBe('/jobs/job-42')

    const body = lastRequest().body as { skills: Record<string, unknown>[] }
    expect(Object.keys(body.skills[0] as Record<string, unknown>).sort()).toEqual(['name', 'type'])
  })
})

describe('状态与回收站的路径', () => {
  it('publish / unpublish / restore 都是 PUT，且都挂在 {id} 之后', async () => {
    await jobsApi.publishJob('job-42')
    expect(lastRequest().method).toBe('put')
    expect(lastRequest().url).toBe('/jobs/job-42/publish')

    await jobsApi.unpublishJob('job-42')
    expect(lastRequest().url).toBe('/jobs/job-42/unpublish')

    await jobsApi.restoreJob('job-42')
    expect(lastRequest().method).toBe('put')
    expect(lastRequest().url).toBe('/jobs/job-42/restore')
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

describe('写侧请求体的反向断言', () => {
  /** 后端 Pydantic 是 `extra="ignore"`：这些键被整丢掉且不报错，所以「没报错」不等于「存进去了」。 */
  const FORBIDDEN_TOP_LEVEL = ['status', 'created_by_user_id', 'origin', 'evidence_text', 'confidence']
  const FORBIDDEN_SKILL_KEYS = ['origin', 'evidence_text', 'confidence', 'id', 'self_rating']

  it('POST 与 PUT 的请求体里都不出现 status / origin / evidence_text / confidence', async () => {
    const payload: JobsContract.JobWriteRequest = {
      company_name: '示例科技',
      title: '后端实习',
      description: '熟练掌握 Python',
      skills: [{ name: 'Python', type: 'hard' }],
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
})
