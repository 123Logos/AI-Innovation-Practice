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

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  calls.push({ method, url })

  if (url === '/profiles/me') {
    return Promise.resolve(
      envelope(config, {
        data: { profile: { skills: [{ id: 'mine-1', name: ' FastAPI ' }] } },
        meta: { request_id: 'req-test' },
      }),
    )
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
      plugins: [pinia, router],
      stubs: { AppTopBar: true },
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

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  calls = []
  failDelete = false
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
    expect(wrapper.text()).not.toContain('模型把握 0.00')
  })

  it('`confidence` 有值就显示「模型把握」两位小数，`null` 时那一格整个不出现', async () => {
    const { wrapper } = await mountDetail()

    // 后端 `DECIMAL(5,4)` 读回来是字符串 `"0.9000"`，展示裁到两位（理由见 `confidenceLabel`）。
    expect(wrapper.text()).toContain('模型把握 0.90')
    expect(wrapper.text()).not.toContain('0.9000')
    // 第二条没有把握 → 页面上只有一句「模型把握」，不是两句。
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

  it('「你已具备」按归一化命中（档案里是 ` FastAPI `、岗位写的是 FastAPI）', async () => {
    const { wrapper } = await mountDetail({
      skills: [
        skillRow('s1', 'FastAPI'),
        skillRow('s2', 'Kubernetes', { evidence_text: null, confidence: null, origin: 'unverified' }),
      ],
    })

    const mine = wrapper.findAll('.badge--mine')
    expect(mine).toHaveLength(1)
    expect(mine[0]?.text()).toBe('你已具备')
    // 只读比对：不写任何表（人岗匹配仍冻结，ADR-011 2.E）。
    expect(callsFor('post')).toEqual([])
    expect(callsFor('put')).toEqual([])
    expect(callsFor('delete')).toEqual([])
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
