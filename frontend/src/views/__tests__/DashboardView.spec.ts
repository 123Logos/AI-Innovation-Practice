/**
 * `DashboardView.vue` 的单测（批次 F7：工作台双态）。
 *
 * 这一批把老板 2026-11-09 的两条原话（「纳入路由，不要删」+「dashboard 也需要有
 * 已登录的状态」）落成两个状态，所以用例分三组：
 *
 * 1. **访客态**：只画引导与模块入口，**一个请求都不发**——访客没有会话，
 *    发出去只会得到 401 再被守卫踢走，那是把「来看看」变成「被弹窗打断」。
 * 2. **登录态**：四张统计卡各自绑定一个真端点，数字来自响应；某一格失败只影响那一格
 *    （显示「—」+「暂时读不到」），不会把另外三格一起拖成 0。
 * 3. **不许有假数据**：源码级断言钉住三件已删除的东西——硬编码模块数组、
 *    「模块正在搭建中」这类空按钮、以及写死的 `0%` / `未设置` 占位数字。
 *    这一组测的是**写法**，挂载后断不出来（假数据与真数据长得一模一样），
 *    与 `layoutGuards.spec.ts` 同一个理由。
 *
 * 桩的口径与 `OrganizationView.spec.ts` 一致：把 axios 的 `defaults.adapter` 换成一只
 * 内存小服务器，按 URL 分派；没桩住的请求明确失败，不静默返回空对象。
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { flushPromises, mount } from '@vue/test-utils'
import { AxiosError, type AxiosInstance, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { authHttp, http } from '@/api/client'
import type { ApiErrorBody, ProfileRead } from '@/api/types'
import { APP_MODULES } from '@/config/modules'
import { useAuthStore } from '@/stores/auth'
import DashboardView from '@/views/DashboardView.vue'

/** 一份「只填了姓名联系方式」的档案：5 项检查点里过 1 项 = 20%。 */
const PROFILE: ProfileRead = {
  id: 'profile-1',
  user_id: 'user-1',
  title: '默认简历',
  is_default: true,
  deleted_at: null,
  paper_size: 'A4',
  theme: 'ink',
  layout_config: { version: 3, sections: [] },
  full_name: '林小满',
  email: 'lin@example.com',
  grade: '大三',
  contact: '13800000000',
  target_role: null,
  created_at: '2026-11-01T09:00:00+08:00',
  updated_at: '2026-11-10T09:00:00+08:00',
  skills: [],
  experiences: [],
  educations: [],
  certificates: [],
  portfolio_links: [],
  city_preferences: [],
}

const TAGS = {
  student_profile_id: 'profile-1',
  tags: [
    {
      tag: 'Vue',
      status: 'verified',
      score: 88,
      source: 'assessment',
      evidence_count: 2,
      updated_at: '2026-11-12T09:00:00+08:00',
    },
    {
      tag: 'TypeScript',
      status: 'verified',
      score: 81,
      source: 'assessment',
      evidence_count: 2,
      updated_at: '2026-11-12T09:00:00+08:00',
    },
    {
      tag: '算法',
      status: 'improving',
      score: 55,
      source: 'assessment',
      evidence_count: 1,
      updated_at: '2026-11-12T09:00:00+08:00',
    },
  ],
  latest_session_id: null,
  latest_summary: null,
  latest_summary_source: null,
}

let calls: string[] = []
let savedAdapters: Array<[AxiosInstance, AxiosInstance['defaults']['adapter']]> = []
let profileStatus = 200
let tagStatus = 200
let jobStatus = 200
let interviewStatus = 200
let jobTotal = 7
let interviewTotal = 3

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

function failure(config: InternalAxiosRequestConfig, status: number, message: string) {
  const body: ApiErrorBody = {
    error: {
      code: status >= 500 ? 'SERVER_ERROR' : 'CLIENT_ERROR',
      message,
      details: {},
      retryable: false,
    },
    meta: { request_id: 'req-dashboard-test' },
  }
  return new AxiosError(
    'Request failed',
    'ERR_BAD_REQUEST',
    config,
    {},
    envelope(config, body, status),
  )
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  calls.push(`${method} ${url}`)

  if (method === 'get' && url === '/profiles/me') {
    if (profileStatus !== 200) {
      return Promise.reject(failure(config, profileStatus, '档案读不到，请稍后重试。'))
    }
    return Promise.resolve(
      envelope(config, { data: { profile: PROFILE }, meta: { request_id: 'r' } }),
    )
  }
  if (method === 'get' && url === '/assessments/profile') {
    if (tagStatus !== 200) {
      return Promise.reject(failure(config, tagStatus, '能力标签读不到，请稍后重试。'))
    }
    return Promise.resolve(
      envelope(config, { data: { profile: TAGS }, meta: { request_id: 'r' } }),
    )
  }
  if (method === 'get' && url === '/jobs') {
    if (jobStatus !== 200) {
      return Promise.reject(failure(config, jobStatus, '岗位数据读不到，请稍后重试。'))
    }
    return Promise.resolve(
      envelope(config, {
        data: { jobs: [] },
        meta: { request_id: 'r', page: 1, page_size: 1, total: jobTotal },
      }),
    )
  }
  if (method === 'get' && url === '/interviews') {
    if (interviewStatus !== 200) {
      return Promise.reject(failure(config, interviewStatus, '面试记录读不到，请稍后重试。'))
    }
    return Promise.resolve(
      envelope(config, {
        data: { sessions: [], total: interviewTotal },
        meta: { request_id: 'r' },
      }),
    )
  }
  return Promise.reject(failure(config, 404, `未桩住的请求：${method.toUpperCase()} ${url}`))
}

/** 只为 `RouterLink` 能解析出 `href`：目标页本身不挂载，所以组件桩成空壳。 */
function makeRouter() {
  const blank = { template: '<div />' }
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'resume-editor', component: blank },
      { path: '/jobs', name: 'job-board', component: blank },
      { path: '/organization', name: 'organization', component: blank },
      { path: '/interviews', name: 'interview-list', component: blank },
      { path: '/assessments', name: 'assessment', component: blank },
      { path: '/teacher', name: 'teacher-dashboard', component: blank },
      { path: '/dashboard', name: 'dashboard', component: blank },
      { path: '/welcome', name: 'welcome', component: blank },
      { path: '/login', name: 'login', component: blank },
      { path: '/register', name: 'register', component: blank },
    ],
  })
}

async function mountView(mode: 'guest' | 'member', permissions: string[] = []) {
  const pinia = createPinia()
  setActivePinia(pinia)
  // `hasPermission()` 读的是 store 里那份权限表（登录后由 `/auth/me` 写进来），
  // 这里直接灌进去——测的是「有权限时画不画这张卡」，不是登录流程。
  useAuthStore().permissions = permissions

  const wrapper = mount(DashboardView, {
    props: { mode },
    global: { plugins: [pinia, makeRouter()] },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  calls = []
  profileStatus = 200
  tagStatus = 200
  jobStatus = 200
  interviewStatus = 200
  jobTotal = 7
  interviewTotal = 3
  savedAdapters = [
    [http, http.defaults.adapter],
    [authHttp, authHttp.defaults.adapter],
  ]
  http.defaults.adapter = fakeAdapter
  authHttp.defaults.adapter = fakeAdapter
})

afterEach(() => {
  for (const [instance, adapter] of savedAdapters) instance.defaults.adapter = adapter
  savedAdapters = []
})

describe('工作台 · 访客态', () => {
  it('画出引导三步与两个入口，且一个请求都不发', async () => {
    const wrapper = await mountView('guest')

    expect(wrapper.text()).toContain('先把做过的事写清楚')
    expect(wrapper.findAll('.step-card')).toHaveLength(3)
    // 反向护栏：访客态不许把登录态那四张统计卡画出来（那些数字只有登录才读得到）。
    expect(wrapper.find('.stat-grid').exists()).toBe(false)
    expect(calls).toEqual([])
  })

  it('模块入口来自模块表，且不含「工作台」自己', async () => {
    const wrapper = await mountView('guest')

    const cards = wrapper.findAll('.module-card')
    const expected = APP_MODULES.filter((module) => module.key !== 'dashboard')
    expect(cards).toHaveLength(expected.length)
    // 逐个比对文案来源：写死一份数组也会「有 N 张卡」，比对名字才能证明读的是那张表。
    expect(cards.map((card) => card.text())).toEqual(expected.map((module) => module.name))
    // 本页自己就是工作台，卡片里不该再有一张「回工作台」（其余模块名照旧都在）。
    expect(cards.map((card) => card.text())).not.toContain('工作台')
  })
})

describe('工作台 · 登录态', () => {
  it('四张卡的数字全部来自各自端点', async () => {
    const wrapper = await mountView('member')

    // 四个端点各发一次，一个不多一个不少。
    expect(calls.sort()).toEqual(
      ['get /assessments/profile', 'get /interviews', 'get /jobs', 'get /profiles/me'].sort(),
    )

    const cards = wrapper.findAll('.stat-card')
    expect(cards).toHaveLength(4)
    // 档案：只填了姓名联系方式 → 5 项过 1 项 = 20%，并且提示下一件该补什么。
    expect(cards[0]?.text()).toContain('20%')
    expect(cards[0]?.text()).toContain('还差：设一个目标岗位')
    // 能力标签：3 条里 2 条 verified，另 1 条待提升。
    expect(cards[1]?.find('.stat-value').text()).toBe('2')
    expect(cards[1]?.text()).toContain('另有 1 条待提升')
    // 岗位与面试：总数来自响应（7 / 3），不是页面里写死的。
    expect(cards[2]?.find('.stat-value').text()).toBe('7')
    expect(cards[3]?.find('.stat-value').text()).toBe('3')
  });

  it('某一格失败只影响那一格：显示「—」而不是 0', async () => {
    tagStatus = 500
    const wrapper = await mountView('member')

    const cards = wrapper.findAll('.stat-card')
    // 失败那一格：— 与「暂时读不到」，绝不画成 0。
    expect(cards[1]?.find('.stat-value').text()).toBe('—')
    expect(cards[1]?.text()).toContain('暂时读不到')
    expect(cards[1]?.find('.stat-value').text()).not.toBe('0')
    // 其余三格照常：一张卡失败不许把整页拖空。
    expect(cards[0]?.find('.stat-value').text()).toBe('20%')
    expect(cards[2]?.find('.stat-value').text()).toBe('7')
    expect(cards[3]?.find('.stat-value').text()).toBe('3')
    // 失败要可见（守则 C1/C3），不能只体现在一个字符上。
    expect(wrapper.get('.page-error').text()).toContain('能力标签读不到')
  })

  it('教师入口按权限出现：有 dashboard:read 才画', async () => {
    const without = await mountView('member')
    expect(without.find('.teacher-entry').exists()).toBe(false)

    const withPermission = await mountView('member', ['dashboard:read'])
    expect(withPermission.find('.teacher-entry').exists()).toBe(true)
    expect(withPermission.get('.teacher-entry').text()).toContain('学情看板')
  })
})

describe('工作台 · 不许有假数据（源码级）', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/views/DashboardView.vue'), 'utf8')
  /**
   * 去掉注释再判。
   *
   * 文件头那段注释**故意**写着「改造前是 `0%` / `未设置` / 模块正在搭建中」，
   * 照着原文扫会把一段历史说明判成违例——这正是 `check_ui_copy.py` 那条
   * 「注释能关闸 / 注释也能误伤」踩过的坑。判据只看真正会执行与会上屏的部分。
   */
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/^\s*\/\/.*$/gm, '')

  it('没有任何硬编码的模块数组，模块名与图标都来自模块表', () => {
    // 反向护栏：先确认它确实读了那张表（否则下面几条会因为「什么都没写」而假绿）。
    expect(code).toContain("from '@/config/modules'")
    expect(code).toContain('v-for="module in entryModules"')
    // 旧数组的指纹是**声明**一个 `routeName:` 字段（L4 的「描述符形状只许有一处声明」），
    // 以及把模块名写进字面量。判据写成 `routeName:`（带冒号）而不是 `routeName`，
    // 是因为模板里合法地要读 `module.routeName`——那正是「读表」而不是「抄表」的证据。
    expect(code).not.toContain('routeName:')
    for (const module of APP_MODULES) {
      expect(code, `${module.name} 不该以字面量写在这个视图里`).not.toContain(`'${module.name}'`)
    }
    expect(code).not.toContain("icon: '")
  })

  it('没有「模块正在搭建中」这类空按钮', () => {
    expect(code).not.toContain('正在搭建中')
    expect(code).not.toContain('showComingSoon')
    // 那个按钮的实现依赖 Element Plus 的全局消息，本页已不引入它。
    expect(code).not.toContain('ElMessage')
  })

  it('没有写死的占位数字：失败用「—」，未读用「…」', () => {
    // `0%` / `未设置` 是改造前那三张假卡的字面量；现在百分比由档案真实字段算出。
    expect(code).not.toContain('0%')
    expect(code).not.toContain('未设置')
    expect(code).toContain("if (state === 'failed') return '—'")
  })
})
