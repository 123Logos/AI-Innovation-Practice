/**
 * 前端与后端 API 的类型契约。
 *
 * 字段命名与 `app/schemas/` 下的 Pydantic 模型逐一对应：
 * - `auth.py`      → UserRead / TokenResponse / RegisterRequest / LoginRequest
 * - `profile.py`   → ProfileUpsertRequest / ProfileRead / SkillRead / ExperienceRead
 * - `resume.py`    → SkillInput / ExperienceInput / ResumeRead
 *
 * 所有响应都被 T6 统一响应模型包一层：成功是 `{ data, meta }`，
 * 失败是 `{ error, meta }`，见 `app/core/responses.py`。
 */

export interface ApiMeta {
  request_id: string
}

export interface ApiSuccess<T> {
  data: T
  meta: ApiMeta
}

export interface ApiErrorPayload {
  code: string
  message: string
  details: unknown
  retryable: boolean
}

export interface ApiErrorBody {
  error: ApiErrorPayload
  meta: ApiMeta
}

/* ---------------------------------- 认证 ---------------------------------- */

export interface UserRead {
  id: string
  username: string
  email: string | null
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export interface RegisterRequest {
  username: string
  password: string
  email?: string | null
}

export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterResponse {
  user: UserRead
}

export interface MeResponse {
  user: UserRead
}

/* ---------------------------------- 档案 ---------------------------------- */

/** 后端 `SkillInput`：名称 + 1–5 自评。 */
export interface SkillInput {
  name: string
  self_rating: number
}

/** 后端 `ExperienceInput`：日期为 `YYYY-MM-DD`，`end_date` 为 null 表示「至今」。 */
export interface ExperienceInput {
  title: string
  start_date: string
  end_date: string | null
  description: string
  responsibility: string
}

export interface SkillRead extends SkillInput {
  id: string
}

export interface ExperienceRead extends ExperienceInput {
  id: string
}

/** 后端 `ProfileUpsertRequest`：技能 3–8 条，经历 1–3 条，技能名唯一。 */
export interface ProfileUpsertRequest {
  full_name: string
  major: string
  grade: string
  contact: string
  skills: SkillInput[]
  experiences: ExperienceInput[]
}

export interface ProfileRead {
  id: string
  user_id: string
  full_name: string
  major: string
  grade: string
  contact: string
  created_at: string
  updated_at: string
  skills: SkillRead[]
  experiences: ExperienceRead[]
}

/** `GET /api/v1/profiles/me` 未填写档案时返回 `{ profile: null }`。 */
export interface ProfileResponse {
  profile: ProfileRead | null
}

/* ---------------------------------- 简历 ---------------------------------- */

export interface ResumeGenerateRequest {
  title: string
}

export interface ResumeRead {
  resume_id: string
  title: string
  content_html: string
  created_at: string
}
