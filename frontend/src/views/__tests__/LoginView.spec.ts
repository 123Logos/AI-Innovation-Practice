/**
 * 登录页对「会话为什么被送回这里」的表达（Phase 2 需求池 §一「Token 静默续期」的收尾）。
 *
 * 为什么新开这个文件（任务书的文件清单里只点名了 `ResumePreview.spec.ts` 一个新文件）：
 * 本任务的两条硬约束都是**登录页屏幕上的一句话**——被踢的人要看得见原因、
 * 主动登出的人不能看见那句假话。清单里另外三个可改的测试文件没有一个挂载过
 * `LoginView`：`byok.spec.ts` 是 client 层的请求头与 401 分流、
 * `AppTopBar.spec.ts` 是顶栏结构、`resume.spec.ts` 是 store。
 * 把断言写进那三个文件里，测到的是机制，不是用户眼睛看到的文字。
 *
 * 这里用的是**真实 `@/router`**，而不是 `AppTopBar.spec.ts` 那种自造 memory router：
 * 「arm 这条提示」的代码就在 `router/index.ts` 的 `setUnauthorizedHandler` 回调里，
 * 而且是模块导入时的副作用；换成假 router 等于把被测的那一行替掉，
 * 「登出 vs 过期」的判据（`current.name === 'login'` 就早退）也就无从验证。
 * `AppTopBar.spec.ts` 躲开真实 router 是怕全局守卫去请 `/auth/me`——
 * 这里把 `auth.user` 预先塞好，`ensureUser()` 第一行就短路，一次网络都不发。
 */

import { flushPromises, mount } from '@vue/test-utils'
import {
  AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  authHttp,
  consumeSessionExpiredNotice,
  http,
  REFRESH_TOKEN_STORAGE_KEY,
  storeSession,
  TOKEN_STORAGE_KEY,
  TOKEN_EXPIRES_AT_KEY,
} from '@/api/client'
import type { ApiErrorBody } from '@/api/types'
import AppTopBar from '@/components/AppTopBar.vue'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'
import LoginView from '@/views/LoginView.vue'
import RegisterView from '@/views/RegisterView.vue'

/** 屏幕上那句提示必须包含的动作，与绝不能出现的内部词（任务 1 的文案红线）。 */
const REQUIRED_ACTION = '重新登录'
const FORBIDDEN_WORDS = ['令牌', 'token', 'Token', 'TOKEN', 'session', 'Session', 'SESSION']

let pinia: Pinia
let savedAdapters: Array<[AxiosInstance, AxiosInstance['defaults']['adapter']]> = []
const mounted: { unmount: () => void }[] = []

/** 记录真实发出去的请求，用来证明「没偷偷多发一次」或「确实发了那一次」。 */
let calls: Array<{ method: string; url: string }> = []

/** 本次用例要让哪个端点失败：`business` = 受保护接口 401，`login` = 登录本身 401。 */
let failing: 'none' | 'business' | 'login' = 'none'

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

/** 后端统一错误模型的真实形状（`app/core/errors.py`），拦截器按这个拆 `ApiError`。 */
function failure(
  config: InternalAxiosRequestConfig,
  status: number,
  code: string,
  message: string,
): AxiosError<ApiErrorBody> {
  const body: ApiErrorBody = {
    error: { code, message, details: {}, retryable: false },
    meta: { request_id: 'req-login-view-test' },
  }
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, envelope(config, body, status))
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  calls.push({ method, url })

  // 续期失败 = 本地已无法自愈：这是「被服务端请回登录页」的唯一触发条件。
  if (url.includes('/auth/refresh')) {
    return Promise.reject(
      failure(config, 401, 'AUTH_REFRESH_INVALID', '登录状态已失效，请重新登录。'),
    )
  }

  if (url.includes('/auth/login') && failing === 'login') {
    return Promise.reject(failure(config, 401, 'AUTH_INVALID', '用户名或密码错误。'))
  }

  if (failing === 'business') {
    return Promise.reject(failure(config, 401, 'AUTH_INVALID', '登录状态已失效，请重新登录。'))
  }

  return Promise.resolve(envelope(config, { data: null, meta: { request_id: 'r' } }))
}

/**
 * 把一个「刚登录、会话还活着」的现场摆出来：
 * 三件套进 localStorage（用生产的 `storeSession`，不手写字面量，免得键名漂移），
 * 内存镜像同步到 auth store（路由守卫读的就是它）。
 */
function seedLiveSession(): void {
  storeSession({
    access_token: 'access-under-test',
    refresh_token: 'refresh-under-test',
    expires_in: 3600,
  })
  const auth = useAuthStore()
  auth.token = 'access-under-test'
  auth.user = {
    id: 'user-under-test',
    username: 'tester',
    email: null,
    created_at: '2026-11-07T10:00:00+08:00',
  }
}

/** 会话被清场后的本地残留（用来证明「被踢」真的发生过）。 */
function sessionCleared(): boolean {
  return (
    localStorage.getItem(TOKEN_STORAGE_KEY) === null &&
    localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY) === null &&
    localStorage.getItem(TOKEN_EXPIRES_AT_KEY) === null
  )
}

function mountAttached(component: Parameters<typeof mount>[0]) {
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(component, { attachTo, global: { plugins: [pinia, router] } })
  mounted.push(wrapper)
  return wrapper
}

/** 走完整链路：站在受保护页面 → 接口 401 → 续期失败 → 被送回登录页。 */
async function kickBackToLogin(): Promise<void> {
  failing = 'business'
  await router.push('/jobs')
  expect(router.currentRoute.value.name).toBe('job-board')

  await http.get('/profiles/profile-under-test').catch(() => undefined)
  await flushPromises()
}

/**
 * 提示有没有出现在屏幕上——这是本文件唯一有效的问法：
 * 断言 `consumeSessionExpiredNotice()` 只能证明标记被 arm 了，证明不了用户看得见。
 */
function noticeText(wrapper: ReturnType<typeof mountAttached>): string | null {
  const node = wrapper.find('.expired-notice')
  return node.exists() ? node.text() : null
}

beforeEach(async () => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  sessionStorage.clear()
  document.body.innerHTML = ''
  calls = []
  failing = 'none'
  // 上一条用例可能 arm 过而没被读走：标记是模块级的，必须每条用例清一次。
  consumeSessionExpiredNotice()

  savedAdapters = [
    [http, http.defaults.adapter],
    [authHttp, authHttp.defaults.adapter],
  ]
  http.defaults.adapter = fakeAdapter
  authHttp.defaults.adapter = fakeAdapter

  // vue-router 的 `started` 只在 install 时置位：第一次挂载会补一次初始导航，
  // 它会按「未登录」把当前路由改成 /login，把用例自己 push 的目标顶掉。
  // 先在这里把这一次消耗掉，后面每条用例拿到的路由状态才是自己摆的。
  const bootstrap = mount({ render: () => null }, { global: { plugins: [pinia, router] } })
  await router.isReady()
  bootstrap.unmount()
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
  for (const [instance, adapter] of savedAdapters) {
    instance.defaults.adapter = adapter
  }
  savedAdapters = []
})

describe('被会话失效踢回登录页时，用户看得见原因与下一步', () => {
  it('续期失败 → 落到登录页，页面上出现「已过期 + 请重新登录」那句话', async () => {
    seedLiveSession()
    await kickBackToLogin()

    expect(router.currentRoute.value.name).toBe('login')
    // redirect 是这句提示能兑现的前提：登录成功后会回到刚才那一页。
    expect(router.currentRoute.value.query.redirect).toBe('/jobs')
    expect(sessionCleared()).toBe(true)

    const wrapper = mountAttached(LoginView)
    const text = noticeText(wrapper)
    expect(text).not.toBeNull()
    expect(text).toContain(REQUIRED_ACTION)
  })

  it('提示是一次性的：显示过一次之后，再进登录页不会重复挂着', async () => {
    seedLiveSession()
    await kickBackToLogin()

    const first = mountAttached(LoginView)
    expect(noticeText(first)).toContain(REQUIRED_ACTION)
    first.unmount()

    // 第二次进入（同一份内存、没有新的失效事件）必须看不见：
    // 挂着一条上一世的过期提醒，等于让用户反复怀疑自己账号坏了。
    const second = mountAttached(LoginView)
    expect(noticeText(second)).toBeNull()
  })

  it('提示不落任何存储：`localStorage` / `sessionStorage` 里搜不到这句话', async () => {
    seedLiveSession()
    await kickBackToLogin()

    const wrapper = mountAttached(LoginView)
    expect(noticeText(wrapper)).toContain(REQUIRED_ACTION)

    // 按内容搜而不是按键名猜：任何一处把这句写进存储（包括塞进某个既有对象里）都算违反红线。
    for (const storage of [localStorage, sessionStorage]) {
      const leaks = Object.entries(storage).filter(([, value]) => String(value).includes('过期'))
      expect(leaks).toEqual([])
    }
  })

  it('文案给出下一步动作，并且不出现「令牌 / token / session」这类内部词', async () => {
    seedLiveSession()
    await kickBackToLogin()

    const wrapper = mountAttached(LoginView)
    const text = noticeText(wrapper)
    expect(text).not.toBeNull()
    expect(text).toContain(REQUIRED_ACTION)
    for (const word of FORBIDDEN_WORDS) {
      expect(text, `提示文案里不该出现内部词「${word}」`).not.toContain(word)
    }
    // 只说「过期了」是不够的：必须交代重新登录之后会怎样，否则用户不敢登。
    expect(text).toContain('登录后')
  })
})

describe('登出 vs 过期（这条区分是提示不撒谎的前提）', () => {
  /**
   * 两半放在一起、同一个登录页、同一时刻，唯一不同的是来源。
   *
   * 判据（`router/index.ts`）：主动登出走 `AppTopBar.handleLogout()`
   * → `resume.reset()` / `job.reset()` → `auth.signOut()`（内部 `logout()` 清本地 + 撤销
   * refresh token）→ `router.replace({name:'login'})`，
   * 全程**不进** `setUnauthorizedHandler` 的回调，而 arm 只发生在那个回调里。
   * 所以这一条测的是「两条路径结构上分开」这个事实本身，而不是某个 `if` 写没写。
   */
  it('点「退出登录」落到登录页：看不见「已过期」；被服务端踢回来才看得见', async () => {
    seedLiveSession()
    await router.push('/jobs')

    const topBar = mountAttached(AppTopBar)
    await topBar.find('.logout-button').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('login')
    expect(sessionCleared()).toBe(true)

    const afterLogout = mountAttached(LoginView)
    expect(noticeText(afterLogout)).toBeNull()
    // 机制层面一起钉住：登出连标记都没 arm，所以「看不见」不是因为根本没渲染登录页。
    expect(consumeSessionExpiredNotice()).toBe(false)
    afterLogout.unmount()

    // 对照组：来源换成「接口 401 + 续期失败」，同一句提示就必须出现。
    await seedLiveSession()
    await kickBackToLogin()
    const afterKick = mountAttached(LoginView)
    expect(noticeText(afterKick)).toContain(REQUIRED_ACTION)
  })

  it('在登录页把密码敲错，不说「登录状态已过期」（他从未登录成功过）', async () => {
    failing = 'login'
    await router.push('/login')

    const wrapper = mountAttached(LoginView)
    await wrapper.find('input[autocomplete="username"]').setValue('tester')
    await wrapper.find('input[autocomplete="current-password"]').setValue('wrong-password')
    await wrapper.find('.primary-button').trigger('click')
    await flushPromises()

    // 登录失败仍然要说人话：后端那句「用户名或密码错误。」照原样显示。
    expect(wrapper.text()).toContain('用户名或密码错误')
    expect(noticeText(wrapper)).toBeNull()

    // 同一次失败会把标记留在内存里吗？也不会：登录页上的 401 走的是早退分支。
    const again = mountAttached(LoginView)
    expect(noticeText(again)).toBeNull()
    expect(consumeSessionExpiredNotice()).toBe(false)
  })
})

describe('不是被踢回来的两种进入方式，都不该出现提示', () => {
  it('直接在地址栏输入 /login（从未登录过）', async () => {
    await router.push('/login')

    const wrapper = mountAttached(LoginView)

    expect(noticeText(wrapper)).toBeNull()
    // 反向护栏：登录页本身渲染得出来，否则上面那句「没有提示」是假绿。
    expect(wrapper.find('.primary-button').exists()).toBe(true)
  })

  it('从注册页点「去登录」跳过来', async () => {
    const register = mountAttached(RegisterView)
    await register.get('.switch-hint a').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('login')

    const wrapper = mountAttached(LoginView)
    expect(noticeText(wrapper)).toBeNull()
  })
})
