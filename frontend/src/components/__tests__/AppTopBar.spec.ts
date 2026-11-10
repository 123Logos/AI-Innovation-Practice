/**
 * 顶栏交互单测（B3 · ADR-011 议题 3.D）。
 *
 * 只测**结构与状态**，不测像素：jsdom 没有布局引擎，scoped 样式与
 * `@media (max-width: 760px)` 在这里根本不生效，所以本文件断言的是
 * 「谁在弹层里、谁在一级项上、`aria-expanded` 有没有跟着开合」这类 DOM/属性判据。
 * 375px 下顶栏是否真的回到单行，要靠真实视口量（见交付说明的「未度量」条目）。
 *
 * 三组用例对应监督者点名的验收项：
 * 1. 「更多」菜单的开关（含 `aria-expanded`、Esc、点外关闭）；
 * 2. 窄屏下「岗位库」仍然是文字链接、且没有被收进菜单；
 * 3. `handleLogout()` 之后岗位 store 为空，并钉住 `resume.reset()` / `job.reset()`
 *    都在 `auth.signOut()` **之前**这个顺序——顺序反了就会出现「下一个账号
 *    短暂看到上一个人的数据」那一帧。
 *
 * 路由用一份本地 memory router，而不是 `@/router`：
 * 后者带着全局守卫，登出时 `replace({ name: 'login' })` 会触发 `/auth/me` 校验，
 * 那是「登录态怎么守」的另一条验收线，这里要的是「跳到登录页」这个事实本身。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router, type RouteRecordRaw } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { authApi } from '@/api'
import { REFRESH_TOKEN_STORAGE_KEY, TOKEN_STORAGE_KEY } from '@/api/client'
import type { jobsApi as JobsContract } from '@/api'
import AppTopBar from '@/components/AppTopBar.vue'
import { useAuthStore } from '@/stores/auth'
import { useJobStore } from '@/stores/job'
import { useResumeStore } from '@/stores/resume'

/** 空组件：本地路由的占位目标，测试只关心 `currentRoute.name`。 */
const BLANK = { template: '<div />' }

/** 挂载出来的实例统一在这里收口，避免组件留在 `document` 上的监听器串到下一个用例。 */
const mounted: { unmount: () => void }[] = []

let pinia: Pinia

function makeRouter(): Router {
  const routes: RouteRecordRaw[] = [
    { path: '/jobs', name: 'job-board', component: BLANK },
    { path: '/trash', name: 'recycle-bin', component: BLANK },
    { path: '/login', name: 'login', component: BLANK },
  ]
  return createRouter({ history: createMemoryHistory(), routes })
}

/**
 * 顶栏的两个 document 监听（pointerdown / keydown）要求组件真的挂进 `document.body`，
 * 否则从弹层元素上冒泡的事件到不了 `document`，「点外关闭」那条就会假绿。
 */
async function mountTopBar() {
  const router = makeRouter()
  await router.push('/jobs')
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(AppTopBar, { attachTo, global: { plugins: [pinia, router] } })
  mounted.push(wrapper)
  return { wrapper, router }
}

/** 把岗位 store 塞成「刚看过上一个人的数据」的样子。 */
function seedJobStore(): void {
  const job = useJobStore()
  job.jobs = [{ id: 'job-of-previous-user' }] as unknown as JobsContract.JobSummary[]
  job.total = 120
  job.page = 3
  job.detail = { id: 'job-detail-of-previous-user' } as unknown as JobsContract.JobRead
  job.trash = [{ id: 'job-trash-of-previous-user' }] as unknown as JobsContract.JobSummary[]
  job.requirementCoverage = [{ name: 'Python', status: 'covered', matched_form: ' Python ' }]
  job.filter.keyword = '字节'
  job.filter.status = 'draft'
  job.filter.includeExpired = true
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  sessionStorage.clear()
  document.body.innerHTML = ''
  // 顶栏默认按「已登录」渲染：`user-name` 读 displayName，登出用例读 token。
  const auth = useAuthStore()
  auth.user = {
    id: 'user-under-test',
    username: 'tester',
    email: null,
    created_at: '2026-11-05T10:00:00+08:00',
  }
  auth.token = 'access-token-under-test'
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
})

describe('「更多」菜单的开关（议题 3.D）', () => {
  it('默认收起：aria-expanded=false，面板不带 --open 类', async () => {
    const { wrapper } = await mountTopBar()
    const trigger = wrapper.find('.more-trigger')

    expect(trigger.exists()).toBe(true)
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(trigger.attributes('aria-controls')).toBe('user-more-menu')
    expect(wrapper.find('#user-more-menu').classes()).not.toContain('user-actions--open')
  })

  it('点「更多」展开 → aria-expanded=true 且面板打开；再点一次收起', async () => {
    const { wrapper } = await mountTopBar()
    const trigger = wrapper.find('.more-trigger')
    const panel = wrapper.find('#user-more-menu')

    await trigger.trigger('click')
    expect(trigger.attributes('aria-expanded')).toBe('true')
    expect(panel.classes()).toContain('user-actions--open')
    // 箭头跟着翻转（与简历切换器的 `.switcher-caret--open` 同一套语言）。
    expect(wrapper.find('.more-caret').classes()).toContain('more-caret--open')

    await trigger.trigger('click')
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(panel.classes()).not.toContain('user-actions--open')
    expect(wrapper.find('.more-caret').classes()).not.toContain('more-caret--open')
  })

  it('Esc 关闭「更多」菜单', async () => {
    const { wrapper } = await mountTopBar()
    await wrapper.find('.more-trigger').trigger('click')
    expect(wrapper.find('#user-more-menu').classes()).toContain('user-actions--open')

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()

    expect(wrapper.find('#user-more-menu').classes()).not.toContain('user-actions--open')
    expect(wrapper.find('.more-trigger').attributes('aria-expanded')).toBe('false')
  })

  it('点面板外关闭、点面板内保持打开（内外都以 moreRoot 为界）', async () => {
    const { wrapper } = await mountTopBar()
    await wrapper.find('.more-trigger').trigger('click')
    const panel = wrapper.find('#user-more-menu')
    expect(panel.classes()).toContain('user-actions--open')

    // 点面板内：事件冒泡到 document，但目标在 `moreRoot` 之内 → 不该关。
    panel.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(panel.classes()).toContain('user-actions--open')

    // 点面板外（body）→ 该关。
    document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(panel.classes()).not.toContain('user-actions--open')
  })

  it('两个弹层互斥：开「更多」会关简历切换器，反之亦然', async () => {
    const { wrapper } = await mountTopBar()
    const switcher = wrapper.find('.switcher-trigger')
    const more = wrapper.find('.more-trigger')

    await switcher.trigger('click')
    expect(switcher.attributes('aria-expanded')).toBe('true')

    await more.trigger('click')
    expect(switcher.attributes('aria-expanded')).toBe('false')
    expect(more.attributes('aria-expanded')).toBe('true')

    await switcher.trigger('click')
    expect(more.attributes('aria-expanded')).toBe('false')
    expect(switcher.attributes('aria-expanded')).toBe('true')
  })
})

describe('窄屏的入口组织（结构判据，不测像素）', () => {
  it('「岗位库」仍然是文字链接，而且没有被收进「更多」面板', async () => {
    const { wrapper } = await mountTopBar()
    const entry = wrapper.find('.jobs-entry')

    expect(entry.exists()).toBe(true)
    // 是 `<a>`（RouterLink 渲染成锚点）而不是按钮或图标，文案就是那三个汉字。
    expect(entry.element.tagName).toBe('A')
    expect(entry.text()).toBe('岗位库')
    expect(entry.attributes('href')).toBe('/jobs')
    // 关键判据：它不在弹层里。弹层收起时是 `display: none`，进去就等于没有入口。
    expect(entry.element.closest('.user-actions')).toBeNull()

    const panel = wrapper.find('#user-more-menu')
    expect(panel.text()).toContain('AI 设置')
    expect(panel.text()).toContain('退出登录')
    expect(panel.text()).not.toContain('岗位库')
  })

  it('.user-area 的直接子项是四个容器，两个动作按钮已让位给弹层', async () => {
    const { wrapper } = await mountTopBar()
    // RouterLink 会自带 `router-link-active` / `aria-current`，所以按「已知入口类名」取值，
    // 而不是照抄整个 class 列表。
    const KNOWN = ['jobs-entry', 'profile-switcher', 'dirty-dot', 'user-name', 'user-more']
    const topLevel = wrapper
      .findAll('.user-area > *')
      .map((node) => KNOWN.find((cls) => node.classes().includes(cls)) ?? '(未知)')

    // 窄屏靠 CSS 再隐掉 `.user-name`（`display: none`），一级项因此是 3 个：
    // 岗位库、简历切换器、更多。DOM 里 `user-name` 仍在原位，宽屏才显示。
    expect(topLevel).toEqual(['jobs-entry', 'profile-switcher', 'user-name', 'user-more'])

    // 两个动作按钮不再是 `.user-area` 的直接子项，而收在 `.user-more` 内；
    // 宽屏时 `.user-more` / `.user-actions` 都是 `display: contents`，不生成盒子，
    // 它们因此仍以原位、原 gap 回到一级项 —— 宽屏布局与改动前一致。
    expect(wrapper.find('.user-area > .ai-settings-button').exists()).toBe(false)
    expect(wrapper.find('.user-area > .logout-button').exists()).toBe(false)
    expect(wrapper.find('.user-more > .more-trigger').exists()).toBe(true)
    expect(wrapper.findAll('#user-more-menu > button')).toHaveLength(2)
  })
})

describe('登出清空岗位 store（handleLogout 的不变量）', () => {
  it('点「退出登录」后岗位 store 全空、本地会话清掉、落到登录页', async () => {
    const { wrapper, router } = await mountTopBar()
    const job = useJobStore()
    const auth = useAuthStore()
    seedJobStore()
    localStorage.setItem(TOKEN_STORAGE_KEY, 'access-token-under-test')

    expect(job.jobs).not.toEqual([])

    await wrapper.find('.logout-button').trigger('click')
    await flushPromises()

    expect(job.jobs).toEqual([])
    expect(job.total).toBe(0)
    expect(job.page).toBe(1)
    expect(job.detail).toBeNull()
    expect(job.trash).toEqual([])
    expect(job.requirementCoverage).toEqual([])
    // 筛选条件也是数据：留着会让下一个账号看到「上一个人筛过的那一屏」。
    expect(job.filter).toEqual({ keyword: '', status: 'published', includeExpired: false })

    expect(auth.token).toBeNull()
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('顺序判据：resume.reset() 与 job.reset() 都发生在 auth.signOut() 之前', async () => {
    const { wrapper } = await mountTopBar()
    const calls: string[] = []
    vi.spyOn(useResumeStore(), 'reset').mockImplementation(() => {
      calls.push('resume.reset')
    })
    vi.spyOn(useJobStore(), 'reset').mockImplementation(() => {
      calls.push('job.reset')
    })
    vi.spyOn(useAuthStore(), 'signOut').mockImplementation(async () => {
      calls.push('auth.signOut')
    })

    await wrapper.find('.logout-button').trigger('click')
    await flushPromises()

    expect(calls).toEqual(['resume.reset', 'job.reset', 'auth.signOut'])
  })

  /**
   * 这一条钉的是一个**曾经真实存在的死 UI**：按钮绑的是 `auth.signingOut` 并显示「退出中…」，
   * 而 `signingOut` 只有 `signOut()` 会置真——那时顶栏调的是 `logout()`，
   * 所以这两个状态永远不可能出现，测试也无从写起。
   *
   * 注意这里**不能** mock `signOut()` 本身：`signingOut` 正是它内部置的，
   * 把它换掉等于把要测的东西测没了（会绿，但什么都没验）。
   * 只把最外层的撤销请求挂在半空，让真实的 store 逻辑跑完。
   */
  it('撤销请求在途时按钮禁用并显示「退出中…」，落地后恢复', async () => {
    const { wrapper } = await mountTopBar()
    localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, 'refresh-token-under-test')
    // `settle` 初值是空函数：忘了调用只会让这条用例超时（响的），
    // 而不是像 `release?.()` 那样静默跳过——那会留下一条恒绿的假用例。
    let settle = (): void => undefined
    const revoke = vi.spyOn(authApi, 'logout').mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          settle = () => resolve()
        }),
    )

    await wrapper.find('.logout-button').trigger('click')
    await flushPromises()

    expect(revoke).toHaveBeenCalledTimes(1)
    expect(revoke).toHaveBeenCalledWith('refresh-token-under-test')
    const busy = wrapper.find('.logout-button')
    expect(busy.attributes('disabled')).toBeDefined()
    expect(busy.text()).toContain('退出中')

    settle()
    await flushPromises()
    expect(wrapper.find('.logout-button').text()).toContain('退出登录')
  })
})

/* ------------------------------------------------------------------ */
/*  批次 F1b：六处原生弹层里的顶栏两处，走的是同一个页面内弹窗           */
/* ------------------------------------------------------------------ */

describe('删除与去回收站走页面内弹层（批次 F1b，守则 C6）', () => {
  // 以下三个 helper 刻意写在 describe 里面而不是文件头部：
  // `docs/ROADMAP.md` §1 F3 行按行号引用了本文件里那段结构判据（`AppTopBar.spec.ts:177-214`），
  // 顶层多插十几行就会把那条引用推到空行上。写在这块作用域里，头部一格不动。
  /**
   * `resume.save()` 的返回类型是 `ProfileRead`，但这几处调用点**只看它有没有被调用**、
   * 不看返回内容（`goTrash` 成功就跳转、失败就提示）。所以给一个空对象而不是编造一份
   * 完整档案：编出来的字段会诱使后来的用例去断言它们，而它们并不存在。
   */
  const SAVE_RESULT = {} as never

  /** 弹窗 `Teleport` 到 body，所以查询走 document，而不是 wrapper（与 JobDetailView.spec 同法）。 */
  function bodyText(selector: string): string {
    return document.body.querySelector(selector)?.textContent ?? ''
  }

  async function bodyClick(selector: string): Promise<void> {
    const el = document.body.querySelector(selector)
    if (!el) throw new Error(`找不到弹窗按钮：${selector}`)
    ;(el as HTMLElement).click()
    await flushPromises()
  }

  /**
   * `ConfirmDialog` 在生产里挂在 `App.vue`，测试这里按同一个 pinia 单独挂一份。
   * 不这么做的话，本文件其实只在验 store 里那一格状态，屏幕上有没有真弹窗没人管。
   */
  async function mountDialogHost() {
    const { default: ConfirmDialog } = await import('@/components/ConfirmDialog.vue')
    const host = mount(ConfirmDialog, { global: { plugins: [pinia] } })
    mounted.push(host)
    return host
  }

  function seedProfiles() {
    const resume = useResumeStore()
    resume.profiles = [
      {
        id: 'profile-under-test',
        title: '投后端的那一版',
        is_default: true,
        full_name: '张三',
        created_at: '2026-11-10T10:00:00+08:00',
        updated_at: '2026-11-10T10:00:00+08:00',
        deleted_at: null,
      },
    ] as typeof resume.profiles
    resume.currentProfileId = 'profile-under-test'
    return resume
  }

  it('点「删除」只打开弹层，此刻一次删除请求都没发；点「确认删除」才删', async () => {
    const resume = seedProfiles()
    const deleteSpy = vi.spyOn(resume, 'deleteProfile').mockResolvedValue(undefined)
    const { wrapper } = await mountTopBar()
    await mountDialogHost()

    await wrapper.find('.switcher-trigger').trigger('click')
    await wrapper.find('.icon-button--danger').trigger('click')
    await flushPromises()

    expect(bodyText('[data-testid="app-dialog"]')).toContain('删除后它进回收站')
    expect(bodyText('[data-testid="app-dialog"]')).toContain('投后端的那一版')
    expect(deleteSpy).toHaveBeenCalledTimes(0)

    await bodyClick('[data-testid="app-dialog-cancel"]')
    expect(deleteSpy).toHaveBeenCalledTimes(0)

    // 反面对照：确认这条路真的删得掉，否则上面那句「没删」只是因为按钮根本没接上。
    await wrapper.find('.icon-button--danger').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-confirm"]')
    expect(deleteSpy).toHaveBeenCalledTimes(1)
    expect(deleteSpy).toHaveBeenCalledWith('profile-under-test')
  })

  it('有未保存修改时点「回收站」：先问，确认才保存并跳转', async () => {
    const resume = seedProfiles()
    const saveSpy = vi.spyOn(resume, 'save').mockResolvedValue(SAVE_RESULT)
    const { wrapper, router } = await mountTopBar()
    await mountDialogHost()
    resume.dirty = true

    await wrapper.find('.switcher-trigger').trigger('click')
    await wrapper.find('.menu-action--ghost').trigger('click')
    await flushPromises()

    // 弹层出现的那一瞬间：既没保存也没跳转。
    expect(bodyText('[data-testid="app-dialog"]')).toContain('上一次保存的那一版')
    expect(saveSpy).toHaveBeenCalledTimes(0)
    expect(router.currentRoute.value.name).toBe('job-board')

    await bodyClick('[data-testid="app-dialog-confirm"]')
    expect(saveSpy).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.name).toBe('recycle-bin')
  })

  it('这条路取消就不跳转；干净状态（没有未保存修改）直接跳转，不打扰用户', async () => {
    const resume = seedProfiles()
    const saveSpy = vi.spyOn(resume, 'save').mockResolvedValue(SAVE_RESULT)
    const { wrapper, router } = await mountTopBar()
    await mountDialogHost()
    resume.dirty = true

    await wrapper.find('.switcher-trigger').trigger('click')
    await wrapper.find('.menu-action--ghost').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-cancel"]')

    expect(saveSpy).toHaveBeenCalledTimes(0)
    expect(router.currentRoute.value.name).toBe('job-board')

    // 对照组：dirty 为 false 时这整个问答都不该发生。
    // （`goTrash` 一进来就收菜单，所以这里要重新打开——不是测试的仪式，是它的行为。）
    resume.dirty = false
    await wrapper.find('.switcher-trigger').trigger('click')
    await wrapper.find('.menu-action--ghost').trigger('click')
    await flushPromises()
    expect(bodyText('[data-testid="app-dialog"]')).toBe('')
    expect(router.currentRoute.value.name).toBe('recycle-bin')
  })

  it('保存失败时不再弹原生 alert，而是当面说明并停在原页', async () => {
    const resume = seedProfiles()
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => undefined)
    const { ApiError } = await import('@/api/client')
    vi.spyOn(resume, 'save').mockRejectedValue(
      new ApiError('保存被拒绝了。', 422, 'VALIDATION_ERROR'),
    )
    const { wrapper, router } = await mountTopBar()
    await mountDialogHost()
    resume.dirty = true

    await wrapper.find('.switcher-trigger').trigger('click')
    await wrapper.find('.menu-action--ghost').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-confirm"]')

    expect(alertSpy).toHaveBeenCalledTimes(0)
    expect(bodyText('[data-testid="app-dialog"]')).toContain('没保存成功，所以停在这一页')
    expect(bodyText('[data-testid="app-dialog"]')).toContain('保存被拒绝了。')
    expect(router.currentRoute.value.name).toBe('job-board')

    await bodyClick('[data-testid="app-dialog-confirm"]')
    alertSpy.mockRestore()
  })

  it('`window.confirm` 一次都没被调用（防的是「将来有人觉得原生省事又改回去」）', async () => {
    const resume = seedProfiles()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(resume, 'deleteProfile').mockResolvedValue(undefined)
    vi.spyOn(resume, 'save').mockResolvedValue(SAVE_RESULT)
    const { wrapper } = await mountTopBar()
    await mountDialogHost()

    await wrapper.find('.switcher-trigger').trigger('click')
    await wrapper.find('.icon-button--danger').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-confirm"]')

    expect(confirmSpy).toHaveBeenCalledTimes(0)
    confirmSpy.mockRestore()
  })
})
