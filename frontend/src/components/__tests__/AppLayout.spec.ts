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
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import App from '@/App.vue'
import { http } from '@/api/client'
import type { ApiErrorBody } from '@/api/types'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'

/* --------------------------- jsdom 缺的那一样 --------------------------- */

/** 什么都不量：本文件断言的是外框结构，纸面尺寸归 `ResumePreview.spec.ts` 管。 */
class StubResizeObserver {
  constructor(_callback: unknown) {}

  observe(): void {}

  unobserve(): void {}

  disconnect(): void {}
}

;(globalThis as unknown as Record<string, unknown>).ResizeObserver = StubResizeObserver

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
  savedAdapter = http.defaults.adapter
  http.defaults.adapter = fakeAdapter

  const auth = useAuthStore()
  auth.token = 'access-token-under-test'
  auth.user = {
    id: 'user-under-test',
    username: 'tester',
    email: null,
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
    // 顶栏与内容区是外框的**两个直接子项**：嵌套漏一层就会变成「顶栏套顶栏」或「没顶栏」。
    expect(wrapper.find('.app-shell > .app-topbar').exists()).toBe(true)
    expect(wrapper.find('.app-shell > main.board-page').exists()).toBe(true)
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
    const wrapper = await visit('/')

    expect(wrapper.findAll('.app-topbar')).toHaveLength(1)
    expect(wrapper.find('.app-shell > .resume-workspace').exists()).toBe(true)
    // `.editor` / `.style-panel` 是 `ResumeEditorView` 打给子组件的类：
    // 它们在，就说明外框换层之后工作区还是三列那一棵，没有退化成空 `<main>`。
    expect(wrapper.find('.resume-workspace > .editor').exists()).toBe(true)
    expect(wrapper.find('.resume-workspace > .style-panel').exists()).toBe(true)
    expect(requested).toContain('get /profiles')
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

    // 表里 `inTopBar` 为真的目前只有一条；F3 加条目时改的是表，不是这里。
    expect(entries).toHaveLength(1)
    expect(entries[0]?.element.tagName).toBe('A')
    expect(entries[0]?.text()).toBe('岗位库')
    expect(entries[0]?.attributes('href')).toBe('/jobs')
    // 判据要真的把抽屉打开再看：收起态它根本不在 DOM 里，`closest` 必然为 null，那是空检。
    await wrapper.find('.account-trigger').trigger('click')
    expect(entries[0]?.element.closest('.drawer-root')).toBeNull()
    expect(wrapper.find('.drawer-root').exists()).toBe(true)
  })
})
