/**
 * 工作区切换器单测（批次 F3 第四片，老板前端清单第 6 条）。
 *
 * 走**真实路由表**而不是自造 memory router：这个组件的判据有一半是「它认得出自己在哪」，
 * 而那件事的答案来自 `router.resolve()` 出来的真实路径。自造一张 `/jobs` 表等于把被测
 * 对象换掉——真实路径改了它照样绿。守卫要登录，所以先把 auth store 摆成「已登录且
 * `/auth/me` 无需再问」的样子（`ensureUser()` 见到内存里有 user 就直接返回，不发请求）。
 *
 * 文案与卡片数量一律对着 `APP_MODULES` 断言，不在这里抄一份名字：抄一份的话，
 * 表改了而组件没跟上时，红的会是这张抄件而不是产品（守则 L4 的同一件事）。
 *
 * 不测像素：弹层贴顶栏下方、375px 不顶出视口、卡片横向排布，jsdom 没有布局引擎，
 * 归 375 / 1280 两档真实视口量（数值附在 PR）。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import WorkspaceSwitcher from '@/components/WorkspaceSwitcher.vue'
import { APP_MODULES } from '@/config/modules'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'
import { settled } from '@/__tests__/setup'

let pinia: Pinia
const mounted: VueWrapper[] = []

async function mountSwitcher(path = '/'): Promise<VueWrapper> {
  await router.push(path)
  await router.isReady()
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(WorkspaceSwitcher, { attachTo, global: { plugins: [pinia, router] } })
  mounted.push(wrapper)
  await flushPromises()
  return wrapper
}

/** 真实浏览器的手势顺序：按下时按钮拿到焦点 → pointerdown → click。 */
async function tap(wrapper: VueWrapper, selector: string): Promise<void> {
  const node = wrapper.find(selector)
  if (!node.exists()) throw new Error(`点不到：${selector}`)
  // jsdom 的 `trigger('click')` 不移动焦点，真浏览器的按下会。少了这一步，
  // 基座记下的「打开它的那个元素」就是 body，焦点归还那条用例会量到假结果。
  ;(node.element as HTMLElement).focus()
  node.element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
  await node.trigger('click')
  await flushPromises()
  // 退出有 240ms 的淡出（批次 F4）：「关掉之后不在了」要等它收尾，否则量到的是正在退场的那一个。
  await settled()
}

const cards = (wrapper: VueWrapper) => wrapper.findAll('.workspace-card')
const cardNames = (wrapper: VueWrapper) => cards(wrapper).map((node) => node.text())

beforeEach(async () => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  sessionStorage.clear()
  document.body.innerHTML = ''

  const auth = useAuthStore()
  auth.token = 'access-token-under-test'
  auth.user = {
    id: 'user-under-test',
    username: 'tester',
    email: null,
    created_at: '2026-11-10T10:00:00+08:00',
  }

  // vue-router 的首次导航只在 install 时补一次，会把本条用例自己 push 的目标顶掉
  // （同一处理见 `AppLayout.spec.ts` 的 beforeEach）。
  const bootstrap = mount({ render: () => null }, { global: { plugins: [pinia, router] } })
  await router.isReady()
  bootstrap.unmount()
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
})

describe('收起态：只有一个触发按钮', () => {
  it('弹层整个不在 DOM 里，aria-expanded 为假、不给 aria-controls', async () => {
    const wrapper = await mountSwitcher()
    const trigger = wrapper.find('.workspace-trigger')

    expect(trigger.exists()).toBe(true)
    expect(trigger.attributes('aria-expanded')).toBe('false')
    // 收起态指一个不存在的 id 是假声明，所以这里必须是「没有这个属性」。
    expect(trigger.attributes('aria-controls')).toBeUndefined()
    expect(wrapper.find('.workspace-popover').exists()).toBe(false)
  })

  it('两个标签恒在 DOM：宽屏显当前工作区名，窄屏显「切换」（显示谁由 CSS 决定）', async () => {
    const wrapper = await mountSwitcher()

    expect(wrapper.find('.workspace-trigger__name').text()).toBe('简历工作台')
    expect(wrapper.find('.workspace-trigger__word').text()).toBe('切换')
    expect(wrapper.find('.workspace-trigger').attributes('aria-label')).toBe(
      '切换工作区，当前：简历工作台',
    )
  })
})

describe('展开态：横向卡片来自模块表', () => {
  it('点触发按钮 → 弹层出现，卡片数量与文案逐项等于模块表', async () => {
    const wrapper = await mountSwitcher()
    await tap(wrapper, '.workspace-trigger')

    const panel = wrapper.find('.workspace-popover')
    expect(panel.exists()).toBe(true)
    expect(panel.attributes('id')).toBe('workspace-panel')
    expect(panel.attributes('aria-label')).toBe('切换工作区')
    expect(wrapper.find('.workspace-trigger').attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('.workspace-trigger').attributes('aria-controls')).toBe('workspace-panel')

    expect(cardNames(wrapper)).toEqual(APP_MODULES.map((module) => module.name))
    // 卡片是导航不是动作：每张都是带 href 的 <a>，指向路由表解析出来的真实路径。
    for (const [index, module] of APP_MODULES.entries()) {
      const card = cards(wrapper)[index]
      expect(card?.element.tagName).toBe('A')
      expect(card?.attributes('href')).toBe(router.resolve({ name: module.routeName }).path)
    }
  })

  it('卡片横向排在一个容器里（不是竖排列表），图标是 SVG 不是字符', async () => {
    const wrapper = await mountSwitcher()
    await tap(wrapper, '.workspace-trigger')

    expect(wrapper.findAll('.workspace-cards')).toHaveLength(1)
    expect(wrapper.findAll('.workspace-cards > .workspace-card')).toHaveLength(APP_MODULES.length)
    const icons = wrapper.findAll('.workspace-card__icon')
    expect(icons).toHaveLength(APP_MODULES.length)
    for (const icon of icons) {
      expect(icon.element.tagName.toLowerCase()).toBe('svg')
      expect(icon.attributes('aria-hidden')).toBe('true')
    }
  })

  it('已经能到的模块都在卡片里；未实现的模块连禁用态都没有', async () => {
    const wrapper = await mountSwitcher()
    await tap(wrapper, '.workspace-trigger')

    // 批次 B5 落地「组织管理」、批次 B11a 落地「模拟面试」之后，**两者**都已能到达，
    // 所以都必须出现在卡片里。两边各自的版本只翻转了自己那一半（另一半仍写
    // `not.toContain`），合并后必须一起翻过来——否则就是「文档说已交付、判据说没实现」。
    expect(cardNames(wrapper)).toContain('组织管理')
    expect(cardNames(wrapper)).toContain('模拟面试')
    expect(wrapper.findAll('.workspace-card[aria-disabled="true"]')).toHaveLength(0)
    expect(wrapper.findAll('.workspace-card--disabled')).toHaveLength(0)
  })

  it('打开时焦点进第一张卡片（键盘用户不必从头 Tab 一遍）', async () => {
    const wrapper = await mountSwitcher()
    await tap(wrapper, '.workspace-trigger')

    expect(document.activeElement).toBe(cards(wrapper)[0]?.element)
  })
})

describe('它认得出自己在哪（最长前缀匹配，路径来自路由表）', () => {
  it('/ → 简历工作台那张卡片是当前，另一张不是', async () => {
    const wrapper = await mountSwitcher('/')
    await tap(wrapper, '.workspace-trigger')

    expect(wrapper.find('.workspace-trigger__name').text()).toBe('简历工作台')
    const current = cards(wrapper).filter((node) => node.attributes('aria-current') === 'page')
    expect(current).toHaveLength(1)
    expect(current[0]?.text()).toBe('简历工作台')
  })

  it('/jobs → 岗位库那张卡片是当前', async () => {
    const wrapper = await mountSwitcher('/jobs')
    await tap(wrapper, '.workspace-trigger')

    expect(wrapper.find('.workspace-trigger__name').text()).toBe('岗位库')
    const current = cards(wrapper).filter((node) => node.attributes('aria-current') === 'page')
    expect(current).toHaveLength(1)
    expect(current[0]?.text()).toBe('岗位库')
  })

  it('/jobs/:id 这类子页面仍归岗位库（不是「认不出」）', async () => {
    const wrapper = await mountSwitcher('/jobs/job-under-test')
    expect(router.currentRoute.value.name).toBe('job-detail')
    await tap(wrapper, '.workspace-trigger')

    expect(wrapper.find('.workspace-trigger__name').text()).toBe('岗位库')
  })

  it('/trash（简历回收站）归简历工作台：/ 是最短前缀，兜得住没有专属卡片的页面', async () => {
    const wrapper = await mountSwitcher('/trash')
    expect(router.currentRoute.value.name).toBe('recycle-bin')
    await tap(wrapper, '.workspace-trigger')

    expect(wrapper.find('.workspace-trigger__name').text()).toBe('简历工作台')
  })
})

describe('关闭只有基座那一条路径', () => {
  it('点卡片 → 跳过去，并且弹层收起', async () => {
    const wrapper = await mountSwitcher('/')
    await tap(wrapper, '.workspace-trigger')
    const target = cards(wrapper)[1]
    expect(target?.text()).toBe('岗位库')

    await tap(wrapper, '.workspace-card:nth-child(2)')

    expect(router.currentRoute.value.name).toBe('job-board')
    expect(wrapper.find('.workspace-popover').exists()).toBe(false)
  })

  it('再点一次触发按钮 → 收起（基座认 aria-controls，不把这一下当「点外面」）', async () => {
    const wrapper = await mountSwitcher()
    await tap(wrapper, '.workspace-trigger')
    expect(wrapper.find('.workspace-popover').exists()).toBe(true)

    await tap(wrapper, '.workspace-trigger')
    expect(wrapper.find('.workspace-popover').exists()).toBe(false)
  })

  it('按 Esc → 收起，焦点还给触发按钮', async () => {
    const wrapper = await mountSwitcher()
    await tap(wrapper, '.workspace-trigger')
    const trigger = wrapper.find('.workspace-trigger').element as HTMLButtonElement

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    await settled()

    expect(wrapper.find('.workspace-popover').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger)
  })

  it('点弹层外面 → 收起；点卡片之间的空隙不算外面', async () => {
    const wrapper = await mountSwitcher()
    await tap(wrapper, '.workspace-trigger')

    wrapper.find('.workspace-cards').element.dispatchEvent(
      new PointerEvent('pointerdown', { bubbles: true }),
    )
    await flushPromises()
    expect(wrapper.find('.workspace-popover').exists()).toBe(true)

    document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    await flushPromises()
    await settled()
    expect(wrapper.find('.workspace-popover').exists()).toBe(false)
  })

  it('不圈焦点：最后一张卡片上按 Tab 不被拦（这是不带遮罩的 Popover，不是模态）', async () => {
    const wrapper = await mountSwitcher()
    await tap(wrapper, '.workspace-trigger')
    const last = cards(wrapper)[APP_MODULES.length - 1]?.element as HTMLAnchorElement
    last.focus()

    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    document.dispatchEvent(event)
    await flushPromises()

    expect(event.defaultPrevented).toBe(false)
    expect(wrapper.find('.workspace-popover').exists()).toBe(true)
  })
})