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
  job.mySkillNames = [' Python ']
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
    expect(job.mySkillNames).toEqual([])
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
