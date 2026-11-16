/**
 * 教师端学情看板（FR-08 / B10a、B10b，`ADR-029`）。
 *
 * 钉住三件事：
 * 1. **数字与文案都来自后端**，前端不自己算覆盖率、不自己编标签状态；
 * 2. **失败可见**（守则 C1/C3）：读取失败时红字 `role="alert"` 出现在 DOM；
 * 3. **下载动作发出去的 id 就是勾选的 id**：批量与单份两条路径分开验，
 *    否则「按钮能点」会掩盖「打包的是别人的简历」这类错。
 *
 * `@/api` 整模块被桩掉；`TAG_STATUS_LABELS` / `BUCKET_LABELS` 走真实模块，
 * 否则「已验证」这几个字会在测试里被桩成另一个答案。
 *
 * **第 30 轮（`ADR-033`）之后打包是异步的**：`downloadBatch` 只回 `task_id`，
 * 进度与结果都在 `GET /tasks/{id}` 里，产物从 `fetchTaskArchive` 取。所以这里多桩了
 * `@/api/tasks` 的 `getTask`（轮询）与 `fetchTaskArchive`（一次性下载），
 * 并按新契约补一条「打包过程中进度可见」——老板点名要的那句话的机械判据。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const teacherToolsApi = vi.hoisted(() => ({
  getDashboard: vi.fn(),
  listResumes: vi.fn(),
  downloadBatch: vi.fn(),
  fetchTaskArchive: vi.fn(),
  exportResume: vi.fn(),
  saveBlob: vi.fn(),
}))

const getTask = vi.hoisted(() => vi.fn())

vi.mock('@/api', () => ({ teacherToolsApi }))
vi.mock('@/api/tasks', () => ({
  getTask,
  downloadTaskFile: vi.fn(),
  TASK_POLL_INTERVAL_MS: 0,
  TASK_POLL_BUDGET_MS: 60_000,
}))

/** 打包完成的任务终态：`result` 里带文件名，产物由 `fetchTaskArchive` 单独取。 */
const PACK_DONE = {
  task_id: 'task0000000000000000000000000000',
  kind: 'batch_download',
  state: 'complete',
  stage: null,
  progress: 100,
  created_at: '2026-11-16T12:00:00+08:00',
  finished_at: '2026-11-16T12:00:09+08:00',
  result: {
    filename: 'resumes_20261116-120009.zip',
    bytes: 4096,
    download_url: '/api/v1/tasks/task/download',
  },
  error: null,
}

import TeacherDashboardView from '@/views/TeacherDashboardView.vue'

const DASHBOARD = {
  organization_id: 'org-1',
  student_total: 3,
  tagged_student_total: 2,
  tag_distribution: [
    { tag: 'Python', status: 'verified', student_count: 2 },
    { tag: 'SQL', status: 'pending', student_count: 1 },
  ],
  match_overview: [
    {
      job_id: 'job-1',
      title: '后端实习',
      required_skill_count: 2,
      student_count: 3,
      avg_coverage: 67,
      no_requirements: false,
      buckets: { below_half: 1, half_to_eighty: 2, eighty_plus: 0 },
    },
  ],
}

const RESUMES = [
  { resume_id: 'r-1', profile_id: 'p-1', full_name: '张三', title: '投后端', version_no: 1 },
  { resume_id: 'r-2', profile_id: 'p-2', full_name: '李四', title: '投算法', version_no: 2 },
]

async function mountView() {
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(TeacherDashboardView, { attachTo })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  teacherToolsApi.getDashboard.mockResolvedValue(DASHBOARD)
  teacherToolsApi.listResumes.mockResolvedValue(RESUMES)
  teacherToolsApi.downloadBatch.mockResolvedValue({
    task_id: PACK_DONE.task_id,
    kind: 'batch_download',
    state: 'pending',
    created_at: PACK_DONE.created_at,
  })
  teacherToolsApi.fetchTaskArchive.mockResolvedValue(new Blob(['zip']))
  getTask.mockResolvedValue(PACK_DONE)
  teacherToolsApi.exportResume.mockResolvedValue(new Blob(['pdf']))
})

describe('看板渲染', () => {
  it('学生数、标签状态、岗位覆盖率都按后端给的值上屏', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('3')
    expect(wrapper.text()).toContain('2')
    expect(wrapper.text()).toContain('Python')
    expect(wrapper.text()).toContain('已验证')
    expect(wrapper.text()).toContain('待验证')
    expect(wrapper.text()).toContain('后端实习')
    expect(wrapper.text()).toContain('平均覆盖 67%')
    expect(wrapper.text()).toContain('一半到八成')
  })

  it('空数据时给空态文案而不是一片空白', async () => {
    teacherToolsApi.getDashboard.mockResolvedValue({
      ...DASHBOARD,
      student_total: 0,
      tagged_student_total: 0,
      tag_distribution: [],
      match_overview: [],
    })
    teacherToolsApi.listResumes.mockResolvedValue([])

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('还没有测评标签')
    expect(wrapper.text()).toContain('还没有已发布的岗位')
    expect(wrapper.text()).toContain('暂时没有可导出的简历版本')
  })

  it('读取失败时红字可见', async () => {
    teacherToolsApi.getDashboard.mockRejectedValueOnce(new Error('boom'))

    const wrapper = await mountView()

    const alert = wrapper.find('[role="alert"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('读取看板数据失败')
  })
})

describe('下载 / 导出', () => {
  it('批量下载只带勾选的 id，且确实触发保存', async () => {
    const wrapper = await mountView()

    await wrapper.findAll('input[type="checkbox"]')[1].setValue(true)
    const button = wrapper.findAll('button').find((item) => item.text().includes('打包下载'))
    await button!.trigger('click')
    await flushPromises()

    expect(teacherToolsApi.downloadBatch).toHaveBeenCalledWith(['r-2'])
    // 产物是从任务端点取回来的那一份，文件名用服务端给的那个（`ADR-033` §2.5）。
    expect(teacherToolsApi.fetchTaskArchive).toHaveBeenCalledWith(PACK_DONE.task_id)
    expect(teacherToolsApi.saveBlob).toHaveBeenCalledTimes(1)
    expect(teacherToolsApi.saveBlob.mock.calls[0][1]).toBe('resumes_20261116-120009.zip')
  })

  it('打包期间进度可见：文字与百分比都来自那一次轮询，不自己编', async () => {
    // 让第二次轮询挂在半空中：视图在 `await pack.start()` 里等终态，
    // 这时屏幕上必须是服务端给的 `stage` / `progress`（不是一句写死的「正在打包…」）。
    let release: ((value: unknown) => void) | undefined
    getTask
      .mockResolvedValueOnce({ ...PACK_DONE, state: 'running', stage: 'rendering', progress: 45 })
      .mockImplementationOnce(() => new Promise((resolve) => (release = resolve)))
    const wrapper = await mountView()

    await wrapper.findAll('input[type="checkbox"]')[0].setValue(true)
    const button = wrapper.findAll('button').find((item) => item.text().includes('打包下载'))
    await button!.trigger('click')
    await flushPromises()

    const progressLine = wrapper.get('[data-testid="pack-progress"]')
    expect(progressLine.text()).toContain('正在逐份生成 PDF')
    expect(progressLine.text()).toContain('45')

    release?.(PACK_DONE)
    await flushPromises()
    await flushPromises()
    // 终态之后进度行收掉：留着「正在逐份生成 PDF」那句话就是假话。
    expect(wrapper.find('[data-testid="pack-progress"]').exists()).toBe(false)
  })

  it('单份导出按行上的 id 发请求', async () => {
    const wrapper = await mountView()

    const buttons = wrapper.findAll('button').filter((item) => item.text().includes('导出 PDF'))
    expect(buttons).toHaveLength(2)
    await buttons[0].trigger('click')
    await flushPromises()

    expect(teacherToolsApi.exportResume).toHaveBeenCalledWith('r-1')
    expect(teacherToolsApi.downloadBatch).not.toHaveBeenCalled()
  })

  it('打包失败时红字是服务端那句人话（不是一句固定的「打包失败」）', async () => {
    getTask.mockResolvedValue({
      ...PACK_DONE,
      state: 'failed',
      stage: 'rendering',
      progress: 45,
      result: null,
      error: {
        code: 'NO_DOWNLOADABLE_RESUME',
        message: '这批简历没有可下载的版本，请重新选择后重试。',
        retryable: false,
      },
    })
    const wrapper = await mountView()

    await wrapper.findAll('input[type="checkbox"]')[0].setValue(true)
    const button = wrapper.findAll('button').find((item) => item.text().includes('打包下载'))
    await button!.trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').text()).toContain('没有可下载的版本')
    expect(teacherToolsApi.fetchTaskArchive).not.toHaveBeenCalled()
  })

  it('下载失败时红字可见，不静默', async () => {
    teacherToolsApi.exportResume.mockRejectedValueOnce(new Error('boom'))
    const wrapper = await mountView()

    const button = wrapper.findAll('button').find((item) => item.text().includes('导出 PDF'))
    await button!.trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').text()).toContain('没能导出')
  })
})
