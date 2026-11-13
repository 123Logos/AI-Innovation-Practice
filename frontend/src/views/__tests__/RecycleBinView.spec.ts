/**
 * 回收站「彻底删除」的确认（批次 F1b 的第 6 处，也是唯一**不可逆**的那一处）。
 *
 * 这里钉的是一条顺序，不长相：点按钮只打开弹层 → 确认才发 DELETE。
 * 原生 `window.confirm` 之前把这条顺序藏在一个同步返回值里，验收时既点不到也断言不了；
 * 换成页面内弹窗后，「还没删」这一步第一次变成可观察的事实。
 *
 * 与 `JobDetailView` 的同名判据刻意保持一致的写法：先证「没删」，再用反面对照证「能删」，
 * 否则「没删」可能只是因为根本没接上。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type RouteRecordRaw } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const profilesApi = vi.hoisted(() => ({
  listTrashProfiles: vi.fn(),
  purgeProfile: vi.fn(),
  restoreProfile: vi.fn(),
  listProfiles: vi.fn(),
  getProfile: vi.fn(),
  createProfile: vi.fn(),
  updateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  setDefaultProfile: vi.fn(),
  renameProfile: vi.fn(),
}))

vi.mock('@/api/profiles', () => profilesApi)

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useAuthStore } from '@/stores/auth'
import { useResumeStore } from '@/stores/resume'
import RecycleBinView from '@/views/RecycleBinView.vue'

const BLANK = { template: '<div />' }
const TRASH_ROW = {
  id: 'profile-in-trash',
  title: '投算法的那一版',
  is_default: false,
  full_name: '张三',
  created_at: '2026-11-01T09:00:00+08:00',
  updated_at: '2026-11-05T09:00:00+08:00',
  deleted_at: '2026-11-06T20:12:00+08:00',
}

let pinia: Pinia

function makeRouter() {
  const routes: RouteRecordRaw[] = [
    { path: '/trash', name: 'recycle-bin', component: BLANK },
    { path: '/editor', name: 'resume-editor', component: BLANK },
    { path: '/jobs', name: 'job-board', component: BLANK },
    { path: '/login', name: 'login', component: BLANK },
  ]
  return createRouter({ history: createMemoryHistory(), routes })
}

function bodyText(selector: string): string {
  return document.body.querySelector(selector)?.textContent ?? ''
}

async function bodyClick(selector: string): Promise<void> {
  const el = document.body.querySelector(selector)
  if (!el) throw new Error(`找不到弹窗按钮：${selector}`)
  ;(el as HTMLElement).click()
  await flushPromises()
}

async function mountView() {
  const router = makeRouter()
  await router.push('/trash')
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(RecycleBinView, { attachTo, global: { plugins: [pinia, router] } })
  // 弹窗挂在 App.vue，测试里按同一个 pinia 补一份，否则这条链路只验到 store 那一格。
  mount(ConfirmDialog, { global: { plugins: [pinia] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  document.body.innerHTML = ''
  localStorage.clear()
  profilesApi.listTrashProfiles.mockResolvedValue([TRASH_ROW])
  profilesApi.listProfiles.mockResolvedValue([])
  profilesApi.purgeProfile.mockResolvedValue(undefined)
  const auth = useAuthStore()
  auth.user = {
    id: 'user-under-test',
    username: 'tester',
    email: null,
    phone: null,
    created_at: '2026-11-10T10:00:00+08:00',
  }
})

describe('彻底删除走页面内弹层（批次 F1b）', () => {
  it('点「彻底删除」只打开弹层并点名是哪一份；确认才发请求', async () => {
    const { wrapper } = await mountView()

    await wrapper.find('.row-button--danger').trigger('click')
    await flushPromises()

    expect(bodyText('[data-testid="app-dialog"]')).toContain('这一步不可逆')
    // 「哪一份」必须单独可见：不可逆操作里最贵的一格就是认错行。
    expect(bodyText('[data-testid="app-dialog"]')).toContain('投算法的那一版')
    expect(profilesApi.purgeProfile).toHaveBeenCalledTimes(0)

    await bodyClick('[data-testid="app-dialog-cancel"]')
    expect(profilesApi.purgeProfile).toHaveBeenCalledTimes(0)

    // 反面对照：确认这条路真的删得掉。
    await wrapper.find('.row-button--danger').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-confirm"]')
    expect(profilesApi.purgeProfile).toHaveBeenCalledTimes(1)
    expect(profilesApi.purgeProfile).toHaveBeenCalledWith('profile-in-trash')
  })

  it('`window.confirm` 一次都没被调用', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { wrapper } = await mountView()

    await wrapper.find('.row-button--danger').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-confirm"]')

    expect(confirmSpy).toHaveBeenCalledTimes(0)
    confirmSpy.mockRestore()
  })

  it('回收站列表为空时，这一整条链路根本没有按钮可点（证明上面两句不是空断言）', async () => {
    profilesApi.listTrashProfiles.mockResolvedValue([])
    const { wrapper } = await mountView()

    expect(wrapper.findAll('.row-button--danger')).toHaveLength(0)
    expect(useResumeStore().trashProfiles).toEqual([])
  })
})
