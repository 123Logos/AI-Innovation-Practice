/**
 * 右侧用户抽屉单测（批次 F3 第二片，老板前端清单第 2 条）。
 *
 * 这个组件自己**没有**任何「怎么关」的逻辑——点外面 / Esc / 焦点归还全在 `OverlayPanel` 基座里，
 * 所以这里的用例只有三类：抽屉里有什么（结构）、点了发什么意图（事件）、退出中长什么样（状态）。
 * 反过来，下面那两条「按 Esc 会 close」「点遮罩会 close」测的是**接线**：
 * 基座换掉或忘记渲染时它们会红，而抽屉自己的行为不该由顶栏的用例来背。
 *
 * 挂载方式：抽屉 `Teleport` 到 `document.body`（原因写在组件里——顶栏的 `backdrop-filter`
 * 会成为 fixed 后代的包含块），所以**所有查询走 `document`**。`wrapper.find()` 对 Teleport
 * 出去的节点永远返回空，用它断言「关掉之后查不到」是一条恒绿的假用例（`ConfirmDialog.spec.ts` 同法）。
 *
 * 不测像素：`min(320px, 86vw)`、贴右边、满高这三条在 jsdom 里没有布局引擎，
 * 归 375 / 1280 两档真实视口量（数值附在 PR）。
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import UserDrawer from '@/components/UserDrawer.vue'

type DrawerProps = { username?: string; signingOut?: boolean; suspended?: boolean }

const mounted: VueWrapper[] = []

/** 挂进 `document.body`：基座把 pointerdown / keydown 注册在 `document` 上，不挂着就冒泡不到。 */
function mountDrawer(props: DrawerProps = {}): VueWrapper {
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(UserDrawer, {
    attachTo,
    props: { username: 'tester', ...props },
  })
  mounted.push(wrapper)
  return wrapper
}

const bodyEl = (selector: string): Element | null => document.body.querySelector(selector)
const bodyAll = (selector: string): Element[] => [...document.body.querySelectorAll(selector)]

/** 真实浏览器的手势顺序：pointerdown 先于 click。 */
function tap(element: Element | null): void {
  if (!(element instanceof HTMLElement)) throw new Error('tap 的目标不存在')
  element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
  element.click()
}

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
})

describe('抽屉里有什么', () => {
  it('面板就是基座给的：id 供 `aria-controls` 指、role 是 group、名字是当前登录用户', () => {
    mountDrawer()
    const panel = bodyEl('#user-drawer')

    expect(panel).not.toBeNull()
    expect(panel?.getAttribute('role')).toBe('group')
    expect(panel?.getAttribute('aria-label')).toBe('账户')
    // 形态类来自 `DrawerPanel`（两个抽屉共用一份尺寸与层级），基座自己的类也在。
    expect([...(panel?.classList ?? [])]).toEqual(
      expect.arrayContaining(['overlay-panel', 'drawer-panel', 'ui-scroll']),
    )
    expect(bodyEl('.drawer-name')?.textContent).toBe('tester')
  })

  it('挂的是 body，不是顶栏里的某个位置', () => {
    const wrapper = mountDrawer()
    const root = bodyEl('.drawer-root')

    // 这一条是 Teleport 的结构判据：留在 `.user-area` 里，抽屉会被顶栏的
    // `backdrop-filter` 收成 58px 高（两档实测过），所以「挂在 body」不是风格选择。
    expect(root?.parentElement).toBe(document.body)
    expect(wrapper.element.parentElement).not.toBe(document.body)
  })

  /*
   * 显式改写（2026-11-15 第 25 轮，守则 R1）：原来钉的是「两行动作，AI 设置在前」。
   * 老板这一轮把「AI 设置」收进新的「设置」弹窗，抽屉里改成三行
   * 「个人资料 / 设置 / 退出登录」。判据从两行变三行不是放宽——每一行的文案、顺序、
   * 危险色仍然逐项比对，而且多了一条「抽屉里不再有 AI 设置这个词」。
   */
  it('三行动作：个人资料、设置在先，退出在后；「AI 设置」已退场', () => {
    mountDrawer()
    const items = bodyAll('.drawer-item')

    expect(items).toHaveLength(3)
    expect(items[0]?.textContent?.trim()).toBe('个人资料')
    expect(items[1]?.textContent?.trim()).toBe('设置')
    expect(items[2]?.textContent?.trim()).toBe('退出登录')
    // 退出是危险色那一行：判据取类名，像素色归两档实测。
    expect(items[2]?.classList.contains('drawer-item--danger')).toBe(true)
    expect(bodyEl('#user-drawer')?.textContent).not.toContain('AI 设置')
  })
})

describe('点了发什么意图（抽屉不做业务决定）', () => {
  it('点「个人资料」只发 openProfile，不碰 close 与 logout', async () => {
    const wrapper = mountDrawer()
    bodyAll('.drawer-item')[0]?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('openProfile')).toHaveLength(1)
    expect(wrapper.emitted('openSettings')).toBeUndefined()
    expect(wrapper.emitted('logout')).toBeUndefined()
    expect(wrapper.emitted('close')).toBeUndefined()
  })

  it('点「设置」只发 openSettings，不碰 close 与 logout', async () => {
    const wrapper = mountDrawer()
    bodyAll('.drawer-item')[1]?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('openSettings')).toHaveLength(1)
    expect(wrapper.emitted('openProfile')).toBeUndefined()
    expect(wrapper.emitted('logout')).toBeUndefined()
    expect(wrapper.emitted('close')).toBeUndefined()
  })

  it('点「退出登录」只发 logout', async () => {
    const wrapper = mountDrawer()
    bodyEl('.drawer-item--danger')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('logout')).toHaveLength(1)
    expect(wrapper.emitted('openSettings')).toBeUndefined()
  })
})

describe('退出中（signingOut）的样子', () => {
  it('退出那一行禁用并改文案，另两行不受影响', () => {
    mountDrawer({ signingOut: true })
    const busy = bodyEl('.drawer-item--danger')

    expect(busy?.hasAttribute('disabled')).toBe(true)
    expect(busy?.textContent?.trim()).toBe('退出中…')
    const first = bodyAll('.drawer-item')[0]
    expect(first?.textContent?.trim()).toBe('个人资料')
    expect(first?.hasAttribute('disabled')).toBe(false)
  })
})

describe('大弹窗打开时整层挂起（2026-11-15 第 25 轮）', () => {
  /**
   * 老板指令第 6 条：大弹窗开着时，点弹窗内部或它的遮罩**不能**把背后的抽屉关掉。
   * 实现是基座的 `suspended`（判据由调用方给），所以这里验的是「挂上之后真的不响应」，
   * 而不是在抽屉里再加一个 if——那会是第二份关闭逻辑。
   */
  it('suspended 时点遮罩、按 Esc 都不关；解除挂起后恢复', async () => {
    const wrapper = mountDrawer({ suspended: true })

    tap(bodyEl('.drawer-mask'))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toBeUndefined()
    // 前置断言：面板确实还在（否则上面那句「没有 close」可能是因为什么都没挂上）。
    expect(bodyEl('#user-drawer')).not.toBeNull()

    await wrapper.setProps({ suspended: false })
    tap(bodyEl('.drawer-mask'))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('挂起期间点抽屉自己的三行仍然发各自的意图（只有「被关掉」这一件事被挂起）', async () => {
    const wrapper = mountDrawer({ suspended: true })
    bodyAll('.drawer-item')[1]?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('openSettings')).toHaveLength(1)
    expect(wrapper.emitted('close')).toBeUndefined()
  })
})

describe('接线：关闭只有基座那一条路径', () => {
  it('点遮罩 → close。遮罩上没有第二个 click 监听，这条靠的是「遮罩在面板之外」', async () => {
    const wrapper = mountDrawer()
    tap(bodyEl('.drawer-mask'))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('按 Esc → close；点抽屉里的两行不 close', async () => {
    const wrapper = mountDrawer()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(1)

    tap(bodyEl('.drawer-item--danger'))
    await wrapper.vm.$nextTick()
    // 上面那一步先证明面板还在（少了这种前置断言，「没有新增 close」会因为
    // Teleport / 卸载而恒成立，那就不是判据了）。
    expect(bodyEl('#user-drawer')).not.toBeNull()
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(wrapper.emitted('logout')).toHaveLength(1)
  })

  it('Tab 圈在抽屉里：最后一行再按 Tab 回到第一行（DrawerPanel 开了 trap-focus）', async () => {
    const wrapper = mountDrawer()
    const items = bodyAll('.drawer-item') as HTMLButtonElement[]
    expect(items).toHaveLength(3)
    items[2]?.focus()

    const event = new KeyboardEvent('keydown', {
      key: 'Tab',
      bubbles: true,
      cancelable: true,
    })
    document.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    // 这条测的是接线：基座的陷阱默认关，抽屉这一层必须显式打开，
    // 否则遮罩盖着整页、焦点却能 Tab 到背后那些点不到的按钮上。
    expect(event.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(items[0])
  })
})
