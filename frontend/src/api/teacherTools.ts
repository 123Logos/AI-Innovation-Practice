/**
 * 教师端工具接口：`app/api/v1/teacher_tools.py`（FR-08 / B10a、B10b，`ADR-029`）。
 *
 * **本模块没有一条 AI 调用**：看板是本地聚合、导出是本地打印，所以 BYOK 白名单
 * 一条都不加（B11b 预留的 `/v1/tools/asr`、`/v1/tools/tts` 也与此无关）。
 *
 * 文件下载走 `responseType: 'blob'`，不套 `unwrap()`：返回体不是 `{data, meta}` 信封。
 * 错误仍由 `client.ts` 的拦截器归一化成 `ApiError`（blob 错误体会被后端写成 JSON，
 * axios 会按 blob 解析；因此下载失败时统一提示固定人话，不回显错误体里的字）。
 */

import { http, unwrap } from './client'
import type { ApiSuccess } from './types'

/* --------------------------------- 读模型 --------------------------------- */

export type DashboardTagStatus = 'verified' | 'pending' | 'improving'

export interface TagDistributionItem {
  tag: string
  status: DashboardTagStatus
  student_count: number
}

export interface CoverageBuckets {
  below_half: number
  half_to_eighty: number
  eighty_plus: number
}

export interface JobCoverageItem {
  job_id: string
  title: string
  required_skill_count: number
  student_count: number
  /** 0–100 的技能覆盖率（整数），不是 AI 匹配分。 */
  avg_coverage: number
  no_requirements: boolean
  buckets: CoverageBuckets
}

export interface TeacherDashboard {
  organization_id: string
  student_total: number
  tagged_student_total: number
  tag_distribution: TagDistributionItem[]
  match_overview: JobCoverageItem[]
}

export interface DownloadableResume {
  resume_id: string
  profile_id: string
  full_name: string
  title: string
  version_no: number
}

export const TAG_STATUS_LABELS: Readonly<Record<DashboardTagStatus, string>> = {
  verified: '已验证',
  pending: '待验证',
  improving: '待提升',
}

export const BUCKET_LABELS: Readonly<Record<keyof CoverageBuckets, string>> = {
  below_half: '低于一半',
  half_to_eighty: '一半到八成',
  eighty_plus: '八成以上',
}

/* ---------------------------------- 端点 ---------------------------------- */

/** `GET /api/v1/teacher/dashboard`：标签分布 + 岗位技能覆盖率概况。 */
export async function getDashboard(): Promise<TeacherDashboard> {
  const response = await http.get<ApiSuccess<TeacherDashboard>>('/teacher/dashboard')
  return unwrap(response)
}

/** `GET /api/v1/teacher/resumes`：本组织可导出的简历清单（不含正文）。 */
export async function listResumes(): Promise<DownloadableResume[]> {
  const response = await http.get<ApiSuccess<{ items: DownloadableResume[] }>>('/teacher/resumes')
  return unwrap(response).items
}

/** `POST /api/v1/teacher/resumes/batch-download`：勾选的简历打成一个 ZIP。 */
export async function downloadBatch(resumeIds: string[]): Promise<Blob> {
  const response = await http.post<Blob>(
    '/teacher/resumes/batch-download',
    { resume_ids: resumeIds, format: 'pdf' },
    { responseType: 'blob' },
  )
  return response.data
}

/** `POST /api/v1/resumes/{id}/export?format=pdf`：单份 PDF。 */
export async function exportResume(resumeId: string): Promise<Blob> {
  const response = await http.post<Blob>(
    `/resumes/${encodeURIComponent(resumeId)}/export`,
    undefined,
    { params: { format: 'pdf' }, responseType: 'blob' },
  )
  return response.data
}

/**
 * 触发浏览器下载。`filename*` 是后端按 RFC 5987 写的，这里只做一次兜底：
 * 拿不到就用调用方给的名字，绝不把响应头里的原文塞进 DOM（XSS 面）。
 */
export function saveBlob(blob: Blob, fallbackName: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fallbackName
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
