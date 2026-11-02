/** 简历接口：`app/api/v1/resumes.py`。 */

import { http, unwrap } from './client'
import type { ApiSuccess, ResumeGenerateRequest, ResumeRead } from './types'

/**
 * `POST /api/v1/resumes/generate`
 *
 * 请求体只有 title；档案、技能、经历由后端按登录用户从数据库读取，
 * 所以前端传任何档案字段都不会生效（不要传）。
 */
export async function generateResume(payload: ResumeGenerateRequest): Promise<ResumeRead> {
  const response = await http.post<ApiSuccess<ResumeRead>>('/resumes/generate', payload)
  return unwrap(response)
}
