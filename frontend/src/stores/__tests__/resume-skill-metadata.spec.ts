/**
 * 技能元数据（B7 / `ADR-027`）的前端逻辑单测。
 *
 * 后端已经保证「落库的值对不对」，这里只钉三条**前端会悄悄弄坏**的事：
 *
 * 1. 改来源 / 改「最近使用」必须真的动草稿并置 `dirty`；
 * 2. `save()` 发出的技能对象必须是后端认的形状：`last_used_on` 从 `YYYY-MM`
 *    补成 `YYYY-MM-DD`（后端是 `DATE`），空串折成 `null`；
 * 3. **`assessment` 那一格整格不传**——它是服务端写的（标准化测评提交时），
 *    客户端传过去会 422；而草稿里必须原样保留，否则用户的下一次保存
 *    会把测评结果抹成「自评」（`ADR-027` §2.3）。
 *
 * 第 4 条钉的是 `comparableOf` 投影：只改这两格也必须判成「有未保存的修改」。
 * 漏算的后果不是报错，而是横幅不弹、用户以为自己的修改已经存进去了。
 */

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ProfileRead } from '@/api/types'
import { DEFAULT_LAYOUT_SECTIONS, DEFAULT_THEME, useResumeStore } from '@/stores/resume'

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

import { useAuthStore } from '@/stores/auth'

const OWNER_ID = 'user-under-test'
const DRAFT_KEY = `resume_draft:${OWNER_ID}:new`

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  profilesApi.updateProfile.mockReset()
})

/** 一份形状完整的档案：`save()` 之后会 `applyDraft(draftFromProfile(profile))`，缺字段就当场炸。 */
function profileFixture(id: string): ProfileRead {
  return {
    id,
    user_id: OWNER_ID,
    title: '我的简历',
    is_default: true,
    deleted_at: null,
    paper_size: 'A4',
    theme: DEFAULT_THEME,
    layout_config: { version: 1, sections: [...DEFAULT_LAYOUT_SECTIONS] },
    full_name: '张三',
    email: null,
    grade: '2027',
    contact: '13800000000',
    target_role: null,
    created_at: '2026-11-13T10:00:00+08:00',
    updated_at: '2026-11-13T10:00:00+08:00',
    skills: [],
    experiences: [],
    educations: [],
    certificates: [],
    portfolio_links: [],
    city_preferences: [],
  }
}

/** 登录态 + 一份已存档案，用于走 `save()`。 */
function readyToSave() {
  const store = useResumeStore()
  const auth = useAuthStore()
  auth.user = {
    id: OWNER_ID,
    username: 'tester',
    email: null,
    phone: null,
    created_at: '2026-11-13T10:00:00+08:00',
  }
  store.currentProfileId = 'profile-x'
  store.savedProfile = profileFixture('profile-x')
  return store
}

async function captureSavePayload(): Promise<Record<string, unknown>> {
  const store = readyToSave()
  let captured: Record<string, unknown> | null = null
  profilesApi.updateProfile.mockImplementation((_id: string, payload: unknown) => {
    captured = payload as Record<string, unknown>
    return Promise.resolve(profileFixture('profile-x'))
  })
  await store.save()
  expect(captured).not.toBeNull()
  return captured!
}

/** 把「上次没保存」的草稿塞进 localStorage，可覆盖任意字段。 */
function seedLocalDraft(patch: Record<string, unknown> = {}): void {
  const store = useResumeStore()
  const data = JSON.parse(JSON.stringify(store.draft)) as Record<string, unknown>
  localStorage.setItem(
    DRAFT_KEY,
    JSON.stringify({ version: 1, saved_at: Date.now(), data: { ...data, ...patch } }),
  )
}

describe('技能元数据：改来源 / 改最近使用', () => {
  it('updateSkillSource 写草稿并置 dirty', () => {
    const store = useResumeStore()
    store.addSkill()
    store.updateSkill(0, 'Python')
    expect(store.dirty).toBe(true)

    store.updateSkillSource(0, 'project')

    expect(store.draft.skills[0]?.source).toBe('project')
  })

  it('updateSkillLastUsed 写草稿；清空时回到空串（而不是 undefined）', () => {
    const store = useResumeStore()
    store.addSkill()
    store.updateSkill(0, 'Python')

    store.updateSkillLastUsed(0, '2026-05')
    expect(store.draft.skills[0]?.last_used_on).toBe('2026-05')

    store.updateSkillLastUsed(0, '')
    expect(store.draft.skills[0]?.last_used_on).toBe('')
  })

  it('`assessment` 行拒绝被改成别的来源（那一档由服务端写）', () => {
    const store = useResumeStore()
    store.addSkill()
    store.updateSkill(0, 'Python')
    store.draft.skills[0]!.source = 'assessment'

    store.updateSkillSource(0, 'self_reported')

    expect(store.draft.skills[0]?.source).toBe('assessment')
  })
})

describe('技能元数据：save 的请求形状', () => {
  it('带上 source 与 last_used_on，月份补成 01、空值折成 null', async () => {
    const store = readyToSave()
    store.addSkill()
    store.updateSkill(0, 'Python')
    store.updateSkillSource(0, 'project')
    store.updateSkillLastUsed(0, '2026-05')
    store.addSkill()
    store.updateSkill(1, 'SQL')

    const payload = await captureSavePayload()

    expect(payload.skills).toEqual([
      { name: 'Python', source: 'project', last_used_on: '2026-05-01' },
      // 没填月份、来源是默认档：仍然显式发 `self_reported`（用户确实选了它）。
      { name: 'SQL', source: 'self_reported', last_used_on: null },
    ])
  })

  it('`assessment` 行**整格省略 source**（传过去会 422，省略 = 沿用库里那一档）', async () => {
    const store = readyToSave()
    store.addSkill()
    store.updateSkill(0, 'Python')
    // 模拟「从服务端读回来的一行」：草稿里保留 assessment，但客户端无权重新声明。
    store.draft.skills[0]!.source = 'assessment'

    const payload = await captureSavePayload()

    expect(payload.skills).toEqual([{ name: 'Python', last_used_on: null }])
    expect((payload.skills as Record<string, unknown>[])[0]).not.toHaveProperty('source')
  })
})

describe('技能元数据：脏判定', () => {
  /**
   * `comparableOf` 是模块内私有函数，没有为了测试导出它。
   * 公开入口是 `detectLocalDraft()`：这里用「本地草稿 vs 当前草稿」的判定结果
   * 反证投影口径，测的是行为而不是实现细节（与 `resume.spec.ts` 同一手法）。
   */
  it('只改来源必须判为「有差异」；完全一致则判为残留并清掉', () => {
    const store = useResumeStore()
    store.draft.skills = [{ name: 'Python', source: 'self_reported', last_used_on: '' }]

    seedLocalDraft()
    store.detectLocalDraft(OWNER_ID)
    expect(store.pendingDraft).toBeNull()
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull()

    store.draft.skills = [{ name: 'Python', source: 'self_reported', last_used_on: '' }]
    seedLocalDraft({ skills: [{ name: 'Python', source: 'project', last_used_on: '' }] })
    store.detectLocalDraft(OWNER_ID)
    expect(store.pendingDraft).not.toBeNull()
    expect(store.pendingDraft?.data.skills[0]?.source).toBe('project')
  })

  it('只改最近使用月份也必须判为「有差异」', () => {
    const store = useResumeStore()
    store.draft.skills = [{ name: 'Python', source: 'self_reported', last_used_on: '' }]

    seedLocalDraft({ skills: [{ name: 'Python', source: 'self_reported', last_used_on: '2026-05' }] })
    store.detectLocalDraft(OWNER_ID)

    expect(store.pendingDraft).not.toBeNull()
    expect(store.pendingDraft?.data.skills[0]?.last_used_on).toBe('2026-05')
  })
})
