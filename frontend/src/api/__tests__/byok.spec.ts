/**
 * BYOK 客户端单测（ADR-009）。
 *
 * 钉住三条最容易静默坏掉、且**只有前端能发现**的链路：
 *
 * 1. Key 只在 `sessionStorage`，绝不进 `localStorage`。写错的后果是关掉浏览器之后
 *    Key 还在磁盘上——那正是这套架构要避免的事，而且没有任何测试会替你发现。
 * 2. `X-DeepSeek-Key` 只挂在生成请求上。挂到全部请求等于把 Key 发给每一个端点。
 * 3. DeepSeek 的 401（`AI_KEY_INVALID`）不能被 401 拦截器当成「登录过期」。
 *    弄错的表现为「Key 打错 → 用户被踢回登录页」，而他会以为是自己的账号坏了。
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

  if (url.includes('/resumes/generate')) {
    const marker = config.headers['X-Stub-401']
    if (typeof marker === 'string' && marker) {
      return Promise.reject(unauthorized(config, marker))
    }
    return Promise.resolve(response(config, { data: { resume_id: 'r-1' }, meta: { request_id: 'req-test' } }))
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
  it('生成请求带 Key，其他请求不带', async () => {
    const seen: Record<string, string | undefined> = {}
    http.defaults.adapter = async (config) => {
      seen[config.url ?? ''] = config.headers['X-DeepSeek-Key'] as string | undefined
      return response(config, { data: null, meta: { request_id: 'r' } })
    }
    setDeepSeekKey(TEST_KEY)

    await http.post('/resumes/generate', { title: 'x' })
    await http.get('/profiles/me')

    expect(seen['/resumes/generate']).toBe(TEST_KEY)
    expect(seen['/profiles/me']).toBeUndefined()
  })

  it('没有 Key 时生成请求也不带这个头', async () => {
    let header: string | undefined = 'unset'
    http.defaults.adapter = async (config) => {
      header = config.headers['X-DeepSeek-Key'] as string | undefined
      return response(config, { data: null, meta: { request_id: 'r' } })
    }

    await http.post('/resumes/generate', { title: 'x' })

    expect(header).toBeUndefined()
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
      .post('/resumes/generate', { title: 'x' }, { headers: { 'X-Stub-401': 'AI_KEY_INVALID' } })
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
      .post('/resumes/generate', { title: 'x' }, { headers: { 'X-Stub-401': 'SESSION_EXPIRED' } })
      .catch(() => undefined)

    expect(requestedUrls.some((url) => url.includes('/auth/refresh'))).toBe(true)
    expect(requestedUrls.filter((url) => url.includes('/resumes/generate'))).toHaveLength(2)
  })
})
