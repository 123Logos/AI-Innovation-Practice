/**
 * `OrganizationView.vue` 的单测（批次 B5，`docs/adr/ADR-021`）。
 *
 * 这四条守的是**用户能看见什么**，不是接口通不通（那是 `tests/test_organizations.py` 的事）：
 *
 * 1. 有组织：名称、组织码、成员人数、成员行都要上屏；
 * 2. 没有组织：`GET /organizations/me` 返回 404 时是**空状态**，不是一条红色错误
 *    —— 用户没做错任何事，把他吓一跳是界面在撒谎；
 * 3. 学生没有成员名单权限：显示「只有教师与学校管理员可以查看」，而不是一片空白；
 * 4. 只有学校管理员看到角色下拉与操作记录（前端据此决定画不画控件，拦截仍在后端）。
 *
 * 桩的口径与 `ResumePreview.spec.ts` 一致：把 axios 的 `defaults.adapter` 换成一只内存小服务器，
 * 不是「返回固定 JSON 的 mock」——按 URL 分派，缺了哪条就明确失败，不会静默返回空对象。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { AxiosError, type AxiosInstance, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { authHttp, http } from '@/api/client'
import type { ApiErrorBody } from '@/api/types'
import OrganizationView from '@/views/OrganizationView.vue'

const ORGANIZATION_ID = 'org-1'

const OVERVIEW = {
  organization: { id: ORGANIZATION_ID, code: 'AI2026', name: '示例大学', kind: 'school' },
  role: 'school_admin',
  member_count: 2,
}

const MEMBERS = [
  {
    user_id: 'user-1',
    username: 'admin01',
    email: 'admin@example.com',
    role: 'school_admin',
    joined_at: '2026-11-12T09:00:00+08:00',
  },
  {
    user_id: 'user-2',
    username: 'teacher06',
    email: null,
    role: 'teacher',
    joined_at: '2026-11-12T09:05:00+08:00',
  },
]

interface AuditItemFixture {
  id: number
  action: string
  actor_user_id: string | null
  actor_username: string | null
  target_type: string
  target_id: string
  result: string
  detail: string
  ip: string
  created_at: string
}

const AUDIT: { items: AuditItemFixture[]; total: number } = {
  items: [
    {
      id: 3,
      action: 'member.role_change',
      actor_user_id: 'user-1',
      // 第 25 轮：后端用 LEFT JOIN 把操作人名字带出来，界面那句「谁 在 时间 动作」靠它。
      actor_username: 'admin01',
      target_type: 'user',
      target_id: 'user-2',
      result: 'ok',
      detail: '{"from":"teacher","to":"student"}',
      ip: '127.0.0.1',
      created_at: '2026-11-12T09:30:00+08:00',
    },
  ],
  total: 1,
}

const INVITATIONS = [
  {
    id: 'invite-1',
    organization_id: ORGANIZATION_ID,
    code: 'ABCD1234EFGH',
    role: 'teacher',
    max_uses: 3,
    used_count: 1,
    expires_at: '2026-11-19T09:00:00+08:00',
    revoked_at: null,
    created_at: '2026-11-12T09:00:00+08:00',
    redeemed_by: ['teacher06'],
  },
]
let calls: string[] = []
let savedAdapters: Array<[AxiosInstance, AxiosInstance['defaults']['adapter']]> = []
let overviewResponse: unknown = OVERVIEW
let meStatus = 200
let membersStatus = 200
/** 操作记录的响应体可替换：新用例要喂不同的 action / 操作人。 */
let auditResponse: { items: AuditItemFixture[]; total: number } = AUDIT

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

function failure(config: InternalAxiosRequestConfig, status: number, message: string) {
  const body: ApiErrorBody = {
    error: { code: status >= 500 ? 'SERVER_ERROR' : 'CLIENT_ERROR', message, details: {}, retryable: false },
    meta: { request_id: 'req-org-test' },
  }
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, envelope(config, body, status))
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  calls.push(`${method} ${url}`)

  if (method === 'get' && url === '/organizations/me') {
    if (meStatus !== 200) {
      return Promise.reject(failure(config, meStatus, '找不到这个组织，或你的账号还没有加入组织。'))
    }
    return Promise.resolve(envelope(config, { data: overviewResponse, meta: { request_id: 'r' } }))
  }
  if (method === 'get' && url === `/organizations/${ORGANIZATION_ID}/members`) {
    if (membersStatus !== 200) {
      return Promise.reject(failure(config, membersStatus, '您没有执行此操作的权限。'))
    }
    return Promise.resolve(envelope(config, { data: { items: MEMBERS }, meta: { request_id: 'r' } }))
  }
  if (method === 'get' && url === `/organizations/${ORGANIZATION_ID}/audit-logs`) {
    return Promise.resolve(envelope(config, { data: auditResponse, meta: { request_id: 'r' } }))
  }
  if (method === 'get' && url === `/organizations/${ORGANIZATION_ID}/invitations`) {
    return Promise.resolve(envelope(config, { data: { items: INVITATIONS }, meta: { request_id: 'r' } }))
  }
  return Promise.reject(failure(config, 404, `未桩住的请求：${method.toUpperCase()} ${url}`))
}

async function mountView() {
  const wrapper = mount(OrganizationView, {
    global: { plugins: [createPinia()] },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  calls = []
  overviewResponse = OVERVIEW
  meStatus = 200
  membersStatus = 200
  auditResponse = AUDIT
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

describe('组织管理页', () => {
  it('有组织时：名称、组织码、成员人数与成员行都上屏', async () => {
    const wrapper = await mountView()

    expect(wrapper.get('.card h2').text()).toBe('示例大学')
    expect(wrapper.text()).toContain('AI2026')
    expect(wrapper.text()).toContain('2 人')
    const rows = wrapper.findAll('.table tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0]?.text()).toContain('admin01')
    // 没填邮箱时给「未填写」，不是空白格（空白会让人以为数据丢了）。
    expect(rows[1]?.text()).toContain('未填写')
    expect(rows[1]?.text()).toContain('教师')
  })

  it('账号没有组织（后端 404）：是空状态，不是红色错误', async () => {
    meStatus = 404
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('你的账号还没有加入任何组织')
    // 反向护栏：这条 404 没有被当成故障弹出来——那会让用户以为系统坏了。
    expect(wrapper.find('.notice--error').exists()).toBe(false)
    // 也没有去请求成员名单（没有组织就没有 id 可查）。
    expect(calls.some((call) => call.includes('/members'))).toBe(false)
  })

  it('学生没有成员名单权限：说清谁能看，而不是一片空白', async () => {
    overviewResponse = { ...OVERVIEW, role: 'student' }
    membersStatus = 403
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('只有教师与学校管理员可以查看成员名单')
    // 学生看不到角色下拉，也看不到操作记录那一节。
    // 断言按标题判，不用 `text()` 整页匹配：页头那句「查看你所在的组织、成员与操作记录」
    // 本来就含这三个字，整页匹配会对着副标题假红。
    expect(wrapper.find('.role-select').exists()).toBe(false)
    expect(wrapper.findAll('h2').map((heading) => heading.text())).not.toContain('操作记录')
  })

  it('学校管理员看得到角色下拉与操作记录，且记录里的动作名翻成了人话', async () => {
    const wrapper = await mountView()

    expect(wrapper.findAll('.role-select')).toHaveLength(2)
    expect(wrapper.text()).toContain('操作记录')
    // `member.role_change` 这类机器名不上屏，翻成「调整角色」。
    expect(wrapper.text()).toContain('调整角色')
    expect(wrapper.text()).not.toContain('member.role_change')
  })

  it('学校管理员看到邀请成员区与已发出的邀请码', async () => {
    const wrapper = await mountView()

    expect(wrapper.findAll('h2').map((heading) => heading.text())).toContain('邀请成员')
    expect(wrapper.find('.invite-code').text()).toBe('ABCD1234EFGH')
    expect(wrapper.find('.invite-form .button').text()).toBe('生成邀请码')
  })

  it('还没有组织时给的是邀请码输入框，失败提示必须上屏', async () => {
    meStatus = 404
    const wrapper = await mountView()

    const input = wrapper.find('.redeem-form input')
    expect(input.exists()).toBe(true)
    await input.setValue('BAD-CODE')
    await wrapper.find('.redeem-form').trigger('submit')
    await flushPromises()

    const alert = wrapper.find('[role="alert"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('未桩住的请求')
  })


  it('发码人能看到这张邀请码已被谁兑换', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('.invite-redeemers').text()).toContain('teacher06')
  })

})

describe('操作记录与时间（2026-11-15 第 25 轮）', () => {
  it('一行读成一句话：用户名 在 时间 动作；时间是 YYYY-MM-DD HH:mm（东八区）', async () => {
    const wrapper = await mountView()

    const row = wrapper.get('.log-item')
    expect(row.get('.log-who').text()).toBe('admin01')
    expect(row.get('.log-in').text()).toBe('在')
    expect(row.get('.log-time').text()).toBe('2026-11-12 09:30')
    expect(row.get('.log-action').text()).toBe('调整角色')
  })

  it('没有操作人的系统动作写「系统」，不是空白也不是 UUID', async () => {
    auditResponse = {
      items: [
        {
          ...AUDIT.items[0]!,
          id: 9,
          action: 'organization.create',
          actor_user_id: null,
          actor_username: null,
        },
      ],
      total: 1,
    }
    const wrapper = await mountView()

    expect(wrapper.get('.log-who').text()).toBe('系统')
  })

  it('动作码翻成人话：查看看板 / 导出简历 PDF；不认识的码原样显示而不是消失', async () => {
    auditResponse = {
      items: [
        { ...AUDIT.items[0]!, id: 11, action: 'dashboard.view' },
        { ...AUDIT.items[0]!, id: 12, action: 'resume.export_pdf' },
        { ...AUDIT.items[0]!, id: 13, action: 'brand.new_action' },
      ],
      total: 3,
    }
    const wrapper = await mountView()

    const labels = wrapper.findAll('.log-action').map((node) => node.text())
    expect(labels).toEqual(['查看看板', '导出简历 PDF', 'brand.new_action'])
  })

  it('成员入组时间与邀请码到期同样用横线版分钟精度', async () => {
    const wrapper = await mountView()

    // 成员表：2026-11-12T09:05:00+08:00 → 2026-11-12 09:05
    expect(wrapper.text()).toContain('2026-11-12 09:05')
    // 邀请码：2026-11-19T09:00:00+08:00 → 2026-11-19 09:00
    expect(wrapper.text()).toContain('2026-11-19 09:00')
  })
})
