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
import type { ApiErrorBody, ProfileRead, UserRole } from '@/api/types'
import { APP_MODULES, staffOnlyModules } from '@/config/modules'
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
/*
 * 「加入班级」横幅的两格开关（第 24 轮）：
 * `orgStatus` 决定 `GET /organizations/me` 回答 200（已在班级）还是 404（还没有班级）——
 * 后端只有这两种答案，500 那一支另有一条用例单独验「什么都不画」。
 * 默认 200：本轮之前写好的用例都不关心这条横幅，让它们的 `calls` 与画面保持原样。
 */
let orgStatus = 200
let redeemStatus = 200
let redeemMessage = ''

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
  if (method === 'get' && url === '/organizations/me') {
    if (orgStatus === 404) {
      // 后端对「账号还没有组织」的正面答复（ADR-021 §2.6），不是故障。
      return Promise.reject(failure(config, 404, '你的账号还没有加入组织。'))
    }
    if (orgStatus !== 200) {
      return Promise.reject(failure(config, orgStatus, '组织信息暂时无法读取，请稍后重试。'))
    }
    return Promise.resolve(
      envelope(config, {
        data: {
          organization: { id: 'org-1', code: 'CJLU2026', name: '中国计量大学', kind: 'school' },
          member: { user_id: 'user-1', role: 'student' },
        },
        meta: { request_id: 'r' },
      }),
    )
  }
  if (method === 'post' && url === '/invitations/redeem') {
    if (redeemStatus !== 200) {
      return Promise.reject(
        failure(config, redeemStatus, redeemMessage || '邀请码无效或已过期。'),
      )
    }
    return Promise.resolve(
      envelope(config, {
        data: {
          organization: { id: 'org-1', code: 'CJLU2026', name: '中国计量大学', kind: 'school' },
          role: 'student',
        },
        meta: { request_id: 'r' },
      }),
    )
  }
  return Promise.reject(failure(config, 404, `未桩住的请求：${method.toUpperCase()} ${url}`))
}

/** 只为 `RouterLink` 能解析出 `href`：目标页本身不挂载，所以组件桩成空壳。 */
let viewRouter: ReturnType<typeof createRouter>

function makeRouter() {
  const blank = { template: '<div />' }
  viewRouter = createRouter({
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
  return viewRouter
}

async function mountView(mode: 'guest' | 'member', role: UserRole | null = 'student') {
  const pinia = createPinia()
  setActivePinia(pinia)
  /*
   * 显式改写（2026-11-15，守则 R1）：原来这里灌的是 `permissions`（配合
   * `auth.hasPermission('dashboard:read')` 那张教师入口卡），现在工作台按**角色**分流，
   * 判据换成直接写 `auth.role`——它与 `/auth/me` 顶层那一格同源，
   * 而「谁能看到哪个入口」的唯一出处仍是 `config/modules.ts::visibleToRoles`。
   */
  const auth = useAuthStore()
  auth.role = role
  auth.user = {
    id: 'user-1',
    username: 'tester',
    email: null,
    phone: null,
    created_at: '2026-11-01T09:00:00+08:00',
  }

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
  orgStatus = 200
  redeemStatus = 200
  redeemMessage = ''
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

    // 五个请求各发一次，一个不多一个不少。
    /*
     * 显式改写（2026-11-15 第 24 轮，守则 R1）：原来钉的是四个端点，第五个是
     * 「加入班级」横幅的判据 `GET /organizations/me`。加一个而不是换判据——
     * 那条横幅只有真的问到后端 404 才允许出现，所以这一次读必须在。
     */
    expect(calls.sort()).toEqual(
      [
        'get /assessments/profile',
        'get /interviews',
        'get /jobs',
        'get /organizations/me',
        'get /profiles/me',
      ].sort(),
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

  /*
   * 显式改写（2026-11-15）：原来这一条叫「教师入口按权限出现：有 dashboard:read 才画」，
   * 判据是权限字符串。工作台改成按角色分流之后，这一条拆成三格，
   * 每格都还在验同一件事——**教师不该看到学生那四张空统计卡，且要有一眼能走进去的入口**。
   */
  it('教师侧角色：四张统计卡不渲染，改成教师工具入口卡', async () => {
    const wrapper = await mountView('member', 'teacher')

    expect(wrapper.find('.stat-grid').exists()).toBe(false)
    expect(wrapper.findAll('.stat-card')).toHaveLength(0)

    const cards = wrapper.findAll('.staff-entry')
    const expected = staffOnlyModules()
    expect(cards).toHaveLength(expected.length)
    // 名字仍从模块表来（守则 L4）：写死一份也会「有 N 张卡」，比对名字才证明读的是那张表。
    // 取 h2 而不是整张卡的 text()——卡里还有一格「教师工具」的标签，整卡文案会把比对变成
    // 「教师工具组织管理」这种拼出来的东西，看着像通过了其实没比对名字。
    expect(cards.map((card) => card.get('h2').text())).toEqual(
      expected.map((module) => module.name),
    )
    // href 由路由表解析：入口指向的必须是那条真路由。
    for (const [index, module] of expected.entries()) {
      expect(cards[index]?.attributes('href')).toBe(
        viewRouter.resolve({ name: module.routeName }).fullPath,
      )
    }
  })

  it('教师侧不发那四个读请求：卡片不渲染，请求也不该照发', async () => {
    await mountView('member', 'teacher')

    expect(calls).toEqual([])
  })

  it('教师侧欢迎语带「老师」二字，学生侧不带', async () => {
    const teacher = await mountView('member', 'teacher')
    expect(teacher.get('.hero--member h1').text()).toBe('tester老师，今天从哪一步接着走？')

    const student = await mountView('member', 'student')
    expect(student.get('.hero--member h1').text()).toBe('tester，今天从哪一步接着走？')
    // 学生侧那四张卡照常（这一句防止「教师分流」把学生页也一起改坏）。
    expect(student.findAll('.stat-card')).toHaveLength(4)
  })

  it('school_admin 也走教师侧那一支', async () => {
    const wrapper = await mountView('member', 'school_admin')

    expect(wrapper.find('.stat-grid').exists()).toBe(false)
    expect(wrapper.findAll('.staff-entry').length).toBe(staffOnlyModules().length)
  })
})

describe('工作台 · 学生「加入班级」横幅（第 24 轮）', () => {
  /*
   * jsdom 里点 `type="submit"` 的按钮不会走表单提交（它不实现浏览器的隐式提交），
   * 所以下面几支都直接对 `<form>` 发 `submit`——那正是组件绑的 `@submit.prevent`，
   * 也是用户按回车时真实走到的一步。
   */

  it('后端回答 404（还没有组织）才画横幅，而且画在四张统计卡之上', async () => {
    orgStatus = 404
    const wrapper = await mountView('member', 'student')

    const banner = wrapper.get('.join-card')
    expect(banner.text()).toContain('加入班级')
    // 「最显眼的位置」是可判的：横幅必须排在统计卡之前（老板指令「顶部或四大卡片上方」）。
    const grid = wrapper.get('.stat-grid').element
    // DOCUMENT_POSITION_FOLLOWING = 4：网格在横幅之后，即横幅在前。
    expect(Boolean(banner.element.compareDocumentPosition(grid) & 4)).toBe(true)
  })

  it('后端回答 200（已在班级）时一条都不画', async () => {
    const wrapper = await mountView('member', 'student')

    expect(wrapper.find('.join-card').exists()).toBe(false)
    // 那次读照发——它同时是「确认不用再画」的依据，不是为了画横幅才发的。
    expect(calls).toContain('get /organizations/me')
  })

  it('组织信息读失败（500）时什么都不画：不替学生编一个「你还没加入班级」', async () => {
    orgStatus = 500
    const wrapper = await mountView('member', 'student')

    expect(wrapper.find('.join-card').exists()).toBe(false)
    // 四张统计卡不受影响：横幅的判据失败不该把整页拖成错误态。
    expect(wrapper.findAll('.stat-card')).toHaveLength(4)
  })

  it('教师侧不画横幅，也一次都不读组织', async () => {
    orgStatus = 404
    const wrapper = await mountView('member', 'teacher')

    expect(wrapper.find('.join-card').exists()).toBe(false)
    // `organization` 本来就是教师的可见入口（模块表），这里再挂一条横幅是第二条路。
    expect(calls).toEqual([])
  })

  it('空邀请码：校验提示上屏、面板不关、一次兑换都不发', async () => {
    orgStatus = 404
    const wrapper = await mountView('member', 'student')
    await wrapper.get('.join-card__trigger').trigger('click')

    const panel = wrapper.get('.join-panel')
    expect(wrapper.get('.join-card__trigger').attributes('aria-expanded')).toBe('true')
    // 浮层归基座（守则 L3）：`role="group"` + `aria-label` 都在面板那一个元素上。
    expect(panel.attributes('role')).toBe('group')
    expect(panel.attributes('aria-label')).toBe('邀请码')

    await wrapper.get('form.join-panel__form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('.join-panel__error').text()).toBe('请填写老师给的邀请码。')
    expect(wrapper.find('.join-panel').exists()).toBe(true)
    expect(calls).not.toContain('post /invitations/redeem')
  })

  it('后端拒绝：原因原样上屏，面板不关（守则 C3）', async () => {
    orgStatus = 404
    redeemStatus = 400
    redeemMessage = '邀请码无效或已过期。'
    const wrapper = await mountView('member', 'student')
    await wrapper.get('.join-card__trigger').trigger('click')
    await wrapper.get('#join-code-input').setValue('WRONG123')
    await wrapper.get('form.join-panel__form').trigger('submit')
    await flushPromises()
    await flushPromises()

    expect(calls).toContain('post /invitations/redeem')
    expect(wrapper.get('.join-panel__error').text()).toBe('邀请码无效或已过期。')
    expect(wrapper.find('.join-panel').exists()).toBe(true)
    expect(wrapper.find('.page-notice').exists()).toBe(false)
  })

  it('兑换成功：调现有 redeem 接口，然后重读这一页——横幅消失、回执留下', async () => {
    orgStatus = 404
    const wrapper = await mountView('member', 'student')
    await wrapper.get('.join-card__trigger').trigger('click')
    await wrapper.get('#join-code-input').setValue('CJLU2026')
    // 这一次提交之后后端改成「已经有组织」，横幅的判据才跟着翻面。
    calls = []
    orgStatus = 200
    await wrapper.get('form.join-panel__form').trigger('submit')
    await flushPromises()
    await flushPromises()

    expect(calls).toContain('post /invitations/redeem')
    // 回执写的是后端给的组织名，不是页面自己编的。
    expect(wrapper.get('.page-notice').text()).toBe('已加入「中国计量大学」')
    expect(wrapper.find('.join-card').exists()).toBe(false)
    // 「刷新页面」落在重读上：横幅判据 + 四张卡各再读一次（`loadMembership` 在 `redeemOpen = false`
    // 之前已经把 orgStatus 读成 200，所以横幅当场就消失）。
    expect(calls.filter((call) => call === 'get /organizations/me')).toHaveLength(1)
    expect(calls.filter((call) => call === 'get /profiles/me')).toHaveLength(1)
  })

  it('按 Esc 关掉面板：开合归基座，不是这里自己写的监听', async () => {
    orgStatus = 404
    const wrapper = await mountView('member', 'student')
    await wrapper.get('.join-card__trigger').trigger('click')
    expect(wrapper.find('.join-panel').exists()).toBe(true)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.find('.join-panel').exists()).toBe(false)
    expect(wrapper.get('.join-card__trigger').attributes('aria-expanded')).toBe('false')
  })

  it('点「取消」也收起面板，且不发明任何请求', async () => {
    orgStatus = 404
    const wrapper = await mountView('member', 'student')
    await wrapper.get('.join-card__trigger').trigger('click')
    calls = []

    await wrapper.get('.join-panel__cancel').trigger('click')
    await flushPromises()

    expect(wrapper.find('.join-panel').exists()).toBe(false)
    expect(calls).toEqual([])
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
    /*
     * 显式改写（2026-11-15 第 24 轮，守则 R1）：这条原来直接扫 `code`，于是它管的其实是
     * 「整个文件里不许出现 `0%` 这两个字符」。本仓一直没有撞上这件事，是因为这一页以前
     * 没有任何百分比 CSS；本轮邀请码面板写了 `width: 100%`，那条 CSS 与「假卡上写死 0%」
     * 没有半点关系。判据因此收窄到它真正想管的地方——**会渲染上屏的标记与脚本**：
     * 剥掉 `<style>` 之后仍然一处都不许有 `0%` / `未设置`，强度没有放宽（模板里再写死
     * 一个 `0%` 照样红），只是不再把样式表里的百分比误当成占位数字。
     */
    const markup = code.replace(/<style[\s\S]*?<\/style>/g, '')
    expect(markup).not.toBe(code) // 反向护栏：真的扫到了那个 CSS 块，不是剥了个空
    expect(markup).not.toContain('0%')
    expect(markup).not.toContain('未设置')
    expect(code).toContain("if (state === 'failed') return '—'")
  })
})
