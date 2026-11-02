/**
 * 档案 + 简历生成 store（T7.3 起接真实后端）。
 *
 * 字段命名与后端 `ProfileUpsertRequest` / `ProfileRead` 严格一致（snake_case），
 * 唯一例外是 `ExperienceDraft.end_date` 用空串表示「至今」——
 * `<input type="date">` 不接受 null，所以在提交时由 `toUpsertPayload()` 转成 null。
 */

import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'

import { profilesApi, resumesApi } from '@/api'
import type { ProfileRead, ProfileUpsertRequest, ResumeRead } from '@/api/types'

/** 与后端 `ProfileUpsertRequest` 的 Field 约束保持一致。 */
export const SKILL_LIMIT = { min: 3, max: 8 } as const
export const EXPERIENCE_LIMIT = { min: 1, max: 3 } as const

export const DEFAULT_SKILL_RATING = 3

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

export interface ProfileDraft {
  full_name: string
  major: string
  grade: string
  contact: string
  skills: SkillDraft[]
  experiences: ExperienceDraft[]
}

function todayIso(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

/** 首次进入时的空白骨架：默认给到后端要求的最小条数，避免用户一进来就撞 422。 */
function createEmptyDraft(): ProfileDraft {
  return {
    full_name: '',
    major: '',
    grade: '',
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
  }
}

function draftFromProfile(profile: ProfileRead): ProfileDraft {
  return {
    full_name: profile.full_name,
    major: profile.major,
    grade: profile.grade,
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
  }
}

export const useResumeStore = defineStore('resume', () => {
  const draft = reactive<ProfileDraft>(createEmptyDraft())
  const savedProfile = ref<ProfileRead | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const generating = ref(false)
  const dirty = ref(false)
  const lastSavedAt = ref<string | null>(null)

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

  function markDirty(): void {
    dirty.value = true
  }

  function applyDraft(next: ProfileDraft, profile: ProfileRead | null): void {
    draft.full_name = next.full_name
    draft.major = next.major
    draft.grade = next.grade
    draft.contact = next.contact
    draft.skills = next.skills
    draft.experiences = next.experiences
    savedProfile.value = profile
    dirty.value = false
  }

  function updateProfileField(
    field: 'full_name' | 'major' | 'grade' | 'contact',
    value: string,
  ): void {
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

  /** 组装请求体：去首尾空格，`end_date` 空串转 null。 */
  function toUpsertPayload(): ProfileUpsertRequest {
    return {
      full_name: draft.full_name.trim(),
      major: draft.major.trim(),
      grade: draft.grade.trim(),
      contact: draft.contact.trim(),
      skills: draft.skills.map((skill) => ({
        name: skill.name.trim(),
        self_rating: skill.self_rating,
      })),
      experiences: draft.experiences.map((experience) => ({
        title: experience.title.trim(),
        start_date: experience.start_date,
        end_date: experience.end_date === '' ? null : experience.end_date,
        description: experience.description.trim(),
        responsibility: experience.responsibility.trim(),
      })),
    }
  }

  /** 本地预校验，规则逐条对齐后端；返回 null 表示通过。 */
  function validate(): string | null {
    if (!draft.full_name.trim()) return '请填写姓名。'
    if (!draft.major.trim()) return '请填写专业。'
    if (!draft.grade.trim()) return '请填写年级。'
    if (!draft.contact.trim()) return '请填写联系方式。'

    if (draft.skills.length < SKILL_LIMIT.min || draft.skills.length > SKILL_LIMIT.max) {
      return `技能数量需在 ${SKILL_LIMIT.min}–${SKILL_LIMIT.max} 条之间。`
    }
    const skillNames = draft.skills.map((skill) => skill.name.trim())
    if (skillNames.some((name) => !name)) return '技能名称不能为空。'
    if (new Set(skillNames).size !== skillNames.length) return '技能名称不能重复。'

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

  /** 保存档案：PUT 整体替换，成功后用服务端返回值回填本地。 */
  async function save(): Promise<ProfileRead> {
    saving.value = true
    try {
      const profile = await profilesApi.saveMyProfile(toUpsertPayload())
      applyDraft(draftFromProfile(profile), profile)
      lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
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

  /** 登出时清空，避免下一个账号看到上一个人的档案。 */
  function reset(): void {
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
    hasSavedProfile,
    isGeneratingResultVisible,
    canAddSkill,
    canRemoveSkill,
    canAddExperience,
    canRemoveExperience,
    updateProfileField,
    updateSkill,
    addSkill,
    removeSkill,
    updateExperience,
    addExperience,
    removeExperience,
    validate,
    toUpsertPayload,
    loadFromServer,
    save,
    generate,
    showLivePreview,
    reset,
  }
})
