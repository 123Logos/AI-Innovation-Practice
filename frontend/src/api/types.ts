/**
 * 前端与后端 API 的类型契约。
 *
 * 字段命名与 `app/schemas/` 下的 Pydantic 模型逐一对应：
 * - `auth.py`      → UserRead / TokenResponse / RegisterRequest / LoginRequest
 * - `profile.py`   → ProfileUpsertRequest / ProfileRead / SkillRead / ExperienceRead / EducationRead
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

/**
 * `POST /auth/login` 与 `POST /auth/refresh` 共用（后端 `TokenResponse`）。
 *
 * `expires_in` 只描述 **access token** 的寿命（秒）；
 * refresh token 的 7 天寿命属于服务端状态，不通过响应体对外承诺。
 */
export interface TokenResponse {
  access_token: string
  refresh_token: string
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

/** 学历枚举，与后端 `ck_educations_degree` 一致。 */
export type Degree = '专科' | '本科' | '硕士' | '博士'

/**
 * 纸张尺寸（D5.1）。与后端 `PaperSize` 同源，只做两档：
 * A4 = 国内招聘事实标准，Letter = 北美投递。
 *
 * 加第三档必须同时改三处：后端 `PaperSize`、`PAPER_SPECS`、`@page` 注入，
 * 否则会出现「能选但印不出来」。
 */
export type PaperSize = 'A4' | 'Letter'

/**
 * 配色主题（D5.2）。8 套预设，与后端 `app/core/document_style.py::ThemeId` 同源。
 *
 * 存的是**预设 id 而不是 hex**：任意颜色无法保证在白色纸张上的对比度，
 * 而「前端算一遍、后端算一遍」的钳制算法必然漂移（ADR-007 §5.2）。
 * 色值本身在 `stores/resume.ts` 的 `THEME_OPTIONS`，与后端 `THEMES` 由
 * `tests/test_profile_contract.py` 逐字比对。
 */
export type ThemeId = 'forest' | 'ink' | 'navy' | 'wine' | 'plum' | 'cocoa' | 'slate' | 'olive'

/**
 * 可调顺序的模块 id（D5.3）。与后端 `document_style.SectionId` 同源。
 *
 * 只有这三个：`visible`（隐藏模块）本轮不做（ADR-007 §6.8(6)），
 * 它与「生成简历要求技能/经历非空」的完整性校验会直接打架。
 */
export type SectionId = 'educations' | 'skills' | 'experiences'

/** 一个模块的排版元信息：id + 标题。**不含内容**（内容在各自子表 / 草稿数组里）。 */
export interface LayoutSection {
  id: SectionId
  title: string
}

/**
 * 后端 `layout_config` 列的 JSON 形状（D5.3）。
 *
 * `version` 目前恒为 1；读接口返回的永远是**解析后的完整配置**
 * （3 段、合法 id、标题非空），前端不需要再实现一遍降级矩阵。
 */
export interface LayoutConfig {
  version: number
  sections: LayoutSection[]
}

/** 后端 `EducationInput`：日期为 `YYYY-MM-DD`，`edu_end` 为 null 表示「至今」。 */
export interface EducationInput {
  school: string
  major: string
  degree: Degree
  edu_start: string
  edu_end: string | null
}

export interface SkillRead extends SkillInput {
  id: string
}

export interface ExperienceRead extends ExperienceInput {
  id: string
}

export interface EducationRead extends EducationInput {
  id: string
}

/**
 * 后端 `ProfileSummary`（D4.1）：档案库列表 / 回收站的轻量行。
 *
 * 刻意不带 skills / experiences / educations——列表只回答「是哪一份」，
 * 把整份简历搬进列表会让「打开档案库」变成一次全量读取。
 */
export interface ProfileSummary {
  id: string
  /** 简历标题，如「我的简历 2」「投后端」。 */
  title: string
  /** 每人恒有且仅有一份默认简历（由服务层保证）。 */
  is_default: boolean
  full_name: string
  created_at: string
  updated_at: string
  /** 非空 = 在回收站里（东八区 ISO 串）。 */
  deleted_at: string | null
}

/** `GET /api/v1/profiles` 与 `GET /api/v1/profiles/trash` 共用。 */
export interface ProfileListResponse {
  profiles: ProfileSummary[]
}

/** 列表排序字段（D4.3）：最近修改 / 创建时间。 */
export type ProfileSortField = 'updated_at' | 'created_at'

/** 列表排序方向；`desc` = 新的在前。 */
export type ProfileSortOrder = 'asc' | 'desc'

/** 列表排序参数（与后端 `GET /profiles` 的查询参数一一对应）。 */
export interface ProfileListQuery {
  orderBy?: ProfileSortField
  order?: ProfileSortOrder
}

/**
 * 后端 `ProfileCreateRequest`。
 *
 * `title` 省略或全空白时由服务端递增生成（我的简历 / 我的简历 2 …）；
 * `copy_from_id` 触发**服务端深拷贝**，不要用「GET 源简历再 PUT」自己拼。
 */
export interface ProfileCreateRequest {
  title?: string | null
  copy_from_id?: string | null
  is_default?: boolean
}

/** 后端 `ProfileUpsertRequest`：技能 ≤8 条、经历 ≤3 条、教育 ≤5 段（无下限），技能名唯一。 */
export interface ProfileUpsertRequest {
  full_name: string
  /** 简历上展示的邮箱；登录邮箱在 `users.email`，两者互不影响。 */
  email?: string | null
  /** 年级已下架，可不传。 */
  grade?: string | null
  contact: string
  /**
   * 纸张尺寸（D5.1）。**三态语义**（ADR-007 §10.2）：
   * 不传 = 保留库里原值（旧客户端安全）；`null` = 恢复默认 A4；枚举值 = 设置。
   */
  paper_size?: PaperSize | null
  /**
   * 配色主题（D5.2）。**三态语义**与 `paper_size` 完全一致：
   * 不传 = 保留库里原值；`null` = 恢复默认 forest；预设 id = 设置。
   */
  theme?: ThemeId | null
  /**
   * 排版（D5.3）。**三态语义**（ADR-007 §10.2）：
   * 不传 = 保留库里原值（旧客户端安全）；`null` = 恢复默认顺序；对象 = 完整快照。
   *
   * 只存元信息（id + title），绝不把内容（技能/经历数组）塞进来：
   * 内容一旦进 JSON，就会和子表出现两份真相。
   */
  layout_config?: LayoutConfig | null
  skills: SkillInput[]
  experiences: ExperienceInput[]
  educations: EducationInput[]
}

export interface ProfileRead {
  id: string
  user_id: string
  /** D4.1：这份简历的名字与默认标记（列表里区分「投后端」「投算法」）。 */
  title: string
  is_default: boolean
  /** 非空 = 在回收站里；详情接口已排除回收站，留字段是为了如实回显。 */
  deleted_at: string | null
  /** D5.1：纸张尺寸。后端保证读侧恒为具体值（不是 null）。 */
  paper_size: PaperSize
  /** D5.2：配色主题。同上，读侧恒为 8 套之一（后端已收敛非法值）。 */
  theme: ThemeId
  /** D5.3：排版。读侧恒为解析后的完整配置（永不 null，脏数据已在后端退化）。 */
  layout_config: LayoutConfig
  full_name: string
  email: string | null
  grade: string
  contact: string
  /** 东八区 ISO 串，如 `2026-11-02T21:25:00+08:00`。 */
  created_at: string
  updated_at: string
  skills: SkillRead[]
  experiences: ExperienceRead[]
  educations: EducationRead[]
}

/** `GET /api/v1/profiles/me` 未填写档案时返回 `{ profile: null }`。 */
export interface ProfileResponse {
  profile: ProfileRead | null
}

/** 单份简历详情 / 新建 / 保存 / 恢复 / 设为默认的响应体。 */
export interface ProfileDetailResponse {
  profile: ProfileRead
}

/* ---------------------------------- 简历 ---------------------------------- */

export interface ResumeGenerateRequest {
  title: string
  /** D4.1：指定用哪一份简历生成；省略则后端取默认简历。 */
  profile_id?: string | null
  /**
   * ADR-009：用户在「AI 服务不可用」提示里点了「改用基础模板」时置 true，
   * 后端据此跳过 DeepSeek、不发外呼。省略即走 AI，缺 Key 会被 400 拦下。
   */
  use_fallback?: boolean
}

export interface ResumeRead {
  resume_id: string
  title: string
  content_html: string
  created_at: string
}
