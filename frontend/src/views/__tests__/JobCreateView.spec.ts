/**
 * 岗位录入 / 编辑页的视图单测（C2，ADR-011 议题 3.B + ADR-010 §5 的草稿态确认制）。
 *
 * store 那一层已经验过合并与文案（`stores/__tests__/job.spec.ts`），这里只验**接线**：
 *
 * 1. 按钮的可用性真的跟着 JD 与在途状态走（禁用做假的表现为「点了没反应」）；
 * 2. 提取成功之后，页面上**一次 `POST /jobs` 都没有发出去**（请求计数在这里数，
 *    不在 store 里数——「提取完顺手保存」这种代码只可能出现在视图的回调里）；
 * 3. 三句核验计数与失败文案真的落到 DOM 上，而不是算好了没人渲染；
 * 4. `evidence` 默认收起（不挤占主视觉），点一下能看到；
 * 5. 编辑态先 GET 回填、保存打 PUT 而不是 POST（打错了就是把已有岗位另存成新岗位）。
 *
 * 路由用一份本地 memory router，而不是 `@/router`：后者带全局守卫，
 * 每次 `push` 都会去请 `/auth/me`，那是「登录态怎么守」的另一条验收线，
 * 这里要的是「跳去了哪一页」这个事实本身（同一先例见 `AppTopBar.spec.ts` 的文件头）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { createMemoryHistory, createRouter, type Router, type RouteRecordRaw } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { http } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import JobCreateView from '@/views/JobCreateView.vue'

const OWN_USER_ID = 'user-under-test'
const BLANK = { template: '<div />' }

/** 发出去的请求（方法 + 路径 + 请求体），用来数「有没有偷偷写库」。 */
interface Call {
  method: string
  url: string
  body: unknown
}

let calls: Call[] = []
let savedAdapter: typeof http.defaults.adapter

/** 提取端点的回包：由用例按需改成失败形状。 */
let extractResponse: 'ok' | 'key-missing' | 'key-invalid' | 'unavailable' | 'truncated' = 'ok'

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

function jobFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 'job-42',
    company_name: '示例科技',
    title: '后端实习',
    location: '上海',
    employment_type: 'internship',
    description: '负责后端接口开发，熟练掌握 Python 与 FastAPI。',
    salary_min: null,
    salary_max: null,
    deadline: null,
    status: 'draft',
    created_by_user_id: OWN_USER_ID,
    created_at: '2026-11-05T10:00:00+08:00',
    updated_at: '2026-11-05T10:00:00+08:00',
    deleted_at: null,
    experience_required: null,
    extract_model: 'deepseek-flash',
    extract_prompt_version: 'v1',
    extracted_at: '2026-11-05T10:00:00+08:00',
    skills: [skillRow('s1', 'Python'), skillRow('s2', '沟通能力强', { type: 'soft' })],
    is_expired: false,
    ...overrides,
  }
}

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

/** 三种 AI 失败的真实信封（第 7 情形多带一个**外层** `meta.finish_reason`）。 */
function extractFailureBody(status: number, code: string, message: string, finishReason?: string): unknown {
  return {
    error: { code, message, details: {}, retryable: status === 502 },
    meta: finishReason ? { request_id: 'req-test', finish_reason: finishReason } : { request_id: 'req-test' },
  }
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  const body = typeof config.data === 'string' && config.data ? JSON.parse(config.data) : config.data
  calls.push({ method, url, body })

  if (method === 'post' && url === '/jobs/extract-skills') {
    if (extractResponse === 'key-missing') {
      return Promise.reject(
        new AxiosError('缺 Key', 'ERR_BAD_REQUEST', config, {}, envelope(config, extractFailureBody(400, 'AI_KEY_MISSING', '请先配置 DeepSeek API Key。'), 400)),
      )
    }
    if (extractResponse === 'key-invalid') {
      return Promise.reject(
        new AxiosError('Key 无效', 'ERR_BAD_REQUEST', config, {}, envelope(config, extractFailureBody(401, 'AI_KEY_INVALID', 'Key 无效或已欠费。'), 401)),
      )
    }
    if (extractResponse === 'unavailable') {
      return Promise.reject(
        new AxiosError('上游挂了', 'ERR_BAD_RESPONSE', config, {}, envelope(config, extractFailureBody(502, 'AI_SERVICE_UNAVAILABLE', 'AI 暂时不可用，请稍后重试。'), 502)),
      )
    }
    if (extractResponse === 'truncated') {
      return Promise.reject(
        new AxiosError('被切断', 'ERR_BAD_RESPONSE', config, {}, envelope(config, extractFailureBody(502, 'AI_SERVICE_UNAVAILABLE', 'AI 输出的技能条目过多，被服务端的 token 上限切断了。', 'length'), 502)),
      )
    }
    return Promise.resolve(
      envelope(config, {
        data: {
          // 三条候选：Python 与表单里已有的那条重名（应被跳过）、其余两条应进表单。
          skills: [
            { name: 'Python', type: 'hard', evidence: '熟练掌握 Python', confidence: 0.9 },
            { name: 'FastAPI', type: 'hard', evidence: '熟悉 FastAPI', confidence: 0.85 },
            { name: 'Docker', type: null, evidence: '熟悉 Docker 部署', confidence: null },
          ],
          meta: {
            model: 'deepseek-flash',
            prompt_version: 'v1',
            received_count: 20,
            dropped_count: 14,
            deduped_count: 1,
            truncated: true,
            finish_reason: 'stop',
          },
        },
        meta: { request_id: 'req-test' },
      }),
    )
  }

  if (method === 'post' && url === '/jobs') {
    return Promise.resolve(
      envelope(config, { data: { job: jobFixture({ id: 'job-new', status: 'draft' }) }, meta: { request_id: 'r' } }, 201),
    )
  }

  if (method === 'put' && /^\/jobs\/[^/]+$/.test(url)) {
    const sent = (body ?? {}) as Record<string, unknown>
    return Promise.resolve(
      envelope(config, { data: { job: jobFixture({ title: String(sent.title ?? '') }) }, meta: { request_id: 'r' } }),
    )
  }

  if (method === 'get' && url === '/profiles/me') {
    return Promise.resolve(envelope(config, { data: { profile: null }, meta: { request_id: 'r' } }))
  }

  // 详情（编辑态回填）与其余 GET。
  return Promise.resolve(envelope(config, { data: { job: jobFixture() }, meta: { request_id: 'r' } }))
}

function makeRouter(): Router {
  const routes: RouteRecordRaw[] = [
    { path: '/jobs', name: 'job-board', component: BLANK },
    { path: '/jobs/new', name: 'job-create', component: BLANK },
    { path: '/jobs/:id', name: 'job-detail', component: BLANK },
  ]
  return createRouter({ history: createMemoryHistory(), routes })
}

async function mountView(props: Record<string, unknown> = {}) {
  const router = makeRouter()
  await router.push('/jobs/new')
  const wrapper = mount(JobCreateView, {
    props,
    global: {
      plugins: [createPinia(), router],
      // 顶栏自带两个 document 监听与一堆简历 store 依赖，与本页要验的东西无关。
      stubs: { AppTopBar: true },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

function callsFor(method: string, url?: string): Call[] {
  return calls.filter((call) => call.method === method && (url === undefined || call.url === url))
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  calls = []
  extractResponse = 'ok'
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
  http.defaults.adapter = savedAdapter
})

describe('录入页的「✨ AI 提取技能」', () => {
  it('JD 为空时按钮禁用；粘进 JD 之后才可点', async () => {
    const { wrapper } = await mountView()
    const button = () => wrapper.find('[data-testid="extract-button"]')

    expect(button().attributes('disabled')).toBeDefined()

    await wrapper.find('[data-testid="jd-input"]').setValue('负责后端接口开发，熟练掌握 Python。')
    expect(button().attributes('disabled')).toBeUndefined()
  })

  it('提取成功后候选进了技能区，而页面**一次 `POST /jobs` 都没有发**', async () => {
    const { wrapper } = await mountView()
    await wrapper.find('[data-testid="jd-input"]').setValue('负责后端接口开发，熟练掌握 Python 与 FastAPI。')
    // 先放一行同名技能，验证「重名跳过」这条文案是从真实表单状态算出来的。
    await wrapper.find('.skill-name').setValue('Python')

    await wrapper.find('[data-testid="extract-button"]').trigger('click')
    await flushPromises()

    // 本轮的红线：提取是纯计算，写库只能由用户点「保存岗位」触发。
    expect(callsFor('post', '/jobs/extract-skills')).toHaveLength(1)
    expect(callsFor('post', '/jobs')).toHaveLength(0)
    expect(callsFor('put')).toEqual([])
    expect(callsFor('delete')).toEqual([])

    // 三条候选里 Python 重名被跳过，FastAPI / Docker 落进表单。
    const names = wrapper.findAll('.skill-name').map((input) => (input.element as HTMLInputElement).value)
    expect(names).toEqual(['Python', 'FastAPI', 'Docker'])
    expect(wrapper.find('[data-testid="extract-notice"]').text()).toContain('已把 2 条候选技能')
    // 而且那句必须说清「还没保存」——否则用户会以为点提取就已经存好了。
    expect(wrapper.find('[data-testid="extract-notice"]').text()).toContain('点「保存岗位」才会写入数据库')
  })

  it('三句核验计数各占一行；0 的那几句整块不出现', async () => {
    const { wrapper } = await mountView()
    await wrapper.find('[data-testid="jd-input"]').setValue('熟练掌握 Python。')
    // 先手打一条 Python：让「前端跳过重名」这一句有真实的来源（AI 候选里也有一条 Python）。
    await wrapper.find('.skill-name').setValue('Python')
    await wrapper.find('[data-testid="extract-button"]').trigger('click')
    await flushPromises()

    const lines = wrapper.findAll('[data-testid="count-line"]').map((line) => line.text())
    expect(lines).toEqual([
      'AI 给出 20 条，其中 14 条无法在 JD 原文中找到依据，已剔除',
      '另有 1 条重名已合并',
      '模型给出的条目超过 15 条，只保留了前 15 条',
    ])
    // 三句是三个独立的 DOM 节点：并成一个数在这里会当场对不上。
    expect(lines).toHaveLength(3)
    expect(lines[0]).not.toContain('重名')
    // 「被合并掉 1 条」（模型自己重名）与「前端跳过 1 条」（与表单重名）必须同时可见、各说各的。
    expect(wrapper.find('[data-testid="extract-counts"]').text()).toContain('已跳过 1 条与现有技能重名')
    // 反向护栏：不存在「共剔除 15 条」这种把两个计数加起来的写法。
    expect(wrapper.find('[data-testid="extract-counts"]').text()).not.toContain('共')
  })

  it('三个计数全为 0 时，计数区一个节点都不渲染', async () => {
    // 把回包改成「模型给了 3 条、一条没剔、没重名、没截断」。
    http.defaults.adapter = async (config) =>
      envelope(config, {
        data: {
          skills: [{ name: 'Rust', type: 'hard', evidence: '熟悉 Rust', confidence: 0.9 }],
          meta: {
            model: 'deepseek-flash',
            prompt_version: 'v1',
            received_count: 1,
            dropped_count: 0,
            deduped_count: 0,
            truncated: false,
            finish_reason: 'stop',
          },
        },
        meta: { request_id: 'req-test' },
      })

    const { wrapper } = await mountView()
    await wrapper.find('[data-testid="jd-input"]').setValue('熟悉 Rust。')
    await wrapper.find('[data-testid="extract-button"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="extract-counts"]').exists()).toBe(false)
    // 候选还是照常进表单（0 计数不等于 0 结果）。
    expect(wrapper.findAll('.skill-name')).toHaveLength(2)
  })

  it('请求在途时按钮禁用并显示 Loading，防住连点烧额度', async () => {
    const { wrapper } = await mountView()
    await wrapper.find('[data-testid="jd-input"]').setValue('熟练掌握 Python。')

    // 让提取故意慢一点：adapter 挂在一个可控的 promise 上，才能观察到「在途」那一帧。
    // 用数组装 resolve 而不是 `let release: … | null = null`：后者在闭包里赋值，
    // 编译器的控制流分析不认为调用点已经赋过值，会把它收窄成 `null` 并报「not callable」。
    const release: ((value: AxiosResponse) => void)[] = []
    http.defaults.adapter = (config) => {
      calls.push({ method: 'post', url: config.url ?? '', body: undefined })
      return new Promise<AxiosResponse>((resolve) => {
        release.push(resolve)
      })
    }

    const click = wrapper.find('[data-testid="extract-button"]').trigger('click')
    // 必须 flush 到请求真的落进 adapter：拦截器链有好几层微任务，
    // 上一版这里只 `await Promise.resolve()` 就去调 release，那是一次**空操作**——
    // 「在途」的断言照样成立，但请求永远不会被放行，于是这条用例从没验过恢复态。
    await flushPromises()
    const button = wrapper.find('[data-testid="extract-button"]')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toContain('提取中')
    expect(button.attributes('aria-busy')).toBe('true')
    // 「保存岗位」在途时也必须禁用：否则用户能在候选还没回来时先把旧表单存下去。
    expect(wrapper.find('[data-testid="save-job"]').attributes('disabled')).toBeDefined()

    // 只可能有一次在途请求；多于一次说明按钮被点了两下，那就不是这一条用例在验的事。
    expect(release).toHaveLength(1)
    release[0]?.(
      envelope(
        { headers: {} } as unknown as InternalAxiosRequestConfig,
        { data: { skills: [], meta: {} }, meta: { request_id: 'r' } },
      ),
    )
    await click
    await flushPromises()

    // 放行之后必须看得见「不在途」：这一句才是上面那套在途断言的反面，
    // 缺了它，按钮卡在 Loading 也能把前面几条全绿。
    // `aria-busy` 是常驻绑定，空闲时是字符串 "false"（合规 ARIA），所以这里比的是值而不是存在性。
    const settled = wrapper.find('[data-testid="extract-button"]')
    expect(settled.text()).not.toContain('提取中')
    expect(settled.attributes('aria-busy')).toBe('false')
  })

  it('失败一定显式提示，并按 code 给不同的下一步', async () => {
    const cases: Array<[typeof extractResponse, string, string]> = [
      ['key-missing', 'AI 设置', '填好'],
      ['key-invalid', 'AI 设置', '无效'],
      ['unavailable', '稍后重试', '没有被保存'],
      ['truncated', '把 JD 里真正想要的技能写清楚', '切断了'],
    ]

    for (const [kind, mustInclude, mustIncludeToo] of cases) {
      extractResponse = kind
      const { wrapper } = await mountView()
      await wrapper.find('[data-testid="jd-input"]').setValue('熟练掌握 Python。')
      await wrapper.find('[data-testid="extract-button"]').trigger('click')
      await flushPromises()

      const alert = wrapper.find('[data-testid="extract-error"]')
      // 不是「什么都不显示」，也不是把候选清空当没事发生。
      expect(alert.exists(), `提取失败（${kind}）必须有可见提示`).toBe(true)
      expect(alert.attributes('role')).toBe('alert')
      expect(alert.text()).toContain(mustInclude)
      expect(alert.text()).toContain(mustIncludeToo)
      // 失败同样不写库。
      expect(callsFor('post', '/jobs')).toHaveLength(0)
      wrapper.unmount()
    }
  })

  it('第 7 情形与普通 502 落在两句不同的话上（判据是 finish_reason，不是文案）', async () => {
    // 两条用例的 `error.message` 都含「切断」二字，如果实现按文案分支就会给出同一个动作。
    extractResponse = 'unavailable'
    const first = await mountView()
    await first.wrapper.find('[data-testid="jd-input"]').setValue('熟练掌握 Python。')
    await first.wrapper.find('[data-testid="extract-button"]').trigger('click')
    await flushPromises()
    const ordinaryText = first.wrapper.find('[data-testid="extract-error"]').text()
    first.wrapper.unmount()

    extractResponse = 'truncated'
    const second = await mountView()
    await second.wrapper.find('[data-testid="jd-input"]').setValue('熟练掌握 Python。')
    await second.wrapper.find('[data-testid="extract-button"]').trigger('click')
    await flushPromises()
    const lengthText = second.wrapper.find('[data-testid="extract-error"]').text()
    second.wrapper.unmount()

    expect(ordinaryText).not.toBe(lengthText)
    expect(lengthText).toContain('切断了')
    // 普通 502 绝不该教用户「换一种 JD 写法」——那会让人以为是自己粘错了内容。
    expect(ordinaryText).not.toContain('把 JD 里真正想要的技能写清楚')
  })

  it('候选的原文依据默认收起，点一下才展开；技能名上留了 hover 提示', async () => {
    const { wrapper } = await mountView()
    await wrapper.find('[data-testid="jd-input"]').setValue('熟练掌握 Python 与 FastAPI。')
    await wrapper.find('[data-testid="extract-button"]').trigger('click')
    await flushPromises()

    // 收起状态：依据不在 DOM 里（不是靠 CSS 藏起来，那样读屏与自动化会当成已呈现）。
    expect(wrapper.text()).not.toContain('原文依据：熟悉 FastAPI')
    const toggle = wrapper.findAll('[data-testid="evidence-toggle"]')
    // 三条候选都有依据 → 三个按钮；那个空行没有，所以不该长出第四个。
    expect(toggle).toHaveLength(3)
    expect(toggle[1]?.attributes('aria-expanded')).toBe('false')

    await toggle[1]?.trigger('click')
    expect(wrapper.text()).toContain('原文依据：熟悉 FastAPI')
    expect(wrapper.text()).toContain('模型把握 0.85')
    // `confidence: null` 的那条（Docker）不该显示「模型把握」，也不该显示 0。
    expect(wrapper.text()).not.toContain('模型把握 null')
    expect(toggle[1]?.attributes('aria-expanded')).toBe('true')

    // 名字上的 hover 提示（`title`）是同一份依据的另一种呈现，不额外占版面。
    const nameInputs = wrapper.findAll('.skill-name')
    // 四行：空行 + Python + FastAPI + Docker（空行没有依据，所以不给 title）。
    expect(nameInputs[0]?.attributes('title')).toBeUndefined()
    expect(nameInputs[3]?.attributes('title')).toBe('原文依据：熟悉 Docker 部署')
  })

  it('点「保存岗位」才发那一次 POST；技能请求体带上确认过的 evidence / confidence', async () => {
    const { wrapper } = await mountView({ mode: 'create' })
    await wrapper.find('[data-testid="jd-input"]').setValue('负责后端接口开发，熟练掌握 Python 与 FastAPI。')
    // 手打一条没有依据的 Python：它既是必填项之一，也让候选里的 Python 走「重名跳过」。
    await wrapper.find('.skill-name').setValue('Python')
    await wrapper.find('[data-testid="extract-button"]').trigger('click')
    await flushPromises()
    expect(callsFor('post', '/jobs')).toHaveLength(0)

    const textInputs = wrapper.findAll('input[type="text"]')
    await textInputs[0].setValue('示例科技')
    await textInputs[1].setValue('后端实习')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(callsFor('post', '/jobs')).toHaveLength(1)
    const body = callsFor('post', '/jobs')[0]?.body as { skills: Record<string, unknown>[] }
    // 三行有名字的技能（空行被 `meaningfulSkills` 滤掉）。
    expect(body.skills).toHaveLength(3)
    // 手打的那条：没有依据就只有两个键，空串 `type` 转 `null`。
    expect(body.skills[0]).toEqual({ name: 'Python', type: null })
    // 候选确认下来的两条：`evidence` 已改名成 `evidence_text`，把握原样带上。
    expect(body.skills[1]).toEqual({
      name: 'FastAPI',
      type: 'hard',
      evidence_text: '熟悉 FastAPI',
      confidence: 0.85,
    })
    // `confidence: null` 不该被写成键。
    expect(body.skills[2]).toEqual({ name: 'Docker', type: null, evidence_text: '熟悉 Docker 部署' })
    expect(body, 'status 由服务端写死，请求体不该带它').not.toHaveProperty('status')
  })

  it('「放弃本次提取」撤掉候选且一个请求都不发；岗位没有被保存', async () => {
    const { wrapper } = await mountView()
    await wrapper.find('[data-testid="jd-input"]').setValue('熟练掌握 Python 与 FastAPI。')
    await wrapper.find('[data-testid="extract-button"]').trigger('click')
    await flushPromises()
    // 空行 + 三条候选。
    expect(wrapper.findAll('.skill-name')).toHaveLength(4)

    const before = calls.length
    await wrapper.find('[data-testid="extract-discard"]').trigger('click')
    await flushPromises()

    // 三条候选都一字未动 → 全部撤回，表单回到一行空行。
    expect(wrapper.findAll('.skill-name')).toHaveLength(1)
    expect(wrapper.find('[data-testid="extract-notice"]').text()).toContain('岗位没有被保存')
    expect(calls.length).toBe(before)
    // 计数说明也一起消失（草稿已清）。
    expect(wrapper.find('[data-testid="extract-counts"]').exists()).toBe(false)
  })

  it('取消（不保存）只跳转，不发任何请求', async () => {
    const { wrapper, router } = await mountView()
    await wrapper.find('[data-testid="jd-input"]').setValue('熟练掌握 Python。')
    const before = calls.length

    await wrapper.find('[data-testid="cancel-button"]').trigger('click')
    await flushPromises()

    expect(calls.length).toBe(before)
    expect(router.currentRoute.value.name).toBe('job-board')
  })

  it('换 JD 后上一批的计数说明消失（那七个数字说的是喂给模型的旧 JD）', async () => {
    const { wrapper } = await mountView()
    await wrapper.find('[data-testid="jd-input"]').setValue('熟练掌握 Python。')
    await wrapper.find('[data-testid="extract-button"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="extract-counts"]').exists()).toBe(true)

    await wrapper.find('[data-testid="jd-input"]').setValue('完全换了一份 JD')
    await flushPromises()
    // 说明没了……
    expect(wrapper.find('[data-testid="extract-counts"]').exists()).toBe(false)
    // ……但已经落进表单的候选行留着（那是用户看过的内容）。空行 + 三条候选。
    expect(wrapper.findAll('.skill-name')).toHaveLength(4)
  })
})

describe('编辑态（`/jobs/:id/edit`，同一个组件的另一种模式）', () => {
  it('先 GET 回填表单，已有技能的 evidence / confidence 带回来了', async () => {
    const { wrapper } = await mountView({ mode: 'edit', jobId: 'job-42' })

    expect(callsFor('get', '/jobs/job-42')).toHaveLength(1)
    expect(wrapper.find('h1').text()).toBe('编辑岗位')

    const names = wrapper.findAll('.skill-name').map((input) => (input.element as HTMLInputElement).value)
    expect(names).toEqual(['Python', '沟通能力强'])
    // 「可回溯原文」的那条带着依据 → 页面上有展开按钮，且展开能看到原文片段。
    const toggle = wrapper.find('[data-testid="evidence-toggle"]')
    expect(toggle.exists()).toBe(true)
    await toggle.trigger('click')
    expect(wrapper.text()).toContain('原文依据：熟练掌握 Python')
    expect(wrapper.text()).toContain('模型把握 0.9000')
  })

  it('点「保存修改」打 PUT /jobs/{id}，不打 POST /jobs（打错等于把岗位复制一份）', async () => {
    const { wrapper, router } = await mountView({ mode: 'edit', jobId: 'job-42' })
    // 基本信息块里前两个文本框是企业名与岗位名（技能名输入框排在它们后面）。
    const titleInput = wrapper.findAll('input[type="text"]')[1]
    await titleInput.setValue('后端实习（改）')

    // `data-testid="save-job"` 是 `type="submit"`，jsdom 不会因按钮 click 触发表单提交，
    // 所以这里走表单的 submit 事件——那正是用户点按钮时浏览器做的事。
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(callsFor('post', '/jobs')).toHaveLength(0)
    expect(callsFor('put', '/jobs/job-42')).toHaveLength(1)
    const body = callsFor('put', '/jobs/job-42')[0]?.body as { title: string; skills: Record<string, unknown>[] }
    expect(body.title).toBe('后端实习（改）')
    // PUT 是整体覆盖：技能必须全量回传，漏一条就等于在库里删一条。
    expect(body.skills.map((skill) => skill.name)).toEqual(['Python', '沟通能力强'])
    expect(body.skills[0]).toMatchObject({ evidence_text: '熟练掌握 Python', confidence: '0.9000' })
    expect(router.currentRoute.value.name).toBe('job-detail')
  })

  it('按钮文案是「保存修改」，且编辑态的「不保存返回」回详情页而不是列表', async () => {
    const { wrapper, router } = await mountView({ mode: 'edit', jobId: 'job-42' })

    expect(wrapper.find('[data-testid="save-job"]').text()).toBe('保存修改')
    expect(wrapper.find('[data-testid="cancel-button"]').text()).toBe('不保存返回')

    await wrapper.find('[data-testid="cancel-button"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('job-detail')
  })

  it('回填失败（他人草稿 404）时保存按钮禁用，不让人把 404 打成一次 PUT', async () => {
    http.defaults.adapter = async (config) =>
      Promise.reject(
        new AxiosError(
          'not found',
          'ERR_BAD_REQUEST',
          config,
          {},
          envelope(config, { error: { code: 'CLIENT_ERROR', message: '岗位不存在。', details: {}, retryable: false }, meta: { request_id: 'r' } }, 404),
        ),
      )

    const { wrapper } = await mountView({ mode: 'edit', jobId: 'job-404' })
    await flushPromises()

    expect(wrapper.text()).toContain('这个岗位不存在、是别人的草稿，或已经在回收站里。')
    expect(wrapper.find('[data-testid="save-job"]').attributes('disabled')).toBeDefined()
    expect(callsFor('put')).toEqual([])
  })
})
