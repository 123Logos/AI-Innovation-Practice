/**
 * 异步任务接口：`app/api/v1/tasks.py`（第 30 轮，`ADR-033` §2.6）。
 *
 * 两条路径，一条语义：
 *
 * - `getTask(taskId)`：轮询四态。**任务失败不是 HTTP 错误**——状态码仍是 200，
 *   失败信息在 `error` 里（`code` 与 `ADR-031` §2.5 的五个码同一套）。所以这里
 *   **不**抛 `ApiError`，把「跑完了没有」交给调用方判断：轮询循环不该把
 *   「PDF 没识别到文字」当成网络故障。
 * - `downloadTaskFile(taskId)`：产物**单次性**下载。第二次会拿到 410
 *   （`ADR-033` §2.5），所以拿到 blob 之后要立刻 `saveBlob()`，别留着链接。
 *
 * `progress` / `stage` 由**服务端**算（`app/core/task_kinds.py::progress_for`）：
 * 前端只画，不自己推——两处各算一遍就会出现「进度条 100% 但状态还是 running」。
 */

import { http, unwrap } from './client'
import type { ApiSuccess, TaskStatus } from './types'

/** 轮询间隔：1.5 秒（老板第 30 轮指定）。 */
export const TASK_POLL_INTERVAL_MS = 1500

/**
 * 轮询的总预算。
 *
 * 后端单任务超时是 900 秒（`WORKER_JOB_TIMEOUT_SECONDS`），但前端不该陪着等 15 分钟：
 * 5 分钟还没结束就停止轮询并告诉用户「任务还在后台跑，稍后回来看」——
 * 这比让一个 `setInterval` 在后台无限转下去诚实，也不会有内存泄漏。
 */
export const TASK_POLL_BUDGET_MS = 300_000

/** `GET /api/v1/tasks/{task_id}`：只读一次，不做重试。 */
export async function getTask(taskId: string): Promise<TaskStatus> {
  const response = await http.get<ApiSuccess<TaskStatus>>(
    `/tasks/${encodeURIComponent(taskId)}`,
  )
  return unwrap(response)
}

/**
 * `GET /api/v1/tasks/{task_id}/download`：取产物。
 *
 * `responseType: 'blob'`：返回体是 ZIP，不是 `{data, meta}` 信封，所以**不走** `unwrap()`。
 * 失败时后端返回 JSON 错误体，axios 会按 blob 解析成 Blob，`client.ts` 的拦截器
 * 因此拿不到 `error.code`——那正是这里必须由调用方给一句固定人话的原因。
 */
export async function downloadTaskFile(taskId: string): Promise<Blob> {
  const response = await http.get<Blob>(`/tasks/${encodeURIComponent(taskId)}/download`, {
    responseType: 'blob',
  })
  return response.data
}
