/**
 * 简历列表单测（批次 F3 第三片：清单第 5 条把列表从顶栏 Popover 搬进左栏 / 抽屉）。
 *
 * 本文件里有两类用例，来源不同，分开说清：
 *
 * 1. **从 `AppTopBar.spec.ts` 整段搬来的五条**（删除 / 去回收站走页面内弹窗，批次 F1b · 守则 C6）。
 *    它们验的行为一字未改，改的只有「怎么把列表摆到屏幕上」：原来要先点 `.switcher-trigger`
 *    展开 Popover，现在列表组件 mount 即在。断言本身（弹窗文案、请求次数、跳转与否、
 *    `window.alert` / `window.confirm` 一次都没被调用）逐条保留——R1 要的是显式改写，不是放宽。
 * 2. **搬动本身逼出来的新判据**：列表自己拉一次数据（原来只有编辑器进门才拉，
 *    所以在岗位库那几页打开切换器看到的是假空态）、行不再是 `role="menuitem"`、
 *    「新建简历」只发意图不开弹窗（弹窗归外框持有，否则抽屉一收起它就跟着消失）。
 *
 * jsdom 前提与 `AppLayout.spec.ts` 同一套：axios `defaults.adapter` 换内存小服务器，
 * 路由用本地 memory router（要验的是「跳到回收站」这个事实，不是全局守卫）。
 */

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router, type RouteRecordRaw } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { http } from '@/api/client'
import type { ApiErrorBody } from '@/api/types'
import ResumeList from '@/components/ResumeList.vue'
import { useResumeStore } from '@/stores/resume'

const BLANK = { template: '<div />' }

const mounted: VueWrapper[] = []
let pinia: Pinia
let router: Router
let requested: string[] = []
let savedAdapter: typeof http.defaults.adapter
/** 内存小服务器返回的简历列表：`seedProfiles()` 往这里放，而不是直接写 store。 */
let profilesPayload: unknown[] = []

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const url = config.url ?? ''
  requested.push(`${(config.method ?? 'get').toLowerCase()} ${url}`)
  if (url === '/profiles') {
    return Promise.resolve(
      envelope(config, {
        data: { profiles: profilesPayload },
        meta: { request_id: 'req-resume-list-test' },
      }),
    )
  }
  const body = {
    error: {
      code: 'CLIENT_ERROR',
      message: `未桩住的请求：${config.method?.toUpperCase()} ${url}`,
      details: {},
      retryable: false,
    },
    meta: { request_id: 'req-resume-list-test' },
  } as unknown as ApiErrorBody
  return Promise.reject(
    new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, envelope(config, body, 404)),
  )
}

function makeRouter(): Router {
  const routes: RouteRecordRaw[] = [
    { path: '/jobs', name: 'job-board', component: BLANK },
    { path: '/trash', name: 'recycle-bin', component: BLANK },
  ]
  return createRouter({ history: createMemoryHistory(), routes })
}

async function mountList() {
  router = makeRouter()
  await router.push('/jobs')
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(ResumeList, { attachTo, global: { plugins: [pinia, router] } })
  mounted.push(wrapper)
  // 列表自己在 onMounted 里拉一次数据，等它落地再断言，否则量到的是「正在加载」那一帧。
  await flushPromises()
  return wrapper
}

/**
 * 摆一份简历。数据放进内存小服务器而不是直接写 store：列表 mount 时会自己拉一次，
 * 直接写 store 会被那一次刷新覆盖掉——这正是本片新加的行为，绕开它就测不到它。
 */
function seedProfiles() {
  profilesPayload = [
    {
      id: 'profile-under-test',
      title: '投后端的那一版',
      is_default: true,
      full_name: '张三',
      created_at: '2026-11-10T10:00:00+08:00',
      updated_at: '2026-11-10T10:00:00+08:00',
      deleted_at: null,
    },
  ]
  const resume = useResumeStore()
  resume.currentProfileId = 'profile-under-test'
  return resume
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  sessionStorage.clear()
  document.body.innerHTML = ''
  requested = []
  profilesPayload = []
  savedAdapter = http.defaults.adapter
  http.defaults.adapter = fakeAdapter
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
  http.defaults.adapter = savedAdapter
})

describe('列表自己拉数据（搬进常驻栏之后必须成立）', () => {
  it('mount 就拉一次列表，并把返回的行渲染出来', async () => {
    seedProfiles()
    const wrapper = await mountList()

    expect(requested).toContain('get /profiles')
    expect(wrapper.findAll('.menu-entry')).toHaveLength(1)
    expect(wrapper.find('.menu-item__title').text()).toBe('投后端的那一版')
    // 当前那份要有「编辑中」，默认那份要有「默认」——两个标签都在同一行上。
    expect(wrapper.find('.current-tag').exists()).toBe(true)
    expect(wrapper.find('.default-tag').exists()).toBe(true)
    expect(wrapper.find('.menu-item--active').exists()).toBe(true)
  })

  it('一份都没有时给的是空态文案，不是「正在加载」卡住', async () => {
    const wrapper = await mountList()

    expect(wrapper.find('.menu-hint').text()).toContain('还没有保存过的简历')
    expect(wrapper.findAll('.menu-entry')).toHaveLength(0)
  })

  it('拉取失败时当面说明，而不是静默留一个空列表', async () => {
    http.defaults.adapter = (config: InternalAxiosRequestConfig) =>
      Promise.reject(
        new AxiosError(
          'Request failed',
          'ERR_BAD_REQUEST',
          config,
          {},
          envelope(
            config,
            {
              error: { code: 'CLIENT_ERROR', message: '读取简历列表失败，请刷新页面重试。' },
              meta: { request_id: 'req-resume-list-test' },
            },
            500,
          ),
        ),
      )
    const wrapper = await mountList()

    expect(wrapper.find('.menu-error').text()).toContain('读取简历列表失败')
  })
})

describe('行的语义与意图（搬动时改的两处）', () => {
  it('行是原生按钮，不再声明 role="menuitem"——外面已经没有 menu 了', async () => {
    seedProfiles()
    const wrapper = await mountList()
    const main = wrapper.find('.menu-item__main')

    expect(main.element.tagName).toBe('BUTTON')
    expect(wrapper.find('[role="menuitem"]').exists()).toBe(false)
    // 动作按钮是主按钮的**兄弟**而不是子节点：button 套 button 是非法嵌套。
    expect(main.element.querySelector('button')).toBeNull()
    expect(wrapper.find('.menu-item > .menu-item__actions').exists()).toBe(true)
  })

  it('点「新建简历」只发 create 意图，弹窗不在列表里开', async () => {
    const wrapper = await mountList()
    await wrapper.find('.menu-action').trigger('click')

    expect(wrapper.emitted('create')).toHaveLength(1)
    // 弹窗归外框：列表里不许有它，否则抽屉一收起它就跟着消失。
    expect(document.body.querySelector('#create-profile-heading')).toBeNull()
  })

  it('点当前那一行发 close（容器该收起），不重复切换', async () => {
    const resume = seedProfiles()
    const switchSpy = vi.spyOn(resume, 'switchProfile')
    const wrapper = await mountList()

    await wrapper.find('.menu-item__main').trigger('click')

    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(switchSpy).toHaveBeenCalledTimes(0)
  })

  it('排序 chip 调 store，不在组件里另存一份排序状态', async () => {
    const resume = seedProfiles()
    const sortSpy = vi.spyOn(resume, 'setSortField').mockResolvedValue(undefined)
    const wrapper = await mountList()

    const chips = wrapper.findAll('.sort-chip')
    expect(chips).toHaveLength(2)
    await chips[1]?.trigger('click')

    expect(sortSpy).toHaveBeenCalledWith('created_at')
  })

  it('行内改名：点图标出现输入框，保存调 store，取消不发请求', async () => {
    const resume = seedProfiles()
    const renameSpy = vi.spyOn(resume, 'renameProfile').mockResolvedValue(undefined as never)
    const wrapper = await mountList()

    await wrapper.find('.icon-button').trigger('click')
    const input = wrapper.find('.rename-input')
    expect(input.exists()).toBe(true)

    await input.setValue('  改成这个名字  ')
    await wrapper.find('.rename-button').trigger('click')
    await flushPromises()
    expect(renameSpy).toHaveBeenCalledWith('profile-under-test', '改成这个名字')

    // 取消这条路：重新打开输入框，点取消，不该再多一次请求。
    await wrapper.find('.icon-button').trigger('click')
    await wrapper.find('.rename-button--ghost').trigger('click')
    expect(wrapper.find('.rename-input').exists()).toBe(false)
    expect(renameSpy).toHaveBeenCalledTimes(1)
  })
})

/* ------------------------------------------------------------------ */
/*  以下五条整段搬自 AppTopBar.spec.ts（批次 F1b · 守则 C6），断言未放宽  */
/* ------------------------------------------------------------------ */

describe('删除与去回收站走页面内弹层（批次 F1b，守则 C6）', () => {
  /**
   * `resume.save()` 的返回类型是 `ProfileRead`，但这几处调用点**只看它有没有被调用**、
   * 不看返回内容（`goTrash` 成功就跳转、失败就提示）。所以给一个空对象而不是编造一份
   * 完整档案：编出来的字段会诱使后来的用例去断言它们，而它们并不存在。
   */
  const SAVE_RESULT = {} as never

  /** 弹窗 `Teleport` 到 body，所以查询走 document，而不是 wrapper（与 JobDetailView.spec 同法）。 */
  function bodyText(selector: string): string {
    return document.body.querySelector(selector)?.textContent ?? ''
  }

  async function bodyClick(selector: string): Promise<void> {
    const el = document.body.querySelector(selector)
    if (!el) throw new Error(`找不到弹窗按钮：${selector}`)
    ;(el as HTMLElement).click()
    await flushPromises()
  }

  /**
   * `ConfirmDialog` 在生产里挂在 `App.vue`，测试这里按同一个 pinia 单独挂一份。
   * 不这么做的话，本文件其实只在验 store 里那一格状态，屏幕上有没有真弹窗没人管。
   */
  async function mountDialogHost() {
    const { default: ConfirmDialog } = await import('@/components/ConfirmDialog.vue')
    const host = mount(ConfirmDialog, { global: { plugins: [pinia] } })
    mounted.push(host)
    return host
  }

  it('点「删除」只打开弹层，此刻一次删除请求都没发；点「确认删除」才删', async () => {
    const resume = seedProfiles()
    const deleteSpy = vi.spyOn(resume, 'deleteProfile').mockResolvedValue(undefined)
    const wrapper = await mountList()
    await mountDialogHost()

    await wrapper.find('.icon-button--danger').trigger('click')
    await flushPromises()

    expect(bodyText('[data-testid="app-dialog"]')).toContain('删除后它进回收站')
    expect(bodyText('[data-testid="app-dialog"]')).toContain('投后端的那一版')
    expect(deleteSpy).toHaveBeenCalledTimes(0)

    await bodyClick('[data-testid="app-dialog-cancel"]')
    expect(deleteSpy).toHaveBeenCalledTimes(0)

    // 反面对照：确认这条路真的删得掉，否则上面那句「没删」只是因为按钮根本没接上。
    await wrapper.find('.icon-button--danger').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-confirm"]')
    expect(deleteSpy).toHaveBeenCalledTimes(1)
    expect(deleteSpy).toHaveBeenCalledWith('profile-under-test')
  })

  it('有未保存修改时点「回收站」：先问，确认才保存并跳转', async () => {
    const resume = seedProfiles()
    const saveSpy = vi.spyOn(resume, 'save').mockResolvedValue(SAVE_RESULT)
    const wrapper = await mountList()
    await mountDialogHost()
    resume.dirty = true

    await wrapper.find('.menu-action--ghost').trigger('click')
    await flushPromises()

    // 弹层出现的那一瞬间：既没保存也没跳转。
    expect(bodyText('[data-testid="app-dialog"]')).toContain('上一次保存的那一版')
    expect(saveSpy).toHaveBeenCalledTimes(0)
    expect(router.currentRoute.value.name).toBe('job-board')

    await bodyClick('[data-testid="app-dialog-confirm"]')
    expect(saveSpy).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.name).toBe('recycle-bin')
  })

  it('这条路取消就不跳转；干净状态（没有未保存修改）直接跳转，不打扰用户', async () => {
    const resume = seedProfiles()
    const saveSpy = vi.spyOn(resume, 'save').mockResolvedValue(SAVE_RESULT)
    const wrapper = await mountList()
    await mountDialogHost()
    resume.dirty = true

    await wrapper.find('.menu-action--ghost').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-cancel"]')

    expect(saveSpy).toHaveBeenCalledTimes(0)
    expect(router.currentRoute.value.name).toBe('job-board')

    // 对照组：dirty 为 false 时这整个问答都不该发生。
    // （原来这里要重新展开 Popover；列表常驻之后直接再点一次即可，行为没变、仪式少了。）
    resume.dirty = false
    await wrapper.find('.menu-action--ghost').trigger('click')
    await flushPromises()
    expect(bodyText('[data-testid="app-dialog"]')).toBe('')
    expect(router.currentRoute.value.name).toBe('recycle-bin')
  })

  it('保存失败时不再弹原生 alert，而是当面说明并停在原页', async () => {
    const resume = seedProfiles()
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => undefined)
    const { ApiError } = await import('@/api/client')
    vi.spyOn(resume, 'save').mockRejectedValue(
      new ApiError('保存被拒绝了。', 422, 'VALIDATION_ERROR'),
    )
    const wrapper = await mountList()
    await mountDialogHost()
    resume.dirty = true

    await wrapper.find('.menu-action--ghost').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-confirm"]')

    expect(alertSpy).toHaveBeenCalledTimes(0)
    expect(bodyText('[data-testid="app-dialog"]')).toContain('没保存成功，所以停在这一页')
    expect(bodyText('[data-testid="app-dialog"]')).toContain('保存被拒绝了。')
    expect(router.currentRoute.value.name).toBe('job-board')

    await bodyClick('[data-testid="app-dialog-confirm"]')
    alertSpy.mockRestore()
  })

  it('`window.confirm` 一次都没被调用（防的是「将来有人觉得原生省事又改回去」）', async () => {
    const resume = seedProfiles()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(resume, 'deleteProfile').mockResolvedValue(undefined)
    vi.spyOn(resume, 'save').mockResolvedValue(SAVE_RESULT)
    const wrapper = await mountList()
    await mountDialogHost()

    await wrapper.find('.icon-button--danger').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-confirm"]')

    expect(confirmSpy).toHaveBeenCalledTimes(0)
    confirmSpy.mockRestore()
  })

  it('去回收站前先发 close：抽屉该收起，常驻栏忽略它即可', async () => {
    const wrapper = await mountList()
    await wrapper.find('.menu-action--ghost').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(router.currentRoute.value.name).toBe('recycle-bin')
  })
})
