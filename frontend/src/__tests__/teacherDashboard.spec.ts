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
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const teacherToolsApi = vi.hoisted(() => ({
  getDashboard: vi.fn(),
  listResumes: vi.fn(),
  downloadBatch: vi.fn(),
  exportResume: vi.fn(),
  saveBlob: vi.fn(),
}))

vi.mock('@/api', () => ({ teacherToolsApi }))

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
  teacherToolsApi.downloadBatch.mockResolvedValue(new Blob(['zip']))
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
    expect(teacherToolsApi.saveBlob).toHaveBeenCalledTimes(1)
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

  it('下载失败时红字可见，不静默', async () => {
    teacherToolsApi.exportResume.mockRejectedValueOnce(new Error('boom'))
    const wrapper = await mountView()

    const button = wrapper.findAll('button').find((item) => item.text().includes('导出 PDF'))
    await button!.trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').text()).toContain('没能导出')
  })
})
