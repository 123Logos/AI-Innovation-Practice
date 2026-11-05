/**
 * 简历接口：`app/api/v1/resumes.py` 的两条路径（ADR-010）。
 *
 * - `generateResume()`：纯模板生成，读的是**数据库里已保存**的档案，会落一行 `resumes`；
 * - `polishResume()`：AI 文案润色，发的是**前端草稿态**，服务端不落库，
 *   结果必须经用户在对照弹窗里点「接受」才写回表单。
 *
 * 两者互不调用：润色失败不影响生成，生成也不消耗用户的 AI 额度。
 */

import { http, unwrap } from './client'
import type {
  ApiSuccess,
  ResumeCopy,
  ResumeGenerateRequest,
  ResumePolishRequest,
  ResumeRead,
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
