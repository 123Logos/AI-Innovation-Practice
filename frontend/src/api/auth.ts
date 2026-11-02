/** 认证接口：`app/api/v1/auth.py` 的三个端点。 */

import { http, unwrap } from './client'
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

/** `POST /api/v1/auth/login` → 200，返回 Bearer Token。 */
export async function login(payload: LoginRequest): Promise<TokenResponse> {
  const response = await http.post<ApiSuccess<TokenResponse>>('/auth/login', payload)
  return unwrap(response)
}

/** `GET /api/v1/auth/me` → 200，需要有效 Token。 */
export async function fetchCurrentUser(): Promise<UserRead> {
  const response = await http.get<ApiSuccess<MeResponse>>('/auth/me')
  return unwrap(response).user
}
