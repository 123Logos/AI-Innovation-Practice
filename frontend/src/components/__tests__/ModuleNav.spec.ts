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
 * 走**真实路由表**（与 `AppLayout.spec.ts` 同一个理由）：要验的就是那张表本身。
 * 守卫要求登录，所以先把 auth store 摆成「已登录且 `/auth/me` 无需再问」。
 */

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import ModuleNav from '@/components/ModuleNav.vue'
import { APP_MODULES } from '@/config/modules'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'

let pinia: Pinia
const mounted: VueWrapper[] = []

async function mountAt(path: string): Promise<VueWrapper> {
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
  it('页签数量、文案与 href 逐项等于 APP_MODULES', async () => {
    const wrapper = await mountAt('/jobs')
    const tabs = wrapper.findAll('.module-tab')

    expect(tabs.map((tab) => tab.text())).toEqual(APP_MODULES.map((module) => module.name))
    // href 由路由表解析出来：模块表的 `routeName` 必须真的能落地（L5 的同一件事）。
    for (const [index, module] of APP_MODULES.entries()) {
      const expected = router.resolve({ name: module.routeName }).fullPath
      expect(tabs[index]?.attributes('href')).toBe(expected)
    }
  })

  it('是一条横向的页签栏（<nav> + 列表），不是下拉或按钮组', async () => {
    const wrapper = await mountAt('/jobs')

    expect(wrapper.element.tagName).toBe('NAV')
    expect(wrapper.attributes('aria-label')).toBe('模块导航')
    // 七个页签都在同一个 <ul> 里并排：用按钮的话键盘与读屏语义都会变成「动作」而不是「页面」。
    expect(wrapper.findAll('.module-nav__track > li')).toHaveLength(APP_MODULES.length)
    expect(wrapper.findAll('.module-tab').every((tab) => tab.element.tagName === 'A')).toBe(true)
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
      const wrapper = await mountAt(path)
      if (activeTab(wrapper) === null) uncovered.push(path)
      wrapper.unmount()
    }

    // 反向护栏：扫到的路由不能是空的，否则这条会因为「一条都没测」而假绿。
    expect(mountedPaths.length).toBeGreaterThan(10)
    expect(uncovered).toEqual([])
  })
})
