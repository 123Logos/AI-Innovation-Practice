/**
 * 外框收敛后的行为单测（批次 F2 · 守则 L1）。
 *
 * 这一批改的是「谁摆顶栏」，所以它的红灯必须落在**结构与同屏性**上：
 * 之前 6 个视图各自摆一次 `<AppTopBar />`，视图直接 mount 就能看见顶栏；
 * 现在外框搬到 `AppLayout`，如果路由忘了嵌套，视图单测**照样全绿**——
 * 因为它们只验内容区，而页面少了一条顶栏在 jsdom 里不会让任何断言变红。
 * 「空检比红更危险」（守则 R1）说的就是这件事，所以本文件走真实路由、真实 `App.vue`：
 *
 * 1. 走 `/jobs`：一棵树里同时出现**恰好一个** `.app-topbar` 与岗位列表内容区，
 *    且顶栏那条「录入岗位」主操作仍在屏幕上（DOM 层面的在场与可点，不含像素）；
 * 2. 走 `/`（简历工作台）：外框与三列工作区同屏，纸面区没有被外框搬走时挤掉；
 * 3. 走 `/login`：访客页**没有**外框——布局是嵌套出来的，不是全局糊上去的。
 *
 * 三条 jsdom 前提，都是既有先例，不是本文件新造的糊法：
 * - `ResizeObserver` 桩（`ResumePreview.spec.ts` 同一份理由：jsdom 没有这个类，
 *   而纸面组件在 `onMounted` 里 new 它，不桩就抛）；
 * - axios `defaults.adapter` 换成内存小服务器（真实 MySQL / 真实 DeepSeek 不在射程内）；
 * - 真实 `@/router`：要验的就是**那张嵌套表本身**，自造 memory router 等于把被测对象换掉。
 *   守卫要求登录，所以先把 `auth` store 摆成「已登录且 `/auth/me` 无需再问」的样子
 *   （`ensureUser()` 见到内存里有 user 就直接返回 true，不发请求）。
 *
 * 375px / 1280px 两档的真实视口实测**不在本文件能力范围内**（jsdom 不跑媒体查询，
 * 见 `AppTopBar.spec.ts` 文件头同一句话），交付说明里已把它列为未做项。
 */

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '@/App.vue'
import { http } from '@/api/client'
import type { ApiErrorBody } from '@/api/types'
import router from '@/router'
import { TOP_BAR_MODULES } from '@/config/modules'
import { useAuthStore } from '@/stores/auth'
import { settled } from '@/__tests__/setup'

/* --------------------------- jsdom 缺的那一样 --------------------------- */

/** 什么都不量：本文件断言的是外框结构，纸面尺寸归 `ResumePreview.spec.ts` 管。 */
class StubResizeObserver {
  constructor(_callback: unknown) {}

  observe(): void {}

  unobserve(): void {}

  disconnect(): void {}
}

;(globalThis as unknown as Record<string, unknown>).ResizeObserver = StubResizeObserver

/* ------------------------- jsdom 缺的第二样：宽度 ------------------------- */

/**
 * `useViewport()` 是全站唯一发宽度 `matchMedia` 的地方（守则 L2），而 jsdom 没有这个函数。
 * 桩必须在**第一次 mount 之前**装好：那个 composable 把每条查询的 Ref 缓存在模块级，
 * 装晚了缓存里已经是「不宽」，之后再换 `window.matchMedia` 也不会重算。
 * 所以连 change 监听一起桩，`setViewportWidth()` 通过监听把新答案推进去。
 *
 * **按宽度求值而不是按「是不是 min-width」**（2026-11-11 显式改写，守则 R1）：
 * 表里现在有两档「至少」——`wide: 1100`（宽屏形状）与 `sidebar: 1440`（左栏常驻）。
 * 旧桩把任何 min-width 都答成同一个布尔，于是 1100–1439 这段**根本表达不出来**，
 * 而那段恰恰是本次裁定改的东西：左栏不常驻、列表走抽屉。
 */
type WidthListener = (event: { matches: boolean }) => void
let viewportWidth = 375
const widthListeners = new Map<string, Set<WidthListener>>()

function matchesQuery(query: string): boolean {
  const min = /min-width:\s*(\d+)px/.exec(query)
  if (min) return viewportWidth >= Number(min[1])
  const max = /max-width:\s*(\d+)px/.exec(query)
  if (max) return viewportWidth <= Number(max[1])
  return false
}

window.matchMedia = ((query: string) => {
  return {
    media: query,
    matches: matchesQuery(query),
    addEventListener: (_type: string, listener: WidthListener) => {
      const set = widthListeners.get(query) ?? new Set<WidthListener>()
      set.add(listener)
      widthListeners.set(query, set)
    },
    removeEventListener: (_type: string, listener: WidthListener) => {
      widthListeners.get(query)?.delete(listener)
    },
  }
}) as unknown as typeof window.matchMedia

function setViewportWidth(px: number): void {
  viewportWidth = px
  for (const [query, listeners] of widthListeners) {
    const matches = matchesQuery(query)
    for (const listener of listeners) listener({ matches })
  }
}

/* ----------------------------- 内存小服务器 ----------------------------- */

/** 发出去的请求，用来证明「页面确实自己拉过数据」，而不是靠异常分支渲染出空壳。 */
let requested: string[] = []
let savedAdapter: typeof http.defaults.adapter

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

function notStubbed(config: InternalAxiosRequestConfig): AxiosError {
  const body = {
    error: {
      code: 'CLIENT_ERROR',
      message: `未桩住的请求：${config.method?.toUpperCase()} ${config.url}`,
      details: {},
      retryable: false,
    },
    meta: { request_id: 'req-app-layout-test' },
  } as unknown as ApiErrorBody
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, envelope(config, body, 404))
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  requested.push(`${method} ${url}`)

  // 空列表 = 「这个账号还没有任何简历 / 岗位」，是首屏最常见的真实形状。
  if (url === '/profiles') {
    return Promise.resolve(
      envelope(config, { data: { profiles: [] }, meta: { request_id: 'req-app-layout-test' } }),
    )
  }
  if (url === '/jobs') {
    return Promise.resolve(
      envelope(
        config,
        {
          data: { jobs: [] },
          meta: { request_id: 'req-app-layout-test', page: 1, page_size: 20, total: 0 },
        },
      ),
    )
  }
  return Promise.reject(notStubbed(config))
}

/* --------------------------------- 挂载 --------------------------------- */

const mounted: VueWrapper[] = []

async function visit(path: string): Promise<VueWrapper> {
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(App, {
    attachTo,
    global: {
      plugins: [pinia, router],
      // 本文件只装 pinia + router，不装 Element Plus：`StylePanel` 的两个弹窗用到它。
      // 桩掉这几个标签，是为了让输出干净——不然 Vue 每次渲染都刷一屏
      // 「Failed to resolve component: el-dialog」，真出错时那份噪音会把要点盖住。
      // 弹窗本身归批次 F1b 与 `StylePanel` 自己的用例管，不在外框的射程内。
      stubs: {
        'el-button': true,
        'el-dialog': true,
        'el-icon': true,
        'el-input-number': true,
        'el-progress': true,
        'el-radio': true,
        'el-radio-group': true,
      },
    },
  })
  mounted.push(wrapper)
  await router.push(path)
  await flushPromises()
  // 页面切换是 out-in 的淡出淡入（批次 F4）：等它收尾，否则量到的是正在退场的上一页。
  await settled()
  return wrapper
}

let pinia: Pinia

beforeEach(async () => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  sessionStorage.clear()
  document.body.innerHTML = ''
  requested = []
  // 每条用例都从 375 起步：jsdom 没有真实视口，要宽屏的用例自己 setViewportWidth()。
  setViewportWidth(375)
  savedAdapter = http.defaults.adapter
  http.defaults.adapter = fakeAdapter

  const auth = useAuthStore()
  auth.token = 'access-token-under-test'
  auth.user = {
    id: 'user-under-test',
    username: 'tester',
    email: null,
    phone: null,
    created_at: '2026-11-10T10:00:00+08:00',
  }

  // vue-router 的首次导航只在 install 时补一次，会把本条用例自己 push 的目标顶掉
  // （同一处理见 `LoginView.spec.ts` 的 beforeEach）。
  const bootstrap = mount({ render: () => null }, { global: { plugins: [pinia, router] } })
  await router.isReady()
  bootstrap.unmount()
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
  http.defaults.adapter = savedAdapter
})

describe('外框只有一处定义（守则 L1）', () => {
  it('/jobs 的一棵树里恰好一个顶栏，且岗位列表内容区与它同屏', async () => {
    const wrapper = await visit('/jobs')

    expect(wrapper.findAll('.app-topbar')).toHaveLength(1)
    // 顶栏与内容区仍同屏，但中间多了外框的两层：`.app-body`（左栏 + 内容）与 `.app-content`
    // （容器查询的参照）。批次 F3 第三片加了常驻左栏，嵌套因此从两层变四层——
    // 这条断言跟着显式改写，验的还是同一件事：嵌套没漏层、顶栏没被套进内容里。
    expect(wrapper.find('.app-shell > .app-topbar').exists()).toBe(true)
    expect(wrapper.find('.app-shell > .app-body > .app-content > main.board-page').exists()).toBe(true)
    // 视图确实自己拉过数据（不是靠异常分支渲染出的一具空壳）。
    expect(requested).toContain('get /jobs')
  })

  it('页面主操作（「录入岗位」）在收敛后仍然在场、仍然带 href', async () => {
    const wrapper = await visit('/jobs')
    const primary = wrapper.find('.primary-link')

    expect(primary.exists()).toBe(true)
    expect(primary.text()).toBe('录入岗位')
    expect(primary.attributes('href')).toBe('/jobs/new')
  })

  it('简历工作台：外框、三列工作区与纸面列同屏，高度依赖没有被搬走', async () => {
    // 这一页的淡入必须真的生效：`<Transition>` 认不了多根，多根时它**静默不动画**
    // 并每次报一条警告。判据取那个真实症状，而不是数模板里有几个标签。
    const warnings: string[] = []
    vi.spyOn(console, 'warn').mockImplementation((...args: unknown[]) => {
      warnings.push(args.map(String).join(' '))
    })
    const wrapper = await visit('/')

    expect(wrapper.findAll('.app-topbar')).toHaveLength(1)
    // 批次 F6 显式改写（R1）：这一页原来是四个并列根，`<Transition>` 认不了多根，
    // 淡入静默失效还每次报一条 Vue 警告。收进单根 `<main class="editor-page">` 之后
    // 嵌套多了一层——验的还是同一件事：外框换层没漏层、工作区没退化成空壳。
    expect(
      wrapper
        .find('.app-shell > .app-body > .app-content > main.editor-page > .resume-workspace')
        .exists(),
    ).toBe(true)
    // `.editor` / `.style-panel` 是 `ResumeEditorView` 打给子组件的类：
    // 它们在，就说明外框换层之后工作区还是三列那一棵，没有退化成空 `<main>`。
    expect(wrapper.find('.resume-workspace > .editor').exists()).toBe(true)
    expect(wrapper.find('.resume-workspace > .style-panel').exists()).toBe(true)
    expect(requested).toContain('get /profiles')
    expect(warnings.filter((line) => line.includes('non-element root node'))).toEqual([])
  })

  it('访客页不带外框：布局是路由嵌套出来的，不是全局糊上去的', async () => {
    // 必须先清会话：`/login` 带 `meta.guestOnly`，已登录会被守卫直接顶回简历工作台
    // （那样本用例测到的其实是「登录页根本没出现」——同一理由见 `LoginView.spec.ts` 的 guestOnly 组）。
    const auth = useAuthStore()
    auth.token = null
    auth.user = null

    const wrapper = await visit('/login')

    expect(wrapper.find('.app-shell').exists()).toBe(false)
    expect(wrapper.findAll('.app-topbar')).toHaveLength(0)
    expect(wrapper.find('form').exists()).toBe(true)
  })
})

describe('顶栏的一级导航来自模块表（守则 L4）', () => {
  it('顶栏渲染出「岗位库」文字入口，href 指向 /jobs，且不在账户抽屉里', async () => {
    const wrapper = await visit('/jobs')
    const entries = wrapper.findAll('.app-shell > .app-topbar .jobs-entry')

    // 期望值从模块表推导：加一条 `inTopBar` 模块时改的是表，不是这里。
    // （原来这里写死「只有一条」，B5 加进「组织管理」时它红了一次——红得对，
    //  但正确的修法是把判据改成跟表走，而不是再加一个数字。）
    expect(entries.map((entry) => entry.text())).toEqual(
      TOP_BAR_MODULES.map((module) => module.name),
    )
    expect(entries.every((entry) => entry.element.tagName === 'A')).toBe(true)
    expect(entries[0]?.attributes('href')).toBe('/jobs')
    // 判据要真的把抽屉打开再看：它 Teleport 到 body 且收起态根本不存在，
    // 不收时 `closest` 必然为 null，那是空检。
    await wrapper.find('.account-trigger').trigger('click')
    expect(document.body.querySelector('.drawer-root')).not.toBeNull()
    expect(entries[0]?.element.closest('.drawer-root')).toBeNull()
  })
})

describe('简历列表住哪（清单第 5 条：宽屏常驻左栏 / 其余宽度折叠成抽屉）', () => {
  const bodyEl = (selector: string): Element | null => document.body.querySelector(selector)

  it('窄屏：不渲染左栏，顶栏给一个触发按钮，点开是满高抽屉里的同一份列表', async () => {
    const wrapper = await visit('/jobs')

    expect(wrapper.find('.resume-sidebar').exists()).toBe(false)
    const trigger = wrapper.find('.resumes-trigger')
    expect(trigger.exists()).toBe(true)
    expect(bodyEl('#resume-drawer')).toBeNull()

    await trigger.trigger('click')
    const drawer = bodyEl('#resume-drawer')
    expect(drawer).not.toBeNull()
    // 抽屉里就是 `ResumeList` 那一棵：排序条与底部两个行动都在，不是另写的一份。
    expect(drawer?.querySelector('.menu-sort')).not.toBeNull()
    expect(drawer?.querySelectorAll('.menu-action')).toHaveLength(2)
    expect(drawer?.closest('body')).toBe(document.body)
    expect(trigger.attributes('aria-expanded')).toBe('true')

    // 再点一次收起：基座按 `aria-controls` 认出这是它自己的触发器，不会先关再开。
    trigger.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await trigger.trigger('click')
    await settled()
    expect(bodyEl('#resume-drawer')).toBeNull()
    expect(trigger.attributes('aria-expanded')).toBe('false')

    // Esc 走基座那一条路径（抽屉自己没写 keydown）。
    await trigger.trigger('click')
    expect(bodyEl('#resume-drawer')).not.toBeNull()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    await settled()
    expect(bodyEl('#resume-drawer')).toBeNull()
  })

  it('≥1440：左栏常驻在内容区左边，触发按钮整个不渲染', async () => {
    setViewportWidth(1440)
    const wrapper = await visit('/jobs')

    const sidebar = wrapper.find('.app-body > .resume-sidebar')
    expect(sidebar.exists()).toBe(true)
    expect(sidebar.attributes('aria-label')).toBe('我的简历')
    // 列表在左栏里，且左栏与内容区是兄弟（不是把内容区套进左栏）。
    expect(sidebar.find('.menu-sort').exists()).toBe(true)
    expect(wrapper.find('.app-body > .app-content > main.board-page').exists()).toBe(true)
    expect(wrapper.find('.resumes-trigger').exists()).toBe(false)
    // 常驻的意思是「不用点一下才出现」：没有任何抽屉被打开。
    expect(bodyEl('#resume-drawer')).toBeNull()
  })

  it('1100–1439：左栏不常驻，列表改走同一个抽屉（裁定：左栏一登场纸面就缩水）', async () => {
    // 这一档是 2026-11-11 裁定新划出来的：1280 的笔记本原来会被 240px 左栏把纸面
    // 从 794px 挤到 645px。左栏退场，但列表不能跟着消失——它走下面那个抽屉。
    setViewportWidth(1280)
    const wrapper = await visit('/jobs')

    expect(wrapper.find('.resume-sidebar').exists()).toBe(false)
    const trigger = wrapper.find('.resumes-trigger')
    expect(trigger.exists()).toBe(true)

    await trigger.trigger('click')
    const drawer = bodyEl('#resume-drawer')
    expect(drawer).not.toBeNull()
    expect(drawer?.querySelector('.menu-sort')).not.toBeNull()
    expect(drawer?.querySelectorAll('.menu-action')).toHaveLength(2)
  })

  it('两个抽屉互斥：开账户抽屉时简历抽屉被基座的「点外面」关掉', async () => {
    const wrapper = await visit('/jobs')
    await wrapper.find('.resumes-trigger').trigger('click')
    expect(bodyEl('#resume-drawer')).not.toBeNull()

    // 真机顺序：pointerdown 先于 click。账户按钮的 `aria-controls` 指的是账户面板，
    // 所以简历抽屉的基座不豁免它 → 判成「点外面」关掉；随后 click 才开账户抽屉。
    const account = wrapper.find('.account-trigger')
    account.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    await settled()
    expect(bodyEl('#resume-drawer')).toBeNull()

    await account.trigger('click')
    expect(bodyEl('#user-drawer')).not.toBeNull()
    // 反向也要成立：开简历抽屉时账户抽屉关掉（同一条机制，不是各写一份互斥）。
    const resumes = wrapper.find('.resumes-trigger')
    resumes.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    await settled()
    expect(bodyEl('#user-drawer')).toBeNull()
    await resumes.trigger('click')
    expect(bodyEl('#resume-drawer')).not.toBeNull()
    expect(bodyEl('#user-drawer')).toBeNull()
  })

  it('「新建简历」弹窗归外框：抽屉收起了它还在，字不会打到一半消失', async () => {
    const wrapper = await visit('/jobs')
    await wrapper.find('.resumes-trigger').trigger('click')
    const createButton = [...document.body.querySelectorAll('.menu-action')].find(
      (node) => node.textContent?.trim() === '新建简历',
    )
    expect(createButton).toBeDefined()
    ;(createButton as HTMLElement).click()
    await flushPromises()

    // 抽屉收起（与原来 openCreate 先关菜单的行为一致），但弹窗活着。
    await settled()
    expect(bodyEl('#resume-drawer')).toBeNull()
    expect(bodyEl('#create-profile-heading')).not.toBeNull()
    expect(bodyEl('.modal-field input')).not.toBeNull()

    // Esc 关弹窗：监听挂在遮罩上，焦点在输入框里，按键冒泡得到。
    bodyEl('.ui-overlay')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    await settled()
    expect(bodyEl('#create-profile-heading')).toBeNull()
  })

  it('常驻左栏里的「新建简历」走同一个意图，开的是同一个弹窗', async () => {
    setViewportWidth(1440)
    const wrapper = await visit('/jobs')
    const createButton = wrapper.find('.resume-sidebar .menu-action')

    expect(createButton.text()).toBe('新建简历')
    await createButton.trigger('click')
    await flushPromises()

    expect(bodyEl('#create-profile-heading')).not.toBeNull()
    // 左栏是常驻的，开弹窗不该把它收掉（它没有可收的东西）。
    expect(wrapper.find('.resume-sidebar').exists()).toBe(true)
  })
})
