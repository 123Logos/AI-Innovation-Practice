/**
 * 顶栏交互单测（B3 · ADR-011 议题 3.D，批次 F3 第二片改为右侧用户抽屉）。
 *
 * 只测**结构与状态**，不测像素：jsdom 没有布局引擎，scoped 样式与
 * `@media (max-width: 760px)` 在这里根本不生效，所以本文件断言的是
 * 「谁在抽屉里、谁在一级项上、`aria-expanded` 有没有跟着开合」这类 DOM/属性判据。
 * 375px 下顶栏是否真的回到单行，要靠真实视口量（数值附在 PR）。
 *
 * 三组用例对应监督者点名的验收项（第一组在 F3 第二片按裁定改写：`ROADMAP.md` §1 F3 行
 * 那句「三段断言显式改写，不许红着过去」说的就是这里）：
 * 1. 右侧用户抽屉的开关（含 `aria-expanded`、Esc、点外关闭、点触发按钮算开合）；
 * 2. 窄屏下「岗位库」仍然是文字链接、且没有被收进抽屉；
 * 3. `handleLogout()` 之后岗位 store 为空，并钉住 `resume.reset()` / `job.reset()`
 *    都在 `auth.signOut()` **之前**这个顺序——顺序反了就会出现「下一个账号
 *    短暂看到上一个人的数据」那一帧。
 *
 * 路由用一份本地 memory router，而不是 `@/router`：
 * 后者带着全局守卫，登出时 `replace({ name: 'login' })` 会触发 `/auth/me` 校验，
 * 那是「登录态怎么守」的另一条验收线，这里要的是「跳到登录页」这个事实本身。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router, type RouteRecordRaw } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { authApi } from '@/api'
import { REFRESH_TOKEN_STORAGE_KEY, TOKEN_STORAGE_KEY } from '@/api/client'
import type { jobsApi as JobsContract } from '@/api'
import AppTopBar from '@/components/AppTopBar.vue'
import { useAuthStore } from '@/stores/auth'
import { useJobStore } from '@/stores/job'
import { useResumeStore } from '@/stores/resume'

/** 空组件：本地路由的占位目标，测试只关心 `currentRoute.name`。 */
const BLANK = { template: '<div />' }

/** 挂载出来的实例统一在这里收口，避免组件留在 `document` 上的监听器串到下一个用例。 */
const mounted: { unmount: () => void }[] = []

let pinia: Pinia

function makeRouter(): Router {
  const routes: RouteRecordRaw[] = [
    { path: '/jobs', name: 'job-board', component: BLANK },
    { path: '/trash', name: 'recycle-bin', component: BLANK },
    { path: '/login', name: 'login', component: BLANK },
  ]
  return createRouter({ history: createMemoryHistory(), routes })
}

/**
 * 顶栏挂到 `document` 上的那些监听（基座的 pointerdown / keydown、顶栏自己管「新建简历」
 * 内联表单的 keydown）要求组件真的挂进 `document.body`，否则从浮层元素上冒泡的事件到不了
 * `document`，「点外关闭」那条就会假绿。
 */
async function mountTopBar() {
  const router = makeRouter()
  await router.push('/jobs')
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(AppTopBar, { attachTo, global: { plugins: [pinia, router] } })
  mounted.push(wrapper)
  return { wrapper, router }
}

/** 从 `mountTopBar()` 里取 wrapper 的类型，免得在下面把泛型抄一遍。 */
type TopBarWrapper = Awaited<ReturnType<typeof mountTopBar>>['wrapper']

/** 把岗位 store 塞成「刚看过上一个人的数据」的样子。 */
function seedJobStore(): void {
  const job = useJobStore()
  job.jobs = [{ id: 'job-of-previous-user' }] as unknown as JobsContract.JobSummary[]
  job.total = 120
  job.page = 3
  job.detail = { id: 'job-detail-of-previous-user' } as unknown as JobsContract.JobRead
  job.trash = [{ id: 'job-trash-of-previous-user' }] as unknown as JobsContract.JobSummary[]
  job.requirementCoverage = [{ name: 'Python', status: 'covered', matched_form: ' Python ' }]
  job.filter.keyword = '字节'
  job.filter.status = 'draft'
  job.filter.includeExpired = true
}

/**
 * 点「退出登录」在用户是两步：先开右侧抽屉，再点里面那一行。
 * 抽屉是 `v-if`，不先开就找不到那一行——这正是清单 4 改出来的新结构，
 * 所以这两步写在这里而不是各用例里抄四遍。
 */
async function openDrawerAndLogout(wrapper: TopBarWrapper): Promise<void> {
  await wrapper.find('.account-trigger').trigger('click')
  ;(document.body.querySelector('.drawer-item--danger') as HTMLElement).click()
}

/*
 * 抽屉 `Teleport` 到 `document.body`（顶栏的 `backdrop-filter` 会成为 fixed 后代的包含块，
 * 留在顶栏里抽屉只有顶栏那一条高，两档实测量到过），所以查它必须走 `document`：
 * `wrapper.find()` 对 Teleport 出去的节点**永远返回空**，用它断言「关掉之后查不到」是恒绿假用例。
 */
const drawerEl = (): Element | null => document.body.querySelector('#user-drawer')
const drawerRootEl = (): Element | null => document.body.querySelector('.drawer-root')
const drawerText = (): string => drawerEl()?.textContent?.replace(/\s+/g, ' ') ?? ''

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  sessionStorage.clear()
  document.body.innerHTML = ''
  // 顶栏默认按「已登录」渲染：`account-name` 读 displayName，登出用例读 token。
  const auth = useAuthStore()
  auth.user = {
    id: 'user-under-test',
    username: 'tester',
    email: null,
    created_at: '2026-11-05T10:00:00+08:00',
  }
  auth.token = 'access-token-under-test'
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
})

describe('右侧用户抽屉的开关（清单 4，批次 F3 第二片）', () => {
  it('默认收起：抽屉整个不在 DOM 里，`aria-expanded=false` 且不指向不存在的 id', async () => {
    const { wrapper } = await mountTopBar()
    const trigger = wrapper.find('.account-trigger')

    expect(trigger.exists()).toBe(true)
    expect(trigger.attributes('aria-expanded')).toBe('false')
    // 原来是恒挂载 + 靠 CSS 类收起，`aria-controls` 因此可以写死。
    // 抽屉改成 `v-if` 后，收起态指向一个不存在的 id 属于假声明，所以这里断言它**没有**。
    expect(trigger.attributes('aria-controls')).toBeUndefined()
    expect(drawerRootEl()).toBeNull()
  })

  it('点「账户」展开 → `aria-expanded=true`、`aria-controls` 指到真实抽屉、箭头翻转；再点一次收起', async () => {
    const { wrapper } = await mountTopBar()
    const trigger = wrapper.find('.account-trigger')

    await trigger.trigger('click')
    expect(trigger.attributes('aria-expanded')).toBe('true')
    expect(trigger.attributes('aria-controls')).toBe('user-drawer')
    // `aria-controls` 指过去必须真有其人，否则这个属性只是好看。
    const drawer = drawerEl()
    expect(drawer).not.toBeNull()
    expect(drawerText()).toContain('AI 设置')
    expect(drawerText()).toContain('退出登录')
    expect(wrapper.find('.account-caret').classes()).toContain('account-caret--open')

    await trigger.trigger('click')
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(drawerEl()).toBeNull()
    expect(wrapper.find('.account-caret').classes()).not.toContain('account-caret--open')
  })

  it('Esc 关闭抽屉：这条由 `OverlayPanel` 基座负责，顶栏没有为它再加一个 keydown 分支', async () => {
    const { wrapper } = await mountTopBar()
    await wrapper.find('.account-trigger').trigger('click')
    expect(drawerEl()).not.toBeNull()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()

    expect(drawerEl()).toBeNull()
    expect(wrapper.find('.account-trigger').attributes('aria-expanded')).toBe('false')
  })

  it('点遮罩与点抽屉外都关闭；点抽屉内的两行不关闭', async () => {
    const { wrapper } = await mountTopBar()
    await wrapper.find('.account-trigger').trigger('click')
    const drawer = drawerEl()
    expect(drawer).not.toBeNull()

    // 抽屉里：pointerdown 命中面板自身 → 基座判为「里面」，不该关。
    drawer?.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(drawerEl()).not.toBeNull()

    // 页面别处：顶栏之外算「外面」→ 该关。
    document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(drawerEl()).toBeNull()

    // 遮罩：重开一次点遮罩，这是手机用户最常用的关闭手势。
    // 遮罩是面板的**兄弟**而不是子节点，所以基座把它算成「外面」——抽屉自己没写 click。
    await wrapper.find('.account-trigger').trigger('click')
    expect(drawerEl()).not.toBeNull()
    ;(document.body.querySelector('.drawer-mask') as Element).dispatchEvent(
      new MouseEvent('pointerdown', { bubbles: true }),
    )
    await flushPromises()
    expect(drawerEl()).toBeNull()
  })

  it('点触发按钮自己算「开合」：pointerdown 不冒泡到基座，所以不会先关再开', async () => {
    const { wrapper } = await mountTopBar()
    const trigger = wrapper.find('.account-trigger')

    await trigger.trigger('click')
    expect(drawerEl()).not.toBeNull()

    // 少了按钮上那句 `@pointerdown.stop`，这一步会让基座判为「点外面」先关，
    // 下一步 click 再被 toggle 打开——「再点一次收起」在真机上永远失效。
    // jsdom 的 `trigger('click')` 不带 pointerdown，所以这条要手工按真实顺序派发；
    // 这正是这个 bug 从旧的手写监听到现在一直没被测出来的原因。
    trigger.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(drawerEl()).not.toBeNull()

    await trigger.trigger('click')
    expect(drawerEl()).toBeNull()
  })

  it('两个浮层互斥：开抽屉会关简历切换器，反之亦然', async () => {
    const { wrapper } = await mountTopBar()
    const switcher = wrapper.find('.switcher-trigger')
    const account = wrapper.find('.account-trigger')

    await switcher.trigger('click')
    expect(switcher.attributes('aria-expanded')).toBe('true')

    await account.trigger('click')
    expect(switcher.attributes('aria-expanded')).toBe('false')
    expect(account.attributes('aria-expanded')).toBe('true')

    await switcher.trigger('click')
    expect(account.attributes('aria-expanded')).toBe('false')
    expect(switcher.attributes('aria-expanded')).toBe('true')
  })
})

describe('简历切换器的开合同样不被「点外面」抢跑', () => {
  it('pointerdown 落在触发按钮上不关菜单，随后的 click 才收起', async () => {
    const { wrapper } = await mountTopBar()
    const trigger = wrapper.find('.switcher-trigger')

    await trigger.trigger('click')
    expect(wrapper.find('.switcher-menu').exists()).toBe(true)

    // 与抽屉那一条同一个机制（按钮上的 `@pointerdown.stop`）。这个 bug 从旧的手写监听
    // 就在，只是那时菜单恒在 DOM 里、只切一个 CSS 类，所以没人发现它「再点一次关不掉」。
    trigger.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(wrapper.find('.switcher-menu').exists()).toBe(true)

    await trigger.trigger('click')
    expect(wrapper.find('.switcher-menu').exists()).toBe(false)
  })
})

describe('窄屏的入口组织（结构判据，不测像素）', () => {
  it('「岗位库」仍然是文字链接，而且没有被收进抽屉', async () => {
    const { wrapper } = await mountTopBar()
    const entry = wrapper.find('.jobs-entry')

    expect(entry.exists()).toBe(true)
    // 是 `<a>`（RouterLink 渲染成锚点）而不是按钮或图标，文案就是那三个汉字。
    expect(entry.element.tagName).toBe('A')
    expect(entry.text()).toBe('岗位库')
    expect(entry.attributes('href')).toBe('/jobs')
    // 关键判据：展开抽屉后它也不在里面。收进抽屉的入口在手机上等于没有入口。
    await wrapper.find('.account-trigger').trigger('click')
    const drawer = drawerEl()
    expect(drawer).not.toBeNull()
    expect(drawer?.contains(entry.element)).toBe(false)
    expect(entry.element.closest('.drawer-root')).toBeNull()
    expect(drawerText()).toContain('AI 设置')
    expect(drawerText()).toContain('退出登录')
    expect(drawerText()).not.toContain('岗位库')
  })

  it('.user-area 的直接子项是三个一级项，两个动作按钮让位给抽屉', async () => {
    const { wrapper } = await mountTopBar()
    // RouterLink 会自带 `router-link-active` / `aria-current`，所以按「已知入口类名」取值，
    // 而不是照抄整个 class 列表。
    const KNOWN = ['jobs-entry', 'profile-switcher', 'dirty-dot', 'account-trigger']
    const topLevel = wrapper
      .findAll('.user-area > *')
      .map((node) => KNOWN.find((cls) => node.classes().includes(cls)) ?? '(未知)')

    // 原来是 4 项（`user-name` 那个不说话的 span 单占一格），现在名字并进了账户按钮：
    // 一级项 3 个 = 岗位库 / 简历切换器 / 账户。窄屏 CSS 再把名字换成「账户」二字。
    expect(topLevel).toEqual(['jobs-entry', 'profile-switcher', 'account-trigger'])
    // 触发按钮里两个标签恒在 DOM，显示谁由 CSS 决定（宽屏名字、窄屏「账户」）。
    expect(wrapper.find('.account-trigger .account-name').text()).toBe('tester')
    expect(wrapper.find('.account-trigger .account-word').text()).toBe('账户')

    // 收起态抽屉整个不在 DOM 里（那两层 `display: contents` 的宽窄屏分叉随这一片删掉了）。
    // 这里不再对 `.ai-settings-button` / `.logout-button` 断言「不是直接子项」——那两个类
    // 已不存在，对不存在的类名断言 false 是一条恒绿的空检。
    expect(drawerRootEl()).toBeNull()

    await wrapper.find('.account-trigger').trigger('click')
    // 展开后 `.user-area` 仍然只有那 3 个一级项：抽屉 Teleport 到 body，不占顶栏的一格。
    expect(
      wrapper
        .findAll('.user-area > *')
        .map((node) => KNOWN.find((cls) => node.classes().includes(cls)) ?? '(未知)'),
    ).toEqual(['jobs-entry', 'profile-switcher', 'account-trigger'])
    expect(drawerRootEl()?.parentElement).toBe(document.body)
    expect(document.querySelectorAll('#user-drawer > button')).toHaveLength(2)

    // 两个动作在整页里**只有一处**：抽屉里那两行。这一条防的是半迁移——
    // 新抽屉建好却忘了删掉旧的一级按钮，用户就会看到两个「退出登录」。
    // 查询走 document：抽屉 Teleport 出去了，`wrapper.findAll('button')` 看不见它（会恒 0）。
    const labels = [...document.querySelectorAll('button')].map(
      (node) => node.textContent?.trim() ?? '',
    )
    expect(labels.filter((text) => text === 'AI 设置')).toHaveLength(1)
    expect(labels.filter((text) => text === '退出登录')).toHaveLength(1)
  })
})

describe('登出清空岗位 store（handleLogout 的不变量）', () => {
  it('点「退出登录」后岗位 store 全空、本地会话清掉、落到登录页', async () => {
    const { wrapper, router } = await mountTopBar()
    const job = useJobStore()
    const auth = useAuthStore()
    seedJobStore()
    localStorage.setItem(TOKEN_STORAGE_KEY, 'access-token-under-test')

    expect(job.jobs).not.toEqual([])

    await openDrawerAndLogout(wrapper)
    await flushPromises()

    expect(job.jobs).toEqual([])
    expect(job.total).toBe(0)
    expect(job.page).toBe(1)
    expect(job.detail).toBeNull()
    expect(job.trash).toEqual([])
    expect(job.requirementCoverage).toEqual([])
    // 筛选条件也是数据：留着会让下一个账号看到「上一个人筛过的那一屏」。
    expect(job.filter).toEqual({ keyword: '', status: 'published', includeExpired: false })

    expect(auth.token).toBeNull()
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('顺序判据：resume.reset() 与 job.reset() 都发生在 auth.signOut() 之前', async () => {
    const { wrapper } = await mountTopBar()
    const calls: string[] = []
    vi.spyOn(useResumeStore(), 'reset').mockImplementation(() => {
      calls.push('resume.reset')
    })
    vi.spyOn(useJobStore(), 'reset').mockImplementation(() => {
      calls.push('job.reset')
    })
    vi.spyOn(useAuthStore(), 'signOut').mockImplementation(async () => {
      calls.push('auth.signOut')
    })

    await openDrawerAndLogout(wrapper)
    await flushPromises()

    expect(calls).toEqual(['resume.reset', 'job.reset', 'auth.signOut'])
  })

  /**
   * 这一条钉的是一个**曾经真实存在的死 UI**：退出那一行绑的是 `auth.signingOut` 并显示「退出中…」，
   * 而 `signingOut` 只有 `signOut()` 会置真——那时顶栏调的是 `logout()`，
   * 所以这两个状态永远不可能出现，测试也无从写起。
   *
   * 注意这里**不能** mock `signOut()` 本身：`signingOut` 正是它内部置的，
   * 把它换掉等于把要测的东西测没了（会绿，但什么都没验）。
   * 只把最外层的撤销请求挂在半空，让真实的 store 逻辑跑完。
   */
  it('撤销请求在途时按钮禁用并显示「退出中…」，落地后恢复', async () => {
    const { wrapper } = await mountTopBar()
    localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, 'refresh-token-under-test')
    // `settle` 初值是空函数：忘了调用只会让这条用例超时（响的），
    // 而不是像 `release?.()` 那样静默跳过——那会留下一条恒绿的假用例。
    let settle = (): void => undefined
    const revoke = vi.spyOn(authApi, 'logout').mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          settle = () => resolve()
        }),
    )

    await openDrawerAndLogout(wrapper)
    await flushPromises()

    expect(revoke).toHaveBeenCalledTimes(1)
    expect(revoke).toHaveBeenCalledWith('refresh-token-under-test')
    const busy = document.body.querySelector('.drawer-item--danger')
    expect(busy?.hasAttribute('disabled')).toBe(true)
    expect(busy?.textContent).toContain('退出中')

    settle()
    await flushPromises()
    expect(document.body.querySelector('.drawer-item--danger')?.textContent).toContain('退出登录')
  })
})

/* ------------------------------------------------------------------ */
/*  批次 F1b：六处原生弹层里的顶栏两处，走的是同一个页面内弹窗           */
/* ------------------------------------------------------------------ */

describe('删除与去回收站走页面内弹层（批次 F1b，守则 C6）', () => {
  // 以下三个 helper 留在 describe 作用域里：批次 F1b 当时的理由是 `docs/ROADMAP.md` §1
  // 按行号引用了本文件的结构判据段，顶层多插十几行会把那条引用推到空行上。那条引用在
  // F3 已改成符号式，理由随之消失；但这三个 helper 只服务这一块用例，挪上去没有收益，就不动。
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

  function seedProfiles() {
    const resume = useResumeStore()
    resume.profiles = [
      {
        id: 'profile-under-test',
        title: '投后端的那一版',
        is_default: true,
        full_name: '张三',
        created_at: '2026-11-10T10:00:00+08:00',
        updated_at: '2026-11-10T10:00:00+08:00',
        deleted_at: null,
      },
    ] as typeof resume.profiles
    resume.currentProfileId = 'profile-under-test'
    return resume
  }

  it('点「删除」只打开弹层，此刻一次删除请求都没发；点「确认删除」才删', async () => {
    const resume = seedProfiles()
    const deleteSpy = vi.spyOn(resume, 'deleteProfile').mockResolvedValue(undefined)
    const { wrapper } = await mountTopBar()
    await mountDialogHost()

    await wrapper.find('.switcher-trigger').trigger('click')
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
    const { wrapper, router } = await mountTopBar()
    await mountDialogHost()
    resume.dirty = true

    await wrapper.find('.switcher-trigger').trigger('click')
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
    const { wrapper, router } = await mountTopBar()
    await mountDialogHost()
    resume.dirty = true

    await wrapper.find('.switcher-trigger').trigger('click')
    await wrapper.find('.menu-action--ghost').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-cancel"]')

    expect(saveSpy).toHaveBeenCalledTimes(0)
    expect(router.currentRoute.value.name).toBe('job-board')

    // 对照组：dirty 为 false 时这整个问答都不该发生。
    // （`goTrash` 一进来就收菜单，所以这里要重新打开——不是测试的仪式，是它的行为。）
    resume.dirty = false
    await wrapper.find('.switcher-trigger').trigger('click')
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
    const { wrapper, router } = await mountTopBar()
    await mountDialogHost()
    resume.dirty = true

    await wrapper.find('.switcher-trigger').trigger('click')
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
    const { wrapper } = await mountTopBar()
    await mountDialogHost()

    await wrapper.find('.switcher-trigger').trigger('click')
    await wrapper.find('.icon-button--danger').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-confirm"]')

    expect(confirmSpy).toHaveBeenCalledTimes(0)
    confirmSpy.mockRestore()
  })
})
