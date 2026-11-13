/**
 * 岗位详情页的单测（C2 的两件小事，ADR-011 议题 3.B v7 + 监督者裁定的弹窗改造）。
 *
 * 1. **实测确认 `evidence_text` / `confidence` 是否如实展示**（不是假设）。做法是喂一份
 *    两格都有真值的详情，再断言屏幕上出现的具体文字——C1 之后这两格不再恒为 `null`，
 *    而 B2 那一版页面根本没渲染 `confidence`。这条用例同时是「缺什么补什么」的证据：
 *    把 `confidence` 那一行删掉，本文件立刻变红。
 * 2. **`origin` 徽标只有两态且不得出现「AI 提取」字样**（v7 裁定：这一列只证明
 *    「这句话在 JD 原文里出现过」，不证明是谁写的）。
 * 3. **删除确认不再是 `window.confirm`**：原生对话框同步阻塞主线程，会把 CDP 驱动的
 *    自动化验收钉死在那一步（本轮之前真发生过，只能退回手写 curl）。
 *    这里用 spy 断言 `window.confirm` 一次都没被调用，并验证弹层自身的开/关/确认。
 *
 * 路由同样用本地 memory router（理由见 `AppTopBar.spec.ts` 文件头：`@/router` 的
 * 全局守卫每次跳转都会去请 `/auth/me`，那是另一条验收线）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { createMemoryHistory, createRouter, type Router, type RouteRecordRaw } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { http } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import JobDetailView from '@/views/JobDetailView.vue'

const OWN_USER_ID = 'user-under-test'
const BLANK = { template: '<div />' }

interface Call {
  method: string
  url: string
  /** 出线的请求体（`config.data` 是 axios 序列化后的字符串时解析回对象）；GET 为 `null`。 */
  body: Record<string, unknown> | null
}

let calls: Call[] = []
let savedAdapter: typeof http.defaults.adapter
/**
 * 让 `fakeAdapter` 的 DELETE 分支返回一条真实形状的失败。
 * 刻意做成开关而不是「整只替换 adapter」：替换会把详情页的初始 `GET /jobs/{id}` 一起拒掉，
 * 页面渲染不出删除按钮，于是这条用例其实在验一个不存在的界面——它照样会「通过」，
 * 因为断言落在一个从未出现的元素上。
 */
let failDelete = false
/**
 * 匹配端点的返回。默认 `null` = **不发这一份**（详情页挂载时根本不该调用它）；
 * 需要结果的用例先赋值再点按钮。做成开关而不是常驻 fixture，是为了让
 * 「mount 完没有 POST /match」这条断言真的能测到东西。
 */
let matchPayload: Record<string, unknown> | null = null

/**
 * `GET /jobs/{id}/requirement-match` 的 `coverage`（ADR-013 §10 第 3c 步）。
 * 默认那份对应下面两条角标用例里的岗位技能行；每条用例跑完在 `beforeEach` 里复位。
 */
let requirementCoverage: Array<{ name: string; status: string; matched_form: string | null }> = [
  { name: 'FastAPI', status: 'covered', matched_form: ' FastAPI ' },
  { name: 'Kubernetes', status: 'missing', matched_form: null },
]
/** 匹配失败的真实形状（429 / 404），与 `failDelete` 同一个理由。 */
let matchFailure: { status: number; code: string; message: string } | null = null
/**
 * 审核动作（提交审核 / 通过 / 驳回）要回哪一份失败。
 *
 * 三条真实来路都用得上：没有组织 → 400 `JOB_ORGANIZATION_REQUIRED`；
 * 岗位已过期 → 409 `JOB_EXPIRED`（第 18 轮裁定 ③，前端不许自己比日期）；
 * 自审 → 409 `JOB_SELF_REVIEW`。做成开关是为了让「成功文案」那条断言不会
 * 因为适配器永远成功而变成自我实现。
 */
let reviewFailure: { status: number; code: string; message: string } | null = null

function reviewFailureError(config: InternalAxiosRequestConfig): AxiosError {
  const failure = reviewFailure!
  return new AxiosError(
    failure.code,
    'ERR_BAD_RESPONSE',
    config,
    {},
    envelope(
      config,
      {
        error: {
          code: failure.code,
          message: failure.message,
          details: {},
          retryable: false,
        },
        meta: { request_id: 'r' },
      },
      failure.status,
    ),
  )
}
/** 当前在挂的组件实例的卸载钩子，见 `afterEach` 里那段为什么要 unmount。 */
let unmountCurrent: (() => void) | null = null
/** 详情页要渲染的岗位：默认「创建者本人 + 一条可回溯、一条无依据」。 */
let jobPayload: Record<string, unknown> = {}
/**
 * 一个 pinia 实例同时喂给 `setActivePinia` 与 `plugins`。
 * 分两个的话测试里 `useAuthStore().user = …` 写的是 A，组件读到的是 B，
 * `isCreatorOf` 恒为 false → 创建者专属的那组按钮全都「看不见」，
 * 于是每条用例都变成在验一个不存在的路径。
 */
let pinia: Pinia

function skillRow(id: string, name: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    name,
    type: 'hard',
    origin: 'jd_verifiable',
    confidence: '0.9000',
    evidence_text: `熟练掌握 ${name}`,
    ...overrides,
  }
}

function makeJobPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'job-42',
    company_name: '示例科技',
    title: '后端实习（AI 方向）',
    location: '上海',
    employment_type: 'internship',
    description: '负责后端接口开发，熟练掌握 Python 与 FastAPI。',
    salary_min: '3000.00',
    salary_max: '6000.00',
    deadline: null,
    status: 'draft',
    created_by_user_id: OWN_USER_ID,
    review_note: null,
    created_at: '2026-11-05T10:00:00+08:00',
    updated_at: '2026-11-05T10:00:00+08:00',
    deleted_at: null,
    experience_required: '3 年以上',
    extract_model: 'deepseek-flash',
    extract_prompt_version: 'v1',
    extracted_at: '2026-11-05T10:00:00+08:00',
    skills: [
      skillRow('s1', 'Python'),
      // 一条没有依据的手动条目：两格都必须是 `null`，页面整块不渲染。
      skillRow('s2', '沟通能力强', { type: 'soft', origin: 'unverified', confidence: null, evidence_text: null }),
    ],
    is_expired: false,
    ...overrides,
  }
}

function makeMatchPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    profile_id: 'profile-mine',
    profile_title: '默认简历',
    job_id: 'job-42',
    // 0.45×0.667 + 0.20×0.667 = 0.4349，除以本次真正参与的权重之和 0.65 → 0.669。
    score: 0.669,
    weights_version: 'v2',
    terms: [
      { name: 'coverage', weight: 0.45, value: 0.667, computed: true, note: null },
      { name: 'evidence', weight: 0.2, value: 0.667, computed: true, note: null },
      { name: 'relevance', weight: 0.15, value: null, computed: false, note: 'not_measurable_v1' },
      { name: 'preference', weight: 0.1, value: null, computed: false, note: 'no_target_role' },
      { name: 'remaining_time', weight: 0.1, value: null, computed: false, note: 'no_deadline' },
    ],
    covered: [
      {
        skill: 'Python',
        evidence: [
          {
            experience_id: 'exp-1',
            field: 'description',
            snippet: '用 Python 重写了订单服务',
            strength: 0.667,
          },
        ],
      },
    ],
    partial: [{ skill: 'FastAPI', reason: '只在 1 处经历里出现，证据偏薄' }],
    gaps: [
      {
        skill: 'MySQL',
        action: '补一条真的用 MySQL 做过的项目经历：做了什么 → 怎么做的 → 结果如何。',
        verify: '按你写的过程细节追问，能答出为什么这样选，就算验证通过。',
      },
    ],
    meta: {
      model: 'deepseek-flash',
      prompt_version: 'v1',
      evidence_mode: 'ai_scored',
      degrade_reason: null,
      candidate_pairs: 2,
      verified_pairs: 2,
      dropped_unreferenced: 0,
      dropped_unknown_skill: 0,
      dropped_unusable_strength: 0,
      dropped_missing_output: 0,
      clamped_strength: 0,
      fragments_truncated: 0,
      truncated: false,
      finish_reason: 'stop',
      weight_sum_used: 0.65,
      no_required_skills: false,
      matcher_version: 'v1',
      note: null,
    },
    ...overrides,
  }
}

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

/** 记一次出线：方法、路径与请求体（审核口径全在请求体里，不能只记两条）。 */
function recordCall(config: InternalAxiosRequestConfig): void {
  calls.push({
    method: (config.method ?? 'get').toLowerCase(),
    url: config.url ?? '',
    body:
      typeof config.data === 'string' && config.data
        ? (JSON.parse(config.data) as Record<string, unknown>)
        : ((config.data as Record<string, unknown> | undefined) ?? null),
  })
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  recordCall(config)
  if (method === 'get' && /^\/jobs\/[^/]+\/requirement-match$/.test(url)) {
    // 第 3c 条：逐条状态由后端给。用例改 `requirementCoverage` 就能模拟 covered/partial/missing
    // 任意组合——包括「前端本地那套归一化会判错」的那些写法组合。
    return Promise.resolve(
      envelope(config, {
        data: {
          job_id: 'job-42',
          profile_id: 'profile-mine',
          profile_title: '默认简历',
          coverage: requirementCoverage,
          meta: { matcher_version: 'v1' },
        },
        meta: { request_id: 'req-test' },
      }),
    )
  }

  if (method === 'post' && url === '/jobs/job-42/match') {
    if (matchFailure) {
      return Promise.reject(
        new AxiosError(
          matchFailure.code,
          'ERR_BAD_REQUEST',
          config,
          {},
          envelope(
            config,
            {
              error: {
                code: matchFailure.code,
                message: matchFailure.message,
                details: {},
                retryable: matchFailure.status === 429,
              },
              meta: { request_id: 'r' },
            },
            matchFailure.status,
          ),
        ),
      )
    }
    // 没准备结果却被调用了：说明有人在 `onMounted` 里自动算了匹配
    // （ADR-013 §10 第 8 步明令禁止——那是拿用户的 Key 换页面浏览量）。
    // 这里返回一次**看得见**的失败而不是直接 throw：throw 在适配器里会变成未捕获拒绝，
    // 测的是 vitest 的报错处理而不是本组件的行为。
    if (!matchPayload) {
      return Promise.reject(
        new AxiosError(
          'tripwire',
          'ERR_BAD_REQUEST',
          config,
          {},
          envelope(
            config,
            {
              error: {
                code: 'INTERNAL_ERROR',
                message: '测试没有准备匹配结果，但页面发出了 POST /match。',
                details: {},
                retryable: false,
              },
              meta: { request_id: 'r' },
            },
            500,
          ),
        ),
      )
    }
    return Promise.resolve(envelope(config, { data: matchPayload, meta: { request_id: 'r' } }))
  }

  if (method === 'delete') {
    if (failDelete) {
      // 真实形状：404 + 统一错误体（他人岗位 / 已被删过都会走这里）。
      return Promise.reject(
        new AxiosError(
          'not found',
          'ERR_BAD_REQUEST',
          config,
          {},
          envelope(
            config,
            {
              error: {
                code: 'CLIENT_ERROR',
                message: '岗位不存在，或已经不是你的岗位。',
                details: {},
                retryable: false,
              },
              meta: { request_id: 'r' },
            },
            404,
          ),
        ),
      )
    }
    return Promise.resolve(envelope(config, { data: { job_id: 'job-42', deleted: true }, meta: { request_id: 'r' } }))
  }

  // 审核流的两个动作：`ADR-024` 之后创建者只能提交审核，上架由审核人那一次 POST /review 决定。
  if (method === 'post' && url === '/jobs/job-42/submit-review') {
    if (reviewFailure) return Promise.reject(reviewFailureError(config))
    // 没有组织的账号拿到的 400 由用例通过 `reviewFailure` 造，这里只走成功路径。
    jobPayload = { ...jobPayload, status: 'pending_review', review_note: null }
    return Promise.resolve(envelope(config, { data: { job: jobPayload }, meta: { request_id: 'r' } }))
  }

  if (method === 'post' && url === '/jobs/job-42/review') {
    if (reviewFailure) return Promise.reject(reviewFailureError(config))
    const body = typeof config.data === 'string' ? (JSON.parse(config.data) as Record<string, unknown>) : {}
    const rejected = body.decision === 'reject'
    jobPayload = {
      ...jobPayload,
      status: rejected ? 'rejected' : 'published',
      review_note: rejected ? String(body.note ?? '') : null,
    }
    return Promise.resolve(envelope(config, { data: { job: jobPayload }, meta: { request_id: 'r' } }))
  }

  if (method === 'get' && url === '/jobs') {
    return Promise.resolve(
      envelope(config, { data: { jobs: [] }, meta: { request_id: 'r', page: 1, page_size: 50, total: 0 } }),
    )
  }

  return Promise.resolve(envelope(config, { data: { job: jobPayload }, meta: { request_id: 'r' } }))
}

function makeRouter(): Router {
  const routes: RouteRecordRaw[] = [
    { path: '/jobs', name: 'job-board', component: BLANK },
    { path: '/jobs/trash', name: 'job-trash', component: BLANK },
    { path: '/jobs/:id', name: 'job-detail', component: BLANK },
    { path: '/jobs/:id/edit', name: 'job-edit', component: BLANK },
  ]
  return createRouter({ history: createMemoryHistory(), routes })
}

async function mountDetail(overrides: Record<string, unknown> = {}) {
  jobPayload = makeJobPayload(overrides)
  const router = makeRouter()
  await router.push('/jobs/job-42')
  await router.isReady()
  // 弹层是 `Teleport` 到 body 的，必须 attachTo 一个挂在 document 上的节点，
  // 否则 `document.body` 里查不到它，Esc 的监听也收不到冒泡。
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(JobDetailView, {
    attachTo,
    global: {
      // 顶栏不再由本视图摆（批次 F2 · `AppLayout`），所以这里连 `stubs` 都不需要：
      // mount 出来就是内容区，弹层与断言的落点一寸没动。
      plugins: [pinia, router],
    },
  })
  await flushPromises()
  // `attachTo` 只是容器；组件实例本身也必须显式卸载，否则它留在原地继续听 document 的事件。
  unmountCurrent = () => wrapper.unmount()
  return { wrapper, router, attachTo }
}

function callsFor(method: string, url?: string): Call[] {
  return calls.filter((call) => call.method === method && (url === undefined || call.url === url))
}

/** 最后一次 `POST /jobs/{id}/review` 的请求体：审核动作的口径全在这两格里。 */
function lastReviewBody(): Record<string, unknown> | null {
  return callsFor('post', '/jobs/job-42/review').pop()?.body ?? null
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  calls = []
  failDelete = false
  matchPayload = null
  matchFailure = null
  reviewFailure = null
  requirementCoverage = [
    { name: 'FastAPI', status: 'covered', matched_form: ' FastAPI ' },
    { name: 'Kubernetes', status: 'missing', matched_form: null },
  ]
  unmountCurrent = null
  savedAdapter = http.defaults.adapter
  http.defaults.adapter = fakeAdapter
  useAuthStore().user = {
    id: OWN_USER_ID,
    username: 'tester',
    email: null,
    created_at: '2026-11-05T10:00:00+08:00',
  }
})

afterEach(() => {
  // 先卸载再清 body：弹层是 `Teleport` 到 body 的，而 Esc 的监听挂在 `document` 上。
  // 只清 `innerHTML` 不卸载，会让上一个用例的实例继续活着——它的监听仍在，
  // 它的 Teleport 目标已经被拔掉，于是「按 Esc 后弹层在不在」这句话测的是遗留实例，
  // 并且顺带抛一个 `insertBefore of null` 的未捕获错误（本轮实测踩过）。
  unmountCurrent?.()
  unmountCurrent = null
  http.defaults.adapter = savedAdapter
  document.body.innerHTML = ''
})

describe('详情页如实展示提取产物（C1 之后这两格有真值了）', () => {
  it('`evidence_text` 有值就显示原文片段；`null` 的条目整块不出现「原文依据」', async () => {
    const { wrapper } = await mountDetail()

    // Python 那条：依据来自 JD 原文，必须看得见。
    expect(wrapper.text()).toContain('原文依据：熟练掌握 Python')

    // 沟通能力强那条两格都是 `null`：不渲染空展开区，也不写「无依据」占位。
    const items = wrapper.findAll('.skill-item')
    expect(items).toHaveLength(2)
    expect(items[1]?.text()).not.toContain('原文依据')
    // 反向护栏：`null` 不能被显示成字面量或 0（那是给用户一份不存在的依据）。
    expect(wrapper.text()).not.toContain('原文依据：null')
    expect(wrapper.text()).not.toContain('AI 把握 0.00')
  })

  it('`confidence` 有值就显示「AI 把握」两位小数，`null` 时那一格整个不出现', async () => {
    const { wrapper } = await mountDetail()

    // 后端 `DECIMAL(5,4)` 读回来是字符串 `"0.9000"`，展示裁到两位（理由见 `confidenceLabel`）。
    expect(wrapper.text()).toContain('AI 把握 0.90')
    expect(wrapper.text()).not.toContain('0.9000')
    // 第二条没有把握 → 页面上只有一句「AI 把握」，不是两句。
    expect(wrapper.findAll('.skill-confidence')).toHaveLength(1)
  })

  it('`origin` 徽标只有两态固定文案，且全页不出现「AI 提取」字样（v7 裁定）', async () => {
    const { wrapper } = await mountDetail()

    const badges = wrapper.findAll('.badge--origin').map((badge) => badge.text())
    expect(badges).toEqual(['可回溯原文', '未标注出处'])

    // 这条是硬红线：这一列只证明「这句话在 JD 原文里出现过」，不证明是谁写的。
    expect(wrapper.text()).not.toContain('AI 提取')
    expect(wrapper.text()).not.toContain('ai_extracted')
  })

  it('未知 `origin` 值（后端将来加第三种）一律落到「未标注出处」，不猜成「可回溯」', async () => {
    const { wrapper } = await mountDetail({
      skills: [skillRow('s1', 'Python', { origin: 'future_value' })],
    })

    expect(wrapper.find('.badge--origin').text()).toBe('未标注出处')
  })

  it('「你已具备」的角标来自 requirement-match：后端说具备才具备', async () => {
    const { wrapper } = await mountDetail({
      skills: [
        skillRow('s1', 'FastAPI'),
        skillRow('s2', 'Kubernetes', { evidence_text: null, confidence: null, origin: 'unverified' }),
      ],
    })

    const mine = wrapper.findAll('.badge--mine')
    expect(mine).toHaveLength(1)
    expect(mine[0]?.text()).toBe('你已具备')
    // `partial` 也是「命中」：角标只回答写法这一件事，证据够不够由匹配卡说。
    expect(mine[0]?.attributes('title')).toContain('FastAPI')

    // 一次详情页一次覆盖查询；仍然不发匹配请求（那要用户点按钮）。
    expect(callsFor('get', '/jobs/job-42/requirement-match')).toHaveLength(1)
    expect(callsFor('post')).toEqual([])
    expect(callsFor('put')).toEqual([])
    expect(callsFor('delete')).toEqual([])
  })

  it('版本号写法：岗位要 Python、简历写 Python3，角标跟着后端判并且说出对面写法', async () => {
    // 这一条是老板 2026-11-08 那一屏的界面版。本地那套 trim+NFKC+小写判不出这一格，
    // 所以只要有人把判定搬回前端，`covered` 就会变成 `missing`，这条当场红。
    requirementCoverage = [{ name: 'Python', status: 'covered', matched_form: 'Python3' }]
    const { wrapper } = await mountDetail({ skills: [skillRow('s1', 'Python')] })

    const badge = wrapper.find('.badge--mine')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('title')).toBe('按你简历里写的「Python3」认的')
  })

  it('后端判 missing 时，简历里有一样名字也不许标「已具备」（判定不许留在前端）', async () => {
    requirementCoverage = [{ name: 'Python', status: 'missing', matched_form: null }]
    const { wrapper } = await mountDetail({ skills: [skillRow('s1', 'Python')] })

    expect(wrapper.find('.badge--mine').exists()).toBe(false)
  })

  it('覆盖查询失败时角标整体不显示，但详情页仍然读得出岗位', async () => {
    http.defaults.adapter = (config: InternalAxiosRequestConfig) => {
      recordCall(config)
      if (/requirement-match$/.test(config.url ?? '')) {
        return Promise.reject(new AxiosError('连不上。', 'ERR_NETWORK'))
      }
      return fakeAdapter(config)
    }
    const { wrapper } = await mountDetail({ skills: [skillRow('s1', 'Python')] })

    expect(wrapper.text()).toContain('Python')
    expect(wrapper.find('.badge--mine').exists()).toBe(false)
    // 岗位本身没读失败：角标是增益信息，不许因为它把详情页拖进错误态。
    expect(wrapper.text()).not.toContain('读取岗位详情失败')
  })

  it('过期只看后端 `is_expired`：前端不再自己比一次日期', async () => {
    const { wrapper } = await mountDetail({ deadline: '2020-01-01', is_expired: true })

    expect(wrapper.text()).toContain('已过期')
  })
})

describe('详情页的编辑入口（议题 3.B v9 补的那一条）', () => {
  it('创建者看得到「编辑」，指向 `/jobs/:id/edit`', async () => {
    const { wrapper } = await mountDetail()

    const link = wrapper.find('[data-testid="edit-link"]')
    expect(link.exists()).toBe(true)
    expect(link.text()).toBe('编辑')
    expect(link.attributes('href')).toBe('/jobs/job-42/edit')
  })

  it('非创建者看不到「编辑」（后端对非创建者的 PUT 一律 404，摆了就是必败按钮）', async () => {
    useAuthStore().user = {
      id: 'someone-else',
      username: 'other',
      email: null,
      created_at: '2026-11-05T10:00:00+08:00',
    }
    const { wrapper } = await mountDetail()

    expect(wrapper.find('[data-testid="edit-link"]').exists()).toBe(false)
    // 发布 / 下架 / 删除也一起消失：这一组按钮全部仅创建者可用（1.F #4~#6）。
    expect(wrapper.find('[data-testid="delete-button"]').exists()).toBe(false)
  })

  it('点「编辑」真的跳到 job-edit（路由名写错的话这里会红）', async () => {
    const { wrapper, router } = await mountDetail()

    await wrapper.find('[data-testid="edit-link"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('job-edit')
    expect(router.currentRoute.value.params.id).toBe('job-42')
  })
})

/**
 * 弹层被 `Teleport` 到 `document.body`，所以它**不在 `wrapper` 的树里**
 * （VTU2 的已知行为：`wrapper.find()` 查不到传送走的节点，`wrapper.html()` 里也没有）。
 * 这里直接查真实 DOM——与用户在浏览器里看到的、CDP 自动化要点的那个节点是同一个。
 */
function bodyEl(selector: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(selector)
  if (!el) throw new Error(`body 里找不到 ${selector}`)
  return el
}

/** 弹层在不在？这是**唯一**有效的问法：`wrapper.find()` 对 Teleport 出去的节点永远返回空。 */
function dialogOpen(): boolean {
  return document.querySelector('[data-testid="delete-dialog"]') !== null
}

/**
 * 一句话说了几遍。
 *
 * `toContain` 只能证明「出现过」，而 2026-11-08 真浏览器实测抓到的正是**出现两遍**：
 * `matchUncomputableLine` 与 `.empty` 在同一屏各说一句「岗位没填技能要求」。
 * 所以这里数次数，不数存在性。
 */
function occurrences(text: string, phrase: string): number {
  return text.split(phrase).length - 1
}

/**
 * 真实 DOM 上的一次点击。
 * 不用 `wrapper.trigger()` 是因为它只能打在组件树内的节点上；
 * 用 `.click()` 顺带保证了 `@click.self` 的语义被如实测到
 * （`target` 就是你点的那个元素，遮罩与面板的差别不会被打桩糊掉）。
 */
async function click(el: Element): Promise<void> {
  ;(el as HTMLElement).click()
  await flushPromises()
}

describe('删除确认：页面内弹层，不再用 window.confirm', () => {
  /**
   * `window.confirm` 同步阻塞主线程，CDP 驱动的验收会停在那一步不能继续
   * （本轮之前的真实事故：浏览器里点删除后流程挂死，只能退回手写 curl）。
   * 这条 spy 断言防的是「将来有人觉得原生弹窗省事又改回去」。
   */
  it('`window.confirm` 一次都没被调用', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => undefined)
    const { wrapper } = await mountDetail()

    await wrapper.find('[data-testid="delete-button"]').trigger('click')
    // 直接确认删除，走完整个链路。
    await click(bodyEl('[data-testid="delete-confirm"]'))

    expect(confirmSpy).toHaveBeenCalledTimes(0)
    expect(alertSpy).toHaveBeenCalledTimes(0)
    expect(callsFor('delete', '/jobs/job-42')).toHaveLength(1)

    confirmSpy.mockRestore()
    alertSpy.mockRestore()
  })

  it('点「删除」只打开弹层，此刻还没有发出 DELETE；点「确认删除」才删', async () => {
    const { wrapper } = await mountDetail()

    // 第一次点：只弹层，不删。这是「确认」的全部意义。
    await wrapper.find('[data-testid="delete-button"]').trigger('click')
    expect(dialogOpen()).toBe(true)
    expect(callsFor('delete')).toHaveLength(0)

    await click(bodyEl('[data-testid="delete-cancel"]'))
    expect(dialogOpen()).toBe(false)
    expect(callsFor('delete')).toHaveLength(0)

    // 反面对照：确认按钮真的删得掉，否则上面那句「不删」只是因为根本没东西可删。
    await wrapper.find('[data-testid="delete-button"]').trigger('click')
    await click(bodyEl('[data-testid="delete-confirm"]'))
    expect(callsFor('delete', '/jobs/job-42')).toHaveLength(1)
  })

  it('弹层是模态对话框：`role=dialog` + `aria-modal` + 用 `aria-labelledby` 指到标题', async () => {
    const { wrapper } = await mountDetail()
    await wrapper.find('[data-testid="delete-button"]').trigger('click')

    const dialog = bodyEl('[data-testid="delete-dialog"] .ui-panel')
    expect(dialog.getAttribute('role')).toBe('dialog')
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(dialog.getAttribute('aria-labelledby')).toBe('job-delete-heading')
    expect(bodyEl('#job-delete-heading').textContent).toBe('删除这条岗位？')
    // 说清后果与找回路径，而不是只问「确认吗」。
    expect(dialog.textContent).toContain('我的删除')
    expect(dialog.textContent).toContain('后端实习（AI 方向）')
  })

  it('Esc 关闭弹层，不删任何东西', async () => {
    const { wrapper } = await mountDetail()
    await wrapper.find('[data-testid="delete-button"]').trigger('click')
    // 先断言它真的开着：少了这一句，「Esc 之后查不到弹层」会因为 Teleport 而恒成立（假绿）。
    expect(dialogOpen()).toBe(true)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()

    expect(dialogOpen()).toBe(false)
    expect(callsFor('delete')).toHaveLength(0)
  })

  it('点遮罩（弹层外的暗区）关闭，点面板内部不算关闭', async () => {
    const { wrapper } = await mountDetail()
    await wrapper.find('[data-testid="delete-button"]').trigger('click')

    // 面板上的点击不该关掉它：`@click.self` 只认遮罩本身。
    await click(bodyEl('[data-testid="delete-dialog"] .ui-panel'))
    expect(dialogOpen()).toBe(true)

    await click(bodyEl('[data-testid="delete-dialog"]'))
    expect(dialogOpen()).toBe(false)
    expect(callsFor('delete')).toHaveLength(0)
  })

  it('确认删除成功后回到列表页；失败时留在弹层外也能看见原因', async () => {
    const { wrapper, router } = await mountDetail()
    await wrapper.find('[data-testid="delete-button"]').trigger('click')
    await click(bodyEl('[data-testid="delete-confirm"]'))

    expect(callsFor('delete', '/jobs/job-42')).toHaveLength(1)
    expect(router.currentRoute.value.name).toBe('job-board')
    // 软删之后列表必须重拉一次，否则用户回到列表还是看见这条。
    expect(callsFor('get', '/jobs')).toHaveLength(1)
  })

  it('删除失败时弹层不关闭，错误显示在页面上（不静默吞掉）', async () => {
    failDelete = true

    const { wrapper, router } = await mountDetail()
    await wrapper.find('[data-testid="delete-button"]').trigger('click')
    await click(bodyEl('[data-testid="delete-confirm"]'))

    // 服务端那句话原样显示（`describeError` 不翻译 API 文案），并且人还在详情页。
    expect(wrapper.text()).toContain('岗位不存在，或已经不是你的岗位。')
    expect(router.currentRoute.value.name).toBe('job-detail')
    // 「不静默吞掉」的另一半：失败后弹层仍然开着，用户看得见自己刚才那一下没成。
    expect(dialogOpen()).toBe(true)
  })
})

/**
 * 匹配卡（ADR-013 §10 第 8 步）。这一组守的四件事：
 *
 * 1. **不自动调用**：每次匹配都可能向 DeepSeek 发一次真实请求、花调用者自己的额度，
 *    所以只能在用户点按钮之后发生。第一条用例是这件事的唯一有效测法——
 *    适配器在「没准备结果却被调用」时返回一次看得见失败，而不是 throw。
 * 2. **AI 失败不是错误**：退化必须显示，但显示成说明而不是红字（§6.5 / §3 的状态码边界）。
 * 3. **五种原因分句**：把它们写成同一句，用户就会去改一个本来没坏的 Key。
 * 4. **技术构成不进界面**：权重、`weight_sum_used`、各计数留在响应里对账
 *    （2026-11-07 人类负责人裁定「不用向客户说明技术上的设计」，作用域见 §7.5）。
 */
function keywordMeta(
  reason: string | null,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    model: null,
    prompt_version: null,
    evidence_mode: 'keyword_fallback',
    degrade_reason: reason,
    candidate_pairs: 0,
    verified_pairs: 0,
    dropped_unreferenced: 0,
    dropped_unknown_skill: 0,
    dropped_unusable_strength: 0,
    dropped_missing_output: 0,
    clamped_strength: 0,
    fragments_truncated: 0,
    truncated: false,
    finish_reason: null,
    weight_sum_used: 0.65,
    no_required_skills: false,
    matcher_version: 'v1',
    note: null,
    ...overrides,
  }
}

describe('匹配卡：只有点按钮才算', () => {
  it('挂载详情页时一个匹配请求都没发', async () => {
    const { wrapper } = await mountDetail()

    expect(callsFor('post', '/jobs/job-42/match')).toEqual([])
    expect(wrapper.find('.match-result').exists()).toBe(false)
    // 首屏只有按钮和那句「是参考不是录用概率」，没有任何看起来像结果的数字。
    expect(wrapper.find('.match-score').exists()).toBe(false)
    expect(wrapper.find('[data-testid="match-score"]').exists()).toBe(false)
  })

  it('点一次才发一次 POST；分数、三档清单、原文片段、用的哪份档案都上屏', async () => {
    matchPayload = makeMatchPayload()
    const { wrapper } = await mountDetail()

    await wrapper.find('[data-testid="match-run"]').trigger('click')
    await flushPromises()

    expect(callsFor('post', '/jobs/job-42/match')).toHaveLength(1)
    // 分数压成整数百分比（`0.669` → `67%`）：小数点后三位在屏幕上没有决策价值。
    expect(wrapper.find('[data-testid="match-score"]').text()).toBe('67%')
    expect(wrapper.text()).toContain('按《默认简历》打分')

    expect(wrapper.text()).toContain('已具备 1 项')
    expect(wrapper.text()).toContain('部分具备 1 项')
    expect(wrapper.text()).toContain('还缺 1 项')
    // 证据片段是用户自己写过的原话，原样显示才能核对；并说明它出自哪一栏。
    expect(wrapper.text()).toContain('用 Python 重写了订单服务')
    expect(wrapper.text()).toContain('项目描述')
    expect(wrapper.text()).toContain('补一条真的用 MySQL 做过的项目经历')
    // 后端给的 partial 原因直接显示，前端不重写一遍（重写就会和后端漂移）。
    expect(wrapper.text()).toContain('只在 1 处经历里出现，证据偏薄')
  })

  it('结果出来后按钮变成「再算一次」，再点真的发第二次', async () => {
    matchPayload = makeMatchPayload()
    const { wrapper } = await mountDetail()

    const button = wrapper.find('[data-testid="match-run"]')
    await button.trigger('click')
    await flushPromises()
    expect(button.text()).toBe('再算一次')

    await button.trigger('click')
    await flushPromises()
    expect(callsFor('post', '/jobs/job-42/match')).toHaveLength(2)
  })

  it('AI 正常参与时不出现退化句', async () => {
    matchPayload = makeMatchPayload()
    const { wrapper } = await mountDetail()

    await wrapper.find('[data-testid="match-run"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('.match-degrade').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('关键词')
  })

  /**
   * 五种退化各说各话。断言的是**渲染出来的五段文字两两不同**，
   * 而不是「各自包含某个词」——后者会因为实现把五句都拼进同一个常量大串而假绿。
   */
  it('五种退化两两不同，且分数照常显示、没有一种被写成红字', async () => {
    const reasons = ['key_missing', 'key_invalid', 'upstream', 'timeout', 'bad_output']
    const lines: string[] = []
    for (const reason of reasons) {
      matchPayload = makeMatchPayload({ meta: keywordMeta(reason) })
      const { wrapper } = await mountDetail()
      await wrapper.find('[data-testid="match-run"]').trigger('click')
      await flushPromises()

      const degrade = wrapper.find('.match-degrade')
      expect(degrade.exists(), reason).toBe(true)
      lines.push(degrade.text())
      // 退化不是错误：分数照样是真的，只是「证据强度」这一项换了算法。
      expect(wrapper.find('[data-testid="match-score"]').exists(), reason).toBe(true)
      expect(wrapper.find('[role="alert"]').exists(), reason).toBe(false)
      // 不在这里 unmount：`mountDetail()` 只记最后一个实例的卸载钩子，
      // 在这里自己拆会让 `afterEach` 再拆一次同一个实例。
    }

    expect(new Set(lines).size).toBe(5)
    for (const line of lines) expect(line).toContain('关键词')
    // 前两种用户自己修得动，所以必须给出去哪儿修；
    // 后三种不许把他支去改一个本来没坏的 Key——这正是 §6.5 要求分句的理由。
    expect(lines[0]).toContain('AI 设置')
    expect(lines[1]).toContain('AI 设置')
    for (const line of lines.slice(2)) {
      expect(line).not.toContain('AI 设置')
      expect(line).not.toContain('API Key')
    }
  })

  it('429 是真失败：说清为什么要等一分钟，而且没有结果区', async () => {
    matchFailure = { status: 429, code: 'RATE_LIMITED', message: '请求太频繁，请稍后重试。' }
    const { wrapper } = await mountDetail()

    await wrapper.find('[data-testid="match-run"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').text()).toContain('等一分钟')
    // 服务端那句「请求太频繁」不直接显示：这条分支的用户动作由 `matchFailureText` 给，
    // 与提取侧同一个分工（文案随上游措辞变，动作不能跟着变）。
    expect(wrapper.find('.match-result').exists()).toBe(false)
  })

  it('404 说清「这条岗位读不到所以没法匹配」，不是一句通用失败', async () => {
    matchFailure = { status: 404, code: 'NOT_FOUND', message: '资源不存在。' }
    const { wrapper } = await mountDetail()

    await wrapper.find('[data-testid="match-run"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').text()).toContain('回收站')
    expect(wrapper.find('.match-result').exists()).toBe(false)
  })

  it('没有档案时明说是按空简历打的分（否则 0% 会被读成算错了）', async () => {
    matchPayload = makeMatchPayload({
      profile_id: null,
      profile_title: null,
      score: 0,
      covered: [],
      partial: [],
      gaps: [],
    })
    const { wrapper } = await mountDetail()

    await wrapper.find('[data-testid="match-run"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('.match-source').text()).toContain('空简历')
    expect(wrapper.find('.match-source').text()).not.toContain('按《')
  })

  it('岗位没有技能要求时说明为什么没有条目，而不是留一片空白', async () => {
    matchPayload = makeMatchPayload({
      covered: [],
      partial: [],
      gaps: [],
      meta: { ...keywordMeta('key_missing'), no_required_skills: true },
    })
    const { wrapper } = await mountDetail()

    await wrapper.find('[data-testid="match-run"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('还没有填写技能要求')
    expect(wrapper.find('.match-tiers').exists()).toBe(false)
  })

  /**
   * 2026-11-07 人类负责人裁定「不用向客户说明技术上的设计」：权重构成、免加权原因、
   * 各计数、`weight_sum_used` 都只留在响应里。这一条守的是**别把它们显示出来**，
   * 而 `evidence_mode` / `degrade_reason` 那两句不在此列（作用域见 ADR-013 §7.5）。
   */
  /**
   * `score = null` 的**唯一**来路（老板 2026-11-08 裁定：核心 term 不在就整个不评估）。
   *
   * 现在是**双向**等价：`score is null ⇔ no_required_skills`。岗位有要求技能时 coverage 必然参与
   * （哪怕算出来是 0），所以「null 但岗位填了要求」这种载荷后端发不出来——
   * 由 `tests/test_match_scoring.py::test_score_is_null_exactly_when_the_job_has_no_required_skills`
   * 用 81 组合枚举钉住。这里不为发不出来的载荷写用例，也不给 `null` 配第二句文案
   * （2026-11-08 真浏览器里抓到过同一屏说两遍）。
   */
  it('score=null（F1:A）：显示「—」而不是 0%，同一句说明只出现一次', async () => {
    matchPayload = makeMatchPayload({
      score: null,
      profile_id: null,
      profile_title: null,
      meta: keywordMeta(null, { note: 'not_evaluated', no_required_skills: true }),
    })
    const { wrapper } = await mountDetail()
    await wrapper.find('[data-testid="match-run"]').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('[data-testid="match-score"]').text()).toBe('—')
    expect(wrapper.text()).not.toContain('0%')
    // 「没有档案」这件事由 `.match-source` 那行说，且只说一次：
    // 曾经这里还有第二句同义反复，浏览器里连着 `.empty` 一共三行。
    expect(occurrences(wrapper.text(), '你还没有保存的简历')).toBe(1)
  })

  it('score=null 且岗位没填技能要求：只说一句，且不骗用户去补东西', async () => {
    matchPayload = makeMatchPayload({
      score: null,
      meta: keywordMeta(null, { note: 'not_evaluated', no_required_skills: true }),
    })
    const { wrapper } = await mountDetail()
    await wrapper.find('[data-testid="match-run"]').trigger('click')
    await flushPromises()

    expect(occurrences(wrapper.text(), '这个岗位还没有填写技能要求')).toBe(1)
    expect(wrapper.text()).not.toContain('暂时没有可比对的要求条目')
    expect(wrapper.text()).not.toContain('先去「我的简历」填一份')
  })

  it('技术构成与计数不进界面：分数之外只有三档清单和那两句能修的说明', async () => {
    matchPayload = makeMatchPayload({ meta: keywordMeta('key_missing') })
    const { wrapper } = await mountDetail()
    await wrapper.find('[data-testid="match-run"]').trigger('click')
    await flushPromises()

    const text = wrapper.text()
    expect(text).not.toContain('0.65')
    expect(text).not.toContain('weight_sum_used')
    expect(text).not.toContain('candidate_pairs')
    expect(text).not.toContain('not_measurable_v1')
    expect(text).not.toContain('权重')
    expect(text).not.toContain('relevance')
    // 反向护栏：`terms[].value` 这些数字确实**没有**被误当成界面元素渲染出来。
    expect(text).not.toContain('0.45')
  })
})

/**
 * 审核流（`ADR-024`）：谁看得见哪一组按钮，以及按钮背后打的是哪条路径。
 *
 * 这一组用例的共同毛病都是「界面上什么都不会报错」：
 * 创建者自己点出 `published`（少了审核）、学生看见审核面板（点了 403）、
 * 驳回理由丢掉（创建者不知道要改什么）。所以每条都同时断言**看得见的文案**
 * 与**发出去的请求**，两边任一改动都会变红。
 */
describe('审核流（ADR-024）：提交审核与审核面板', () => {
  /** 把当前账号换成「有 job:review 的同组织老师」。 */
  function actAsReviewer(): void {
    useAuthStore().permissions = ['job:review']
  }

  it('创建者的草稿只有「提交审核」，点了打 POST /jobs/{id}/submit-review 并落到待审', async () => {
    const { wrapper } = await mountDetail({ status: 'draft' })

    expect(wrapper.find('[data-testid="submit-review-button"]').exists()).toBe(true)
    // 「发布」这个词从一个创建者能点的按钮上彻底消失：那是 ADR-024 §2.6 删掉的直发通道。
    expect(wrapper.find('.owner-actions').text()).not.toContain('发布')
    // 创建者自己也看不见审核面板——哪怕他碰巧有 `job:review`（下面那条单独钉）。
    expect(wrapper.find('.review-panel').exists()).toBe(false)

    await wrapper.find('[data-testid="submit-review-button"]').trigger('click')
    await flushPromises()

    expect(callsFor('post', '/jobs/job-42/submit-review')).toHaveLength(1)
    expect(wrapper.text()).toContain('已提交审核')
    expect(wrapper.text()).toContain('待审核')
  })

  it('创建者即便有 `job:review` 也不出审核面板（自审的前端半边，后端 409 兜底）', async () => {
    actAsReviewer()
    const { wrapper } = await mountDetail({ status: 'pending_review' })

    expect(wrapper.find('.review-panel').exists()).toBe(false)
    expect(wrapper.find('[data-testid="approve-button"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="reject-button"]').exists()).toBe(false)
  })

  it('没有 `job:review` 的账号（学生）对别人的待审岗位既看不到面板也看不到按钮', async () => {
    const { wrapper } = await mountDetail({ status: 'pending_review', created_by_user_id: 'other-user' })

    expect(wrapper.find('.review-panel').exists()).toBe(false)
    // 别人的岗位连创建者操作区都不该有。
    expect(wrapper.find('.owner-actions').exists()).toBe(false)
  })

  it('审核人看到面板：通过打 POST /review + decision=approve，且不带 note 键', async () => {
    actAsReviewer()
    const { wrapper } = await mountDetail({ status: 'pending_review', created_by_user_id: 'other-user' })

    expect(wrapper.find('.review-panel').exists()).toBe(true)

    await wrapper.find('[data-testid="approve-button"]').trigger('click')
    await flushPromises()

    expect(callsFor('post', '/jobs/job-42/review')).toHaveLength(1)
    expect(wrapper.text()).toContain('已通过')
  })

  it('驳回把理由作为 note 发出去，并把理由留在页面上给创建者看', async () => {
    actAsReviewer()
    const { wrapper } = await mountDetail({ status: 'pending_review', created_by_user_id: 'other-user' })

    await wrapper.find('[data-testid="review-note"]').setValue('岗位职责写得太笼统')
    await wrapper.find('[data-testid="reject-button"]').trigger('click')
    await flushPromises()

    const sent = lastReviewBody()
    expect(sent).toEqual({ decision: 'reject', note: '岗位职责写得太笼统' })
    expect(wrapper.text()).toContain('已驳回')

    // 驳回后的详情（适配器已把 status 改成 rejected）必须把理由显示出来。
    // 重新挂载一次，模拟创建者刷新页面看到的那一屏。
    const after = await mountDetail({
      status: 'rejected',
      created_by_user_id: 'other-user',
      review_note: '岗位职责写得太笼统',
    })
    expect(after.wrapper.text()).toContain('驳回理由：岗位职责写得太笼统')
  })

  it('岗位已过期：后端 409 那句话原样显示，页面不许自己编一句日期理由', async () => {
    actAsReviewer()
    reviewFailure = {
      status: 409,
      code: 'JOB_EXPIRED',
      message: '岗位已过截止时间，请先更新截止日期再审核。',
    }
    const { wrapper } = await mountDetail({ status: 'pending_review', created_by_user_id: 'other-user' })

    await wrapper.find('[data-testid="approve-button"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('岗位已过截止时间，请先更新截止日期再审核。')
    expect(wrapper.text()).not.toContain('已通过')
    // 反向护栏：过期判据只有一份（`app/core/deadline.py`），前端不许出现第二套日期判断的产物。
    expect(wrapper.text()).not.toContain('is_expired')
  })

  it('没有组织：400 的「请先绑定组织」也走同一条受信文案通道', async () => {
    reviewFailure = {
      status: 400,
      code: 'JOB_ORGANIZATION_REQUIRED',
      message: '发布岗位前请先绑定组织。',
    }
    const { wrapper } = await mountDetail({ status: 'draft' })

    await wrapper.find('[data-testid="submit-review-button"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('发布岗位前请先绑定组织。')
    expect(wrapper.text()).not.toContain('已提交审核')
  })
})
