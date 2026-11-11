/**
 * 顶栏交互单测（B3 · ADR-011 议题 3.D，批次 F3 第二片改为右侧用户抽屉）。
 *
 * 只测**结构与状态**，不测像素：jsdom 没有布局引擎，scoped 样式与
 * `@media (max-width: 760px)` 在这里根本不生效，所以本文件断言的是
 * 「谁在抽屉里、谁在一级项上、`aria-expanded` 有没有跟着开合」这类 DOM/属性判据。
 * 375px 下顶栏是否真的回到单行，要靠真实视口量（数值附在 PR）。
 *
 * 三组用例对应监督者点名的验收项（第一组在 F3 第二片按裁定改写：`ROADMAP.md` §1 F3 行
 * 那句「三段断言显式改写，不许红着过去」说的就是这里）：
 * 1. 右侧用户抽屉的开关（含 `aria-expanded`、Esc、点外关闭、点触发按钮算开合）；
 * 2. 窄屏下「岗位库」仍然是文字链接、且没有被收进抽屉；
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
import { settled } from '@/__tests__/setup'

/** 空组件：本地路由的占位目标，测试只关心 `currentRoute.name`。 */
const BLANK = { template: '<div />' }

/** 挂载出来的实例统一在这里收口，避免组件留在 `document` 上的监听器串到下一个用例。 */
const mounted: { unmount: () => void }[] = []

let pinia: Pinia

function makeRouter(): Router {
  const routes: RouteRecordRaw[] = [
    // 工作区切换器会 `router.resolve()` **模块表里的每一项**（它靠真实路径判断当前工作区），
    // 所以这份替身必须覆盖整张表：少一条它当场抛「No match」，而不是静默少一张卡片。
    { path: '/', name: 'resume-editor', component: BLANK },
    { path: '/jobs', name: 'job-board', component: BLANK },
    { path: '/trash', name: 'recycle-bin', component: BLANK },
    { path: '/login', name: 'login', component: BLANK },
  ]
  return createRouter({ history: createMemoryHistory(), routes })
}

/**
 * 顶栏挂到 `document` 上的那些监听（基座的 pointerdown / keydown、顶栏自己管「新建简历」
 * 内联表单的 keydown）要求组件真的挂进 `document.body`，否则从浮层元素上冒泡的事件到不了
 * `document`，「点外关闭」那条就会假绿。
 */
async function mountTopBar(props: { resumesOpen?: boolean; showResumesTrigger?: boolean } = {}) {
  const router = makeRouter()
  await router.push('/jobs')
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(AppTopBar, {
    attachTo,
    // 批次 F3 第三片起这两个 prop 由外框给：窄屏才有简历触发按钮，开合状态在外框手里。
    // 默认按窄屏摆，因为 jsdom 没有 matchMedia，`useViewport()` 在生产里的默认答案也是「不宽」。
    props: { resumesOpen: false, showResumesTrigger: true, ...props },
    global: { plugins: [pinia, router] },
  })
  mounted.push(wrapper)
  return { wrapper, router }
}

/** 从 `mountTopBar()` 里取 wrapper 的类型，免得在下面把泛型抄一遍。 */
type TopBarWrapper = Awaited<ReturnType<typeof mountTopBar>>['wrapper']

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

/**
 * 点「退出登录」在用户是两步：先开右侧抽屉，再点里面那一行。
 * 抽屉是 `v-if`，不先开就找不到那一行——这正是清单 4 改出来的新结构，
 * 所以这两步写在这里而不是各用例里抄四遍。
 */
async function openDrawerAndLogout(wrapper: TopBarWrapper): Promise<void> {
  await wrapper.find('.account-trigger').trigger('click')
  ;(document.body.querySelector('.drawer-item--danger') as HTMLElement).click()
}

/*
 * 抽屉 `Teleport` 到 `document.body`（顶栏的 `backdrop-filter` 会成为 fixed 后代的包含块，
 * 留在顶栏里抽屉只有顶栏那一条高，两档实测量到过），所以查它必须走 `document`：
 * `wrapper.find()` 对 Teleport 出去的节点**永远返回空**，用它断言「关掉之后查不到」是恒绿假用例。
 */
const drawerEl = (): Element | null => document.body.querySelector('#user-drawer')
const drawerRootEl = (): Element | null => document.body.querySelector('.drawer-root')
const drawerText = (): string => drawerEl()?.textContent?.replace(/\s+/g, ' ') ?? ''

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  sessionStorage.clear()
  document.body.innerHTML = ''
  // 顶栏默认按「已登录」渲染：`account-name` 读 displayName，登出用例读 token。
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

describe('右侧用户抽屉的开关（清单 4，批次 F3 第二片）', () => {
  it('默认收起：抽屉整个不在 DOM 里，`aria-expanded=false` 且不指向不存在的 id', async () => {
    const { wrapper } = await mountTopBar()
    const trigger = wrapper.find('.account-trigger')

    expect(trigger.exists()).toBe(true)
    expect(trigger.attributes('aria-expanded')).toBe('false')
    // 原来是恒挂载 + 靠 CSS 类收起，`aria-controls` 因此可以写死。
    // 抽屉改成 `v-if` 后，收起态指向一个不存在的 id 属于假声明，所以这里断言它**没有**。
    expect(trigger.attributes('aria-controls')).toBeUndefined()
    expect(drawerRootEl()).toBeNull()
  })

  it('点「账户」展开 → `aria-expanded=true`、`aria-controls` 指到真实抽屉、箭头翻转；再点一次收起', async () => {
    const { wrapper } = await mountTopBar()
    const trigger = wrapper.find('.account-trigger')

    await trigger.trigger('click')
    expect(trigger.attributes('aria-expanded')).toBe('true')
    expect(trigger.attributes('aria-controls')).toBe('user-drawer')
    // `aria-controls` 指过去必须真有其人，否则这个属性只是好看。
    const drawer = drawerEl()
    expect(drawer).not.toBeNull()
    expect(drawerText()).toContain('AI 设置')
    expect(drawerText()).toContain('退出登录')
    expect(wrapper.find('.account-caret').classes()).toContain('account-caret--open')

    await trigger.trigger('click')
    // 退出有淡出（批次 F4）：等它收尾再判「不在了」，否则量到的是正在退场的那一个。
    await settled()
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(drawerEl()).toBeNull()
    expect(wrapper.find('.account-caret').classes()).not.toContain('account-caret--open')
  })

  it('Esc 关闭抽屉：这条由 `OverlayPanel` 基座负责，顶栏没有为它再加一个 keydown 分支', async () => {
    const { wrapper } = await mountTopBar()
    await wrapper.find('.account-trigger').trigger('click')
    expect(drawerEl()).not.toBeNull()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    await settled()

    expect(drawerEl()).toBeNull()
    expect(wrapper.find('.account-trigger').attributes('aria-expanded')).toBe('false')
  })

  it('点遮罩与点抽屉外都关闭；点抽屉内的两行不关闭', async () => {
    const { wrapper } = await mountTopBar()
    await wrapper.find('.account-trigger').trigger('click')
    const drawer = drawerEl()
    expect(drawer).not.toBeNull()

    // 抽屉里：pointerdown 命中面板自身 → 基座判为「里面」，不该关。
    drawer?.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(drawerEl()).not.toBeNull()

    // 页面别处：顶栏之外算「外面」→ 该关。
    document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    await settled()
    expect(drawerEl()).toBeNull()

    // 遮罩：重开一次点遮罩，这是手机用户最常用的关闭手势。
    // 遮罩是面板的**兄弟**而不是子节点，所以基座把它算成「外面」——抽屉自己没写 click。
    await wrapper.find('.account-trigger').trigger('click')
    expect(drawerEl()).not.toBeNull()
    ;(document.body.querySelector('.drawer-mask') as Element).dispatchEvent(
      new MouseEvent('pointerdown', { bubbles: true }),
    )
    await flushPromises()
    await settled()
    expect(drawerEl()).toBeNull()
  })

  it('点触发按钮自己算「开合」：基座按 aria-controls 认出它，所以不会先关再开', async () => {
    const { wrapper } = await mountTopBar()
    const trigger = wrapper.find('.account-trigger')

    await trigger.trigger('click')
    expect(drawerEl()).not.toBeNull()

    // 少了基座里那句「`aria-controls` 指向我的按钮不算外面」，这一步会判为「点外面」先关，
    // 下一步 click 再被 toggle 打开——「再点一次收起」在真机上永远失效。
    // jsdom 的 `trigger('click')` 不带 pointerdown，所以这条要手工按真实顺序派发；
    // 这正是这个 bug 从旧的手写监听到现在一直没被测出来的原因。
    trigger.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(drawerEl()).not.toBeNull()

    await trigger.trigger('click')
    await settled()
    expect(drawerEl()).toBeNull()
  })

})

describe('简历入口（清单第 5 条：列表搬去左栏 / 抽屉，顶栏只留触发按钮）', () => {
  /** 摆一份当前简历，好让触发按钮里那句「我在改哪一份」有真东西可显示。 */
  function seedCurrent(): void {
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
  }

  it('窄屏渲染触发按钮并带当前简历名；宽屏整个不渲染（左栏常驻，不需要按钮）', async () => {
    seedCurrent()
    const narrow = await mountTopBar({ showResumesTrigger: true })
    const trigger = narrow.wrapper.find('.resumes-trigger')

    expect(trigger.exists()).toBe(true)
    expect(trigger.find('.resumes-trigger__label').text()).toBe('简历')
    expect(trigger.find('.resumes-trigger__title').text()).toBe('投后端的那一版')
    expect(trigger.attributes('title')).toBe('当前简历：投后端的那一版')

    const wide = await mountTopBar({ showResumesTrigger: false })
    // 不是 `display: none` 藏起来，是根本不渲染：宽屏 Tab 不该停在一个没有作用的按钮上。
    expect(wide.wrapper.find('.resumes-trigger').exists()).toBe(false)
  })

  it('点它只发 toggle-resumes 意图；开合状态与 aria 都跟着外框给的 prop 走', async () => {
    const closed = await mountTopBar({ resumesOpen: false })
    const trigger = closed.wrapper.find('.resumes-trigger')

    expect(trigger.attributes('aria-expanded')).toBe('false')
    // 收起态抽屉不在 DOM 里，指一个不存在的 id 是假声明。
    expect(trigger.attributes('aria-controls')).toBeUndefined()
    await trigger.trigger('click')
    expect(closed.wrapper.emitted('toggle-resumes')).toHaveLength(1)
    // 顶栏自己不持有开合：prop 没变，按钮就还是收起态（真正开抽屉的是外框）。
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(document.body.querySelector('#resume-drawer')).toBeNull()

    const open = await mountTopBar({ resumesOpen: true })
    const openTrigger = open.wrapper.find('.resumes-trigger')
    expect(openTrigger.attributes('aria-expanded')).toBe('true')
    expect(openTrigger.attributes('aria-controls')).toBe('resume-drawer')
    expect(openTrigger.find('.resumes-trigger__caret').classes()).toContain(
      'resumes-trigger__caret--open',
    )
  })

  it('触发按钮不吞掉 pointerdown：别的浮层要看得见这一下，两个抽屉才不会叠在一起', async () => {
    const { wrapper } = await mountTopBar()
    const seen: Event[] = []
    const onDocument = (event: Event): void => {
      seen.push(event)
    }
    document.addEventListener('pointerdown', onDocument)
    try {
      wrapper.find('.resumes-trigger').element.dispatchEvent(
        new MouseEvent('pointerdown', { bubbles: true }),
      )
      await flushPromises()
      // 这里**不**写 `@pointerdown.stop`：吞掉它只能豁免自己，账户抽屉就看不见这一下，
      // 于是「开简历抽屉时账户抽屉还开着」。豁免由基座按 `aria-controls` 精确到面板来做。
      expect(seen).toHaveLength(1)

      await wrapper.find('.resumes-trigger').trigger('click')
      expect(wrapper.emitted('toggle-resumes')).toHaveLength(1)
    } finally {
      document.removeEventListener('pointerdown', onDocument)
    }
  })
})

describe('窄屏的入口组织（结构判据，不测像素）', () => {
  it('「岗位库」仍然是文字链接，而且没有被收进抽屉', async () => {
    const { wrapper } = await mountTopBar()
    const entry = wrapper.find('.jobs-entry')

    expect(entry.exists()).toBe(true)
    // 是 `<a>`（RouterLink 渲染成锚点）而不是按钮或图标，文案就是那三个汉字。
    expect(entry.element.tagName).toBe('A')
    expect(entry.text()).toBe('岗位库')
    expect(entry.attributes('href')).toBe('/jobs')
    // 关键判据：展开抽屉后它也不在里面。收进抽屉的入口在手机上等于没有入口。
    await wrapper.find('.account-trigger').trigger('click')
    const drawer = drawerEl()
    expect(drawer).not.toBeNull()
    expect(drawer?.contains(entry.element)).toBe(false)
    expect(entry.element.closest('.drawer-root')).toBeNull()
    expect(drawerText()).toContain('AI 设置')
    expect(drawerText()).toContain('退出登录')
    expect(drawerText()).not.toContain('岗位库')
  })

  it('.user-area 的一级项：窄屏三个（岗位库 / 简历 / 账户），宽屏两个（左栏常驻，不需要简历按钮）', async () => {
    // RouterLink 会自带 `router-link-active` / `aria-current`，所以按「已知入口类名」取值，
    // 而不是照抄整个 class 列表。
    const KNOWN = ['jobs-entry', 'resumes-trigger', 'dirty-dot', 'account-trigger']
    const items = (wrapper: TopBarWrapper): string[] =>
      wrapper
        .findAll('.user-area > *')
        .map((node) => KNOWN.find((cls) => node.classes().includes(cls)) ?? '(未知)')

    const narrow = await mountTopBar({ showResumesTrigger: true })
    // 清单第 5 条之前这里是「岗位库 / 简历切换器 / 账户」，切换器自己带着一整个 Popover；
    // 现在那一格只剩一个触发按钮，列表本体在左栏或抽屉里（`ResumeList.vue` 一份）。
    expect(items(narrow.wrapper)).toEqual(['jobs-entry', 'resumes-trigger', 'account-trigger'])
    // 账户按钮里两个标签恒在 DOM，显示谁由 CSS 决定（宽屏名字、窄屏「账户」）。
    expect(narrow.wrapper.find('.account-trigger .account-name').text()).toBe('tester')
    expect(narrow.wrapper.find('.account-trigger .account-word').text()).toBe('账户')

    const wide = await mountTopBar({ showResumesTrigger: false })
    expect(items(wide.wrapper)).toEqual(['jobs-entry', 'account-trigger'])

    // 收起态抽屉整个不在 DOM 里。这里不再对 `.ai-settings-button` / `.logout-button`
    // 断言「不是直接子项」——那两个类已不存在，对不存在的类名断言 false 是恒绿空检。
    expect(drawerRootEl()).toBeNull()

    await narrow.wrapper.find('.account-trigger').trigger('click')
    // 展开后一级项不变：抽屉 Teleport 到 body，不占顶栏的一格。
    expect(items(narrow.wrapper)).toEqual(['jobs-entry', 'resumes-trigger', 'account-trigger'])
    expect(drawerRootEl()?.parentElement).toBe(document.body)
    expect(document.querySelectorAll('#user-drawer .drawer-item')).toHaveLength(2)

    // 两个动作在整页里**只有一处**：抽屉里那两行。这一条防的是半迁移——
    // 新抽屉建好却忘了删掉旧的一级按钮，用户就会看到两个「退出登录」。
    // 查询走 document：抽屉 Teleport 出去了，`wrapper.findAll('button')` 看不见它（会恒 0）。
    const labels = [...document.querySelectorAll('button')].map(
      (node) => node.textContent?.trim() ?? '',
    )
    expect(labels.filter((text) => text === 'AI 设置')).toHaveLength(1)
    expect(labels.filter((text) => text === '退出登录')).toHaveLength(1)
  })

  it('品牌只留产品名一行：副标题已按裁定删掉，不是藏起来（§6 第 9 条）', async () => {
    const { wrapper } = await mountTopBar()

    // 原来那行写死「简历工作台」，站在 /jobs 上就是事实错误——右边的切换器已经
    // 准确回答了「我在哪」。裁定是**去掉**，所以判据是 DOM 里没有这一行，
    // 而不是 `display: none`（jsdom 不跑 CSS，藏起来在这里量不出来）。
    expect(wrapper.find('.brand-sub').exists()).toBe(false)
    expect(wrapper.find('.brand-text').text()).toBe('职业发展助手')
    expect(wrapper.findAll('.brand-text > *')).toHaveLength(1)
  })

  it('工作区切换器在 Logo 右侧那一组里，不占 .user-area 的格子（清单第 6 条）', async () => {
    const { wrapper } = await mountTopBar()

    // 顶栏仍是「左一组 / 右一组」两个直接子项：切换器并进左边那一组，
    // 而不是变成第三个 flex 子项（那样 space-between 会把它甩到正中间）。
    expect(wrapper.findAll('.app-topbar > *').map((node) => node.classes()[0])).toEqual([
      'brand-side',
      'user-area',
    ])
    expect(wrapper.findAll('.brand-side > *').map((node) => node.classes()[0])).toEqual([
      'brand',
      'workspace-switcher',
    ])
    expect(wrapper.find('.user-area .workspace-switcher').exists()).toBe(false)
    // 它在品牌**之后**：清单第 6 条说的是「Logo 右侧」，顺序反了就是另一个设计。
    const side = wrapper.find('.brand-side').element
    expect(side.children[0]?.classList.contains('brand')).toBe(true)
    expect(side.children[1]?.classList.contains('workspace-switcher')).toBe(true)
  })

  it('三个浮层两两互斥：开账户抽屉时切换器收起，开切换器时账户抽屉收起', async () => {
    const { wrapper } = await mountTopBar()
    const press = async (selector: string): Promise<void> => {
      const node = wrapper.find(selector)
      node.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
      await node.trigger('click')
      await flushPromises()
      await settled()
    }

    await press('.workspace-trigger')
    expect(wrapper.find('.workspace-popover').exists()).toBe(true)

    await press('.account-trigger')
    expect(drawerEl()).not.toBeNull()
    expect(wrapper.find('.workspace-popover').exists()).toBe(false)

    await press('.workspace-trigger')
    expect(wrapper.find('.workspace-popover').exists()).toBe(true)
    expect(drawerEl()).toBeNull()
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

    await openDrawerAndLogout(wrapper)
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

    await openDrawerAndLogout(wrapper)
    await flushPromises()

    expect(calls).toEqual(['resume.reset', 'job.reset', 'auth.signOut'])
  })

  /**
   * 这一条钉的是一个**曾经真实存在的死 UI**：退出那一行绑的是 `auth.signingOut` 并显示「退出中…」，
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

    await openDrawerAndLogout(wrapper)
    await flushPromises()

    expect(revoke).toHaveBeenCalledTimes(1)
    expect(revoke).toHaveBeenCalledWith('refresh-token-under-test')
    const busy = document.body.querySelector('.drawer-item--danger')
    expect(busy?.hasAttribute('disabled')).toBe(true)
    expect(busy?.textContent).toContain('退出中')

    settle()
    await flushPromises()
    expect(document.body.querySelector('.drawer-item--danger')?.textContent).toContain('退出登录')
  })
})

/* ------------------------------------------------------------------ */
/*  批次 F1b 那五条（删除 / 去回收站走页面内弹窗）已整段搬到                */
/*  `ResumeList.spec.ts`：清单第 5 条把列表搬出顶栏之后，那些行为的主语      */
/*  不再是顶栏。断言逐条保留，只去掉「先展开 Popover」那一步仪式。           */
/* ------------------------------------------------------------------ */
