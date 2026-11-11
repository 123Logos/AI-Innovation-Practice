/**
 * `OverlayPanel` 基座的行为用例（批次 F3 第一片，守则 L3）。
 *
 * 这一组用例管的是**那两件事只在这里做一次**之后，行为还对不对：
 * 点外面关、点里面不关、Esc 关、别的键不关、收起之后监听真的注销了。
 * 最后一条是这片的真实风险——`v-if` 收起时若漏掉 `removeEventListener`，
 * 每一次开合都在 document 上多挂一份监听，那种泄漏不会当场报错，只会在
 * 用户开了十几个面板之后表现为「点一下关掉两层」。
 *
 * 全部挂在真实 `document` 上（`attachTo: document.body`）：基座监听的是 document，
 * 用一个游离的 wrapper 触发不到它，那样的绿是假的。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick, ref } from 'vue'

import OverlayPanel from '@/components/OverlayPanel.vue'

function mountPanel(options: { opened?: boolean } = {}) {
  const opened = ref(options.opened ?? true)
  const onClose = vi.fn()

  const wrapper = mount(
    {
      components: { OverlayPanel },
      setup() {
        return { opened, onClose }
      },
      template: `
        <div>
          <button class="outside" type="button">外面</button>
          <OverlayPanel
            v-if="opened"
            panel-id="probe-panel"
            panel-class="probe-menu"
            label="探针面板"
            @close="onClose"
          >
            <button class="inside" type="button">里面</button>
          </OverlayPanel>
        </div>
      `,
    },
    { attachTo: document.body },
  )

  return { wrapper, opened, onClose }
}

describe('OverlayPanel：点外面关闭', () => {
  it('面板挂上后，文档上的 pointerdown 落在面板外 → 发一次 close', async () => {
    const { onClose } = mountPanel()

    document.querySelector<HTMLButtonElement>('.outside')?.dispatchEvent(
      new PointerEvent('pointerdown', { bubbles: true }),
    )
    await nextTick()

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('落在面板内部（含子节点）→ 不算点外面，一次都不发', async () => {
    const { onClose, wrapper } = mountPanel()

    wrapper.find('.inside').element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    await nextTick()

    expect(onClose).not.toHaveBeenCalled()
  })

  it('面板收起（卸载）之后再点外面 → 不再有监听在空转', async () => {
    const { opened, onClose } = mountPanel()

    opened.value = false
    await nextTick()

    document.querySelector<HTMLButtonElement>('.outside')?.dispatchEvent(
      new PointerEvent('pointerdown', { bubbles: true }),
    )
    await nextTick()

    expect(onClose).not.toHaveBeenCalled()
  })
})

describe('OverlayPanel：键盘关闭', () => {
  it('Escape → 发一次 close', async () => {
    const { onClose } = mountPanel()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('其他键（Enter / 方向键）→ 不归基座管，一次都不发', async () => {
    const { onClose } = mountPanel()

    for (const key of ['Enter', 'ArrowDown', 'a']) {
      document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
    }
    await nextTick()

    expect(onClose).not.toHaveBeenCalled()
  })

  it('面板收起后按 Escape → 同样不留监听', async () => {
    const { opened, onClose } = mountPanel()

    opened.value = false
    await nextTick()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()

    expect(onClose).not.toHaveBeenCalled()
  })
})

describe('OverlayPanel：焦点进出', () => {
  it('打开时焦点落到面板里第一个可聚焦项（键盘用户不必从头 Tab 一遍）', () => {
    const { wrapper } = mountPanel()

    const inside = wrapper.find<HTMLButtonElement>('.inside').element
    expect(document.activeElement).toBe(inside)
  })

  it('面板里没有可聚焦项时，焦点落在面板本身（Esc 之后才有地方可还）', async () => {
    const wrapper = mount(
      {
        components: { OverlayPanel },
        template: `<OverlayPanel v-if="true" label="只有文字的说明"><p>说明文字</p></OverlayPanel>`,
      },
      { attachTo: document.body },
    )

    const panel = wrapper.element as HTMLElement
    expect(document.activeElement).toBe(panel)
    expect(panel.getAttribute('tabindex')).toBe('-1')
  })

  it('关闭（Esc 触发）后焦点还给打开它的那个元素', async () => {
    const opened = ref(true)
    const wrapper = mount(
      {
        components: { OverlayPanel },
        setup: () => ({ opened }),
        template: `
          <div>
            <button class="trigger" type="button" @click="opened = !opened">开</button>
            <OverlayPanel v-if="opened" label="探针面板"><button class="inside" type="button">里</button></OverlayPanel>
          </div>
        `,
      },
      { attachTo: document.body },
    )

    const trigger = wrapper.find<HTMLButtonElement>('.trigger').element
    // 先收起再打开：焦点归还是「卸载那一刻」的行为，必须让面板在触发器持焦之后重新挂载。
    opened.value = false
    await nextTick()
    trigger.focus()
    trigger.click()
    await nextTick()
    expect(document.activeElement).toBe(wrapper.find('.inside').element)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    opened.value = false
    await nextTick()

    expect(document.activeElement).toBe(trigger)
  })

  it('用户已经自己点了别处 → 关闭时不抢焦点', async () => {
    const opened = ref(true)
    const wrapper = mount(
      {
        components: { OverlayPanel },
        setup: () => ({ opened }),
        template: `
          <div>
            <button class="elsewhere" type="button">别处</button>
            <OverlayPanel v-if="opened" label="探针面板"><button class="inside" type="button">里</button></OverlayPanel>
          </div>
        `,
      },
      { attachTo: document.body },
    )

    const elsewhere = wrapper.find<HTMLButtonElement>('.elsewhere').element
    elsewhere.focus()
    await nextTick()

    opened.value = false
    await nextTick()

    expect(document.activeElement).toBe(elsewhere)
  })
})

describe('OverlayPanel：面板自己长得像它接管的那个壳', () => {
  it('id / role / aria-label 由 props 决定，调用方的 class 与 panelClass 合并到同一个根', () => {
    const { wrapper } = mountPanel()
    const root = wrapper.find('#probe-panel')

    expect(root.exists()).toBe(true)
    expect(root.attributes('role')).toBe('menu')
    expect(root.attributes('aria-label')).toBe('探针面板')
    // 调用方在组件上写的 `class` 会落到根元素——顶栏「更多」面板正是靠这一点
    // 保住 `display: contents` 的宽屏形态，所以这条不是顺手断言，是这片的依赖。
    expect(root.classes()).toEqual(['overlay-panel', 'probe-menu'])
  })

  it('role 可以是 group：一组动作按钮不是菜单', () => {
    const wrapper = mount(OverlayPanel, {
      props: { label: '更多操作', role: 'group' },
      slots: { default: '<button type="button">AI 设置</button>' },
    })

    expect(wrapper.attributes('role')).toBe('group')
    expect(wrapper.text()).toContain('AI 设置')
  })

  it('调用方直接写在组件上的 class 与 panelClass 合并，不互相覆盖', () => {
    // 基座的能力判据：`panelClass` 是形态类，调用方写在组件上的 `class` 是它自己那一层，
    // 两者必须并存。今天的两个抽屉走 `DrawerPanel` 传 `panel-class="drawer-panel ui-scroll"`，
    // 没有调用方再写 attrs class；这条留着是因为一旦 fallthrough 被覆盖，
    // 将来任何调用方加的状态类（`--open` 之类）都会静默失效。
    const wrapper = mount(OverlayPanel, {
      props: { label: '更多操作', panelClass: 'probe-menu' },
      attrs: { class: ['user-actions', 'user-actions--open'] },
    })

    expect(wrapper.classes()).toEqual(
      expect.arrayContaining(['overlay-panel', 'probe-menu', 'user-actions', 'user-actions--open']),
    )
  })
})

describe('OverlayPanel：自己的触发按钮不算「外面」（aria-controls 反向链接）', () => {
  /**
   * 这一组自己收挂载：基座的监听挂在 document 上，不 unmount 就会留给下一条用例，
   * 而 `document.querySelector('.trigger-a')` 取的是文档里第一个匹配——
   * 上一条用例留下的同名按钮会让这一条量到别人的面板（绿得没有意义）。
   */
  const local: { unmount: () => void }[] = []
  afterEach(() => {
    while (local.length) local.pop()?.unmount()
  })

  /**
   * 两个面板 + 两个触发按钮，摆成顶栏的真实形状：
   * 点自己的触发按钮该由调用方开合（基座不关），点**别人的**触发按钮该关（互斥）。
   * 这两条必须同时成立——只写 `@pointerdown.stop` 能满足前者，却会让后者失效，
   * 于是两个贴右边的满高抽屉叠在一起。
   */
  function mountTwoPanels() {
    const onCloseA = vi.fn()
    const onCloseB = vi.fn()
    const wrapper = mount(
      {
        components: { OverlayPanel },
        setup() {
          return { onCloseA, onCloseB }
        },
        template: `
          <div>
            <button class="trigger-a" type="button" aria-controls="panel-a">开 A</button>
            <button class="trigger-b" type="button" aria-controls="panel-b">开 B</button>
            <button class="plain" type="button">别处</button>
            <OverlayPanel panel-id="panel-a" label="甲" @close="onCloseA">
              <button type="button">A 里面</button>
            </OverlayPanel>
            <OverlayPanel panel-id="panel-b" label="乙" @close="onCloseB">
              <button type="button">B 里面</button>
            </OverlayPanel>
          </div>
        `,
      },
      { attachTo: document.body },
    )
    local.push(wrapper)
    return { wrapper, onCloseA, onCloseB }
  }

  function press(selector: string): void {
    document.querySelector(selector)?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
  }

  it('点自己的触发按钮 → 不关（那一下是调用方的开合）', async () => {
    const { onCloseA } = mountTwoPanels()
    press('.trigger-a')
    await nextTick()
    expect(onCloseA).not.toHaveBeenCalled()
  })

  it('点别人的触发按钮 → 关（两个抽屉因此不会同时开着）', async () => {
    const { onCloseA, onCloseB } = mountTwoPanels()
    press('.trigger-b')
    await nextTick()
    expect(onCloseA).toHaveBeenCalledTimes(1)
    expect(onCloseB).not.toHaveBeenCalled()
  })

  it('点与任何面板都无关的地方 → 两个都关', async () => {
    const { onCloseA, onCloseB } = mountTwoPanels()
    press('.plain')
    await nextTick()
    expect(onCloseA).toHaveBeenCalledTimes(1)
    expect(onCloseB).toHaveBeenCalledTimes(1)
  })

  it('没有 panelId 的面板不认任何触发按钮：点外面照常关', async () => {
    const onClose = vi.fn()
    const wrapper = mount(
      {
        components: { OverlayPanel },
        setup() {
          return { onClose }
        },
        template: `
          <div>
            <button class="trigger-a" type="button" aria-controls="panel-a">开 A</button>
            <OverlayPanel label="无 id" @close="onClose"><button type="button">里面</button></OverlayPanel>
          </div>
        `,
      },
      { attachTo: document.body },
    )
    local.push(wrapper)
    press('.trigger-a')
    await nextTick()
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
