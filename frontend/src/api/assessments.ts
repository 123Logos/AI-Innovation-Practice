/**
 * 标准化题库测评与画像接口：`app/api/v1/assessments.py` 的八个路径（FR-04 / B9，`ADR-028`）。
 *
 * 命名与写法照 `api/interviews.ts`：`http` 实例 + `unwrap()` 拆信封，错误由 `client.ts`
 * 的拦截器归一化成 `ApiError`，这里不自造第二套错误处理。
 *
 * **本文件有一条 AI 调用**：`summarizeSession()` → `POST /assessments/sessions/{id}/summary`。
 * 它会把用户在「AI 设置」里填的 DeepSeek 凭据转发出去（白名单在 `client.ts`，这里不手工加头）。
 * 没有凭据时后端**不报错**：返回 `source='fallback'` 与一段由标签拼出来的文案——
 * 所以调用方不能把「拿到 200」当成「这段是 AI 写的」，标题必须按 `source` 换（守则 C4）。
 *
 * 两条与后端逐字对齐的枚举都在这里导出：界面不许自己再写一份中文映射，
 * 否则同一份数据在不同页面会有两套叫法（`TAG_LABELS` 在后端的 `assessment_rules.py` 里同源）。
 */

import { http, unwrap } from './client'
import type { ApiSuccess } from './types'

/* --------------------------------- 枚举与常量 --------------------------------- */

/** 会话状态（`ck_assessment_sessions_status`）。三条迁移单向，终态任何写入都是 409。 */
export type AssessmentSessionStatus = 'in_progress' | 'submitted' | 'abandoned'

/** 标签三态（`ck_profile_tags_status`）。阈值口径在后端 `app/core/assessment_rules.py`。 */
export type TagStatus = 'verified' | 'pending' | 'improving'

/** 概述来源。`fallback` = 不是 AI 写的，界面必须换标题（`ADR-028` §2.5）。 */
export type SummarySource = 'ai' | 'fallback'

/** 三态的中文叫法。与后端 `TAG_LABELS` 逐字一致——同一份数据不许有两套名字。 */
export const TAG_STATUS_LABELS: Readonly<Record<TagStatus, string>> = {
  verified: '已验证',
  pending: '待验证',
  improving: '待提升',
}

/** 会话状态的中文叫法。 */
export const SESSION_STATUS_LABELS: Readonly<Record<AssessmentSessionStatus, string>> = {
  in_progress: '进行中',
  submitted: '已提交',
  abandoned: '已放弃',
}

/**
 * 概述标题：`ai` 与 `fallback` 必须一眼能分开（守则 C4「降级不许伪装」）。
 *
 * 两个词都不含黑话：这里不能说「降级」「模型」「上游」——用户要读的是
 * 「这段是谁写的」，不是我们的故障名词。
 */
export const SUMMARY_HEADINGS: Readonly<Record<SummarySource, string>> = {
  ai: 'AI 分析',
  fallback: '自动整理的概述',
}

/* --------------------------------- 读模型 --------------------------------- */

/** 一个选项。**没有对错**：判分在服务端，前端拿到的只有 key 与文案。 */
export interface AssessmentOption {
  key: string
  label: string
}

/** 一道题。**没有 `answer_key`**（`ADR-028` §2.1：答案永不进响应）。 */
export interface AssessmentQuestion {
  id: string
  skill_name: string
  prompt: string
  options: AssessmentOption[]
}

/** 题库目录的一行：某个技能有几道题。目录里没有题干。 */
export interface CatalogSkill {
  skill_key: string
  skill_name: string
  question_count: number
}

/** 题库目录：能测什么。 */
export interface AssessmentCatalog {
  bank_version: string
  total_questions: number
  skills: CatalogSkill[]
}

/** 一场测评的元信息。分数在 `in_progress` 时是 `null`（不是 0）。 */
export interface AssessmentSession {
  id: string
  student_profile_id: string
  status: AssessmentSessionStatus
  bank_version: string
  question_count: number
  correct_count: number | null
  score: number | null
  started_at: string
  submitted_at: string | null
  abandoned_at: string | null
  ai_summary: string | null
  ai_summary_source: SummarySource | null
  ai_summary_at: string | null
}

/** 一条作答。`is_correct` 在提交前是 `null`。 */
export interface AssessmentAnswer {
  question_id: string
  chosen_key: string
  is_correct: boolean | null
}

/** 画像里的一个标签。`source` 今天只有 `assessment`（枚举留位给项目/教师证据）。 */
export interface ProfileTag {
  tag: string
  status: TagStatus
  score: number
  source: 'assessment'
  evidence_count: number
  updated_at: string
}

/** 我的画像：标签 + 最近一次已提交会话的概述与来源。 */
export interface AssessmentProfile {
  student_profile_id: string
  tags: ProfileTag[]
  latest_session_id: string | null
  latest_summary: string | null
  latest_summary_source: SummarySource | null
}

/** 提交后每个技能的判定结果（与 `profile_tags` 同口径，但只含**本次**会话的）。 */
export interface SessionTag {
  tag: string
  status: TagStatus
  score: number
  evidence_count: number
}

/** 生成概述的结果。`note` 只描述这一次请求（不落库）。 */
export interface SessionSummary {
  session_id: string
  summary: string
  source: SummarySource
  note: string | null
}

/* ---------------------------------- 端点 ---------------------------------- */

/** `GET /api/v1/assessments`：题库目录（技能 + 题数 + 版本，**不含题干与答案**）。 */
export async function getCatalog(): Promise<AssessmentCatalog> {
  const response = await http.get<ApiSuccess<{ catalog: AssessmentCatalog }>>('/assessments')
  return unwrap(response).catalog
}

/**
 * `POST /api/v1/assessments/sessions`：开一场测评。
 *
 * `profile_id` 省略时用默认简历；没有档案 → 400，指定了别人的档案 → 404。
 */
export async function startSession(profileId?: string | null): Promise<{
  session: AssessmentSession
  questions: AssessmentQuestion[]
}> {
  const response = await http.post<ApiSuccess<{ session: AssessmentSession; questions: AssessmentQuestion[] }>>(
    '/assessments/sessions',
    profileId ? { profile_id: profileId } : {},
  )
  return unwrap(response)
}

/** `GET /api/v1/assessments/sessions/{id}`：会话详情（题目按出题顺序 + 我的答案）。 */
export async function getSession(id: string): Promise<{
  session: AssessmentSession
  questions: AssessmentQuestion[]
  answers: AssessmentAnswer[]
}> {
  const response = await http.get<
    ApiSuccess<{ session: AssessmentSession; questions: AssessmentQuestion[]; answers: AssessmentAnswer[] }>
  >(`/assessments/sessions/${id}`)
  return unwrap(response)
}

/**
 * `PUT /api/v1/assessments/sessions/{id}/answers`：批量**覆盖**答案。
 *
 * 只覆盖传进来的那几题（不是清空整张卷），所以「点一个选项就存一条」是安全的。
 * 已提交 / 已放弃的会话会 409（界面此时已切到结果态，正常走不到）。
 */
export async function saveAnswers(
  id: string,
  answers: Array<{ question_id: string; chosen_key: string }>,
): Promise<{ session: AssessmentSession; answers: AssessmentAnswer[] }> {
  const response = await http.put<
    ApiSuccess<{ session: AssessmentSession; answers: AssessmentAnswer[] }>
  >(`/assessments/sessions/${id}/answers`, { answers })
  return unwrap(response)
}

/** `POST /api/v1/assessments/sessions/{id}/submit`：判分 + 写标签 + 升级技能来源。 */
export async function submitSession(id: string): Promise<{
  session: AssessmentSession
  tags: SessionTag[]
}> {
  const response = await http.post<ApiSuccess<{ session: AssessmentSession; tags: SessionTag[] }>>(
    `/assessments/sessions/${id}/submit`,
  )
  return unwrap(response)
}

/** `POST /api/v1/assessments/sessions/{id}/abandon`：放弃（不判分、不写标签）。 */
export async function abandonSession(id: string): Promise<AssessmentSession> {
  const response = await http.post<ApiSuccess<{ session: AssessmentSession }>>(
    `/assessments/sessions/${id}/abandon`,
  )
  return unwrap(response).session
}

/**
 * `POST /api/v1/assessments/sessions/{id}/summary`：生成画像概述（BYOK）。
 *
 * **这一条会转发用户的 DeepSeek 凭据**（填了凭据时），由 `client.ts` 的白名单注入。
 * 无论成功与否都是 200：失败时 `source='fallback'` + `note` 说明原因，
 * 事实（标签、分数）一个字都不少——调用方按 `source` 换标题，不能当失败处理。
 */
export async function summarizeSession(id: string): Promise<SessionSummary> {
  const response = await http.post<ApiSuccess<{ summary: SessionSummary }>>(
    `/assessments/sessions/${id}/summary`,
  )
  return unwrap(response).summary
}

/** `GET /api/v1/assessments/profile`：我的画像（标签 + 最近一次概述）。 */
export async function getProfile(profileId?: string | null): Promise<AssessmentProfile> {
  const response = await http.get<ApiSuccess<{ profile: AssessmentProfile }>>(
    '/assessments/profile',
    { params: profileId ? { profile_id: profileId } : undefined },
  )
  return unwrap(response).profile
}
