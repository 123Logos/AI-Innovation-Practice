/**
 * axios 实例与 JWT 注入。
 *
 * 设计要点：
 * 1. `baseURL` 固定为 `/api/v1`，开发期由 Vite 代理转发到 `http://localhost:8000`，
 *    生产期由同域反向代理转发，因此前端永远不发跨域请求。
 * 2. Token 只存 `localStorage`，Pinia 里的 token 是它的镜像；
 *    这样刷新页面不会掉线，也不会出现「两个真相」。
 * 3. 401 统一处理：清 Token + 通知上层跳登录页。
 *    跳转动作由 `main.ts` 注入，避免 client 反向依赖 router 造成循环引用。
 */

import axios, { AxiosError, type AxiosInstance, type AxiosResponse } from 'axios'

import type { ApiErrorBody, ApiSuccess } from './types'

export const TOKEN_STORAGE_KEY = 'ai-career.access-token'

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

export function getStoredToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY)
  } catch {
    return null
  }
}

export function setStoredToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    // 隐私模式下 localStorage 可能不可写；此时仅在内存中保留会话。
  }
}

export function clearStoredToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY)
  } catch {
    // 同上，忽略。
  }
}

type UnauthorizedHandler = () => void

let unauthorizedHandler: UnauthorizedHandler | null = null

/** 由 `main.ts` 注入「跳转登录页」的实现。 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

/** 登录/注册自身的 401 是业务结果（账号密码错），不该触发全局跳转。 */
function isAuthEntrypoint(url: string | undefined): boolean {
  if (!url) return false
  return url.includes('/auth/login') || url.includes('/auth/register')
}

export const http: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

http.interceptors.request.use((config) => {
  const token = getStoredToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorBody>) => {
    const status = error.response?.status ?? 0
    const body = error.response?.data
    const payload = body && typeof body === 'object' && 'error' in body ? body.error : undefined

    const apiError = new ApiError(
      payload?.message ??
        (status === 0 ? '无法连接到服务，请确认后端已启动。' : '请求失败，请稍后重试。'),
      status,
      payload?.code ?? (status === 0 ? 'NETWORK_ERROR' : 'UNKNOWN_ERROR'),
      payload?.details ?? null,
    )

    if (status === 401 && !isAuthEntrypoint(error.config?.url)) {
      clearStoredToken()
      unauthorizedHandler?.()
    }

    return Promise.reject(apiError)
  },
)

/** 统一拆掉 `{ data, meta }` 外壳，业务代码只拿到 `data`。 */
export function unwrap<T>(response: AxiosResponse<ApiSuccess<T>>): T {
  return response.data.data
}
