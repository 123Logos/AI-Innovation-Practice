/**
 * 教师端看板 · 简历导出（B10b + 2026-11-15 第 25 轮的两处改动）。
 *
 * 这一批加的是「按学生筛」：下拉选项**从清单自己去重**（不另开一个「学生列表」接口，
 * 那会变成两份名单各说一套），列表容器封顶 400px 免得几十个人把页面撑开。
 *
 * jsdom 没有布局引擎，所以「400px + 溢出滚动」这一半只能读源码（它是一条 CSS 事实），
 * 行为那一半（选项、过滤、空态、换筛选清勾选）才挂载验证。
 */
import { flushPromises, mount } from '@vue/test-utils'
import {
  AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { authHttp, http } from '@/api/client'
import type { ApiErrorBody } from '@/api/types'
import TeacherDashboardView from '@/views/TeacherDashboardView.vue'

const DASHBOARD = {
  organization_id: 'org-1',
  student_total: 3,
  tagged_student_total: 2,
  tag_distribution: [],
  match_overview: [],
}

const RESUMES = [
  { resume_id: 'r-1', profile_id: 'p-1', full_name: '张三', title: '投后端', version_no: 2 },
  { resume_id: 'r-2', profile_id: 'p-2', full_name: '李四', title: '投前端', version_no: 1 },
  { resume_id: 'r-3', profile_id: 'p-1', full_name: '张三', title: '投算法', version_no: 1 },
]

let savedAdapters: Array<[AxiosInstance, AxiosInstance['defaults']['adapter']]> = []

function envelope(config: InternalAxiosRequestConfig, data: unknown): AxiosResponse {
  return { data, status: 200, statusText: 'OK', headers: {}, config } as AxiosResponse
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  if (method === 'get' && url === '/teacher/dashboard') {
    return Promise.resolve(envelope(config, { data: DASHBOARD, meta: { request_id: 'r' } }))
  }
  if (method === 'get' && url === '/teacher/resumes') {
    return Promise.resolve(envelope(config, { data: { items: RESUMES }, meta: { request_id: 'r' } }))
  }
  const body: ApiErrorBody = {
    error: { code: 'CLIENT_ERROR', message: `未桩住：${url}`, details: {}, retryable: false },
    meta: { request_id: 'r' },
  }
  return Promise.reject(
    new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, {
      data: body,
      status: 404,
      statusText: 'Not Found',
      headers: {},
      config,
    } as AxiosResponse),
  )
}

async function mountView() {
  setActivePinia(createPinia())
  const wrapper = mount(TeacherDashboardView, { global: { plugins: [createPinia()] } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  savedAdapters = [
    [http, http.defaults.adapter],
    [authHttp, authHttp.defaults.adapter],
  ]
  http.defaults.adapter = fakeAdapter
  authHttp.defaults.adapter = fakeAdapter
})

afterEach(() => {
  for (const [instance, adapter] of savedAdapters) instance.defaults.adapter = adapter
  savedAdapters = []
})

describe('学生下拉筛选（第 25 轮：下拉，不是输入框）', () => {
  it('选项来自清单去重后的姓名，第一项是「全部学生」，且不是 input', async () => {
    const wrapper = await mountView()

    const select = wrapper.get('#resume-student-filter')
    expect(select.element.tagName).toBe('SELECT')
    const options = [...select.element.querySelectorAll('option')].map((option) => option.textContent)
    expect(options).toEqual(['全部学生', '李四', '张三'])
    // 老板明写「不要做成输入框」：这一条防的是有人拿 `list=` + `<input>` 糊一个下拉。
    expect(wrapper.find('input[list]').exists()).toBe(false)
  })

  it('选一个学生：列表只剩他的简历（张三两版都在，因为筛选是「人」不是「版本」）', async () => {
    const wrapper = await mountView()
    expect(wrapper.findAll('.resume-item')).toHaveLength(3)

    await wrapper.get('#resume-student-filter').setValue('张三')

    const names = wrapper.findAll('.resume-name').map((node) => node.text())
    expect(names).toEqual(['张三', '张三'])
    expect(wrapper.findAll('.resume-title').map((node) => node.text())).toEqual([
      '投后端 · v2',
      '投算法 · v1',
    ])
  })

  it('选「全部学生」回到整份清单；换筛选会清空已有勾选', async () => {
    const wrapper = await mountView()
    const boxes = wrapper.findAll('.resume-item input[type="checkbox"]')
    await boxes[0]?.setValue(true)
    expect(wrapper.get('.primary-button').text()).toContain('已选 1 份')

    await wrapper.get('#resume-student-filter').setValue('李四')
    expect(wrapper.findAll('.resume-item')).toHaveLength(1)
    // 勾选是「为这次打包」，换人之后屏幕外的勾选会让按钮上的数字说不清是谁。
    expect(wrapper.get('.primary-button').text()).toContain('已选 0 份')

    await wrapper.get('#resume-student-filter').setValue('')
    expect(wrapper.findAll('.resume-item')).toHaveLength(3)
  })
})

describe('列表容器封顶（CSS 事实，读源码）', () => {
  it('`.resume-list` 有 max-height: 400px 与 overflow-y: auto', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/views/TeacherDashboardView.vue'), 'utf8')
    const rule = /\.resume-list \{[^}]*\}/.exec(source)?.[0] ?? ''

    expect(rule).toContain('max-height: 400px')
    expect(rule).toContain('overflow-y: auto')
  })
})
