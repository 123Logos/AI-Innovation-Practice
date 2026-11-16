/**
 * 通用任务轮询（第 30 轮，`ADR-033` §4 / §5 第 9 条）。
 *
 * 两个 API，一个实现：
 *
 * - `waitForTask(taskId)`：**纯异步函数**，给 store 用（`stores/resume.ts` 的导入要在
 *   `async` 流程里等结果）。成功返回终态，失败抛 `TaskFailedError`。
 * - `usePollTask()`：**组合式**，给视图用（教师端打包要在屏幕上显示进度）。
 *   它持有响应式的 `task` / `polling`，组件卸载时 `stop()`。
 *
 * 三条判据：
 *
 * 1. **只认终态**：`pending` / `running` 继续等，`complete` / `failed` 立即返回。
 *    「HTTP 200 就算成功」在这里是错的：`GET /tasks/{id}` 对失败任务也回 200
 *    （`ADR-033` §2.6），业务成功与否在 `state` 里。
 * 2. **失败抛的是服务端那句人话**：`TaskFailedError.message` 直接来自 `error.message`，
 *    `code` 原样带出，调用方仍能按 `AI_KEY_MISSING` 分叉给「去设置」的链接。
 * 3. **可停**：卸载 / 用户取消 / 超预算都停表。`setInterval` 在后台无限转下去
 *    既是内存泄漏也是一串白打的请求。
 */

import { getCurrentInstance, onUnmounted, ref, type Ref } from 'vue'

import { TASK_POLL_BUDGET_MS, TASK_POLL_INTERVAL_MS, getTask } from '@/api/tasks'
import type { TaskErrorBody, TaskStatus } from '@/api/types'

/**
 * 任务失败（`state === 'failed'`）。
 *
 * 刻意**不继承** `ApiError`：`ApiError` 说的是「这个请求失败了」，
 * 而这里请求是成功的、任务失败了——两者在 UI 上的下一步不同
 * （前者重试，后者看 `code` 决定改什么）。
 */
export class TaskFailedError extends Error {
  readonly code: string
  readonly retryable: boolean

  constructor(error: TaskErrorBody | null) {
    super(error?.message ?? '任务没有完成，请再试一次。')
    this.name = 'TaskFailedError'
    this.code = error?.code ?? 'TASK_INTERNAL'
    this.retryable = error?.retryable ?? true
  }
}

export interface PollOptions {
  /** 轮询间隔，默认 1.5 秒（老板第 30 轮指定）。测试传 0 用假时钟推进。 */
  intervalMs?: number
  /** 总预算，默认 5 分钟；超了抛 `TaskFailedError('TASK_LOST')`。 */
  budgetMs?: number
  /** 每次取到新状态就回调（视图用它刷新进度条）。 */
  onUpdate?: (task: TaskStatus) => void
  /** 外部取消信号（组件卸载时置真）。 */
  shouldStop?: () => boolean
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * 等到任务终态。**返回值一定是 `complete`**；`failed` 与超预算都抛 `TaskFailedError`。
 */
export async function waitForTask(
  taskId: string,
  options: PollOptions = {},
): Promise<TaskStatus> {
  const interval = options.intervalMs ?? TASK_POLL_INTERVAL_MS
  const budget = options.budgetMs ?? TASK_POLL_BUDGET_MS
  const startedAt = Date.now()

  for (;;) {
    const status = await getTask(taskId)
    options.onUpdate?.(status)
    if (status.state === 'complete') return status
    if (status.state === 'failed') throw new TaskFailedError(status.error)
    if (options.shouldStop?.()) {
      // 调用方自己不要了（组件卸载）：抛一个**不可重试**的终止信号，
      // 而不是假装成功返回一个 running 状态。
      throw new TaskFailedError({
        code: 'TASK_CANCELLED',
        message: '任务已停止等待，它仍在后台执行。',
        retryable: false,
      })
    }
    if (Date.now() - startedAt >= budget) {
      throw new TaskFailedError({
        code: 'TASK_LOST',
        message: '任务还在后台执行，请稍后回来查看；连续失败请重新发起。',
        retryable: true,
      })
    }
    await sleep(interval)
  }
}

export interface UsePollTask {
  /** 最近一次拿到的任务状态（视图直接绑它画进度）。 */
  task: Ref<TaskStatus | null>
  /** 正在等待终态。 */
  polling: Ref<boolean>
  /** 启动轮询并等终态；失败抛 `TaskFailedError`。 */
  start: (taskId: string) => Promise<TaskStatus>
  /** 主动停止（组件卸载、用户取消）。 */
  stop: () => void
}

/**
 * 视图侧的轮询器。**一个实例只跟一条任务**：`start()` 会先停掉上一条，
 * 免得「连点两次打包」时两个灯一起转。
 */
export function usePollTask(options: PollOptions = {}): UsePollTask {
  const task = ref<TaskStatus | null>(null)
  const polling = ref(false)
  let stopped = false

  function stop(): void {
    stopped = true
    polling.value = false
  }

  async function start(taskId: string): Promise<TaskStatus> {
    stopped = false
    polling.value = true
    task.value = null
    try {
      return await waitForTask(taskId, {
        ...options,
        shouldStop: () => stopped,
        onUpdate: (status) => {
          task.value = status
          options.onUpdate?.(status)
        },
      })
    } finally {
      polling.value = false
    }
  }

  // 只在组件里挂卸载钩子：store / 测试里直接 `waitForTask()`，没有组件实例，
  // 调 `onUnmounted` 会打一条「no active component instance」警告。
  if (getCurrentInstance()) onUnmounted(stop)

  return { task, polling, start, stop }
}
