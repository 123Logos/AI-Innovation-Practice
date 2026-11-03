/**
 * 档案 + 简历生成 store（T7.3 起接真实后端）。
 *
 * 字段命名与后端 `ProfileUpsertRequest` / `ProfileRead` 严格一致（snake_case），
 * 唯一例外是 `ExperienceDraft.end_date` / `EducationDraft.edu_start` 用空串表示「至今」
 * 或「未填」——`<input type="date">` / `<input type="month">` 不接受 null，
 * 所以在提交时由 `toUpsertPayload()` 转换：
 *   经历 end_date  空串 → null（至今）
 *   教育 edu_start `YYYY-MM` → `YYYY-MM-01`（后端是 DATE）
 *   教育 edu_end   空串 → null（至今）
 *
 * 技能熟练度（`self_rating`）与年级（`grade`）已从 UI 移除：
 * 前者类型保留、提交时统一写 `DEFAULT_SKILL_RATING`（3）；
 * 后者前后端都不再传写，数据库列保留但不再参与业务。
 *
 * 专业（`major`）自 T7.6 起只属于「教育经历」的每一段，主表不再有该字段，
 * 因此 `ProfileDraft` 里也没有顶层的 `major`；旧草稿里残留的顶层 `major`
 * 会在恢复时被 `normalizeDraft()` 重建对象时自然丢弃。
 *
 * 邮箱（`email`，用户反馈轮）：是**简历上展示的邮箱**，
 * 与登录邮箱 `users.email` 无关，可空。
 *
 * 校验时机（用户反馈轮）：
 * - 保存档案**不拦必填**：只提交「填了锚点字段」的行（技能名 / 项目名 / 学校），
 *   空行直接丢弃，全空就等于存一份空档案；
 * - 生成简历才是严格时刻，见 `validateForGenerate()`：
 *   手机号必须匹配 `PHONE_PATTERN`，开始时间不得晚于今天。
 *   结束时间允许未来（应届生的「预计毕业」、在做的项目「预计结束」），
 *   只要求不早于开始时间。
 *
 * 教育经历（T7.5）是一对多子表，前端与后端的渲染顺序都是「入学时间倒序」。
 *
 * 本地草稿：按用户 id 隔离存在 `localStorage`，只写不覆盖服务端数据；
 * 用户点预览区上方的「保存修改」（`save()`）成功后立即清除。
 *
 * 保存入口自 D3 起只有一个：纸面编辑与左侧表单都只改 Pinia，
 * `dirty` 为真即「未落库」，由 `ResumePreview.vue` 工具栏的按钮统一提交。
 */

import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { profilesApi, resumesApi } from '@/api'
import type { Degree, ProfileRead, ProfileUpsertRequest, ResumeRead } from '@/api/types'
import { currentMonthValue, todayIsoDate } from '@/utils/datetime'

/** 与后端 `ProfileUpsertRequest` 的 Field 约束保持一致（只有上限，保存不拦必填）。 */
export const SKILL_LIMIT = { min: 0, max: 8 } as const
export const EXPERIENCE_LIMIT = { min: 0, max: 3 } as const
/** 后端 `educations` 最多 5 段；UI 至少保留 1 行空的，避免区段消失。 */
export const EDUCATION_LIMIT = { min: 1, max: 5 } as const

/** 生成简历时的最低要求（后端 `ResumeService` 对技能/经历为空会返回 400）。 */
export const GENERATE_MIN = { skills: 1, experiences: 1, educations: 1 } as const

/** 学历下拉选项，顺序与后端枚举声明一致。 */
export const DEGREE_OPTIONS: readonly Degree[] = ['专科', '本科', '硕士', '博士']

/** UI 已不再采集熟练度；提交时一律用它填后端必填字段。 */
export const DEFAULT_SKILL_RATING = 3

/**
 * 中国大陆手机号；与后端 `app/schemas/profile.py::PHONE_PATTERN` 同源，
 * 两边必须同步修改，否则前端放过、后端 422。
 */
export const PHONE_PATTERN = /^1[3-9]\d{9}$/

const DRAFT_STORAGE_PREFIX = 'resume_draft:'
const DRAFT_VERSION = 1
/** 输入停顿多久后落本地草稿（毫秒）。 */
const DRAFT_WRITE_DELAY = 400

export interface SkillDraft {
  name: string
  self_rating: number
}

export interface ExperienceDraft {
  title: string
  start_date: string
  /** 空串表示「至今」，提交时转 null。 */
  end_date: string
  description: string
  responsibility: string
}

export interface EducationDraft {
  school: string
  major: string
  degree: Degree
  /** `YYYY-MM`，与 `<input type="month">` 的取值格式一致；提交时补 `-01`。 */
  edu_start: string
  /** `YYYY-MM`，空串表示「至今」。 */
  edu_end: string
}

export interface ProfileDraft {
  full_name: string
  /** 简历上展示的邮箱（可空），与登录邮箱无关。 */
  email: string
  contact: string
  skills: SkillDraft[]
  experiences: ExperienceDraft[]
  educations: EducationDraft[]
}

/**
 * 新教育经历的默认入学时间：**当前年份的 4 年前的 9 月**。
 *
 * 面向本科大四/应届生：4 年前 9 月入学，正好覆盖「现在毕业」这段学历，
 * 比默认当前月份少一次手工改。
 */
function defaultEducationStart(): string {
  return `${Number(currentMonthValue().slice(0, 4)) - 4}-09`
}

/** 后端返回 `2021-09-01`，`<input type="month">` 只认 `2021-09`。 */
function toMonth(value: string | null | undefined): string {
  if (!value) return ''
  return value.slice(0, 7)
}

/** 提交给后端的 DATE：月份补 `-01`；空串返回 null。 */
function toIsoDate(month: string): string | null {
  if (!month) return null
  return month.length === 7 ? `${month}-01` : month
}

function createEmptyEducation(): EducationDraft {
  return {
    school: '',
    major: '',
    degree: '本科',
    edu_start: defaultEducationStart(),
    edu_end: '',
  }
}

/**
 * 首次进入时的空白骨架。
 *
 * 技能给 3 行只是「常见条数」的顺手默认，不是后端要求——保存不拦必填，
 * 空行会在 `toUpsertPayload()` 里被丢弃。
 */
function createEmptyDraft(): ProfileDraft {
  return {
    full_name: '',
    email: '',
    contact: '',
    skills: Array.from({ length: 3 }, () => ({
      name: '',
      self_rating: DEFAULT_SKILL_RATING,
    })),
    experiences: [
      {
        title: '',
        start_date: todayIsoDate(),
        end_date: '',
        description: '',
        responsibility: '',
      },
    ],
    educations: [createEmptyEducation()],
  }
}

function draftFromProfile(profile: ProfileRead): ProfileDraft {
  return {
    full_name: profile.full_name,
    email: profile.email ?? '',
    contact: profile.contact,
    skills: profile.skills.map((skill) => ({
      name: skill.name,
      self_rating: skill.self_rating,
    })),
    experiences: profile.experiences.map((experience) => ({
      title: experience.title,
      start_date: experience.start_date,
      end_date: experience.end_date ?? '',
      description: experience.description,
      responsibility: experience.responsibility,
    })),
    educations: (profile.educations ?? []).map((education) => ({
      school: education.school,
      major: education.major,
      degree: education.degree,
      edu_start: toMonth(education.edu_start),
      edu_end: toMonth(education.edu_end),
    })),
  }
}

/* ------------------------------ 本地草稿工具 ------------------------------ */

/** 写入 `localStorage` 的草稿信封，带版本号以便未来结构升级时安全丢弃旧草稿。 */
export interface LocalDraft {
  version: number
  /** 草稿写入时间（epoch ms），仅用于提示文案。 */
  saved_at: number
  data: ProfileDraft
}

function draftStorageKey(ownerId: string): string {
  return `${DRAFT_STORAGE_PREFIX}${ownerId}`
}

/** `localStorage` 的内容不可信：任何字段不符就当作没有草稿。 */
function isProfileDraft(value: unknown): value is ProfileDraft {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<ProfileDraft>
  return (
    typeof candidate.full_name === 'string' &&
    typeof candidate.contact === 'string' &&
    Array.isArray(candidate.skills) &&
    candidate.skills.every(
      (skill: unknown) =>
        Boolean(skill) &&
        typeof skill === 'object' &&
        typeof (skill as SkillDraft).name === 'string',
    ) &&
    Array.isArray(candidate.experiences) &&
    candidate.experiences.every(
      (experience: unknown) =>
        Boolean(experience) &&
        typeof experience === 'object' &&
        typeof (experience as ExperienceDraft).title === 'string',
    ) &&
    // T7.5 之前写入的草稿没有 educations，缺省视为合法（恢复时补空数组）；
    // 但只要有这个字段，就必须是数组，避免脏数据把渲染层搞崩。
    (candidate.educations === undefined || Array.isArray(candidate.educations)) &&
    // 用户反馈轮才新增 email，旧草稿没有该字段同样合法；有则必须是字符串。
    (candidate.email === undefined || typeof candidate.email === 'string')
  )
}

function readDraft(ownerId: string): LocalDraft | null {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(draftStorageKey(ownerId))
  } catch {
    return null
  }
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<LocalDraft>
    if (parsed.version !== DRAFT_VERSION || !isProfileDraft(parsed.data)) return null
    return {
      version: DRAFT_VERSION,
      saved_at: typeof parsed.saved_at === 'number' ? parsed.saved_at : 0,
      data: parsed.data,
    }
  } catch {
    return null
  }
}

function removeDraft(ownerId: string): void {
  try {
    localStorage.removeItem(draftStorageKey(ownerId))
  } catch {
    // localStorage 被禁用时忽略，草稿是增益能力，不能阻断主流程。
  }
}

/**
 * 补齐缺失字段，让旧草稿也能安全恢复。
 *
 * - 已下线的 `grade`（年级）直接丢弃；
 * - T7.6 已删除主表 `major`（专业），旧草稿里的顶层 `major` 一并丢弃——
 *   重建对象时不拷贝该键，运行时自然消失；
 * - 已下线的 `self_rating` 抹平为默认值；
 * - T7.5 之前的草稿没有 `educations`，补空数组而不是报错；
 * - 用户反馈轮之前的草稿没有 `email`，补空串。
 */
function normalizeDraft(raw: ProfileDraft): ProfileDraft {
  return {
    full_name: raw.full_name ?? '',
    email: raw.email ?? '',
    contact: raw.contact ?? '',
    skills: (raw.skills ?? [])
      .filter((skill) => skill && typeof skill.name === 'string')
      .map((skill) => ({ name: skill.name, self_rating: DEFAULT_SKILL_RATING })),
    experiences: (raw.experiences ?? [])
      .filter((experience) => experience && typeof experience.title === 'string')
      .map((experience) => ({
        title: experience.title,
        start_date: experience.start_date ?? '',
        end_date: experience.end_date ?? '',
        description: experience.description ?? '',
        responsibility: experience.responsibility ?? '',
      })),
    educations: (raw.educations ?? [])
      .filter((education) => education && typeof education.school === 'string')
      .map((education) => ({
        school: education.school,
        major: education.major ?? '',
        degree: DEGREE_OPTIONS.includes(education.degree) ? education.degree : '本科',
        edu_start: toMonth(education.edu_start),
        edu_end: toMonth(education.edu_end),
      })),
  }
}

/* ------------------------------ 行有效性过滤 ------------------------------ */

/*
 * 保存不再拦必填，但也不能把用户没开始填的空行提交上去（后端 `name` / `title` /
 * `school` 是必填锚点）。规则：锚点字段为空即视为「UI 占位行」，提交与比较时都跳过。
 */

function meaningfulSkills(source: ProfileDraft): SkillDraft[] {
  return (source.skills ?? []).filter((skill) => skill.name.trim() !== '')
}

function meaningfulExperiences(source: ProfileDraft): ExperienceDraft[] {
  return (source.experiences ?? []).filter((experience) => experience.title.trim() !== '')
}

function meaningfulEducations(source: ProfileDraft): EducationDraft[] {
  return (source.educations ?? []).filter((education) => education.school.trim() !== '')
}

/**
 * 用于「草稿 vs 服务端」比较的归一化序列化。
 *
 * 刻意忽略 `self_rating`：UI 已不采集它，提交后恒为 3，
 * 若把它算进比较，一份本来没改过的草稿也会被判成「有未保存修改」。
 * 同时也只比较「有效行」，否则一份 3 个空技能行的空白骨架
 * 会跟服务端的 0 条技能判成「有差异」，每次进页面都弹恢复提示。
 * 顶层 `major` 已不存在（T7.6），无需再参与比较。
 *
 * 所有字段都用 `?? ''` 兜底：草稿来自 `localStorage`，可能是任何一个旧版本的结构
 * （比如本轮之前没有 `email`），这里绝不能因为缺一个键就抛异常。
 */
function comparableOf(source: ProfileDraft): string {
  return JSON.stringify({
    full_name: (source.full_name ?? '').trim(),
    email: (source.email ?? '').trim(),
    contact: (source.contact ?? '').trim(),
    skills: meaningfulSkills(source).map((skill) => skill.name.trim()),
    experiences: meaningfulExperiences(source).map((experience) => [
      experience.title,
      experience.start_date,
      experience.end_date,
      experience.description,
      experience.responsibility,
    ]),
    educations: meaningfulEducations(source).map((education) => [
      education.school,
      education.major,
      education.degree,
      education.edu_start,
      education.edu_end,
    ]),
  })
}

export const useResumeStore = defineStore('resume', () => {
  const draft = reactive<ProfileDraft>(createEmptyDraft())
  const savedProfile = ref<ProfileRead | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const generating = ref(false)
  const dirty = ref(false)
  const lastSavedAt = ref<string | null>(null)

  /* -------------------------------- 草稿状态 -------------------------------- */
  const draftOwnerId = ref<string | null>(null)
  /** 检测到的、尚未被用户决定的本地草稿。 */
  const pendingDraft = ref<LocalDraft | null>(null)
  const autosavedAt = ref<number | null>(null)

  const generatedHtml = ref<string | null>(null)
  const generatedResumeId = ref<string | null>(null)
  const generatedTitle = ref<string | null>(null)

  const hasSavedProfile = computed(() => savedProfile.value !== null)
  const isGeneratingResultVisible = computed(() => generatedHtml.value !== null)

  /** 后端只接受 3–8 条技能，这里同步限制 UI，避免用户删到 2 条才发现被拒。 */
  const canAddSkill = computed(() => draft.skills.length < SKILL_LIMIT.max)
  const canRemoveSkill = computed(() => draft.skills.length > SKILL_LIMIT.min)
  const canAddExperience = computed(() => draft.experiences.length < EXPERIENCE_LIMIT.max)
  const canRemoveExperience = computed(() => draft.experiences.length > EXPERIENCE_LIMIT.min)
  /** 教育经历：至少保留 1 段，最多与后端 `max_length=5` 对齐。 */
  const canAddEducation = computed(() => draft.educations.length < EDUCATION_LIMIT.max)
  const canRemoveEducation = computed(() => draft.educations.length > EDUCATION_LIMIT.min)

  function markDirty(): void {
    dirty.value = true
  }

  function applyDraft(next: ProfileDraft, profile: ProfileRead | null): void {
    draft.full_name = next.full_name
    draft.email = next.email
    draft.contact = next.contact
    draft.skills = next.skills
    draft.experiences = next.experiences
    draft.educations = next.educations
    savedProfile.value = profile
    dirty.value = false
  }

  function updateProfileField(field: 'full_name' | 'email' | 'contact', value: string): void {
    draft[field] = value
    markDirty()
  }

  function updateSkill(index: number, field: keyof SkillDraft, value: string | number): void {
    const skill = draft.skills[index]
    if (!skill) return
    if (field === 'self_rating') {
      skill.self_rating = Math.min(5, Math.max(1, Number(value) || DEFAULT_SKILL_RATING))
    } else {
      skill.name = String(value)
    }
    markDirty()
  }

  function addSkill(): void {
    if (!canAddSkill.value) return
    draft.skills.push({ name: '', self_rating: DEFAULT_SKILL_RATING })
    markDirty()
  }

  function removeSkill(index: number): void {
    if (!canRemoveSkill.value) return
    draft.skills.splice(index, 1)
    markDirty()
  }

  function updateExperience(index: number, field: keyof ExperienceDraft, value: string): void {
    const experience = draft.experiences[index]
    if (!experience) return
    experience[field] = value
    markDirty()
  }

  function addExperience(): void {
    if (!canAddExperience.value) return
    draft.experiences.push({
      title: '',
      start_date: todayIsoDate(),
      end_date: '',
      description: '',
      responsibility: '',
    })
    markDirty()
  }

  function removeExperience(index: number): void {
    if (!canRemoveExperience.value) return
    draft.experiences.splice(index, 1)
    markDirty()
  }

  function updateEducation(index: number, field: keyof EducationDraft, value: string): void {
    const education = draft.educations[index]
    if (!education) return
    if (field === 'degree') {
      if (!DEGREE_OPTIONS.includes(value as Degree)) return
      education.degree = value as Degree
    } else {
      education[field] = value
    }
    markDirty()
  }

  function addEducation(): void {
    if (!canAddEducation.value) return
    draft.educations.push(createEmptyEducation())
    markDirty()
  }

  function removeEducation(index: number): void {
    if (!canRemoveEducation.value) return
    draft.educations.splice(index, 1)
    markDirty()
  }

  /**
   * 组装请求体：去首尾空格，日期空串转 null，熟练度固定 3，年级/专业不再提交。
   *
   * 只提交「有效行」（锚点字段非空），所以整份全空就是空数组——
   * 后端已去掉技能/经历的下限，这种「空档案」可以正常保存。
   */
  function toUpsertPayload(): ProfileUpsertRequest {
    return {
      full_name: draft.full_name.trim(),
      // 空串等同「没填」，交给后端存 NULL。
      email: draft.email.trim() || null,
      contact: draft.contact.trim(),
      skills: meaningfulSkills(draft).map((skill) => ({
        name: skill.name.trim(),
        // 后端 `SkillInput.self_rating` 仍必填（1–5），但 UI 不再采集，统一写 3。
        self_rating: DEFAULT_SKILL_RATING,
      })),
      experiences: meaningfulExperiences(draft).map((experience) => ({
        title: experience.title.trim(),
        start_date: experience.start_date,
        end_date: experience.end_date === '' ? null : experience.end_date,
        description: experience.description.trim(),
        responsibility: experience.responsibility.trim(),
      })),
      educations: meaningfulEducations(draft).map((education) => ({
        school: education.school.trim(),
        major: education.major.trim(),
        degree: education.degree,
        // 后端是 DATE：`YYYY-MM` 补成当月 1 号；空值由生成前校验拦住。
        edu_start: toIsoDate(education.edu_start) ?? '',
        edu_end: toIsoDate(education.edu_end),
      })),
    }
  }

  /** 宽松的邮箱格式检查：只拦明显不是邮箱的输入，不做 RFC 全量校验。 */
  function looksLikeEmail(value: string): boolean {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)
  }

  /**
   * 生成简历前的完整性校验（严格）。
   *
   * 保存已经不拦必填了，所以「能不能生成」的全部判断都收敛到这里：
   * 姓名 / 手机号必填且号码合法，邮箱填了就必须像邮箱，
   * 教育经历至少 1 段且四要素齐全，技能与项目各至少 1 条并不超上限；
   * 开始时间不得晚于今天（与后端 `shanghai_today()` 同口径），
   * 结束时间允许未来——应届生要写「预计毕业时间」，只校验不早于开始时间。
   * 返回 null 表示通过。
   */
  function validateForGenerate(): string | null {
    if (!draft.full_name.trim()) return '请先填写姓名，再生成简历。'
    const contact = draft.contact.trim()
    if (!contact) return '请先填写手机号，再生成简历。'
    if (!PHONE_PATTERN.test(contact)) {
      return '手机号格式不正确，请填写 11 位中国大陆手机号（如 13800000000）。'
    }
    const email = draft.email.trim()
    if (email && !looksLikeEmail(email)) return '邮箱格式不正确，请检查后再生成。'

    const today = todayIsoDate()
    // 教育经历是「年月」精度，比较基准也截到月，否则 2026-11 与 2026-11-02 会误判。
    const todayMonth = today.slice(0, 7)

    const skills = meaningfulSkills(draft)
    if (skills.length < GENERATE_MIN.skills) return '至少填写 1 条技能，再生成简历。'
    if (skills.length > SKILL_LIMIT.max) return `技能最多 ${SKILL_LIMIT.max} 条。`
    const skillNames = skills.map((skill) => skill.name.trim())
    if (new Set(skillNames).size !== skillNames.length) return '技能名称不能重复。'

    const educations = meaningfulEducations(draft)
    if (educations.length < GENERATE_MIN.educations) {
      return '至少填写 1 段教育经历（学校、专业、学历、入学时间），再生成简历。'
    }
    if (educations.length > EDUCATION_LIMIT.max) {
      return `教育经历最多 ${EDUCATION_LIMIT.max} 段。`
    }
    for (const [index, education] of educations.entries()) {
      const label = `第 ${index + 1} 段教育经历`
      if (!education.major.trim()) return `${label}缺少专业。`
      if (!education.degree) return `${label}缺少学历。`
      if (!education.edu_start) return `${label}缺少入学时间。`
      if (education.edu_start > todayMonth) return `${label}的入学时间不能选择未来。`
      // 毕业时间允许未来（应届生填「预计毕业时间」），只要求不早于入学时间。
      if (education.edu_end && education.edu_end < education.edu_start) {
        return `${label}的毕业时间不能早于入学时间。`
      }
    }

    const experiences = meaningfulExperiences(draft)
    if (experiences.length < GENERATE_MIN.experiences) return '至少填写 1 段项目经历，再生成简历。'
    if (experiences.length > EXPERIENCE_LIMIT.max) {
      return `项目经历最多 ${EXPERIENCE_LIMIT.max} 条。`
    }
    for (const [index, experience] of experiences.entries()) {
      const label = `第 ${index + 1} 段经历`
      if (!experience.start_date) return `${label}缺少开始日期。`
      if (!experience.description.trim()) return `${label}缺少项目描述。`
      if (!experience.responsibility.trim()) return `${label}缺少个人职责。`
      if (experience.start_date > today) return `${label}的开始时间不能选择未来。`
      // 结束时间允许未来（「预计结束」），只要求不早于开始时间。
      if (experience.end_date && experience.end_date < experience.start_date) {
        return `${label}的结束日期不能早于开始日期。`
      }
    }
    return null
  }

  /** 进入编辑器时调用：拉取当前用户档案，未填写则保留空白骨架。 */
  async function loadFromServer(): Promise<void> {
    loading.value = true
    try {
      const profile = await profilesApi.getMyProfile()
      applyDraft(profile ? draftFromProfile(profile) : createEmptyDraft(), profile)
    } finally {
      loading.value = false
    }
  }

  /**
   * 唯一的保存动作（表单 + 纸面共用）：PUT 整体替换，
   * 成功后用服务端返回值回填本地（`applyDraft` 会把 `dirty` 归零），并清掉本地草稿。
   */
  async function save(): Promise<ProfileRead> {
    saving.value = true
    try {
      const profile = await profilesApi.saveMyProfile(toUpsertPayload())
      applyDraft(draftFromProfile(profile), profile)
      lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
      // 已经落库，本地草稿失去意义，立刻删除，避免下次进来误提示恢复。
      clearDraft()
      return profile
    } finally {
      saving.value = false
    }
  }

  /** 生成简历：只传 title，内容由后端按登录用户从库里组装。 */
  async function generate(title = '我的简历'): Promise<ResumeRead> {
    generating.value = true
    try {
      const resume = await resumesApi.generateResume({ title })
      generatedHtml.value = resume.content_html
      generatedResumeId.value = resume.resume_id
      generatedTitle.value = resume.title
      return resume
    } finally {
      generating.value = false
    }
  }

  /** 回到「实时预览」模式，丢掉已生成的 HTML（不影响数据库里的简历）。 */
  function showLivePreview(): void {
    generatedHtml.value = null
    generatedResumeId.value = null
    generatedTitle.value = null
  }

  /* -------------------------------- 本地草稿 -------------------------------- */

  let autosaveTimer: ReturnType<typeof setTimeout> | null = null
  let stopDraftWatcher: (() => void) | null = null

  function cancelScheduledWrite(): void {
    if (autosaveTimer !== null) {
      clearTimeout(autosaveTimer)
      autosaveTimer = null
    }
  }

  function persistNow(): void {
    const owner = draftOwnerId.value
    if (!owner) return
    try {
      const envelope: LocalDraft = {
        version: DRAFT_VERSION,
        saved_at: Date.now(),
        // 深拷贝：draft 是 reactive，直接 JSON.stringify 会带上 Proxy 语义，
        // 且草稿必须是写入那一刻的快照，不能被后续编辑改写。
        data: JSON.parse(JSON.stringify(draft)) as ProfileDraft,
      }
      localStorage.setItem(draftStorageKey(owner), JSON.stringify(envelope))
      autosavedAt.value = envelope.saved_at
    } catch {
      // 超出配额 / 无痕模式禁用 storage：草稿是增益能力，失败必须静默。
    }
  }

  function scheduleWrite(): void {
    cancelScheduledWrite()
    autosaveTimer = setTimeout(() => {
      autosaveTimer = null
      persistNow()
    }, DRAFT_WRITE_DELAY)
  }

  /**
   * 绑定草稿归属用户并开启自动写入。
   *
   * 只在 `dirty` 为真时写草稿——`loadFromServer()` / `reset()` 回填数据时
   * `dirty` 是 false，所以「刚进页面」不会凭空生成一份草稿。
   */
  function startDraftAutosave(ownerId: string): void {
    draftOwnerId.value = ownerId
    stopDraftWatcher?.()
    stopDraftWatcher = watch(
      draft,
      () => {
        if (!dirty.value) return
        scheduleWrite()
      },
      { deep: true },
    )
  }

  function stopDraftAutosave(): void {
    stopDraftWatcher?.()
    stopDraftWatcher = null
    cancelScheduledWrite()
  }

  /**
   * 读取本地草稿并判断是否值得提示恢复。
   *
   * 判定用「内容是否与服务端不同」（忽略 self_rating），而不是裸比时间戳：
   * 后端 `updated_at` 是 UTC naive 字符串，前端 `Date.parse` 会按本地时区解析，
   * 直接比时间会稳定偏 8 小时，误判成「草稿更新」。时间只用于提示文案。
   */
  function detectLocalDraft(ownerId: string): void {
    const found = readDraft(ownerId)
    if (!found) {
      pendingDraft.value = null
      return
    }
    if (comparableOf(found.data) === comparableOf(draft)) {
      // 和服务器一模一样，说明上次其实已经保存成功，草稿是残留，直接清掉。
      removeDraft(ownerId)
      pendingDraft.value = null
      return
    }
    pendingDraft.value = found
  }

  /** 用户选择「恢复」：把草稿灌回表单，并标记为未保存。 */
  function restoreDraft(): void {
    const pending = pendingDraft.value
    if (!pending) return
    applyDraft(normalizeDraft(pending.data), savedProfile.value)
    dirty.value = true
    pendingDraft.value = null
  }

  /** 用户选择「丢弃」：删本地草稿，保留服务端数据。 */
  function discardDraft(): void {
    const owner = draftOwnerId.value
    if (owner) removeDraft(owner)
    pendingDraft.value = null
  }

  function clearDraft(): void {
    cancelScheduledWrite()
    const owner = draftOwnerId.value
    if (owner) removeDraft(owner)
    autosavedAt.value = null
    pendingDraft.value = null
  }

  /** 登出时清空，避免下一个账号看到上一个人的档案。 */
  function reset(): void {
    // 先解绑归属，再清空 draft：否则监听器会把空白骨架写成上一个人的草稿。
    draftOwnerId.value = null
    cancelScheduledWrite()
    pendingDraft.value = null
    autosavedAt.value = null
    applyDraft(createEmptyDraft(), null)
    lastSavedAt.value = null
    showLivePreview()
  }

  return {
    draft,
    savedProfile,
    loading,
    saving,
    generating,
    dirty,
    lastSavedAt,
    generatedHtml,
    generatedResumeId,
    generatedTitle,
    pendingDraft,
    autosavedAt,
    hasSavedProfile,
    isGeneratingResultVisible,
    canAddSkill,
    canRemoveSkill,
    canAddExperience,
    canRemoveExperience,
    canAddEducation,
    canRemoveEducation,
    updateProfileField,
    updateSkill,
    addSkill,
    removeSkill,
    updateExperience,
    addExperience,
    removeExperience,
    updateEducation,
    addEducation,
    removeEducation,
    validateForGenerate,
    toUpsertPayload,
    loadFromServer,
    save,
    generate,
    showLivePreview,
    startDraftAutosave,
    stopDraftAutosave,
    detectLocalDraft,
    restoreDraft,
    discardDraft,
    clearDraft,
    reset,
  }
})
