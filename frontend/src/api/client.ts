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
 *      被踢的那一次还会在 `sessionExpiredNoticeArmed` 上留一个一次性标记，
 *      让登录页能说出「他为什么被送回来了」（arm 的判据在 `router/index.ts`）。
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
  /**
   * 错误信封**外层** `meta.finish_reason`（正常响应里根本没有这个键）。
   *
   * 它唯一的用途是把 `/jobs/extract-skills` 的第 7 情形（输出被服务端 token 上限切断）
   * 与第 3/4/5/6 情形分开：两者同为 502 + `AI_SERVICE_UNAVAILABLE`，错误码一模一样，
   * 而用户该做的下一步完全不同（切断 → 少粘点 JD 内容；其余 → 稍后重试）。
   * 判据只能是这个字段——按文案分支等于把「服务端哪天改措辞」变成一次功能回归。
   */
  readonly finishReason: string | null

  constructor(
    message: string,
    status: number,
    code: string,
    details: unknown = null,
    finishReason: string | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
    this.finishReason = finishReason
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
 * 用户自带的 DeepSeek Key（ADR-009，调用范围见 ADR-010）。
 *
 * 存 `sessionStorage` 而不是 `localStorage`：标签页关掉即消失，不落盘、不同步、
 * 不会被别的标签页读到。它仍然是「能被 XSS 读到」的存储 —— 之所以可以接受，
 * 是因为现在唯一的注入路径是 `ResumePreview.vue` 的 `v-html`，而喂给它的内容由纯模板
 * 生成器 `escape()` 过；AI 产出的是纯文本文案，进的是 textarea 与对照弹窗的文本插值，
 * 连到 `v-html` 的机会都没有。想更保守，就改成每次润色时手输 Key（不存储），代价是体验。
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

/**
 * 哪些请求携带 `X-DeepSeek-Key`（BYOK）。
 *
 * 判据是一份**白名单**而不是模式匹配，且只列真正消耗用户 AI 额度的端点。
 * 今天两条精确路径 + 两条模式：
 * - `/resumes/polish`（ADR-010）
 * - `POST /jobs/extract-skills`（ADR-011 2.A）
 * - `POST /jobs/{id}/match`（ADR-013 §6）
 * - `POST /interviews/{id}/messages`（FR-07 / B11a）：面试官的下一段由 AI 生成，
 *   带凭据时转发 DeepSeek；和岗位匹配同族——没有凭据不会报错，而是走内置题库的可见降级。
 *
 * 为什么岗位侧只扩这三条：`/jobs` 下其余端点（列表 / 创建 / 详情 / 更新 / publish /
 * unpublish / trash / restore / 删除）全部走我们自己的数据库，一个字节都不发往 DeepSeek。
 * 用 `url.includes('/jobs')` 这种宽判据把它们一并纳进来，等于把用户的 Key 复制到
 * 九个与服务毫无必要的接收方——泄漏面按端点个数翻倍，而且这些端点的日志、
 * 代理访问日志、任何一处误打请求头都会带上它。
 * **每扩一个 URL 都必须能回答「这个请求会不会被转发给 DeepSeek」，答不上来就不许加。**
 *
 * 关于那条模式，必须把话说准（ADR-013 §6.1，我上一版把它写大了）：
 * `[^/]+` 只吃一个路径段，所以 `/jobs/x/y/match`、`/jobs/match/extra` 都进不来；
 * 但它**同样命中** `/jobs/extract-skills/match` 这种根本没有路由的路径（返回 404，
 * 一个字节都不发往 DeepSeek）。所以「命中即转发」这句话对不存在的路径是假的。
 * 真正可辩护、且能被测的判据是**路由表论证**：
 * 「凡是能被这条正则命中、且在 FastAPI 里真的注册过的 POST 路由，其处理器一定转发 DeepSeek」——
 * 那条断言在后端 `tests/test_job_match.py::test_only_the_match_route_under_jobs_forwards_to_deepseek`。
 * 它守的是未来有人新加一条 `/jobs/{a}/{b}` 形状而不转发 AI 的路由，那才是这条约定唯一的失效方式。
 *
 * 另外这条模式**绑方法**：只有 POST 才注入。GET `/jobs/{id}/match` 不存在，
 * 但「模式不看方法」这种写法一旦落地，任何路径拼错的 GET 都会带着 Key 出门。
 *
 * 面试那条模式同理要求 `{id}` 是 UUID 形状（后端 `interview_sessions.id` 即 CHAR(36) UUID）：
 * 这样既不会误中 `/interviews/extra/messages` 这种「看起来像、但没有路由」的路径
 * （它返回 404，一个字节都不发往 DeepSeek），也和「命中即转发」的路线表论证一致。
 */
const BYOK_KEY_PATHS = ['/resumes/polish', '/jobs/extract-skills'] as const

const UUID_SEGMENT = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

const BYOK_KEY_PATTERNS: ReadonlyArray<{ method: string; pattern: RegExp }> = [
  { method: 'post', pattern: /^\/jobs\/[^/]+\/match$/ },
  // FR-07 / B11a：面试发消息会转发 AI 凭据（用户填了凭据时）。
  // `{id}` 必须是 UUID 形状：排掉 `/interviews/extra/messages` 这类无路由的近似路径。
  { method: 'post', pattern: new RegExp(`^\\/interviews\\/${UUID_SEGMENT}\\/messages$`) },
]

function isDeepSeekKeyRequest(url: string | undefined, method?: string): boolean {
  if (!url) return false
  // `url` 在这里是 axios 的相对路径（不含 baseURL），可能带查询串；只看路径那一段。
  const path = url.split('?')[0].replace(/\/+$/, '')
  if ((BYOK_KEY_PATHS as readonly string[]).includes(path)) return true
  const normalizedMethod = (method ?? 'get').toLowerCase()
  return BYOK_KEY_PATTERNS.some(
    (entry) => entry.method === normalizedMethod && entry.pattern.test(path),
  )
}

type UnauthorizedHandler = () => void
let unauthorizedHandler: UnauthorizedHandler | null = null

/** 由 `router/index.ts` 注入「跳转登录页」的实现。 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

/* -------------------------- 会话过期的一次性提示 -------------------------- */

/**
 * 「你的登录状态过期了」这条提示的**唯一一份**状态：模块里的一个布尔，读一次即清空。
 *
 * 为什么不用 `?expired=1` 这类 query 标记，也不写任何存储：
 * - query 会跟着 URL 活下来——刷新、后退、收藏、把链接发给别人，都会让一个
 *   早已重新登录的人继续看见「登录状态已过期」。这条提示说的是
 *   「刚刚那一次跳转的原因」，原因只属于那一次跳转。
 * - 写 `localStorage` 更错：提示会跨账号存活，下一个登录进来的人被莫名告知过期。
 * - 内存里这一个布尔随页面卸载自然消失，配合「读取即清空」正好等于「展示过就作废」。
 *
 * 谁来按这个开关（判据）见 `router/index.ts` 的 `setUnauthorizedHandler` 回调：
 * 只有「从受保护页面被送回登录页」才 arm，主动点退出登录与登录失败都不 arm。
 * 这里只提供槽位，不自行决定语义，因为 client 层看不见路由。
 */
let sessionExpiredNoticeArmed = false

export function armSessionExpiredNotice(): void {
  sessionExpiredNoticeArmed = true
}

/** 一次性取走提示标记：返回 `true` 的那一次是唯一一次，之后自动回到 `false`。 */
export function consumeSessionExpiredNotice(): boolean {
  const armed = sessionExpiredNoticeArmed
  sessionExpiredNoticeArmed = false
  return armed
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
      (status === 0 ? '连不上服务。刷新页面再试一次；如果一直打不开，是服务这边的问题。' : '请求失败，请稍后重试。'),
    status,
    payload?.code ?? (status === 0 ? 'NETWORK_ERROR' : 'UNKNOWN_ERROR'),
    payload?.details ?? null,
    readFinishReason(body),
  )
}

/**
 * 从错误信封的**外层** `meta` 里读 `finish_reason`。
 *
 * 为什么不去读 `data.meta`：第 7 情形由 `app/api/v1/jobs.py:144-153` 就地组装，走的是
 * `error_response()`，那个形状里根本没有 `data` 键（`app/core/errors.py:15-23`），
 * `finish_reason` 被追加在外层 `meta` 上；而成功响应的七个计数在 `data.meta`、
 * 外层只有 `request_id`（`app/core/responses.py:10-11`）。同一个名字住在两层里，
 * 读错的那一层拿到 `undefined`，第 7 情形就会被并进「稍后重试」，用户反复重试同一条长 JD。
 *
 * `ApiMeta` 类型里没有这个键（它只描述成功信封的 `request_id`），所以在这里做一次
 * 结构探测而不是改 `types.ts`：那是全局契约，为一个端点的附加字段扩它不值。
 */
function readFinishReason(body: ApiErrorBody | undefined): string | null {
  const meta = (body as { meta?: Record<string, unknown> } | undefined)?.meta
  const value = meta?.finish_reason
  return typeof value === 'string' ? value : null
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

  // BYOK：只有白名单里的三个 AI 端点携带用户自带的 DeepSeek Key（ADR-010 / 011 2.A / 013 §6）。
  if (isDeepSeekKeyRequest(config.url, config.method)) {
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
