/**
 * 版本历史 / 差异 / 按岗位定制的 store 契约（批次 B8 剩余，`ADR-026`）。
 *
 * 只钉**动作接线与状态归属**，不验渲染（渲染在 `ResumeVersionsDialog.spec.ts`）：
 *
 * 1. `customizeForJob` 发的请求体只有 `job_id` + `profile_id`，且**不碰**
 *    `generatedHtml` / `generatedResumeId`——那两个字段代表「纸面上显示的那一版」，
 *    定制版不移动链头，所以它们必须原封不动（`ADR-026` §2.2）。
 * 2. `loadVersions` / `compareVersions` 的 loading 开关在失败时也必须复位，
 *    否则一次网络抖动会让按钮永久停在「加载中」。
 * 3. `reset()` 要清掉三份数据：它们都属于上一个账号（登出后不能留在内存里）。
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ResumeCustomizeResult, ResumeDiff, ResumeVersionSummary } from '@/api/types'

const resumesApi = vi.hoisted(() => ({
  generateResume: vi.fn(),
  polishResume: vi.fn(),
  listResumeVersions: vi.fn(),
  diffResumeVersions: vi.fn(),
  customizeResume: vi.fn(),
}))
vi.mock('@/api/resumes', () => resumesApi)

const profilesApi = vi.hoisted(() => ({
  listProfiles: vi.fn(),
  getProfile: vi.fn(),
  createProfile: vi.fn(),
  updateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  setDefaultProfile: vi.fn(),
  renameProfile: vi.fn(),
  listTrashProfiles: vi.fn(),
  restoreProfile: vi.fn(),
  purgeProfile: vi.fn(),
}))
vi.mock('@/api/profiles', () => profilesApi)

import { useResumeStore } from '@/stores/resume'

const VERSION_ONE: ResumeVersionSummary = {
  resume_id: 'resume-v1',
  version_no: 1,
  title: '通用版',
  origin: 'generated',
  is_head: true,
  target_job_id: null,
  created_at: '2026-11-12T10:00:00+08:00',
}

const VERSION_TWO: ResumeVersionSummary = {
  resume_id: 'resume-v2',
  version_no: 2,
  title: '通用版 · 面向「Python 后端实习」',
  origin: 'customized',
  is_head: false,
  target_job_id: 'job-1',
  created_at: '2026-11-13T10:00:00+08:00',
}

const CUSTOMIZE_RESULT: ResumeCustomizeResult = {
  resume: {
    resume_id: 'resume-v2',
    title: '通用版 · 面向「Python 后端实习」',
    content_html: '<html><body>定制版</body></html>',
    created_at: '2026-11-13T10:00:00+08:00',
    version_no: 2,
  },
  based_on_version_no: 1,
  focus: {
    job_id: 'job-1',
    job_title: 'Python 后端实习',
    matched: ['Python'],
    missing: ['Docker'],
  },
}

const DIFF: ResumeDiff = {
  from_version: VERSION_ONE,
  to_version: VERSION_TWO,
  comparable: true,
  note: null,
  summary: {
    added: 0,
    removed: 0,
    changed: 0,
    sections_changed: 1,
    section_order_changed: false,
    item_order_changed: true,
  },
  sections: [],
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.clearAllMocks()
})

describe('版本历史与按岗位定制（ADR-026）', () => {
  it('定制只发 job_id 与当前档案，且不改变纸面上那一版', async () => {
    const store = useResumeStore()
    store.currentProfileId = 'profile-1'
    // 纸面上现在显示的是第一版：定制成功之后它必须还在。
    store.generatedHtml = '<html><body>第一版</body></html>'
    store.generatedResumeId = 'resume-v1'
    resumesApi.customizeResume.mockResolvedValue(CUSTOMIZE_RESULT)

    const result = await store.customizeForJob('job-1')

    expect(resumesApi.customizeResume).toHaveBeenCalledWith({
      job_id: 'job-1',
      profile_id: 'profile-1',
    })
    expect(result.focus.matched).toEqual(['Python'])
    expect(store.lastCustomize?.resume.resume_id).toBe('resume-v2')
    // 定制不移动链头：纸面上的那一版一个字都不该变。
    expect(store.generatedResumeId).toBe('resume-v1')
    expect(store.generatedHtml).toBe('<html><body>第一版</body></html>')
  })

  it('定制失败时把错误抛给调用方，且不留下半截结果', async () => {
    const store = useResumeStore()
    resumesApi.customizeResume.mockRejectedValue(new Error('岗位已过截止时间'))

    await expect(store.customizeForJob('job-1')).rejects.toThrow('岗位已过截止时间')
    expect(store.lastCustomize).toBeNull()
    expect(store.customizing).toBe(false)
  })

  it('拉历史版本会写进 store，失败时 loading 也要复位', async () => {
    const store = useResumeStore()
    resumesApi.listResumeVersions.mockResolvedValue({
      profile_id: 'profile-1',
      current_version_id: 'resume-v1',
      versions: [VERSION_TWO, VERSION_ONE],
    })

    await store.loadVersions()
    expect(store.versions.map((item) => item.resume_id)).toEqual(['resume-v2', 'resume-v1'])
    expect(store.versionsLoading).toBe(false)

    resumesApi.listResumeVersions.mockRejectedValue(new Error('网络错误'))
    await expect(store.loadVersions()).rejects.toThrow('网络错误')
    // 失败不能把按钮永久钉在「加载中」。
    expect(store.versionsLoading).toBe(false)
  })

  it('对比两版写入 versionDiff；`null` 与「比了两版一样」是两件事', async () => {
    const store = useResumeStore()
    expect(store.versionDiff).toBeNull()

    resumesApi.diffResumeVersions.mockResolvedValue(DIFF)
    await store.compareVersions('resume-v1', 'resume-v2')

    expect(resumesApi.diffResumeVersions).toHaveBeenCalledWith('resume-v1', 'resume-v2')
    expect(store.versionDiff?.summary.item_order_changed).toBe(true)
    expect(store.diffing).toBe(false)
  })

  it('登出清空三份数据', async () => {
    const store = useResumeStore()
    store.versions = [VERSION_TWO]
    store.versionDiff = DIFF
    store.lastCustomize = CUSTOMIZE_RESULT

    store.reset()

    expect(store.versions).toEqual([])
    expect(store.versionDiff).toBeNull()
    expect(store.lastCustomize).toBeNull()
  })
})
