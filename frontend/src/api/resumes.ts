/**
 * 简历接口：`app/api/v1/resumes.py` 的两条路径（ADR-010）+ B8 剩余的三条（ADR-026）。
 *
 * - `generateResume()`：纯模板生成，读的是**数据库里已保存**的档案，会落一行 `resumes`；
 * - `polishResume()`：AI 文案润色，发的是**前端草稿态**，服务端不落库，
 *   结果必须经用户在对照弹窗里点「接受」才写回表单；
 * - `listResumeVersions()` / `diffResumeVersions()`：纯读，不动任何一张表；
 * - `customizeResume()`：会落一行 **`origin='customized'`** 的版本，
 *   但它**不移动链头**——「当前简历」仍然由 `/resumes/current` 的指针决定。
 *
 * 生成与润色互不调用：润色失败不影响生成，生成也不消耗用户的 AI 额度。
 */

import { http, unwrap } from './client'
import type {
  ApiSuccess,
  ResumeCopy,
  ResumeCustomizeRequest,
  ResumeCustomizeResult,
  ResumeDiff,
  ResumeGenerateRequest,
  ResumePolishRequest,
  ResumeRead,
  ResumeVersionList,
} from './types'

/**
 * `POST /api/v1/resumes/generate`
 *
 * 请求体只有 title（和可选的 `profile_id`）；档案、技能、经历由后端按登录用户
 * 从数据库读取，所以前端传任何档案字段都不会生效（不要传）。
 *
 * 用 `http` 实例的默认 15 秒超时：模板渲染 + 落库都在百毫秒级，没有理由为它等 AI 的预算。
 */
export async function generateResume(payload: ResumeGenerateRequest): Promise<ResumeRead> {
  const response = await http.post<ApiSuccess<ResumeRead>>('/resumes/generate', payload)
  return unwrap(response)
}

/**
 * `POST /api/v1/resumes/polish`
 *
 * 超时单独放宽到 45 秒：`http` 实例默认 15 秒，而后端给 DeepSeek 的预算是 30 秒（ADR-009）。
 * 按默认值会先被自己掐断，报成「无法连接到服务」，用户以为后端挂了，其实 AI 还在跑。
 *
 * Key 由 `client.ts` 的拦截器按 URL 注入 `X-DeepSeek-Key`，这里不用管。
 */
const POLISH_TIMEOUT_MS = 45_000

export async function polishResume(payload: ResumePolishRequest): Promise<ResumeCopy> {
  const response = await http.post<ApiSuccess<ResumeCopy>>('/resumes/polish', payload, {
    timeout: POLISH_TIMEOUT_MS,
  })
  return unwrap(response)
}

/* --------------------- 版本历史 / 差异 / 按岗位定制（ADR-026） --------------------- */

/**
 * `GET /api/v1/resumes/versions`：该档案的全部版本，按版本号倒序。
 *
 * 空历史返回**空数组**而不是 404——「还没生成过」是 `/resumes/current` 的错误口径，
 * 这里回答的是「历史上有哪些版本」。
 */
export async function listResumeVersions(profileId?: string | null): Promise<ResumeVersionList> {
  const response = await http.get<ApiSuccess<ResumeVersionList>>('/resumes/versions', {
    params: profileId ? { profile_id: profileId } : {},
  })
  return unwrap(response)
}

/**
 * `GET /api/v1/resumes/versions/diff`：两版的结构级差异。
 *
 * 两个 id 必须属于同一份档案且都属于当前用户，否则 404（不区分原因，
 * 免得这个端点变成版本 id 的探测器）。
 */
export async function diffResumeVersions(
  fromVersionId: string,
  toVersionId: string,
): Promise<ResumeDiff> {
  const response = await http.get<ApiSuccess<ResumeDiff>>('/resumes/versions/diff', {
    params: { from_version_id: fromVersionId, to_version_id: toVersionId },
  })
  return unwrap(response)
}

/**
 * `POST /api/v1/resumes/customize`：按岗位定制一份。
 *
 * 服务端**只重排**技能与经历的顺序，一个字都不改，也不会让这一版成为「当前简历」。
 * 失败口径：档案不完整 400 / 还没生成过 404 / 岗位不可读 404 / 岗位过期 409。
 */
export async function customizeResume(
  payload: ResumeCustomizeRequest,
): Promise<ResumeCustomizeResult> {
  const response = await http.post<ApiSuccess<ResumeCustomizeResult>>('/resumes/customize', payload)
  return unwrap(response)
}
