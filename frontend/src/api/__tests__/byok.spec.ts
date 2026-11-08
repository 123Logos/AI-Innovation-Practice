/**
 * BYOK 客户端单测（ADR-009 的存储与分流 + ADR-010 的注入范围 + ADR-011 2.A 的岗位扩展）。
 *
 * 钉住三条最容易静默坏掉、且**只有前端能发现**的链路：
 *
 * 1. Key 只在 `sessionStorage`，绝不进 `localStorage`。写错的后果是关掉浏览器之后
 *    Key 还在磁盘上——那正是这套架构要避免的事，而且没有任何测试会替你发现。
 * 2. `X-DeepSeek-Key` 只挂在**真正被转发给 DeepSeek 的端点**上：
 *    `/resumes/polish`（ADR-010）与 `POST /jobs/extract-skills`（ADR-011 2.A）。
 *    挂到全部请求等于把 Key 发给每一个端点；挂回 `/resumes/generate` 则等于承认
 *    「生成还在调 AI」——那条路已经不需要它。注入范围的完整矩阵见下面那个 describe。
 * 3. DeepSeek 的 401（`AI_KEY_INVALID`）不能被 401 拦截器当成「登录过期」。
 *    弄错的表现为「Key 打错 → 用户被踢回登录页」，而他会以为是自己的账号坏了。
 *    分流判据是 code 而不是 URL（ADR-010 §6），所以岗位侧一接上就自动复用同一条分流——
 *    「自动复用」不能靠读代码宣布，下面有一条用例专门走提取请求证明它。
 *
 * 做法：把 axios 的 adapter 换成假的，让**真实拦截器链**跑一遍但不碰网络；
 * 断言看的是最终发出去的请求头和调用次数，不是内部实现细节。
 */

import {
  AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { jobsApi } from '@/api'
import {
  ApiError,
  authHttp,
  DEEPSEEK_KEY_STORAGE_KEY,
  clearDeepSeekKey,
  getDeepSeekKey,
  http,
  setDeepSeekKey,
  setUnauthorizedHandler,
} from '@/api/client'
import type { ApiErrorBody, ApiSuccess } from '@/api/types'

const TEST_KEY = 'sk-frontend-test-key'

/** `defaults.adapter` 的声明类型允许字符串/数组，还原时必须按同一类型存回去。 */
type SavedAdapter = AxiosInstance['defaults']['adapter']

/** adapter 记录下来的请求 url 列表，用来断言「有没有偷偷去刷新令牌」。 */
let requestedUrls: string[] = []
let savedAdapters: Array<[AxiosInstance, SavedAdapter]> = []

/**
 * 两个实例都要 stub：续期走的是**没有拦截器的 `authHttp`**（见 client.ts 设计要点 4），
 * 只换 `http` 的话刷新那一步会真的出网，对照组用例就会以「刷新失败」收场，
 * 看起来像业务坏了，其实是用例自己漏了一处接缝。
 */
function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const url = config.url ?? ''
  requestedUrls.push(url)

  if (url.includes('/auth/refresh')) {
    return Promise.resolve(
      response(config, {
        data: { access_token: 'renewed', refresh_token: 'r2', expires_in: 900 },
        meta: { request_id: 'req-test' },
      } satisfies ApiSuccess<{ access_token: string; refresh_token: string; expires_in: number }>),
    )
  }

  if (url.includes('/resumes/polish')) {
    const marker = config.headers['X-Stub-401']
    if (typeof marker === 'string' && marker) {
      return Promise.reject(unauthorized(config, marker))
    }
    return Promise.resolve(
      response(config, { data: { skills: ['Python'], experiences: [] }, meta: { request_id: 'req-test' } }),
    )
  }

  return Promise.resolve(response(config, { data: null, meta: { request_id: 'req-test' } }))
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  requestedUrls = []
  savedAdapters = [
    [http, http.defaults.adapter],
    [authHttp, authHttp.defaults.adapter],
  ]
  http.defaults.adapter = fakeAdapter
  authHttp.defaults.adapter = fakeAdapter
})

afterEach(() => {
  for (const [instance, adapter] of savedAdapters) {
    instance.defaults.adapter = adapter
  }
  savedAdapters = []
  setUnauthorizedHandler(null)
  clearDeepSeekKey()
})

function response<T>(config: InternalAxiosRequestConfig, data: T, status = 200): AxiosResponse<T> {
  return {
    data,
    status,
    statusText: 'OK',
    headers: {},
    config,
  } as AxiosResponse<T>
}

/** 造一个带后端统一错误体的 401，走 axios 的真实错误形状。 */
function unauthorized(config: InternalAxiosRequestConfig, code: string): AxiosError<ApiErrorBody> {
  const body: ApiErrorBody = {
    error: { code, message: 'stub', details: {}, retryable: false },
    meta: { request_id: 'req-test' },
  }
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, response(config, body, 401))
}

describe('DeepSeek Key 的存储位置', () => {
  it('写入 sessionStorage 并去掉首尾空格，读取拿回同一个值', () => {
    setDeepSeekKey(`  ${TEST_KEY}  `)

    expect(getDeepSeekKey()).toBe(TEST_KEY)
    expect(sessionStorage.getItem(DEEPSEEK_KEY_STORAGE_KEY)).toBe(TEST_KEY)
  })

  it('绝不落到 localStorage', () => {
    setDeepSeekKey(TEST_KEY)

    expect(localStorage.getItem(DEEPSEEK_KEY_STORAGE_KEY)).toBeNull()
    // 顺带确认没有别的键被误写（Key 只应存在于 sessionStorage）。
    expect(Object.keys(localStorage)).toEqual([])
  })

  it('清除后读不到，sessionStorage 里也不留残值', () => {
    setDeepSeekKey(TEST_KEY)
    clearDeepSeekKey()

    expect(getDeepSeekKey()).toBe('')
    expect(sessionStorage.getItem(DEEPSEEK_KEY_STORAGE_KEY)).toBeNull()
  })
})

describe('X-DeepSeek-Key 的注入范围', () => {
  it('润色请求带 Key，生成请求与其他请求都不带', async () => {
    const seen: Record<string, string | undefined> = {}
    http.defaults.adapter = async (config) => {
      seen[config.url ?? ''] = config.headers['X-DeepSeek-Key'] as string | undefined
      return response(config, { data: null, meta: { request_id: 'r' } })
    }
    setDeepSeekKey(TEST_KEY)

    await http.post('/resumes/polish', { mode: 'medium' })
    await http.post('/resumes/generate', { title: 'x' })
    await http.get('/profiles/me')

    expect(seen['/resumes/polish']).toBe(TEST_KEY)
    // ADR-010 之后生成路径不再调 AI：带上 Key 就是白送一次泄漏面。
    expect(seen['/resumes/generate']).toBeUndefined()
    expect(seen['/profiles/me']).toBeUndefined()
  })

  it('没有 Key 时润色请求也不带这个头', async () => {
    let header: string | undefined = 'unset'
    http.defaults.adapter = async (config) => {
      header = config.headers['X-DeepSeek-Key'] as string | undefined
      return response(config, { data: null, meta: { request_id: 'r' } })
    }

    await http.post('/resumes/polish', { mode: 'medium' })

    expect(header).toBeUndefined()
  })
})

/**
 * 本轮（C2）唯一的鉴权面扩张：Key 从「只有润色」扩到「润色 + `POST /jobs/extract-skills`」。
 * 这条矩阵是本轮最重要的一组断言，**钉满两侧**：
 *
 * - 正向两个：漏一个 = 功能直接坏（后端 400 `AI_KEY_MISSING`）。
 * - 反向八个：多一个 = Key 泄漏面按端点个数扩大。`/jobs` 下那七个端点全部只走我们
 *   自己的数据库，一个字节都不发往 DeepSeek，带上 Key 纯属白送；而它们都会进
 *   访问日志、代理日志、异常上报——每一处都是多一份泄漏点。
 *
 * 全部通过 `jobsApi` 的**真实封装**发请求，而不是手写 URL 字符串：
 * 手写 URL 的测试只能证明「匹配器认识我编的那个串」，走封装才能证明
 * 「`api/jobs.ts` 里那条真实路径确实在白名单里」——两处任一边改动都会让这组断言变红。
 *
 * `spy` 记录的是拦截器处理完之后、真正交给 adapter 的请求头（即出线那一刻）。
 */
describe('Key 注入矩阵：岗位侧只扩 `/jobs/extract-skills` 这一个 URL', () => {
  /** url → 出线时看到的 `X-DeepSeek-Key`。用数组而不是对象，避免同 URL 覆盖。 */
  let sentHeaders: Array<{ url: string; key: string | undefined; method: string }>

  /** 一份能同时喂饱 `listJobs` / `getJob` / `createJob` / `extractJobSkills` 的信封。 */
  function universalEnvelope(config: InternalAxiosRequestConfig): AxiosResponse {
    return response(config, {
      data: {
        jobs: [],
        job: { id: 'job-1' },
        skills: [],
        // 提取结果的七个计数住在 `data.meta`；这里给最小可用值。
        meta: {
          model: 'deepseek-flash',
          prompt_version: 'v1',
          received_count: 0,
          dropped_count: 0,
          deduped_count: 0,
          truncated: false,
          finish_reason: 'stop',
        },
      },
      meta: { request_id: 'r', page: 1, page_size: 50, total: 0 },
    })
  }

  beforeEach(() => {
    sentHeaders = []
    http.defaults.adapter = async (config) => {
      sentHeaders.push({
        url: config.url ?? '',
        method: (config.method ?? 'get').toLowerCase(),
        key: config.headers['X-DeepSeek-Key'] as string | undefined,
      })
      return universalEnvelope(config)
    }
    setDeepSeekKey(TEST_KEY)
  })

  /** 取某个「方法 + 路径」最后一次出线时的 Key。 */
  function keyFor(method: string, url: string): string | undefined | 'MISSING' {
    const hit = sentHeaders.filter((item) => item.method === method && item.url === url).pop()
    return hit ? hit.key : 'MISSING'
  }

  it('两个 AI 端点带 Key：/resumes/polish 与 /jobs/extract-skills', async () => {
    await http.post('/resumes/polish', { mode: 'medium' })
    await jobsApi.extractJobSkills({ description: '熟练掌握 Python', title: '后端实习' })

    expect(keyFor('post', '/resumes/polish')).toBe(TEST_KEY)
    expect(keyFor('post', '/jobs/extract-skills')).toBe(TEST_KEY)
    // 反向确认：这两次确实各发出了一次，不是断言了一个空集合。
    expect(sentHeaders.map((item) => item.url)).toEqual(['/resumes/polish', '/jobs/extract-skills'])
  })

  it('`/jobs` 下其余九个端点一律不带 Key（含第 3c 条的 requirement-match）', async () => {
    // 这一串就是「宽判据会把 Key 送到哪儿」的清单：`includes('/jobs')` 会把它们全纳进去。
    await jobsApi.listJobs({ status: 'draft' })
    await jobsApi.getJob('job-42')
    await jobsApi.createJob({ company_name: 'c', title: 't', description: 'd', skills: [] })
    await jobsApi.updateJob('job-42', { company_name: 'c', title: 't', description: 'd', skills: [] })
    await jobsApi.publishJob('job-42')
    await jobsApi.unpublishJob('job-42')
    await jobsApi.listJobTrash()
    await jobsApi.restoreJob('job-42')
    await jobsApi.deleteJob('job-42')
    // 第 3c 条：这条 GET 是纯计算的只读端点，**不进**白名单。Key 在这里没有转发对象——
    // 后端 `requirement_match()` 不 import `app/integrations`，一次出网请求都不产生。
    await jobsApi.getRequirementMatch('job-42')

    expect(sentHeaders.length).toBe(10)
    for (const item of sentHeaders) {
      expect(item.key, `${item.method} ${item.url} 不该携带 DeepSeek Key`).toBeUndefined()
    }
    // 逐条列出被访问过的路径：万一封装改了 URL，上面那个循环会因为集合变了而假绿。
    expect(sentHeaders.map((item) => `${item.method} ${item.url}`).sort()).toEqual([
      'delete /jobs/job-42',
      'get /jobs',
      'get /jobs/job-42',
      'get /jobs/job-42/requirement-match',
      'get /jobs/trash',
      'post /jobs',
      'put /jobs/job-42',
      'put /jobs/job-42/publish',
      'put /jobs/job-42/restore',
      'put /jobs/job-42/unpublish',
    ])
  })

  it('第三条模式判据认不到 requirement-match：同形不同尾，注入面不扩张', async () => {
    // 这条是「白名单第 3 条是 `[^/]+/match` 整尾」的直接后果：
    // `/jobs/{id}/requirement-match` 与它同形（一段 id + 一段静态尾），
    // 但尾段不同。如果谁把判据松成 `/jobs/[^/]+/.*match` 或 `includes('match')`，
    // 用户的 Key 就会被送到一条**根本不转发 DeepSeek** 的只读端点上——那是 2026-11-07
    // 「答不上来就不许加」明确挡住的那类错误。
    await http.get('/jobs/job-42/requirement-match')
    await http.get('/jobs/job-42/match') // 换方法（GET 而非 POST）也不算注入面

    expect(sentHeaders.map((item) => item.key)).toEqual([undefined, undefined])
  })

  it('近似路径也不带：`/jobs/{id}/extract-skills`、`/jobs/extract-skills/extra`、`/jobs/extract-skills-today`', async () => {
    // 这三个都是「按 `includes` 写判据时会一起命中」的形状；判据必须做到整段相等。
    await http.get('/jobs/job-42/extract-skills')
    await http.post('/jobs/extract-skills/extra', {})
    await http.post('/jobs/extract-skills-today', {})
    await http.post('/jobs/extract-skills/', {}) // 尾斜杠归一后**应当**命中，是白名单内的那条

    expect(sentHeaders[0]?.key).toBeUndefined()
    expect(sentHeaders[1]?.key).toBeUndefined()
    expect(sentHeaders[2]?.key).toBeUndefined()
    expect(sentHeaders[3]?.key).toBe(TEST_KEY)
  })

  it('白名单第三条是**唯一一条模式**：`POST /jobs/{id}/match` 带 Key', async () => {
    // ADR-013 §6.1 的「正向」断言。id 故意取两种形状：一个像 uuid，一个完全不像——
    // 判据如果不小心被写成 `[0-9a-f-]{36}` 这种「更像 id」的模式，第二条就会红。
    await http.post('/jobs/3f2a1b9c-0000-4000-8000-000000000001/match')
    await http.post('/jobs/not-a-uuid/match')

    expect(sentHeaders.map((item) => item.key)).toEqual([TEST_KEY, TEST_KEY])
  })

  it('这条模式不扩张：多一段、少一段、换方法、都不注入', async () => {
    // 前四条是模式本身不该吃到的形状；最后一条是 §6.1 里「判据绑方法」这条纪律：
    // GET `/jobs/{id}/match` 今天不存在，但「模式不看方法」一旦落地，
    // 任何拼错的 GET 都会带着用户的 Key 出门。
    await http.post('/jobs/a/b/match')
    await http.post('/jobs/match')
    await http.post('/jobs/1/match/extra')
    await http.post('/resumes/polish/match')
    await http.get('/jobs/1/match')

    for (const item of sentHeaders) {
      expect(item.key, `${item.method} ${item.url} 不该携带 DeepSeek Key`).toBeUndefined()
    }
  })

  it('归一化后的两种写法仍算同一条：尾斜杠与查询串', async () => {
    // `client.ts` 先剥查询串、再剥尾斜杠，然后才比判据——这条次序是既有行为，
    // 新模式必须沿用，否则「同一个端点带不带 Key」会取决于 URL 怎么写。
    await http.post('/jobs/1/match/')
    await http.post('/jobs/1/match?retry=1')
    await http.post('/jobs/1/match/?retry=1')

    expect(sentHeaders.map((item) => item.key)).toEqual([TEST_KEY, TEST_KEY, TEST_KEY])
  })

  it('`matchJob()` 走的是那条模式，而不是手工加头', async () => {
    await jobsApi.matchJob('job-42')
    expect(sentHeaders).toHaveLength(1)
    expect(sentHeaders[0]).toMatchObject({
      method: 'post',
      url: '/jobs/job-42/match',
      key: TEST_KEY,
    })
  })

  it('提取请求撞 AI_KEY_INVALID(401) 时不进刷新重放、不踢下线（复用既有分流）', async () => {
    localStorage.setItem('ai-career.access-token', 'old-access')
    localStorage.setItem('ai-career.refresh-token', 'old-refresh')
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)

    // 后端第 2 情形的真实形状：401 + `error.code = AI_KEY_INVALID`（`app/main.py` 的处理器）。
    http.defaults.adapter = async (config) => {
      requestedUrls.push(config.url ?? '')
      return Promise.reject(unauthorized(config, 'AI_KEY_INVALID'))
    }

    const error = await jobsApi.extractJobSkills({ description: '熟练掌握 Python' }).catch((e) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('AI_KEY_INVALID')
    expect((error as ApiError).status).toBe(401)
    // 用户要修的是 AI 设置里的 Key，不是重新登录：刷新一次都不该发。
    expect(requestedUrls.filter((url) => url.includes('/auth/refresh'))).toEqual([])
    expect(onUnauthorized).not.toHaveBeenCalled()
    expect(localStorage.getItem('ai-career.access-token')).toBe('old-access')
    // 原请求也只打了一次（没有重放）。
    expect(requestedUrls.filter((url) => url.includes('/jobs/extract-skills'))).toHaveLength(1)
  })
})

describe('401 分流：登录过期 vs Key 无效', () => {
  /** 让「能续期」的前置条件成立：有 refresh token、有 access token。 */
  function seedSession(): void {
    localStorage.setItem('ai-career.access-token', 'old-access')
    localStorage.setItem('ai-career.refresh-token', 'old-refresh')
  }

  it('AI_KEY_INVALID 不触发令牌刷新，也不踢用户下线', async () => {
    seedSession()
    setDeepSeekKey(TEST_KEY)
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)

    const error = await http
      .post('/resumes/polish', { mode: 'medium' }, { headers: { 'X-Stub-401': 'AI_KEY_INVALID' } })
      .catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('AI_KEY_INVALID')
    expect((error as ApiError).status).toBe(401)
    expect(requestedUrls.filter((url) => url.includes('/auth/refresh'))).toEqual([])
    expect(onUnauthorized).not.toHaveBeenCalled()
    // 会话必须原样保留：Key 无效跟登录态没有关系。
    expect(localStorage.getItem('ai-career.access-token')).toBe('old-access')
  })

  it('对照组：普通 401 仍然会刷新令牌并重放请求', async () => {
    seedSession()

    // 重放的那一次仍带着同一个 stub 头，所以最终还是会失败；这里要的是**调用序列**。
    await http
      .post('/resumes/polish', { mode: 'medium' }, { headers: { 'X-Stub-401': 'SESSION_EXPIRED' } })
      .catch(() => undefined)

    expect(requestedUrls.some((url) => url.includes('/auth/refresh'))).toBe(true)
    expect(requestedUrls.filter((url) => url.includes('/resumes/polish'))).toHaveLength(2)
  })
})
