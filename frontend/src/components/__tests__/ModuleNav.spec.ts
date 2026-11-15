/**
 * 模块二级导航（`ModuleNav.vue`，2026-11-14 UI 收尾第 4 条）。
 *
 * 它替代了原来的「工作区切换器」弹层：模块入口从「点开才知道有什么」变成一条常驻横栏。
 * 用例分三组，分别守三件事：
 *
 * 1. **页签就是模块表**：数量、文案、href 全从 `APP_MODULES` 推导。写死一份清单会让
 *    「加一个模块」变成两处改动，且表改了而组件没跟上时，红的会是这张抄件而不是产品。
 * 2. **高亮是「归属」不是「名称相等」**：详情页（`/jobs/{id}`、`/interviews/{id}`）、
 *    新建 / 回收站、简历回收站都要落到正确的页签上——这层映射写在 `ROUTE_OWNER`，
 *    是唯一需要人维护的一张表。
 * 3. **没有一条路由会「谁都不高亮」**：遍历真实路由表，凡是 `requiresAuth` 的页面，
 *    都必须能归到某个页签。新加一条路由却忘了补别名时，这条会红——
 *    页面照常渲染、只是导航上没有任何一格亮着，那种问题肉眼很难发现。
 *
 * 2026-11-15 第 24 轮加第四组：**窄屏那一棵下拉**。它不是第二个入口——条目仍是同一个
 * `visibleModules`，只是 375px 上横滚 242px 的那一排换成了「当前模块 ▾ + 竖排菜单」。
 * jsdom 不跑媒体查询，所以这里能断的是「两棵名单相等 + 交互与基座一致」，
 * 「哪一棵在屏上」归 CSS 源码闸（`layoutGuards.spec.ts`）与 CDP 实测（守则 R2）。
 *
 * 走**真实路由表**（与 `AppLayout.spec.ts` 同一个理由）：要验的就是那张表本身。
 * 守卫要求登录，所以先把 auth store 摆成「已登录且 `/auth/me` 无需再问」。
 */

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import ModuleNav from '@/components/ModuleNav.vue'
import type { UserRole } from '@/api/types'
import { APP_MODULES, modulesVisibleTo } from '@/config/modules'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'

let pinia: Pinia
const mounted: VueWrapper[] = []

async function mountAt(path: string, role: UserRole = 'student'): Promise<VueWrapper> {
  // 角色直接写进 store：这一份测的是「导航按角色画什么」，不是 `/auth/me` 怎么把角色送进来
  // （那一格由 `stores/__tests__/auth.spec.ts` 管）。
  useAuthStore().role = role
  await router.push(path)
  await router.isReady()
  const wrapper = mount(ModuleNav, { global: { plugins: [pinia, router] } })
  mounted.push(wrapper)
  await flushPromises()
  return wrapper
}

/** 当前高亮的那个页签文案；没有就是 `null`（比空字符串更能区分「没高亮」）。 */
function activeTab(wrapper: VueWrapper): string | null {
  const active = wrapper
    .findAll('.module-tab')
    .find((node) => node.attributes('aria-current') === 'page')
  return active?.text() ?? null
}

/**
 * 等浮层的离场动效走完。
 *
 * 菜单包在 `<Transition name="ui-fade-down">` 里（与两个抽屉同一套做法），
 * Vue 要等一帧 + `setTimeout(duration)` 才真的把节点移出 DOM；jsdom 量到的时长是 0，
 * 所以等 30ms 真实时间就够，而不是「关不掉」。`aria-expanded` 是即时可读的判据，
 * 这一支等的是 DOM 层面那句「已经不在了」。
 */
async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 200))
  await flushPromises()
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  // 守卫要「有令牌 + `/auth/me` 不再发请求」。`ensureUser()` 见到内存里有 user 直接返回 true。
  const auth = useAuthStore()
  auth.token = 'test-token'
  // `UserRead` 只有五格（id / username / email / phone / created_at）——角色在
  // `role` 这个**独立的** store 字段上，不在 user 里。
  auth.user = {
    id: 'user-1',
    username: 'tester',
    email: 'tester@example.com',
    phone: null,
    created_at: '2026-11-01T09:00:00+08:00',
  }
  auth.role = 'student'
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
})

describe('页签来自模块表', () => {
  /*
   * 显式改写（2026-11-15，守则 R1 / L4）：这两条原来钉的是「页签逐项等于 APP_MODULES」，
   * 而这一轮加了角色过滤——学生看不见教师侧那两个入口。
   * 判据跟着换成「逐项等于 `modulesVisibleTo(role)`」：
   *  - 仍然是**从那张表推导**，不是抄一份名字（抄一份的话，表改了而用例没跟上，红的会是抄件）；
   *  - 数量、顺序、文案、href 四件事一格没放宽，只是量的对象从全表换成「这个角色能看的那一段」。
   */
  it('学生看到的页签数量、文案与 href 逐项等于 modulesVisibleTo("student")', async () => {
    const wrapper = await mountAt('/jobs', 'student')
    const expected = modulesVisibleTo('student')
    const tabs = wrapper.findAll('.module-tab')

    expect(tabs.map((tab) => tab.text())).toEqual(expected.map((module) => module.name))
    // href 由路由表解析出来：模块表的 `routeName` 必须真的能落地（L5 的同一件事）。
    for (const [index, module] of expected.entries()) {
      const resolved = router.resolve({ name: module.routeName }).fullPath
      expect(tabs[index]?.attributes('href')).toBe(resolved)
    }
  })

  it('教师侧角色看到的是全表（那两个教师入口对学生是隐藏的，对教师不是）', async () => {
    const wrapper = await mountAt('/jobs', 'teacher')

    expect(wrapper.findAll('.module-tab').map((tab) => tab.text())).toEqual(
      APP_MODULES.map((module) => module.name),
    )
  })

  /*
   * 显式改写（2026-11-15 第 24 轮，守则 R1）：原来这条的标题里写着「不是下拉或按钮组」，
   * 那是对**当时只有一棵**页签栏的断言。这一轮窄屏多了下拉那一棵，所以判据换成两件事：
   * ① 宽屏那一排仍然是 `<nav>` + 同一个 `<ul>` 里的链接（结构一格没放宽，
   *    把页签换成按钮组仍然会红）；② 窄屏那一棵是**同一份名单的第二种形态**，
   *    不是第二个入口——它读的还是同一个 `visibleModules`，见下面那一组用例。
   */
  it('宽屏那一排仍是横向页签栏（<nav> + 列表 + 链接），不是按钮组', async () => {
    const wrapper = await mountAt('/jobs')

    expect(wrapper.element.tagName).toBe('NAV')
    expect(wrapper.attributes('aria-label')).toBe('模块导航')
    // 页签都在同一个 <ul> 里并排：用按钮的话键盘与读屏语义都会变成「动作」而不是「页面」。
    expect(wrapper.findAll('.module-nav__track > li')).toHaveLength(
      modulesVisibleTo('student').length,
    )
    expect(wrapper.findAll('.module-tab').every((tab) => tab.element.tagName === 'A')).toBe(true)
  })
})

describe('窄屏形态：当前模块 + 下拉菜单（2026-11-15 第 24 轮）', () => {
  /**
   * 为什么要有这一棵：375px 上七个页签的轨道要横向滚 242px（本轮 CDP 实测），
   * 滚过去的那几个既看不见也点不到。窄屏改成一行「当前模块 ▾」+ 竖排菜单，
   * 轨道与收起按钮则由 CSS 藏掉——这一行没有第二件东西，所以不存在「按钮被挤出屏幕」。
   */
  it('菜单条目逐项等于 modulesVisibleTo(role)，href 由路由表解析', async () => {
    const wrapper = await mountAt('/jobs', 'teacher')
    await wrapper.get('.module-nav__menu-trigger').trigger('click')

    const expected = modulesVisibleTo('teacher')
    const items = wrapper.findAll('.module-nav__menu-item')
    expect(items.map((item) => item.text())).toEqual(expected.map((module) => module.name))
    for (const [index, module] of expected.entries()) {
      expect(items[index]?.attributes('href')).toBe(
        router.resolve({ name: module.routeName }).fullPath,
      )
    }
  })

  it('两棵读同一份名单：页签文案与菜单文案逐项相等（不是第二份数组）', async () => {
    const wrapper = await mountAt('/jobs', 'student')
    const tabs = wrapper.findAll('.module-tab').map((tab) => tab.text())

    await wrapper.get('.module-nav__menu-trigger').trigger('click')
    expect(wrapper.findAll('.module-nav__menu-item').map((item) => item.text())).toEqual(tabs)
  })

  it('触发按钮说的是当前模块；当前页不属于任何模块时说「可用模块」', async () => {
    const jobs = await mountAt('/jobs')
    expect(jobs.get('.module-nav__menu-name').text()).toBe('岗位库')
    // 详情页归岗位库（`ROUTE_OWNER`），下拉那句跟着一起算，不是「无归属」。
    await jobs.get('.module-nav__menu-trigger').trigger('click')
    expect(
      jobs.find('.module-nav__menu-item[aria-current="page"]')?.text(),
    ).toBe('岗位库')

    const welcome = await mountAt('/welcome')
    expect(welcome.get('.module-nav__menu-name').text()).toBe('可用模块')
  })

  it('点一条菜单项：跳转并且面板收起（老板指令「选完后自动收起并跳转」）', async () => {
    const wrapper = await mountAt('/jobs', 'teacher')
    await wrapper.get('.module-nav__menu-trigger').trigger('click')
    expect(wrapper.find('.module-nav__menu').exists()).toBe(true)

    const target = wrapper
      .findAll('.module-nav__menu-item')
      .find((item) => item.attributes('href') === '/organization')
    expect(target, '菜单里没有组织管理入口').toBeDefined()
    await target?.trigger('click')
    await settle()

    expect(router.currentRoute.value.path).toBe('/organization')
    expect(wrapper.find('.module-nav__menu').exists()).toBe(false)
    expect(wrapper.get('.module-nav__menu-trigger').attributes('aria-expanded')).toBe('false')
  })

  it('开合、Esc、点外面都归浮层基座（守则 L3），菜单本身是 role="group"', async () => {
    const wrapper = await mountAt('/jobs', 'student')
    expect(wrapper.get('.module-nav__menu-trigger').attributes('aria-expanded')).toBe('false')
    // `aria-controls` 只在展开时给：面板不在 DOM 里时指一个不存在的 id 是假声明。
    expect(wrapper.get('.module-nav__menu-trigger').attributes('aria-controls')).toBeUndefined()

    await wrapper.get('.module-nav__menu-trigger').trigger('click')
    const panel = wrapper.get('.module-nav__menu')
    expect(panel.attributes('role')).toBe('group')
    expect(panel.attributes('aria-label')).toBe('切换模块')
    expect(wrapper.get('.module-nav__menu-trigger').attributes('aria-controls')).toBe(
      'module-nav-menu',
    )

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settle()
    expect(wrapper.find('.module-nav__menu').exists()).toBe(false)

    // 再开一次，点面板外面（body）也应收起——这条走的就是基座那一份 pointerdown。
    await wrapper.get('.module-nav__menu-trigger').trigger('click')
    document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    await settle()
    expect(wrapper.find('.module-nav__menu').exists()).toBe(false)
  })

  it('角色读不到（null）时菜单也是空的：窄屏不许绕过 fail-closed', async () => {
    const wrapper = await mountAt('/jobs', null as unknown as UserRole)
    await wrapper.get('.module-nav__menu-trigger').trigger('click')

    expect(wrapper.findAll('.module-nav__menu-item')).toHaveLength(0)
  })
})

describe('按角色过滤入口（2026-11-15，老板指令「基于角色的导航过滤」）', () => {
  it('学生看不到「学情看板」与「组织管理」两个页签', async () => {
    const wrapper = await mountAt('/jobs', 'student')
    const names = wrapper.findAll('.module-tab').map((tab) => tab.text())

    const staffOnly = APP_MODULES.filter((module) => !module.visibleToRoles.includes('student'))
    expect(staffOnly.length).toBeGreaterThan(0)
    for (const module of staffOnly) {
      expect(names, `${module.name} 不该出现在学生的导航里`).not.toContain(module.name)
    }
  })

  it('school_admin 两个教师入口都在', async () => {
    const wrapper = await mountAt('/jobs', 'school_admin')
    const names = wrapper.findAll('.module-tab').map((tab) => tab.text())

    for (const module of APP_MODULES.filter((m) => m.visibleToRoles.includes('school_admin'))) {
      expect(names).toContain(module.name)
    }
  })

  it('角色还没读到（null）时一个页签都不画——fail-closed，不猜身份', async () => {
    const wrapper = await mountAt('/jobs', null as unknown as UserRole)
    expect(wrapper.findAll('.module-tab')).toHaveLength(0)
  })
})

describe('手动收起（2026-11-14 UI 收尾第 1 条：显隐由人控制，不由滚动控制）', () => {
  const trackHidden = (wrapper: VueWrapper): boolean =>
    (wrapper.find('.module-nav__track').attributes('style') ?? '').includes('display: none')

  it('默认展开；点一次页签整条藏起来，只留当前模块与展开入口；再点回来', async () => {
    const wrapper = await mountAt('/jobs')
    expect(trackHidden(wrapper)).toBe(false)
    expect(wrapper.find('.module-nav__toggle').attributes('aria-expanded')).toBe('true')

    await wrapper.find('.module-nav__toggle').trigger('click')
    expect(trackHidden(wrapper)).toBe(true)
    // 收起态仍然要说得出「你在哪个模块」，那句话来自模块表而不是第二份文案。
    expect(wrapper.find('.module-nav__current').text()).toBe('岗位库')
    expect(wrapper.find('.module-nav__toggle').attributes('aria-expanded')).toBe('false')

    await wrapper.find('.module-nav__toggle').trigger('click')
    expect(trackHidden(wrapper)).toBe(false)
    expect(wrapper.findAll('.module-nav__track > li')).toHaveLength(
      modulesVisibleTo('student').length,
    )
  })

  it('收起态那句模块名按「归属」算：详情页收起来仍写它所属的模块', async () => {
    const wrapper = await mountAt('/jobs/job-under-test')
    await wrapper.find('.module-nav__toggle').trigger('click')
    expect(wrapper.find('.module-nav__current').text()).toBe('岗位库')
  })
})

describe('高亮按「归属」判定', () => {
  it('一级页面：/jobs → 岗位库、/assessments → 能力测评', async () => {
    expect(activeTab(await mountAt('/jobs'))).toBe('岗位库')
    expect(activeTab(await mountAt('/assessments'))).toBe('能力测评')
  })

  it('详情页仍归它的列表页：/jobs/{id} → 岗位库、/interviews/{id} → 模拟面试', async () => {
    /*
     * 这两条是「高亮不能只看路由名相等」的全部理由：详情页的名字与模块页签不同，
     * 若只比 `routeName`，进详情页后整条导航会一格都不亮——用户以为自己离开了那个模块。
     */
    expect(activeTab(await mountAt('/jobs/job-under-test'))).toBe('岗位库')
    expect(activeTab(await mountAt('/interviews/iv-under-test'))).toBe('模拟面试')
  })

  it('新建 / 回收站也归岗位库；简历回收站归简历工作台', async () => {
    expect(activeTab(await mountAt('/jobs/new'))).toBe('岗位库')
    expect(activeTab(await mountAt('/jobs/trash'))).toBe('岗位库')
    // 简历回收站是简历工作台的下钻页（入口在简历列表底部），不该出现第八个页签。
    expect(activeTab(await mountAt('/trash'))).toBe('简历工作台')
  })

  it('工作台与访客页各自的情况：/dashboard 高亮工作台，/welcome 没有高亮', async () => {
    expect(activeTab(await mountAt('/dashboard'))).toBe('工作台')
    // 访客态不挂外框（也就没有这条导航），这条只是把「没有归属时返回 null」钉住。
    expect(activeTab(await mountAt('/welcome'))).toBeNull()
  })
})

describe('没有一条受保护路由会「谁都不高亮」', () => {
  it('真实路由表里每个 requiresAuth 的记录都能归到某个页签', async () => {
    const mountedPaths: string[] = []
    for (const record of router.getRoutes()) {
      if (!record.meta.requiresAuth || !record.name) continue
      if (record.path.includes(':')) {
        // 带参数的路径用占位值替换，只要能解析出那条记录即可（高亮看的是 route.name）。
        mountedPaths.push(record.path.replace(/:[^/]+/g, 'placeholder'))
        continue
      }
      mountedPaths.push(record.path)
    }

    const uncovered: string[] = []
    for (const path of mountedPaths) {
      // 用教师侧角色来跑这一条：它看得到全表，所以「有没有页签归属」这件事
      // 不会被角色过滤干扰。学生看不见的入口由上面那一组用例单独钉。
      const wrapper = await mountAt(path, 'teacher')
      if (activeTab(wrapper) === null) uncovered.push(path)
      wrapper.unmount()
    }

    // 反向护栏：扫到的路由不能是空的，否则这条会因为「一条都没测」而假绿。
    expect(mountedPaths.length).toBeGreaterThan(10)
    expect(uncovered).toEqual([])
  })
})
