/**
 * 能力测评与画像前端（FR-04 / B9，`ADR-028`）。
 *
 * 钉四件事，不钉长相：
 *
 * 1. **三段状态真的会走**：目录 → 答题 → 结果。比分数字由服务端给，界面不自己算。
 * 2. **一选就存**：点一个选项就发一次覆盖写（后端是「覆盖这几题」，不是清空整张卷），
 *    所以中途刷新不丢已答。断言的是**发出去的请求体**，不是内部状态。
 * 3. **概述必须标明出处**（守则 C4）：`source==='ai'` 才写「AI 分析」，
 *    `fallback` 必须写「自动整理的概述」并把 `note` 上屏——后端在没凭据时照旧给一段
 *    由标签拼的文字，把它标成 AI 就是撒谎。这条用**正反两例**对照，只测一边证明不了分流。
 * 4. **失败可见**（守则 C1/C3）：读取失败时红字 `role="alert"` 出现在 DOM 里。
 *
 * `@/api` 整模块被桩掉（只留 `assessmentsApi` 与 `isApiError`）；视图里的枚举与中文映射
 * （`TAG_STATUS_LABELS` / `SUMMARY_HEADINGS`）仍走真实模块，否则「标签叫什么」这件事
 * 会在测试里被桩成另一个答案。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const assessmentsApi = vi.hoisted(() => ({
  getCatalog: vi.fn(),
  startSession: vi.fn(),
  getSession: vi.fn(),
  saveAnswers: vi.fn(),
  submitSession: vi.fn(),
  abandonSession: vi.fn(),
  summarizeSession: vi.fn(),
  getProfile: vi.fn(),
}))

vi.mock('@/api', () => ({
  assessmentsApi,
  isApiError: vi.fn(() => false),
}))

import AssessmentView from '@/views/AssessmentView.vue'

const CATALOG = {
  bank_version: 'v1',
  total_questions: 3,
  skills: [
    { skill_key: 'python', skill_name: 'Python', question_count: 2 },
    { skill_key: 'sql', skill_name: 'SQL', question_count: 1 },
  ],
}

const QUESTIONS = [
  {
    id: 'q-1',
    skill_name: 'Python',
    prompt: 'tuple 创建后还能改吗？',
    options: [
      { key: 'A', label: '能' },
      { key: 'B', label: '不能' },
    ],
  },
]

const SESSION = {
  id: '11111111-1111-1111-1111-111111111111',
  student_profile_id: 'p-1',
  status: 'in_progress' as const,
  bank_version: 'v1',
  question_count: 3,
  correct_count: null,
  score: null,
  started_at: '2026-11-13T21:00:00+08:00',
  submitted_at: null,
  abandoned_at: null,
  ai_summary: null,
  ai_summary_source: null,
  ai_summary_at: null,
}

async function mountView() {
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(AssessmentView, { attachTo })
  await flushPromises()
  return wrapper
}

/** 走到答题态：目录已加载 → 点「开始测评」。 */
async function mountAnswering() {
  assessmentsApi.startSession.mockResolvedValue({ session: SESSION, questions: QUESTIONS })
  const wrapper = await mountView()
  const startButton = wrapper.findAll('button').find((item) => item.text().includes('开始测评'))
  expect(startButton, '目录态应当有「开始测评」').toBeTruthy()
  await startButton!.trigger('click')
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  // `start()` 会把进行中的会话 id 写进 sessionStorage，用例之间必须清掉，
  // 否则下一个用例的 `load()` 会去恢复上一场，红在完全无关的断言上。
  sessionStorage.clear()
  assessmentsApi.getCatalog.mockResolvedValue(CATALOG)
  assessmentsApi.getProfile.mockResolvedValue({
    student_profile_id: 'p-1',
    tags: [],
    latest_session_id: null,
    latest_summary: null,
    latest_summary_source: null,
  })
  assessmentsApi.saveAnswers.mockResolvedValue({ session: SESSION, answers: [] })
  // 默认也给一份可用的提交结果：`mountResult()` 会用自己的标签再覆盖一次。
  assessmentsApi.submitSession.mockResolvedValue({
    session: { ...SESSION, status: 'submitted', correct_count: 2, score: 67 },
    tags: [],
  })
})

describe('目录态', () => {
  it('列出可测的技能与题数；没有标签时不显示「我的画像」', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Python')
    expect(wrapper.text()).toContain('SQL')
    expect(wrapper.text()).toContain('共 3 道题')
    expect(wrapper.text()).not.toContain('我的画像')
  })

  it('读取失败时红字可见（不是只 console.error）', async () => {
    assessmentsApi.getCatalog.mockRejectedValueOnce(new Error('boom'))

    const wrapper = await mountView()

    const alert = wrapper.find('[role="alert"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('读取测评内容失败')
  })

  it('刷新后恢复进行中的这一场：题目与已选答案都回来', async () => {
    sessionStorage.setItem('ai-career.assessment-session-id', SESSION.id)
    assessmentsApi.getSession.mockResolvedValue({
      session: SESSION,
      questions: QUESTIONS,
      answers: [{ question_id: 'q-1', chosen_key: 'B', is_correct: null }],
    })

    const wrapper = await mountView()

    expect(assessmentsApi.getSession).toHaveBeenCalledWith(SESSION.id)
    expect(wrapper.text()).toContain('已答 1 / 1 题')
    const selected = wrapper.findAll('input[type="radio"]')[1].element as HTMLInputElement
    expect(selected.checked).toBe(true)
  })

  it('会话读不到时清掉标记并安静回目录，不弹红字', async () => {
    sessionStorage.setItem('ai-career.assessment-session-id', SESSION.id)
    assessmentsApi.getSession.mockRejectedValueOnce(new Error('404'))

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('开始测评')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(sessionStorage.getItem('ai-career.assessment-session-id')).toBeNull()
  })
})

describe('答题态', () => {
  it('开始后渲染题目与选项，进度从 0 起', async () => {
    const wrapper = await mountAnswering()

    expect(wrapper.text()).toContain('tuple 创建后还能改吗？')
    expect(wrapper.text()).toContain('已答 0 / 1 题')
    expect(sessionStorage.getItem('ai-career.assessment-session-id')).toBe(SESSION.id)
  })

  it('点一个选项就立刻发一次覆盖写，进度跟着走', async () => {
    const wrapper = await mountAnswering()

    await wrapper.findAll('input[type="radio"]')[1].setValue(true)
    await flushPromises()

    expect(assessmentsApi.saveAnswers).toHaveBeenCalledTimes(1)
    expect(assessmentsApi.saveAnswers).toHaveBeenCalledWith(SESSION.id, [
      { question_id: 'q-1', chosen_key: 'B' },
    ])
    expect(wrapper.text()).toContain('已答 1 / 1 题')
  })
})

describe('结果态', () => {
  const SUBMITTED = {
    ...SESSION,
    status: 'submitted' as const,
    correct_count: 2,
    score: 67,
    submitted_at: '2026-11-13T21:10:00+08:00',
  }

  async function mountResult() {
    assessmentsApi.submitSession.mockResolvedValue({
      session: SUBMITTED,
      tags: [
        { tag: 'Python', status: 'verified', score: 100, evidence_count: 2 },
        { tag: 'SQL', status: 'pending', score: 50, evidence_count: 2 },
      ],
    })
    const wrapper = await mountAnswering()
    const submitButton = wrapper.findAll('button').find((item) => item.text().includes('提交'))
    await submitButton!.trigger('click')
    await flushPromises()
    return wrapper
  }

  it('提交前把当前整卷选择再覆盖一次，且确实发生在判分之前', async () => {
    const wrapper = await mountAnswering()
    await wrapper.findAll('input[type="radio"]')[1].setValue(true)
    await flushPromises()
    const submitButton = wrapper.findAll('button').find((item) => item.text().includes('提交'))
    await submitButton!.trigger('click')
    await flushPromises()

    // 逐题点击发一次，提交时再整卷发一次（防在途请求乱序）。
    expect(assessmentsApi.saveAnswers).toHaveBeenCalledTimes(2)
    expect(assessmentsApi.saveAnswers).toHaveBeenLastCalledWith(SESSION.id, [
      { question_id: 'q-1', chosen_key: 'B' },
    ])
    const saveOrders = assessmentsApi.saveAnswers.mock.invocationCallOrder
    const saveOrder = saveOrders[saveOrders.length - 1] ?? 0
    const submitOrder = assessmentsApi.submitSession.mock.invocationCallOrder[0] ?? 0
    expect(saveOrder).toBeLessThan(submitOrder)
  })

  it('分数与三态标签都按服务端给的值上屏', async () => {
    const wrapper = await mountResult()

    expect(wrapper.text()).toContain('67')
    expect(wrapper.text()).toContain('答对 2 / 3 题')
    expect(wrapper.text()).toContain('已验证')
    expect(wrapper.text()).toContain('待验证')
  })

  it('自动整理的概述不会被标成 AI 分析，且说明文字可见', async () => {
    assessmentsApi.summarizeSession.mockResolvedValue({
      session_id: SESSION.id,
      summary: '本次标准化测评共 3 题，答对 2 题。',
      source: 'fallback',
      note: '未配置 AI 密钥，以下为根据测评结果自动整理的概述。',
    })
    const wrapper = await mountResult()

    const summaryButton = wrapper.findAll('button').find((item) => item.text().includes('生成概述'))
    await summaryButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('自动整理的概述')
    expect(wrapper.text()).not.toContain('AI 分析')
    expect(wrapper.text()).toContain('未配置 AI 密钥')
    expect(wrapper.text()).toContain('本次标准化测评共 3 题')
  })

  it('真 AI 生成时才写「AI 分析」', async () => {
    assessmentsApi.summarizeSession.mockResolvedValue({
      session_id: SESSION.id,
      summary: '你在 Python 上已有可验证的基础。',
      source: 'ai',
      note: null,
    })
    const wrapper = await mountResult()

    const summaryButton = wrapper.findAll('button').find((item) => item.text().includes('生成概述'))
    await summaryButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('AI 分析')
    expect(wrapper.text()).not.toContain('自动整理的概述')
  })
})
