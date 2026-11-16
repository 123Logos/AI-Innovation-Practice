/**
 * 轮询钩子（第 30 轮，`ADR-033` §4）。
 *
 * 这个文件只钉四件「不写用例就一定写错」的事：
 *
 * 1. **只认终态**：`pending` / `running` 继续等。`GET /tasks/{id}` 对失败任务也回 200，
 *    所以「请求成功」不等于「任务成功」——把这两件事混起来，前端会把失败当成功。
 * 2. **失败抛的是服务端那句话**：`code` 原样带出（`AI_KEY_MISSING` 要能继续点去设置），
 *    `message` 就是那句人话，不是这里另编的。
 * 3. **间隔真的是 1.5 秒**：老板第 30 轮点名。用假时钟推进来量，不等真实 1.5 秒。
 * 4. **可停**：卸载 / 超预算都要停表，而且停的时候**不假装成功**。
 *
 * `@/api/tasks` 整体被 mock：本文件验的是轮询编排，不是 axios。
 */

import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TaskFailedError, usePollTask, waitForTask } from '@/composables/usePollTask'
import type { TaskStatus } from '@/api/types'

const getTask = vi.hoisted(() => vi.fn())

vi.mock('@/api/tasks', async () => {
  const actual = await vi.importActual<typeof import('@/api/tasks')>('@/api/tasks')
  return { ...actual, getTask }
})

const TASK_ID = 'task0000000000000000000000000000'

function status(overrides: Partial<TaskStatus> = {}): TaskStatus {
  return {
    task_id: TASK_ID,
    kind: 'import_pdf',
    state: 'pending',
    stage: 'queued',
    progress: 5,
    created_at: '2026-11-16T12:00:00+08:00',
    finished_at: null,
    result: null,
    error: null,
    ...overrides,
  }
}

const COMPLETE = status({
  state: 'complete',
  stage: null,
  progress: 100,
  finished_at: '2026-11-16T12:00:09+08:00',
  result: { profile: {} as never, meta: {} as never },
})

beforeEach(() => {
  getTask.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('waitForTask：等到终态', () => {
  it('pending → running → complete：三次请求，每次都把状态推给回调', async () => {
    getTask
      .mockResolvedValueOnce(status())
      .mockResolvedValueOnce(status({ state: 'running', stage: 'extracting', progress: 35 }))
      .mockResolvedValueOnce(COMPLETE)
    const seen: string[] = []

    const result = await waitForTask(TASK_ID, {
      intervalMs: 0,
      onUpdate: (task) => seen.push(`${task.state}:${task.progress}`),
    })

    expect(getTask).toHaveBeenCalledTimes(3)
    expect(seen).toEqual(['pending:5', 'running:35', 'complete:100'])
    expect(result.state).toBe('complete')
  })

  it('间隔就是 1.5 秒：假时钟推两次才发第三次请求（不是在转圈）', async () => {
    vi.useFakeTimers()
    getTask
      .mockResolvedValueOnce(status())
      .mockResolvedValueOnce(status())
      .mockResolvedValueOnce(COMPLETE)

    const pending = waitForTask(TASK_ID, { intervalMs: 1500 })
    await vi.advanceTimersByTimeAsync(0)
    expect(getTask).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1500)
    expect(getTask).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(1500)
    await expect(pending).resolves.toMatchObject({ state: 'complete' })
    expect(getTask).toHaveBeenCalledTimes(3)
  })

  it('任务失败：抛 `TaskFailedError`，码与句子都来自服务端', async () => {
    getTask.mockResolvedValue(
      status({
        state: 'failed',
        stage: 'extracting',
        progress: 35,
        error: { code: 'PDF_INVALID', message: '这份文件没识别到文字。', retryable: false },
      }),
    )

    const failure = await waitForTask(TASK_ID, { intervalMs: 0 }).catch((error) => error)

    expect(failure).toBeInstanceOf(TaskFailedError)
    expect(failure.code).toBe('PDF_INVALID')
    expect(failure.message).toBe('这份文件没识别到文字。')
    expect(failure.retryable).toBe(false)
  })

  it('超预算：抛 TASK_LOST 而不是一直转下去', async () => {
    getTask.mockResolvedValue(status({ state: 'running', stage: 'structuring', progress: 70 }))

    const failure = await waitForTask(TASK_ID, { intervalMs: 0, budgetMs: 0 }).catch((e) => e)

    expect(failure).toBeInstanceOf(TaskFailedError)
    expect(failure.code).toBe('TASK_LOST')
    expect(failure.message).toContain('后台')
  })

  it('被外部停掉：抛不可重试的终止信号，**不假装成功**', async () => {
    getTask.mockResolvedValue(status({ state: 'running' }))

    const failure = await waitForTask(TASK_ID, { intervalMs: 0, shouldStop: () => true }).catch(
      (error) => error,
    )

    expect(failure).toBeInstanceOf(TaskFailedError)
    expect(failure.code).toBe('TASK_CANCELLED')
    expect(failure.retryable).toBe(false)
  })
})

describe('usePollTask：视图侧', () => {
  it('轮询期间 `polling` 为真、`task` 跟着更新，结束后 `polling` 归假', async () => {
    getTask.mockResolvedValueOnce(status({ state: 'running', stage: 'rendering', progress: 45 }))
    getTask.mockResolvedValueOnce(COMPLETE)
    const poller = usePollTask({ intervalMs: 0 })

    expect(poller.polling.value).toBe(false)
    const pending = poller.start(TASK_ID)
    await flushPromises()
    expect(poller.polling.value).toBe(true)
    expect(poller.task.value?.stage).toBe('rendering')

    const result = await pending

    expect(result.state).toBe('complete')
    expect(poller.polling.value).toBe(false)
  })

  it('`stop()` 之后不再等：调用方取消不该留下一个转不停的定时器', async () => {
    getTask.mockResolvedValue(status({ state: 'running' }))
    const poller = usePollTask({ intervalMs: 0 })

    const pending = poller.start(TASK_ID).catch((error) => error)
    poller.stop()

    const failure = await pending
    expect(failure).toBeInstanceOf(TaskFailedError)
    expect(failure.code).toBe('TASK_CANCELLED')
    expect(poller.polling.value).toBe(false)
  })
})
