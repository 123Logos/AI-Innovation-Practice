/** 简历接口：`app/api/v1/resumes.py`。 */

import { http, unwrap } from './client'
import type { ApiSuccess, ResumeGenerateRequest, ResumeRead } from './types'

/**
 * `POST /api/v1/resumes/generate`
 *
 * 请求体只有 title（和可选的 `use_fallback`）；档案、技能、经历由后端按登录用户
 * 从数据库读取，所以前端传任何档案字段都不会生效（不要传）。
 *
 * 超时单独放宽到 45 秒：`http` 实例的默认超时是 15 秒，而后端给 DeepSeek 的预算
 * 是 30 秒（ADR-009）。按默认值会先被自己掐断，报成「无法连接到服务」，
 * 用户以为后端挂了，其实 AI 还在跑。
 */
const GENERATE_TIMEOUT_MS = 45_000

export async function generateResume(payload: ResumeGenerateRequest): Promise<ResumeRead> {
  const response = await http.post<ApiSuccess<ResumeRead>>('/resumes/generate', payload, {
    timeout: GENERATE_TIMEOUT_MS,
  })
  return unwrap(response)
}
