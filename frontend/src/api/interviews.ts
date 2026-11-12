/**
 * 文本模拟面试接口：`app/api/v1/interviews.py` 的五个路径（FR-07 / B11a，ADR-022）。
 *
 * 命名与写法照 `api/jobs.ts`：复数模块名、`http` 实例、`unwrap()` 拆信封，
 * 错误一律由 `client.ts` 的拦截器归一化成 `ApiError`，这里不自造第二套错误处理。
 *
 * **本文件有一条 AI 调用**：`sendInterviewMessage()` → `POST /interviews/{id}/messages`。
 * 它**会向 AI 转发用户自带的 DeepSeek 凭据**（当用户在「AI 设置」里填了凭据时），
 * 由 `client.ts` 的白名单注入 `X-DeepSeek-Key`，这里不手工加头——和岗位匹配同一套 BYOK 纪律。
 * 没有凭据时后端不报错，而是走内置题库继续（可见降级，见下方 `interviewDegradeHint`）。
 *
 * 降级是「可见降级」而不是静默：后端把失败翻译成 `ai_degrade_reason` 后照样返回 200，
 * 前端必须把这一格**原样展示给用户**（守则 C4），绝不能让用户误以为「AI 说了这句」。
 * `interviewDegradeHint()` 把它映射成人话，且刻意不提任何开发黑话（守则 C2）。
 */

import { http, unwrap } from './client'
import type { ApiSuccess } from './types'

/* --------------------------------- 枚举与常量 --------------------------------- */

/** 会话状态（`ck_interview_sessions_status`，后端 `STATUS_ACTIVE`/`STATUS_ENDED`）。 */
export type InterviewSessionStatus = 'active' | 'ended'

/** 一轮对话的发言人（`ck_interview_turns_speaker`）。 */
export type InterviewSpeaker = 'interviewer' | 'candidate'

/** 后端 `InterviewTurnRead.speaker` 的取值字典，前端只做展示不做判定。 */
export const SPEAKER_LABELS: Readonly<Record<InterviewSpeaker, string>> = {
  interviewer: '面试官',
  candidate: '我',
}

/** 候选人单次作答的应用层长度上限（与 `app/schemas/interview.py::ANSWER_MAX_LEN` 对齐）。 */
export const ANSWER_MAX_LEN = 5000

/** 创建会话时目标岗位名的长度上限（与迁移列宽 `target_role VARCHAR(200)` 对齐）。 */
export const TARGET_ROLE_MAX_LEN = 200

/* --------------------------------- 读模型 --------------------------------- */

/** 后端 `InterviewTurnRead`：一轮问答。 */
export interface InterviewTurnRead {
  id: string
  session_id: string
  sequence: number
  speaker: InterviewSpeaker
  content: string
  /** 非空 = 这一轮内容是本地降级模板产出的，不是真 AI（必须可见提示）。 */
  ai_degrade_reason: string | null
  /** 东八区 ISO 串。 */
  created_at: string
}

/** 后端 `InterviewSessionRead`：会话元信息。 */
export interface InterviewSessionRead {
  id: string
  user_id: string
  job_id: string | null
  status: InterviewSessionStatus
  target_role: string | null
  created_at: string
  updated_at: string
  ended_at: string | null
}

/** 后端 `InterviewSessionDetailRead`：会话详情 = 元信息 + 全部轮次。 */
export interface InterviewSessionDetail {
  session: InterviewSessionRead
  turns: InterviewTurnRead[]
}

/** 后端 `InterviewMessageResult`：发作答后返回的两轮 + 降级原因。 */
export interface InterviewMessageResult {
  candidate_turn: InterviewTurnRead
  interviewer_turn: InterviewTurnRead
  ai_degrade_reason: string | null
}

/** 后端 `InterviewListResult`：会话列表（带分页 meta）。 */
export interface InterviewListResult {
  sessions: InterviewSessionRead[]
  page: number
  page_size: number
  total: number
}

/* --------------------------------- 写模型 --------------------------------- */

/** 后端 `InterviewSessionCreate`：创建会话的请求体。 */
export interface CreateInterviewRequest {
  job_id?: string | null
  target_role?: string | null
}

/** 创建会话的返回（后端 `create_interview` 的 data）：会话 + 开场问题 + 降级原因。 */
export interface CreateInterviewResult {
  session: InterviewSessionRead
  opening_turn: InterviewTurnRead
  ai_degrade_reason: string | null
}

/* ------------------------------ 降级可见提示 ------------------------------ */

/**
 * 把后端的 `ai_degrade_reason` 翻成**人话**给前端展示。
 *
 * 五档与 `app/services/interview_service.py::_DEGRADE_TEXT` 的键逐字对齐，但文案
 * 重写得更像产品说的话、且不出现任何开发黑话（守则 C2：「降级 / 模型 / 上游 / 额度 /
 * 后端 / 服务端」等都禁止上屏）。「AI 设置」出现是因为用户要亲手去那个界面粘贴凭据，
 * 属 C2 允许的豁免（用户必须去某界面拿凭据时，可显示该界面里的原词）。
 *
 * 返回 `null` 表示这一轮是真 AI，不展示降级条。
 */
export function interviewDegradeHint(reason: string | null): string | null {
  switch (reason) {
    case 'key_missing':
      return '还没填写 AI 使用凭据，这一轮用了内置题库。完成当前回答后，去「AI 设置」粘贴你的凭据，后续会更贴合你的简历。'
    case 'key_invalid':
      return '你的 AI 使用凭据不被接受（可能填错或已失效）。去「AI 设置」核对一下，这一轮用内置题库继续。'
    case 'timeout':
      return '刚才那一下等得有点久，回答没能生成。可以再发一次，或者稍后再试。'
    case 'upstream':
      return '这一轮回答没能生成，用了内置题库继续。可以再发一次。'
    case 'bad_output':
      return '这一轮回答没能生成，用了内置题库继续。可以再发一次。'
    default:
      return null
  }
}

/* ---------------------------------- 端点 ---------------------------------- */

/**
 * `POST /api/v1/interviews`：开一场模拟面试，并立刻拿到面试官的开场问题。
 *
 * `job_id` 提供但不可见（不存在 / 未发布 / 回收站）会 404；开场时 AI 不可用也不中断，
 * 开场问题走本地降级模板并带 `ai_degrade_reason`（可见降级）。
 */
export async function createInterview(
  payload: CreateInterviewRequest,
): Promise<CreateInterviewResult> {
  const response = await http.post<ApiSuccess<CreateInterviewResult>>('/interviews', payload)
  return unwrap(response)
}

/** `GET /api/v1/interviews`：当前用户自己的会话列表（按最近更新倒序，带分页）。 */
export async function listInterviews(page = 1, pageSize = 20): Promise<InterviewListResult> {
  const response = await http.get<ApiSuccess<InterviewListResult>>('/interviews', {
    params: { page, page_size: pageSize },
  })
  return unwrap(response)
}

/**
 * `GET /api/v1/interviews/{id}`：会话详情（元信息 + 全部轮次）。
 *
 * 非所有者或不存在在后端是同一个 404（越权与不存在同义），调用方只需当作「读不到」。
 */
export async function getInterview(id: string): Promise<InterviewSessionDetail> {
  const response = await http.get<ApiSuccess<InterviewSessionDetail>>(`/interviews/${id}`)
  return unwrap(response)
}

/**
 * `POST /api/v1/interviews/{id}/messages`：发一条作答，拿面试官的下一段。
 *
 * **这一条会向 AI 转发用户的 DeepSeek 凭据**（当用户填了凭据时），由 `client.ts` 的
 * 白名单注入，这里不手工加头。后端不会因为 AI 失败返回 4xx/5xx，而是把失败翻译成
 * `ai_degrade_reason` 后照样返回 200——调用方必须把它展示出来，不能当失败处理。
 * 已结束的会话会 400（UI 上已先禁用输入框，正常走不到这里）。
 */
export async function sendInterviewMessage(id: string, content: string): Promise<InterviewMessageResult> {
  const response = await http.post<ApiSuccess<InterviewMessageResult>>(`/interviews/${id}/messages`, {
    content,
  })
  return unwrap(response)
}

/** `POST /api/v1/interviews/{id}/end`：结束会话（status=ended）。 */
export async function endInterview(id: string): Promise<InterviewSessionRead> {
  const response = await http.post<ApiSuccess<{ session: InterviewSessionRead }>>(
    `/interviews/${id}/end`,
  )
  return unwrap(response).session
}
