/**
 * 认证接口：`app/api/v1/auth.py` 的全部端点。
 *
 * `/auth/refresh` 与 `/auth/logout` 走 `client.ts` 的裸实例：
 * 它们本身是「修复登录态」的动作，进带拦截器的实例会在失败时
 * 触发又一次续期尝试，形成自激循环。
 */

import { http, requestLogout, requestTokenRefresh, unwrap } from './client'
import type {
  ApiSuccess,
  LoginRequest,
  MeResponse,
  RegisterRequest,
  RegisterResponse,
  TokenResponse,
  UserRead,
} from './types'

/** `POST /api/v1/auth/register` → 201，返回创建出来的用户（不含密码哈希）。 */
export async function register(payload: RegisterRequest): Promise<UserRead> {
  const response = await http.post<ApiSuccess<RegisterResponse>>('/auth/register', payload)
  return unwrap(response).user
}

/** `POST /api/v1/auth/login` → 200，返回 access + refresh 双 Token。 */
export async function login(payload: LoginRequest): Promise<TokenResponse> {
  const response = await http.post<ApiSuccess<TokenResponse>>('/auth/login', payload)
  return unwrap(response)
}

/**
 * `GET /api/v1/auth/me` 的完整响应：`user` + `role` + `organization` + `permissions`。
 *
 * 后端从 B5 起就把这四样放在同一层（`ADR-021` §2.6），但前端长期只取了 `user`，
 * 于是「有没有某个权限」在前端无处可问，只能靠角色名猜——而角色名不是权限边界，
 * 后端每个端点都自己判一次（`app/core/permissions.py` 的模块纪律）。
 * B6 审核流需要「有 `job:review` 才显示审核入口」，所以在这里把上下文完整取回来。
 */
export async function fetchCurrentContext(): Promise<MeResponse> {
  const response = await http.get<ApiSuccess<MeResponse>>('/auth/me')
  return unwrap(response)
}

/** `GET /api/v1/auth/me` → 200，需要有效 Token。只要用户本身时用这个。 */
export async function fetchCurrentUser(): Promise<UserRead> {
  const current = await fetchCurrentContext()
  return current.user
}

/**
 * `POST /api/v1/auth/refresh` → 200，旧 refresh token 立即作废（轮换）。
 *
 * 拦截器内部的自动续期走的是同一个实现（`client.refreshSession`），
 * 这里导出是为了让业务侧能在明确时机主动续期（例如「离开页面前保活」）。
 */
export function refresh(refreshToken: string): Promise<TokenResponse> {
  return requestTokenRefresh(refreshToken)
}

/** `POST /api/v1/auth/logout` → 204，幂等撤销 refresh token。 */
export function logout(refreshToken: string): Promise<void> {
  return requestLogout(refreshToken)
}
