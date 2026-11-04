/**
 * 前端 API 层统一出口。
 *
 * 视图与 store 只从这里 import，不直接依赖 axios 或具体端点模块，
 * 便于后续替换 mock / 增加请求重试而不用改业务代码。
 */

export * as authApi from './auth'
export * as profilesApi from './profiles'
export * as resumesApi from './resumes'

export {
  ApiError,
  clearDeepSeekKey,
  getDeepSeekKey,
  hasDeepSeekKey,
  isApiError,
  setDeepSeekKey,
  TOKEN_STORAGE_KEY,
} from './client'
export type * from './types'
