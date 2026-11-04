/**
 * axios 实例、JWT 注入与静默续期。
 *
 * 设计要点：
 * 1. `baseURL` 固定为 `/api/v1`，开发期由 Vite 代理转发到 `http://localhost:8000`，
 *    生产期由同域反向代理转发，因此前端永远不发跨域请求。
 * 2. 会话的真相是 `localStorage`（access token + refresh token + access 过期时刻），
 *    Pinia 里的 token 只是它的镜像；这样刷新页面不会掉线，也不会出现「两个真相」。
 * 3. 401 统一处理分两步：
 *    - 能续期就先续期，再重放原请求（Single-flight，见 `refreshSession`）；
 *    - 续不了才清 Token + 通知上层跳登录页（跳转动作由 `router` 注入，
 *      避免 client 反向依赖 router 造成循环引用）。
 * 4. 续期请求走 `authHttp`（**没有拦截器**的裸实例）：
 *    若走带拦截器的 `http`，刷新失败产生的 401 会再次进入 401 分支，
 *    形成「刷新失败 → 触发刷新 → 刷新失败」的自激循环，且会被判定为业务请求。
 */

import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'

import type { ApiErrorBody, ApiSuccess, TokenResponse } from './types'

export const TOKEN_STORAGE_KEY = 'ai-career.access-token'
export const REFRESH_TOKEN_STORAGE_KEY = 'ai-career.refresh-token'
export const TOKEN_EXPIRES_AT_KEY = 'ai-career.access-token-expires-at'

/**
 * 提前量：距离过期不足 10 秒就当作「已过期」主动续期。
 *
 * 留这点余量是为了让「即将过期的请求」不要刚好卡在服务端判断的边界上：
 * 请求在途的几百毫秒里 Token 到期，就会白跑一次 401 + 重放。
 */
const REFRESH_LEEWAY_MS = 10_000

/** 后端 T6 统一错误模型的前端映射，UI 只认这个类型。 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(message: string, status: number, code: string, details: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

/* ------------------------------- 本地存储 -------------------------------- */

function readItem(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeItem(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // 隐私模式下 localStorage 可能不可写；此时仅在内存中保留会话。
  }
}

function removeItem(key: string): void {
  try {
    window.localStorage.removeItem(key)
  } catch {
    // 同上，忽略。
  }
}

export function getStoredToken(): string | null {
  return readItem(TOKEN_STORAGE_KEY)
}

export function getStoredRefreshToken(): string | null {
  return readItem(REFRESH_TOKEN_STORAGE_KEY)
}

/** access token 的过期时刻（毫秒时间戳）；没记录过返回 `null`。 */
export function getTokenExpiresAt(): number | null {
  const raw = readItem(TOKEN_EXPIRES_AT_KEY)
  if (!raw) return null
  const value = Number(raw)
  return Number.isFinite(value) && value > 0 ? value : null
}

/** 登录 / 刷新拿到的双 Token；`expires_in` 只描述 access token 的寿命（秒）。 */
export interface SessionTokens {
  access_token: string
  refresh_token: string
  expires_in: number
}

/**
 * 落盘一整套会话。
 *
 * 过期时刻按**客户端当前时钟**换算成绝对时间戳，读取时同样用 `Date.now()`
 * 比较，所以客户端时钟快慢不影响判断（两端都用相对差），只有跨设备不同步才有影响。
 */
export function storeSession(tokens: SessionTokens): void {
  writeItem(TOKEN_STORAGE_KEY, tokens.access_token)
  writeItem(REFRESH_TOKEN_STORAGE_KEY, tokens.refresh_token)
  writeItem(TOKEN_EXPIRES_AT_KEY, String(Date.now() + tokens.expires_in * 1000))
}

/**
 * 清空整套会话（access + refresh + 过期时刻）。
 *
 * 名字保留 `clearStoredToken` 是为了不打断既有调用方（`stores/auth.ts`），
 * 语义已从「只清 access token」扩大为「清掉本地整个会话」——
 * 只清 access 会留下一个孤儿 refresh token，下次刷新又会把用户「救回来」。
 */
export function clearStoredToken(): void {
  removeItem(TOKEN_STORAGE_KEY)
  removeItem(REFRESH_TOKEN_STORAGE_KEY)
  removeItem(TOKEN_EXPIRES_AT_KEY)
}

/* -------------------------------- BYOK ---------------------------------- */

/**
 * 用户自带的 DeepSeek Key（ADR-009）。
 *
 * 存 `sessionStorage` 而不是 `localStorage`：标签页关掉即消失，不落盘、不同步、
 * 不会被别的标签页读到。它仍然是「能被 XSS 读到」的存储 —— 之所以可以接受，
 * 是因为生成物的注入路径只有一条（`ResumePreview.vue` 的 `v-html`），
 * 而那条路径上的每个用户字段都经过后端 `escape()`，AI 也只被允许产出纯文本文案。
 * 想更保守，就改成每次生成时手输 Key（不存储），代价是体验。
 */
export const DEEPSEEK_KEY_STORAGE_KEY = 'deepseek-api-key'

/** 内存镜像：隐私模式下 `sessionStorage` 写不进去，本次会话内仍要能用。 */
let memoryDeepSeekKey = ''

export function getDeepSeekKey(): string {
  try {
    const stored = window.sessionStorage.getItem(DEEPSEEK_KEY_STORAGE_KEY)?.trim()
    if (stored) return stored
  } catch {
    // 读不到就当没有，回落到内存镜像。
  }
  return memoryDeepSeekKey
}

export function setDeepSeekKey(value: string): void {
  const trimmed = value.trim()
  memoryDeepSeekKey = trimmed
  if (!trimmed) {
    try {
      window.sessionStorage.removeItem(DEEPSEEK_KEY_STORAGE_KEY)
    } catch {
      // 忽略：内存镜像已经清掉了。
    }
    return
  }
  try {
    window.sessionStorage.setItem(DEEPSEEK_KEY_STORAGE_KEY, trimmed)
  } catch {
    // 隐私模式下写不进去：只在本次标签页的内存里保留，刷新即失。
  }
}

export function clearDeepSeekKey(): void {
  memoryDeepSeekKey = ''
  try {
    window.sessionStorage.removeItem(DEEPSEEK_KEY_STORAGE_KEY)
  } catch {
    // 同上，忽略。
  }
}

export function hasDeepSeekKey(): boolean {
  return getDeepSeekKey().length > 0
}

/** 生成请求才带 Key：其他接口带一个无关的鉴权头毫无意义，还多一处泄漏面。 */
function isResumeGenerateRequest(url: string | undefined): boolean {
  if (!url) return false
  return url.includes('/resumes/generate')
}

type UnauthorizedHandler = () => void
let unauthorizedHandler: UnauthorizedHandler | null = null

/** 由 `router/index.ts` 注入「跳转登录页」的实现。 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

type SessionChangeHandler = (tokens: SessionTokens | null) => void

let sessionChangeHandler: SessionChangeHandler | null = null

/**
 * 由 `stores/auth.ts` 注入「会话变了」的回调。
 *
 * 拦截器在组件上下文之外完成续期，没法直接改 Pinia 里的内存镜像，
 * 所以用回调把新 Token（或 `null`＝已失效）推给 store，避免出现
 * 「localStorage 有新 Token、`isAuthenticated` 却还看旧值」的撕裂状态。
 */
export function setSessionChangeHandler(handler: SessionChangeHandler | null): void {
  sessionChangeHandler = handler
}

/* -------------------------------- 实例 ---------------------------------- */

/** 业务请求实例：注入 Authorization，并负责 401 续期与重放。 */
export const http: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

/**
 * 认证裸实例：只用于 `/auth/refresh` 与 `/auth/logout`。
 *
 * 不带任何拦截器 —— 刷新失败必须是「客户端的普通失败」，
 * 而不是又一轮 401 处理。
 */
export const authHttp: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

/* ------------------------------ 错误归一化 ------------------------------- */

/** `AxiosError` → `ApiError`：把 T6 的 `{ error, meta }` 拆成 UI 好用的形状。 */
function toApiError(error: AxiosError<ApiErrorBody>): ApiError {
  const status = error.response?.status ?? 0
  const body = error.response?.data
  const payload = body && typeof body === 'object' && 'error' in body ? body.error : undefined

  return new ApiError(
    payload?.message ??
      (status === 0 ? '无法连接到服务，请确认后端已启动。' : '请求失败，请稍后重试。'),
    status,
    payload?.code ?? (status === 0 ? 'NETWORK_ERROR' : 'UNKNOWN_ERROR'),
    payload?.details ?? null,
  )
}

const SESSION_EXPIRED_MESSAGE = '登录状态已失效，请重新登录。'

function sessionExpiredError(): ApiError {
  return new ApiError(SESSION_EXPIRED_MESSAGE, 401, 'SESSION_EXPIRED')
}

/* ------------------------------- 续期执行器 ------------------------------ */

/** 会话彻底失效：清本地 → 通知 store → 通知上层跳登录页。三步都幂等。 */
function expireSession(): void {
  clearStoredToken()
  sessionChangeHandler?.(null)
  unauthorizedHandler?.()
}

/** 刷新 `/auth/refresh` 拿新双 Token（走裸实例，不触发拦截器）。 */
export async function requestTokenRefresh(refreshToken: string): Promise<TokenResponse> {
  const response = await authHttp.post<ApiSuccess<TokenResponse>>('/auth/refresh', {
    refresh_token: refreshToken,
  })
  return response.data.data
}

/** 撤销 refresh token（服务端幂等，恒 204）。 */
export async function requestLogout(refreshToken: string): Promise<void> {
  await authHttp.post('/auth/logout', { refresh_token: refreshToken })
}

/**
 * 正在进行的刷新（即 `isRefreshing`）。
 *
 * `null` = 空闲。并发请求共用同一个 Promise 而不是各自发起刷新：
 * 后端的刷新是**一次性**的（旧令牌用一次即作废），两个并发刷新里必然有一个
 * 拿着已作废的令牌，表现为「刷新成功后立刻又掉线」。
 */
let refreshPromise: Promise<string> | null = null

export function isRefreshing(): boolean {
  return refreshPromise !== null
}

/** 当前 access token 是否已过期或即将过期。 */
function isAccessTokenStale(): boolean {
  const expiresAt = getTokenExpiresAt()
  // 没有过期时刻（老会话 / 存储被清）时不猜，交给 401 兜底。
  if (expiresAt === null) return false
  return Date.now() >= expiresAt - REFRESH_LEEWAY_MS
}

/**
 * 静默续期：返回新的 access token。
 *
 * 所有调用方共享同一次网络往返；失败时统一清场并抛出 `ApiError`。
 */
export function refreshSession(): Promise<string> {
  if (refreshPromise) return refreshPromise

  refreshPromise = runRefresh().finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

async function runRefresh(): Promise<string> {
  const refreshToken = getStoredRefreshToken()
  if (!refreshToken) {
    expireSession()
    throw sessionExpiredError()
  }

  try {
    const tokens = await requestTokenRefresh(refreshToken)
    storeSession(tokens)
    sessionChangeHandler?.(tokens)
    return tokens.access_token
  } catch (error) {
    // 验签失败 / 已撤销 / 已过期：本地已无法自愈，必须回登录页。
    expireSession()
    throw axios.isAxiosError(error) ? toApiError(error) : sessionExpiredError()
  }
}

/* ------------------------------- 拦截器 ---------------------------------- */

/** 登录 / 注册 / 刷新 / 登出自身不参与「续期再重放」，否则会自我递归。 */
function isAuthEntrypoint(url: string | undefined): boolean {
  if (!url) return false
  return (
    url.includes('/auth/login') ||
    url.includes('/auth/register') ||
    url.includes('/auth/refresh') ||
    url.includes('/auth/logout')
  )
}

/** 只对「需要登录态」的请求做提前续期；认证入口直接跳过。 */
function shouldRefreshAhead(url: string | undefined): boolean {
  return !isAuthEntrypoint(url)
}

interface RetryableConfig extends InternalAxiosRequestConfig {
  /** 已经因 401 续期重放过一次；再 401 就说明令牌本身没用，不再循环。 */
  _retried?: boolean
}

http.interceptors.request.use(async (config) => {
  if (shouldRefreshAhead(config.url) && getStoredRefreshToken() && isAccessTokenStale()) {
    // 失败时直接抛给调用方：`runRefresh` 内部已经清场并跳转，
    // 这里再发一次必然 401 的请求没有意义。
    await refreshSession()
  }

  const token = getStoredToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }

  // BYOK：只有生成简历这一个接口需要用户自带的 DeepSeek Key（ADR-009）。
  if (isResumeGenerateRequest(config.url)) {
    const deepSeekKey = getDeepSeekKey()
    if (deepSeekKey) {
      config.headers['X-DeepSeek-Key'] = deepSeekKey
    }
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorBody>) => {
    const apiError = toApiError(error)

    if (apiError.status !== 401) {
      return Promise.reject(apiError)
    }

    // BYOK 的 401 说的是「你的 DeepSeek Key 无效/欠费」，与登录态无关。
    // 不先分流就会走进下面的刷新重放，最坏情况是把用户踢回登录页，
    // 而他要修的其实是 AI 设置里的 Key。
    if (apiError.code === 'AI_KEY_INVALID') {
      return Promise.reject(apiError)
    }

    const config = error.config as RetryableConfig | undefined

    // 1) 登录失败 / 刷新失败 / 已重放过仍 401：不再尝试续期。
    if (isAuthEntrypoint(config?.url) || config?._retried) {
      expireSession()
      return Promise.reject(apiError)
    }

    // 2) 没有 refresh token，或拿不到原始请求配置：无从续期。
    if (!config || !getStoredRefreshToken()) {
      expireSession()
      return Promise.reject(apiError)
    }

    // 3) 续期。并发的多个 401 会在这里挂起，
    //    共享 `refreshSession()` 返回的同一个 Promise（Single-flight）。
    let accessToken: string
    try {
      accessToken = await refreshSession()
    } catch (refreshError) {
      // `runRefresh` 已经清场并触发跳转；这里只负责让原请求失败。
      return Promise.reject(isApiError(refreshError) ? refreshError : apiError)
    }

    // 4) 用新令牌重放原请求。置 `_retried` 保证只重放一次。
    config._retried = true
    config.headers.Authorization = `Bearer ${accessToken}`
    return http.request(config)
  },
)

/** 统一拆掉 `{ data, meta }` 外壳，业务代码只拿到 `data`。 */
export function unwrap<T>(response: AxiosResponse<ApiSuccess<T>>): T {
  return response.data.data
}
