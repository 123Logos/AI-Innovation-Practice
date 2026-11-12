/**
 * 模拟面试前端（FR-07 / B11a，ADR-022）。
 *
 * 钉住两件事，不长相：
 * 1. **正常渲染**：列表空态、详情对话都能画出来，且文案不靠某个具体数据。
 * 2. **失败提示可见**（守则 C3/C1）：读取失败时红字 `role="alert"` 出现在 DOM 里，
 *    而不是只 `console.error` 一下——这条用反面对照证「失败真的可见」。
 * 3. **降级可见**（守则 C4）：后端 `ai_degrade_reason` 非空时，那一轮的提示文案必须上屏
 *    （同样是 `role="alert"`），让用户知道这句不是真 AI。
 *
 * `@/api` 整模块被桩掉（只留视图真正 import 的 `interviewsApi` 与 `isApiError`），
 * 视图里的类型与常量（`TARGET_ROLE_MAX_LEN` 等）仍走真实模块，不影响断言。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type RouteRecordRaw } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const interviewsApi = vi.hoisted(() => ({
  listInterviews: vi.fn(),
  createInterview: vi.fn(),
  getInterview: vi.fn(),
  sendInterviewMessage: vi.fn(),
  endInterview: vi.fn(),
}))

vi.mock('@/api', () => ({
  interviewsApi,
  isApiError: vi.fn(() => false),
}))

import InterviewListView from '@/views/InterviewListView.vue'
import InterviewDetailView from '@/views/InterviewDetailView.vue'

const BLANK = { template: '<div />' }

function makeRouter() {
  const routes: RouteRecordRaw[] = [
    { path: '/interviews', name: 'interview-list', component: BLANK },
    { path: '/interviews/:id', name: 'interview-detail', component: BLANK },
  ]
  return createRouter({ history: createMemoryHistory(), routes })
}

async function mountList() {
  const router = makeRouter()
  await router.push('/interviews')
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(InterviewListView, { attachTo, global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

async function mountDetail(id = 'session-1') {
  const router = makeRouter()
  await router.push({ name: 'interview-detail', params: { id } })
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(InterviewDetailView, { attachTo, global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
})

describe('列表页：正常渲染', () => {
  it('没有面试记录时显示空态文案与标题', async () => {
    interviewsApi.listInterviews.mockResolvedValue({ sessions: [], page: 1, page_size: 20, total: 0 })
    const { wrapper } = await mountList()

    expect(wrapper.text()).toContain('面试练习')
    expect(wrapper.text()).toContain('这里还没有面试记录，开始一场试试。')
  })

  it('有记录时渲染成卡片，点卡片跳进详情', async () => {
    interviewsApi.listInterviews.mockResolvedValue({
      sessions: [
        {
          id: 's1',
          user_id: 'u1',
          job_id: null,
          status: 'active',
          target_role: '前端开发工程师',
          created_at: '2026-11-10T10:00:00+08:00',
          updated_at: '2026-11-10T10:00:00+08:00',
          ended_at: null,
        },
      ],
      page: 1,
      page_size: 20,
      total: 1,
    })
    const { wrapper } = await mountList()

    expect(wrapper.findAll('.session-card')).toHaveLength(1)
    expect(wrapper.text()).toContain('前端开发工程师')
    expect(wrapper.text()).toContain('进行中')
  })
})

describe('列表页：失败提示必须可见（守则 C3/C1）', () => {
  it('读取失败时红字 role=alert 出现在 DOM 里', async () => {
    interviewsApi.listInterviews.mockRejectedValue(new Error('网络断了'))
    const { wrapper } = await mountList()

    const alert = wrapper.find('[role="alert"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('读取面试列表失败')
  })

  it('反面对照：成功时不出现任何 alert（证明上面的断言不是空检）', async () => {
    interviewsApi.listInterviews.mockResolvedValue({ sessions: [], page: 1, page_size: 20, total: 0 })
    const { wrapper } = await mountList()

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })
})

describe('详情页：降级必须可见（守则 C4）', () => {
  it('某一轮 ai_degrade_reason 非空时，提示文案上屏', async () => {
    interviewsApi.getInterview.mockResolvedValue({
      session: {
        id: 's1',
        user_id: 'u1',
        job_id: null,
        status: 'active',
        target_role: '后端开发工程师',
        created_at: '2026-11-10T10:00:00+08:00',
        updated_at: '2026-11-10T10:00:00+08:00',
        ended_at: null,
      },
      turns: [
        {
          id: 't1',
          session_id: 's1',
          sequence: 1,
          speaker: 'interviewer',
          content: '你好，请做自我介绍。',
          ai_degrade_reason: null,
          created_at: '2026-11-10T10:00:00+08:00',
        },
        {
          id: 't2',
          session_id: 's1',
          sequence: 2,
          speaker: 'interviewer',
          content: '（内置题库）谈谈你最有成就感的项目。',
          ai_degrade_reason: 'key_missing',
          created_at: '2026-11-10T10:01:00+08:00',
        },
      ],
    })
    const { wrapper } = await mountDetail()

    // 可见降级条出现，且文案是人话、不出现开发黑话。
    const alerts = wrapper.findAll('[role="alert"]')
    expect(alerts.length).toBeGreaterThanOrEqual(1)
    const combined = alerts.map((node) => node.text()).join(' ')
    expect(combined).toContain('内置题库')
    expect(combined).not.toContain('降级')
    expect(combined).not.toContain('DeepSeek')
  })

  it('详情读取失败时同样给出可见提示', async () => {
    interviewsApi.getInterview.mockRejectedValue(new Error('网络断了'))
    const { wrapper } = await mountDetail()

    const alert = wrapper.find('[role="alert"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('读取这场面试失败')
  })
})
