/**
 * 个人资料弹窗（2026-11-15 第 25 轮，老板指令第 3 条）。
 *
 * 这一组最要紧的是**保存语义**：
 *
 * - 底层接口是 `PUT /profiles/me`，**整体覆盖**：请求体里少带子表就是清空子表。
 *   所以用例里有一条专门盯「子表原样带上」（技能 / 经历 / 教育三格）。
 * - 用户把原本有值的字段清空时，**旧值必须留在库里**。在 PUT 契约下「不发这个键」
 *   等于发默认值（`email` 默认 `None`、`contact` 默认 `""`），所以实现是「把旧值填回去再发」。
 *   这条在按钮文案上看不出来，只能看请求体。
 *
 * 桩的口径与 `DashboardView.spec.ts` 一致：把 axios 的 `defaults.adapter` 换成一只
 * 内存小服务器，按 URL + 方法分派；没桩住的请求明确失败。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import {
  AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { authHttp, http } from '@/api/client'
import type { ApiErrorBody, ProfileRead, ProfileUpsertRequest } from '@/api/types'
import ProfileModal from '@/components/ProfileModal.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const PROFILE: ProfileRead = {
  id: 'profile-1',
  user_id: 'user-1',
  title: '默认简历',
  is_default: true,
  deleted_at: null,
  paper_size: 'A4',
  theme: 'ink',
  layout_config: { version: 3, sections: [] },
  full_name: '林小满',
  email: 'lin@example.com',
  grade: '大三',
  contact: '13800000000',
  target_role: '前端工程师',
  created_at: '2026-11-01T09:00:00+08:00',
  updated_at: '2026-11-10T09:00:00+08:00',
  skills: [
    {
      id: 'skill-1',
      name: 'Vue',
      source: 'self_reported',
      last_used_on: '2026-09',
      first_added_at: '2026-09-01T09:00:00+08:00',
    } as ProfileRead['skills'][number],
  ],
  experiences: [],
  educations: [],
  certificates: [],
  portfolio_links: [],
  city_preferences: [],
}

let calls: string[] = []
let savedAdapters: Array<[AxiosInstance, AxiosInstance['defaults']['adapter']]> = []
let profile: ProfileRead | null = PROFILE
/** 最近一次 PUT 的请求体——被判的是它，不是屏幕上的字。 */
let lastPayload: ProfileUpsertRequest | null = null
let orgStatus = 200

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

function failure(config: InternalAxiosRequestConfig, status: number, message: string) {
  const body: ApiErrorBody = {
    error: {
      code: status >= 500 ? 'SERVER_ERROR' : 'CLIENT_ERROR',
      message,
      details: {},
      retryable: false,
    },
    meta: { request_id: 'req-profile-test' },
  }
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, envelope(config, body, status))
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  calls.push(`${method} ${url}`)

  if (method === 'get' && url === '/profiles/me') {
    return Promise.resolve(envelope(config, { data: { profile }, meta: { request_id: 'r' } }))
  }
  if (method === 'put' && url === '/profiles/me') {
    lastPayload = JSON.parse(String(config.data)) as ProfileUpsertRequest
    const merged: ProfileRead = {
      ...(profile ?? PROFILE),
      full_name: lastPayload.full_name,
      contact: lastPayload.contact,
      email: (lastPayload.email as string | null) ?? null,
      target_role: (lastPayload.target_role as string | null) ?? null,
    }
    return Promise.resolve(envelope(config, { data: { profile: merged }, meta: { request_id: 'r' } }))
  }
  if (method === 'get' && url === '/organizations/me') {
    if (orgStatus === 404) {
      return Promise.reject(failure(config, 404, '你的账号还没有加入组织。'))
    }
    if (orgStatus !== 200) {
      return Promise.reject(failure(config, orgStatus, '组织信息暂时无法读取，请稍后重试。'))
    }
    return Promise.resolve(
      envelope(config, {
        data: {
          organization: { id: 'org-1', code: 'CJLU2026', name: '中国计量大学', kind: 'school' },
          role: 'student',
          member_count: 2,
        },
        meta: { request_id: 'r' },
      }),
    )
  }
  return Promise.reject(failure(config, 404, `未桩住的请求：${method.toUpperCase()} ${url}`))
}

const mounted: VueWrapper[] = []
const $ = (selector: string): HTMLElement | null =>
  document.querySelector(selector) as HTMLElement | null
const inputs = (): HTMLInputElement[] => [
  ...document.querySelectorAll<HTMLInputElement>('.profile-form input'),
]

async function openModal() {
  const wrapper = mount(ProfileModal, {
    attachTo: document.body.appendChild(document.createElement('div')),
  })
  mounted.push(wrapper)
  useUiStore().openProfile()
  await flushPromises()
  await flushPromises()
  return wrapper
}

async function setInput(index: number, value: string) {
  const input = inputs()[index]
  if (!input) throw new Error(`第 ${index} 个输入框不存在`)
  input.value = value
  input.dispatchEvent(new Event('input'))
  await flushPromises()
}

async function save() {
  ;($('[data-testid="profile-save"]') as HTMLButtonElement).click()
  await flushPromises()
  await flushPromises()
}

beforeEach(() => {
  calls = []
  profile = PROFILE
  lastPayload = null
  orgStatus = 200
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.role = 'student'
  auth.user = {
    id: 'user-1',
    username: 'tester',
    email: null,
    phone: null,
    created_at: '2026-11-01T09:00:00+08:00',
  }
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
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
})

describe('只读的身份三格', () => {
  it('用户名 / 组织 / 身份都来自真实来源，不是输入框', async () => {
    await openModal()

    const facts = [...document.querySelectorAll('.profile-fact')].map((fact) => [
      fact.querySelector('dt')?.textContent?.trim(),
      fact.querySelector('dd')?.textContent?.trim(),
    ])
    expect(facts).toEqual([
      ['用户名', 'tester'],
      ['组织', '中国计量大学'],
      ['身份', '学生'],
    ])
  })

  it('没有组织（404）时写「未加入组织」', async () => {
    orgStatus = 404
    await openModal()

    expect(document.querySelector('.profile-fact:nth-child(2) dd')?.textContent?.trim()).toBe(
      '未加入组织',
    )
  })

  it('读不到（500）时写「暂时读不到」——不许把「不知道」说成「没有」', async () => {
    orgStatus = 500
    await openModal()

    expect(document.querySelector('.profile-fact:nth-child(2) dd')?.textContent?.trim()).toBe(
      '暂时读不到',
    )
  })
})

describe('空字段显示灰色占位，而不是空框', () => {
  it('没填的那几格给出「暂未填写 ××」', async () => {
    profile = { ...PROFILE, email: null, target_role: null }
    await openModal()

    const placeholders = inputs().map((input) => input.placeholder)
    expect(placeholders).toEqual([
      '暂未填写姓名',
      '暂未填写手机号',
      '暂未填写邮箱',
      '暂未填写期望职位',
    ])
    expect(inputs()[2]?.value).toBe('')
    expect(inputs()[3]?.value).toBe('')
  })
})

describe('保存语义（这一批最要紧的一条）', () => {
  it('把原本有值的字段清空 → 旧值留在请求体里，绝不发空串 / null', async () => {
    await openModal()

    await setInput(2, '') // 邮箱原本 lin@example.com
    await setInput(1, '') // 手机号原本 13800000000
    await save()

    expect(calls).toContain('put /profiles/me')
    expect(lastPayload?.email).toBe('lin@example.com')
    expect(lastPayload?.contact).toBe('13800000000')
    expect(lastPayload?.email).not.toBe('')
    expect(lastPayload?.email).not.toBeNull()
    expect(lastPayload?.contact).not.toBe('')
  })

  it('原本为空、这次填了值 → 正常提交', async () => {
    profile = { ...PROFILE, email: null }
    await openModal()
    await setInput(2, 'new@example.com')
    await save()

    expect(lastPayload?.email).toBe('new@example.com')
  })

  it('PUT 是整体覆盖：技能 / 经历 / 教育随请求体原样带上（少带一格就是清空一格）', async () => {
    await openModal()
    await setInput(0, '林小满（改名）')
    await save()

    expect(lastPayload?.skills).toHaveLength(1)
    expect(lastPayload?.skills[0]?.name).toBe('Vue')
    expect(lastPayload?.experiences).toEqual([])
    expect(lastPayload?.educations).toEqual([])
    // 纸张 / 配色也跟着带上，理由同 `payloadFromDraft` 的注释。
    expect(lastPayload?.paper_size).toBe('A4')
    expect(lastPayload?.theme).toBe('ink')
  })

  it('没有改动时保存按钮禁用；改了才可用', async () => {
    await openModal()
    expect(($('[data-testid="profile-save"]') as HTMLButtonElement).disabled).toBe(true)

    await setInput(3, '后端工程师')
    expect(($('[data-testid="profile-save"]') as HTMLButtonElement).disabled).toBe(false)
  })

  it('手机号不合法：校验提示上屏，一次 PUT 都不发', async () => {
    await openModal()
    await setInput(1, '12345')
    await save()

    expect(document.querySelector('.modal-error')?.textContent).toContain('手机号格式不正确')
    expect(calls).not.toContain('put /profiles/me')
  })

  it('保存成功：回执上屏，且表单与「原始值」同步（再点保存不会重复提交）', async () => {
    await openModal()
    await setInput(0, '林小满（改名）')
    await save()

    expect(document.querySelector('.profile-notice')?.textContent).toContain('已保存')
    expect(($('[data-testid="profile-save"]') as HTMLButtonElement).disabled).toBe(true)
  })
})
