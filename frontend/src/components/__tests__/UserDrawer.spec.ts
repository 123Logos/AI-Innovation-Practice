/**
 * 右侧用户抽屉单测（批次 F3 第二片，老板前端清单第 2 条）。
 *
 * 这个组件自己**没有**任何「怎么关」的逻辑——点外面 / Esc / 焦点归还全在 `OverlayPanel` 基座里，
 * 所以这里的用例只有三类：抽屉里有什么（结构）、点了发什么意图（事件）、退出中长什么样（状态）。
 * 反过来，下面那两条「按 Esc 会 close」「点遮罩会 close」测的是**接线**：
 * 基座换掉或忘记渲染时它们会红，而抽屉自己的行为不该由顶栏的用例来背。
 *
 * 不测像素：`min(320px, 86vw)`、贴右边、满高这三条在 jsdom 里没有布局引擎，
 * 归 375 / 1280 两档真实视口量（数值附在 PR）。
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import UserDrawer from '@/components/UserDrawer.vue'

type DrawerProps = { username?: string; signingOut?: boolean }

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

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
})

describe('抽屉里有什么', () => {
  it('面板就是基座给的：id 供 `aria-controls` 指、role 是 group、名字是当前登录用户', () => {
    const wrapper = mountDrawer()
    const panel = wrapper.find('#user-drawer')

    expect(panel.exists()).toBe(true)
    expect(panel.attributes('role')).toBe('group')
    expect(panel.attributes('aria-label')).toBe('账户')
    // 基座的形态类与抽屉自己的形态类各管一层，两条都得在，否则 CSS 选择器落空。
    expect(panel.classes()).toEqual(expect.arrayContaining(['overlay-panel', 'user-drawer']))
    expect(wrapper.find('.drawer-name').text()).toBe('tester')
  })

  it('只有两行动作，顺序是「AI 设置」在前、退出在后，且没有第三个入口', () => {
    const wrapper = mountDrawer()
    const items = wrapper.findAll('.drawer-item')

    expect(items).toHaveLength(2)
    expect(items[0]?.text()).toBe('AI 设置')
    expect(items[1]?.text()).toBe('退出登录')
    // 退出是危险色那一行：判据取类名，像素色归两档实测。
    expect(items[1]?.classes()).toContain('drawer-item--danger')
  })
})

describe('点了发什么意图（抽屉不做业务决定）', () => {
  it('点「AI 设置」只发 openAiSettings，不碰 close', async () => {
    const wrapper = mountDrawer()
    await wrapper.findAll('.drawer-item')[0]?.trigger('click')

    expect(wrapper.emitted('openAiSettings')).toHaveLength(1)
    expect(wrapper.emitted('logout')).toBeUndefined()
    expect(wrapper.emitted('close')).toBeUndefined()
  })

  it('点「退出登录」只发 logout', async () => {
    const wrapper = mountDrawer()
    await wrapper.find('.drawer-item--danger').trigger('click')

    expect(wrapper.emitted('logout')).toHaveLength(1)
    expect(wrapper.emitted('openAiSettings')).toBeUndefined()
  })
})

describe('退出中（signingOut）的样子', () => {
  it('退出那一行禁用并改文案，另一行不受影响', () => {
    const wrapper = mountDrawer({ signingOut: true })
    const busy = wrapper.find('.drawer-item--danger')

    expect(busy.attributes('disabled')).toBeDefined()
    expect(busy.text()).toBe('退出中…')
    expect(wrapper.findAll('.drawer-item')[0]?.text()).toBe('AI 设置')
    expect(wrapper.findAll('.drawer-item')[0]?.attributes('disabled')).toBeUndefined()
  })
})

describe('接线：关闭只有基座那一条路径', () => {
  it('点遮罩 → close。遮罩上没有第二个 click 监听，这条靠的是「遮罩在面板之外」', async () => {
    const wrapper = mountDrawer()
    wrapper.find('.drawer-mask').element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('按 Esc → close；点抽屉里的两行不 close', async () => {
    const wrapper = mountDrawer()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(1)

    await wrapper.find('.drawer-item--danger').trigger('pointerdown')
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})
