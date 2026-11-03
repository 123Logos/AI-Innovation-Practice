/**
 * 档案接口：`app/api/v1/profiles.py`（D4.1 多份简历 + 回收站）。
 *
 * 端点分两类，别混用：
 * - **按 id**：`/profiles/{id}`，多份简历的主通道；
 * - **`/me` 别名**：永远指向「默认简历」，只为兼容 D4.2 之前的老前端保留。
 *
 * 越权与不存在的 id 后端都返回 404（不返回 403，避免泄露「该 id 存在」），
 * 所以调用方拿到 404 只需当作「这份简历没了」，不必区分两种情况。
 */

import { http, unwrap } from './client'
import type {
  ApiSuccess,
  ProfileCreateRequest,
  ProfileDetailResponse,
  ProfileListQuery,
  ProfileListResponse,
  ProfileRead,
  ProfileResponse,
  ProfileSummary,
  ProfileUpsertRequest,
} from './types'

/* --------------------------------- 列表 ---------------------------------- */

/**
 * `GET /api/v1/profiles`，不含回收站。
 *
 * 排序（D4.3）：默认 `updated_at desc`（最近改过的在前）。
 * 后端对字段做白名单校验，传错值会 422 而不是静默忽略——所以这里也用联合类型收口。
 */
export async function listProfiles(options: ProfileListQuery = {}): Promise<ProfileSummary[]> {
  const response = await http.get<ApiSuccess<ProfileListResponse>>('/profiles', {
    params: {
      order_by: options.orderBy ?? 'updated_at',
      order: options.order ?? 'desc',
    },
  })
  return unwrap(response).profiles
}

/** `GET /api/v1/profiles/trash`，回收站，最近删除的在前。 */
export async function listTrashProfiles(): Promise<ProfileSummary[]> {
  const response = await http.get<ApiSuccess<ProfileListResponse>>('/profiles/trash')
  return unwrap(response).profiles
}

/* --------------------------------- 单份 ---------------------------------- */

/** `GET /api/v1/profiles/{id}`，含技能 / 经历 / 教育。 */
export async function getProfile(profileId: string): Promise<ProfileRead> {
  const response = await http.get<ApiSuccess<ProfileResponse>>(`/profiles/${profileId}`)
  const profile = unwrap(response).profile
  if (!profile) {
    // 详情端点要么返回 profile，要么 404；返回 null 说明契约被改坏了。
    throw new Error('服务端未返回简历数据')
  }
  return profile
}

/** `POST /api/v1/profiles`：新建一份；`copy_from_id` 由服务端深拷贝。 */
export async function createProfile(payload: ProfileCreateRequest = {}): Promise<ProfileRead> {
  const response = await http.post<ApiSuccess<ProfileDetailResponse>>('/profiles', payload)
  return unwrap(response).profile
}

/** `PUT /api/v1/profiles/{id}`：整体覆盖指定简历的内容（子表全量替换）。 */
export async function updateProfile(
  profileId: string,
  payload: ProfileUpsertRequest,
): Promise<ProfileRead> {
  const response = await http.put<ApiSuccess<ProfileDetailResponse>>(
    `/profiles/${profileId}`,
    payload,
  )
  return unwrap(response).profile
}

/** `PUT /api/v1/profiles/{id}/default`：设为默认，其余未删除简历自动取消默认。 */
export async function setDefaultProfile(profileId: string): Promise<ProfileRead> {
  const response = await http.put<ApiSuccess<ProfileDetailResponse>>(
    `/profiles/${profileId}/default`,
  )
  return unwrap(response).profile
}

/**
 * `PUT /api/v1/profiles/{id}/title`：只改标题（D4.3）。
 *
 * 走独立端点而不是 `PUT /profiles/{id}`：后者是整份内容全量替换，
 * 只为改名就得先把简历完整 GET 下来再 PUT 回去，中途任何一次并发保存都会丢内容。
 * 重名时后端返回 400「该简历名称已存在」，由调用方展示。
 */
export async function renameProfile(profileId: string, title: string): Promise<ProfileRead> {
  const response = await http.put<ApiSuccess<ProfileDetailResponse>>(
    `/profiles/${profileId}/title`,
    { title },
  )
  return unwrap(response).profile
}

/* ------------------------------- 回收站操作 ------------------------------- */

/** `DELETE /api/v1/profiles/{id}`：软删除（进回收站，30 天后惰性物理清理）。 */
export async function deleteProfile(profileId: string): Promise<void> {
  await http.delete(`/profiles/${profileId}`)
}

/** `PUT /api/v1/profiles/{id}/restore`：从回收站恢复。 */
export async function restoreProfile(profileId: string): Promise<ProfileRead> {
  const response = await http.put<ApiSuccess<ProfileDetailResponse>>(
    `/profiles/${profileId}/restore`,
  )
  return unwrap(response).profile
}

/** `DELETE /api/v1/profiles/{id}/permanent`：彻底删除（仅限回收站内，否则 400）。 */
export async function purgeProfile(profileId: string): Promise<void> {
  await http.delete(`/profiles/${profileId}/permanent`)
}

/* --------------------------- /me 兼容入口（默认简历） -------------------------- */

/**
 * `GET /api/v1/profiles/me`
 *
 * 用户还没填过档案时后端返回 `{ profile: null }`（不是 404），
 * 这里如实翻译成 `null`，让调用方用「空表单」而不是「异常」来处理首次进入。
 *
 * D4.2 起主流程走 `listProfiles()` + `getProfile(id)`，本函数仅作兼容保留。
 */
export async function getMyProfile(): Promise<ProfileRead | null> {
  const response = await http.get<ApiSuccess<ProfileResponse>>('/profiles/me')
  return unwrap(response).profile
}

/** `PUT /api/v1/profiles/me`，整体替换**默认简历**；一份都没有时后端会先建一份。 */
export async function saveMyProfile(payload: ProfileUpsertRequest): Promise<ProfileRead> {
  const response = await http.put<ApiSuccess<ProfileResponse>>('/profiles/me', payload)
  const updated = unwrap(response).profile
  if (!updated) {
    throw new Error('服务端未返回档案数据')
  }
  return updated
}
