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
 * 教育经历（T7.5）是一对多子表，前端与后端的渲染顺序都是「入学时间倒序」。
 *
 * 本地草稿：按用户 id 隔离存在 `localStorage`，只写不覆盖服务端数据；
 * 用户点「保存档案」成功后立即清除。
 */

import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { profilesApi, resumesApi } from '@/api'
import type { Degree, ProfileRead, ProfileUpsertRequest, ResumeRead } from '@/api/types'

/** 与后端 `ProfileUpsertRequest` 的 Field 约束保持一致。 */
export const SKILL_LIMIT = { min: 3, max: 8 } as const
export const EXPERIENCE_LIMIT = { min: 1, max: 3 } as const
/** 后端 `educations` 最多 5 段；UI 至少保留 1 段。 */
export const EDUCATION_LIMIT = { min: 1, max: 5 } as const

/** 学历下拉选项，顺序与后端枚举声明一致。 */
export const DEGREE_OPTIONS: readonly Degree[] = ['专科', '本科', '硕士', '博士']

/** UI 已不再采集熟练度；提交时一律用它填后端必填字段。 */
export const DEFAULT_SKILL_RATING = 3

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
  major: string
  contact: string
  skills: SkillDraft[]
  experiences: ExperienceDraft[]
  educations: EducationDraft[]
}

/** 当前日期（`YYYY-MM-DD`），用作新经历的默认开始日期。 */
function todayIso(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

/** 当前月份（`YYYY-MM`），用作新教育经历入学时间的默认值。 */
function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
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
    edu_start: currentMonth(),
    edu_end: '',
  }
}

/** 首次进入时的空白骨架：默认给到后端要求的最小条数，避免用户一进来就撞 422。 */
function createEmptyDraft(): ProfileDraft {
  return {
    full_name: '',
    major: '',
    contact: '',
    skills: Array.from({ length: SKILL_LIMIT.min }, () => ({
      name: '',
      self_rating: DEFAULT_SKILL_RATING,
    })),
    experiences: [
      {
        title: '',
        start_date: todayIso(),
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
    major: profile.major,
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
    typeof candidate.major === 'string' &&
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
    (candidate.educations === undefined || Array.isArray(candidate.educations))
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
 * - 已下线的 `self_rating` 抹平为默认值；
 * - T7.5 之前的草稿没有 `educations`，补空数组而不是报错。
 */
function normalizeDraft(raw: ProfileDraft): ProfileDraft {
  return {
    full_name: raw.full_name ?? '',
    major: raw.major ?? '',
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

/**
 * 用于「草稿 vs 服务端」比较的归一化序列化。
 *
 * 刻意忽略 `self_rating`：UI 已不采集它，提交后恒为 3，
 * 若把它算进比较，一份本来没改过的草稿也会被判成「有未保存修改」。
 */
function comparableOf(source: ProfileDraft): string {
  return JSON.stringify({
    full_name: source.full_name,
    major: source.major,
    contact: source.contact,
    skills: source.skills.map((skill) => skill.name),
    experiences: source.experiences.map((experience) => [
      experience.title,
      experience.start_date,
      experience.end_date,
      experience.description,
      experience.responsibility,
    ]),
    educations: (source.educations ?? []).map((education) => [
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
    draft.major = next.major
    draft.contact = next.contact
    draft.skills = next.skills
    draft.experiences = next.experiences
    draft.educations = next.educations
    savedProfile.value = profile
    dirty.value = false
  }

  function updateProfileField(field: 'full_name' | 'major' | 'contact', value: string): void {
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
      start_date: todayIso(),
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

  /** 组装请求体：去首尾空格，日期空串转 null，熟练度固定 3，年级不再提交。 */
  function toUpsertPayload(): ProfileUpsertRequest {
    return {
      full_name: draft.full_name.trim(),
      major: draft.major.trim(),
      contact: draft.contact.trim(),
      skills: draft.skills.map((skill) => ({
        name: skill.name.trim(),
        // 后端 `SkillInput.self_rating` 仍必填（1–5），但 UI 不再采集，统一写 3。
        self_rating: DEFAULT_SKILL_RATING,
      })),
      experiences: draft.experiences.map((experience) => ({
        title: experience.title.trim(),
        start_date: experience.start_date,
        end_date: experience.end_date === '' ? null : experience.end_date,
        description: experience.description.trim(),
        responsibility: experience.responsibility.trim(),
      })),
      educations: draft.educations.map((education) => ({
        school: education.school.trim(),
        major: education.major.trim(),
        degree: education.degree,
        // 后端是 DATE：`YYYY-MM` 补成当月 1 号；空值由校验拦住，这里兜底给空串外的 null。
        edu_start: toIsoDate(education.edu_start) ?? '',
        edu_end: toIsoDate(education.edu_end),
      })),
    }
  }

  /** 本地预校验，规则逐条对齐后端；返回 null 表示通过。 */
  function validate(): string | null {
    if (!draft.full_name.trim()) return '请填写姓名。'
    if (!draft.major.trim()) return '请填写专业。'
    if (!draft.contact.trim()) return '请填写联系方式。'

    if (draft.skills.length < SKILL_LIMIT.min || draft.skills.length > SKILL_LIMIT.max) {
      return `技能数量需在 ${SKILL_LIMIT.min}–${SKILL_LIMIT.max} 条之间。`
    }
    const skillNames = draft.skills.map((skill) => skill.name.trim())
    if (skillNames.some((name) => !name)) return '技能名称不能为空。'
    if (new Set(skillNames).size !== skillNames.length) return '技能名称不能重复。'

    if (
      draft.educations.length < EDUCATION_LIMIT.min ||
      draft.educations.length > EDUCATION_LIMIT.max
    ) {
      return `教育经历需在 ${EDUCATION_LIMIT.min}–${EDUCATION_LIMIT.max} 段之间。`
    }
    for (const [index, education] of draft.educations.entries()) {
      const label = `第 ${index + 1} 段教育经历`
      if (!education.school.trim()) return `${label}缺少学校名称。`
      if (!education.major.trim()) return `${label}缺少专业。`
      if (!education.degree) return `${label}缺少学历。`
      if (!education.edu_start) return `${label}缺少入学时间。`
      if (education.edu_end && education.edu_end < education.edu_start) {
        return `${label}的毕业时间不能早于入学时间。`
      }
    }

    if (
      draft.experiences.length < EXPERIENCE_LIMIT.min ||
      draft.experiences.length > EXPERIENCE_LIMIT.max
    ) {
      return `经历数量需在 ${EXPERIENCE_LIMIT.min}–${EXPERIENCE_LIMIT.max} 条之间。`
    }
    for (const [index, experience] of draft.experiences.entries()) {
      const label = `第 ${index + 1} 段经历`
      if (!experience.title.trim()) return `${label}缺少项目名称。`
      if (!experience.start_date) return `${label}缺少开始日期。`
      if (!experience.description.trim()) return `${label}缺少项目描述。`
      if (!experience.responsibility.trim()) return `${label}缺少个人职责。`
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

  /** 保存档案：PUT 整体替换，成功后用服务端返回值回填本地，并清掉本地草稿。 */
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
    validate,
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
