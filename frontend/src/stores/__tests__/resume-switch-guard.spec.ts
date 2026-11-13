/**
 * 批次 F1b 里最要紧的一处调用点：`stores/resume.ts` 的「切换前先处理未保存修改」。
 *
 * 为什么单独给它一个文件：这一处**没有模板**（它在 store 的动作内部），原来写的是
 * `window.confirm`。换成页面内弹窗之后，「用户取消」必须仍然意味着**什么都没发生**——
 * 这是整条改造的安全边界。原来它是同步的 `if (!shouldSave) return false`，
 * 现在是 `await` 一个 Promise：接线写错就会变成「取消也照样切」，而那会直接吃掉用户的修改。
 *
 * `getProfile` 在这里刻意让它失败：本用例要验的是**顺序**（先问 → 确认才保存 → 才去拉新简历），
 * 不是新简历的内容。失败路径正好还能多钉一件事——`activateProfile` 会把指针退回去。
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
import { useDialogStore } from '@/stores/dialog'
import { DEFAULT_LAYOUT_SECTIONS, DEFAULT_THEME, useResumeStore } from '@/stores/resume'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.clearAllMocks()
  const auth = useAuthStore()
  auth.user = {
    id: 'user-under-test',
    username: 'tester',
    email: null,
    phone: null,
    created_at: '2026-11-10T10:00:00+08:00',
  }
})

describe('切换简历前的未保存拦截走页面内弹窗（批次 F1b）', () => {
  function seedTwoProfiles() {
    const store = useResumeStore()
    store.profiles = [
      {
        id: 'profile-a',
        title: '投后端的那一版',
        is_default: true,
        full_name: '张三',
        created_at: '2026-11-10T10:00:00+08:00',
        updated_at: '2026-11-10T10:00:00+08:00',
        deleted_at: null,
      },
    ]
    store.currentProfileId = 'profile-a'
    return store
  }

  it('用户点「先不切换」时：一次请求都不发、指针不动、`switchProfile` 返回 false', async () => {
    const store = seedTwoProfiles()
    const dialog = useDialogStore()
    store.dirty = true

    const switching = store.switchProfile('profile-b')
    await Promise.resolve()

    // 弹窗此刻正拦在中间：这是「取消也什么都不做」成立的前提。
    expect(dialog.pending?.title).toBe('切换前先处理未保存的修改')
    expect(getProfileCalls()).toBe(0)
    expect(store.currentProfileId).toBe('profile-a')

    dialog.settle(false)
    expect(await switching).toBe(false)
    expect(getProfileCalls()).toBe(0)
    expect(store.currentProfileId).toBe('profile-a')
    expect(store.dirty).toBe(true)
  })

  it('用户点「保存并切换」时：先保存，再拉新简历（顺序钉住，且原生 confirm 没被碰）', async () => {
    const store = seedTwoProfiles()
    const dialog = useDialogStore()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    store.dirty = true
    // 这里不 spy `store.save`：`ensureSavedBeforeSwitch()` 调的是 store 内部的 `save` 闭包，
    // spy 挂在外层属性上拦截不到，会得到一个「调用了但没人看见」的假失败。
    // 所以判据落在真正可观察的那一层——保存实际发出的 PUT。
    profilesApi.updateProfile.mockResolvedValue(profileFixture('profile-a'))
    profilesApi.listProfiles.mockResolvedValue([])
    // 本用例不验新简历的内容，只验顺序：让它失败，顺便钉住「拉取失败会把指针退回去」。
    profilesApi.getProfile.mockRejectedValue(new Error('这份 fixture 不参与本用例'))

    const switching = store.switchProfile('profile-b')
    await Promise.resolve()
    expect(confirmSpy).toHaveBeenCalledTimes(0)

    dialog.settle(true)
    await expect(switching).rejects.toThrow()

    expect(profilesApi.updateProfile).toHaveBeenCalledTimes(1)
    expect(profilesApi.getProfile).toHaveBeenCalledWith('profile-b')
    expect(store.currentProfileId).toBe('profile-a')
    confirmSpy.mockRestore()
  })

  it('没有未保存修改时根本不打扰用户：弹窗从不出现', async () => {
    const store = seedTwoProfiles()
    const dialog = useDialogStore()
    store.dirty = false
    profilesApi.getProfile.mockRejectedValue(new Error('同上'))

    const switching = store.switchProfile('profile-b')
    await expect(switching).rejects.toThrow()

    expect(dialog.pending).toBeNull()
    // 「不打扰」的可观察版本：既没有弹窗，也没有任何一次保存请求。
    expect(profilesApi.updateProfile).toHaveBeenCalledTimes(0)
  })
})

import type { ProfileRead } from '@/api/types'

/** 一份形状完整的档案：`save()` 之后会 `applyDraft(draftFromProfile(profile))`，缺字段就当场炸。 */
function profileFixture(id: string): ProfileRead {
  return {
    id,
    user_id: 'user-under-test',
    title: '投后端的那一版',
    is_default: true,
    deleted_at: null,
    paper_size: 'A4',
    theme: DEFAULT_THEME,
    layout_config: { version: 1, sections: [...DEFAULT_LAYOUT_SECTIONS] },
    full_name: '张三',
    email: null,
    grade: '2027',
    contact: '13800000000',
    target_role: '后端开发',
    created_at: '2026-11-10T10:00:00+08:00',
    updated_at: '2026-11-10T10:00:00+08:00',
    skills: [],
    experiences: [],
    educations: [],
    // 批次 B4（ADR-015 §2.1）新增的三组子表读数：形状完整的档案必须带上它们，
    // 否则 `draftFromProfile()` 一类「按契约读」的路径在测试里会与真实响应不同形。
    certificates: [],
    portfolio_links: [],
    city_preferences: [],
  }
}

function getProfileCalls(): number {
  return profilesApi.getProfile.mock.calls.length
}
