/** 档案接口：`app/api/v1/profiles.py`。 */

import { http, unwrap } from './client'
import type {
  ApiSuccess,
  ProfileRead,
  ProfileResponse,
  ProfileUpsertRequest,
} from './types'

/**
 * `GET /api/v1/profiles/me`
 *
 * 用户还没填过档案时后端返回 `{ profile: null }`（不是 404），
 * 这里如实翻译成 `null`，让调用方用「空表单」而不是「异常」来处理首次进入。
 */
export async function getMyProfile(): Promise<ProfileRead | null> {
  const response = await http.get<ApiSuccess<ProfileResponse>>('/profiles/me')
  return unwrap(response).profile
}

/** `PUT /api/v1/profiles/me`，整体替换档案、技能与经历。 */
export async function saveMyProfile(payload: ProfileUpsertRequest): Promise<ProfileRead> {
  const response = await http.put<ApiSuccess<ProfileResponse>>('/profiles/me', payload)
  const updated = unwrap(response).profile
  if (!updated) {
    throw new Error('服务端未返回档案数据')
  }
  return updated
}
