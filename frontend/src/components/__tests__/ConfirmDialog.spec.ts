/**
 * 批次 F1b 的弹层基础件（`stores/dialog.ts` + `components/ConfirmDialog.vue`）。
 *
 * 这里钉的是**这个组件存在的理由**，不是它的长相：
 *
 * 1. 一次「确认」必须是一次普通 click 事件，能被读、被点、被断言。原生
 *    `window.confirm` 同步阻塞主线程，CDP 驱动的验收会停在那一步（`JobDetailView.vue`
 *    里记着这次事故）——所以本文件同时用 spy 钉住「原生弹层一次都没被调用」。
 * 2. 调用点拿到的是 `Promise<boolean>`，`false` 这条路必须**什么都不做**。
 *    这一条是整个改造的安全性所在：把「取消」写成「仍然执行动作」比原生弹窗更糟。
 * 3. 同时只有一个弹窗，后来的请求立刻 `false`。不许叠两层遮罩。
 * 4. 键盘与遮罩的关闭都算取消（和 `JobDetailView` 的先例一致）。
 *
 * 挂载方式：`ConfirmDialog` 用 `Teleport to="body"`，所以查询走 `document.body`
 * （与 `JobDetailView.spec.ts` 的 `bodyEl` 同一套做法）。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useDialogStore } from '@/stores/dialog'
import { settled } from '@/__tests__/setup'

let pinia: Pinia

function bodyEl(selector: string): Element | null {
  return document.body.querySelector(selector)
}

async function click(el: Element | null): Promise<void> {
  if (!el) throw new Error(`找不到元素：${String(el)}`)
  ;(el as HTMLElement).click()
  await flushPromises()
}

function mountDialog() {
  return mount(ConfirmDialog, { global: { plugins: [pinia] } })
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('确认弹窗的三条主干行为', () => {
  it('点「确认」→ Promise 兑现 true，弹窗关掉', async () => {
    mountDialog()
    const dialog = useDialogStore()

    const pending = dialog.askConfirm('删除这份简历？', '删除后它进回收站，30 天内可以恢复。')
    await flushPromises()
    expect(bodyEl('[data-testid="app-dialog"]')).not.toBeNull()

    await click(bodyEl('[data-testid="app-dialog-confirm"]'))
    expect(await pending).toBe(true)
    expect(dialog.pending).toBeNull()
    // 退出有淡出（批次 F4）：等它收尾再判「不在了」。
    await settled()
    expect(bodyEl('[data-testid="app-dialog"]')).toBeNull()
  })

  it('点「取消」→ Promise 兑现 false，弹窗关掉，且什么都没被执行', async () => {
    mountDialog()
    const dialog = useDialogStore()

    const pending = dialog.askConfirm('彻底删除这份简历？', '这一步不可逆。')
    await flushPromises()

    await click(bodyEl('[data-testid="app-dialog-cancel"]'))
    // false 这条路的全部意义就是「调用方停在动作之前」：这里没有任何后续请求可发，
    // 所以断言落在 store 状态上——弹窗关了、请求槽空了，但用户没被替做决定。
    expect(await pending).toBe(false)
    expect(dialog.pending).toBeNull()
  })

  it('标题、正文、subject 各落在各自的节点上（后果要说清，不能只问「确认吗」）', async () => {
    mountDialog()
    const dialog = useDialogStore()

    void dialog.askConfirm('彻底删除这份简历？', '这一步不可逆：会永久消失，恢复不了。', {
      subject: '投后端的那一版',
      confirmLabel: '确认彻底删除',
      tone: 'danger',
    })
    await flushPromises()

    expect(bodyEl('[data-testid="app-dialog"] .dialog-title')?.textContent).toBe(
      '彻底删除这份简历？',
    )
    expect(bodyEl('[data-testid="app-dialog"] .dialog-body')?.textContent).toContain('不可逆')
    expect(bodyEl('[data-testid="app-dialog"] .dialog-subject')?.textContent).toContain(
      '投后端的那一版',
    )
    expect(bodyEl('[data-testid="app-dialog-confirm"]')?.textContent).toBe('确认彻底删除')
  })

  it('危险动作的确认按钮走 danger 形状，普通动作不走', async () => {
    mountDialog()
    const dialog = useDialogStore()

    void dialog.askConfirm('甲', '甲', { tone: 'danger' })
    await flushPromises()
    expect(bodyEl('[data-testid="app-dialog-confirm"]')?.className).toContain(
      'dialog-button--danger',
    )

    dialog.close()
    void dialog.askConfirm('乙', '乙')
    await flushPromises()
    expect(bodyEl('[data-testid="app-dialog-confirm"]')?.className).toContain(
      'dialog-button--primary',
    )
  })
})

describe('提示弹窗（原来的 window.alert 归宿）', () => {
  it('只有一个按钮，没有「取消」', async () => {
    mountDialog()
    const dialog = useDialogStore()

    const pending = dialog.askNotice('没保存成功，所以停在这一页', '再试一次，或先刷新页面。')
    await flushPromises()

    expect(bodyEl('[data-testid="app-dialog-cancel"]')).toBeNull()
    expect(bodyEl('[data-testid="app-dialog-confirm"]')?.textContent).toBe('知道了')

    await click(bodyEl('[data-testid="app-dialog-confirm"]'))
    expect(await pending).toBeUndefined()
  })
})

describe('关闭方式与并发', () => {
  it('Esc 与点遮罩都算取消', async () => {
    mountDialog()
    const dialog = useDialogStore()

    const first = dialog.askConfirm('切换前先处理未保存的修改', '现在切换会覆盖这份简历。')
    await flushPromises()
    await bodyEl('[data-testid="app-dialog"]')?.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    )
    await flushPromises()
    expect(await first).toBe(false)

    const second = dialog.askConfirm('再来一次', '用遮罩关。')
    await flushPromises()
    const overlay = bodyEl('[data-testid="app-dialog"]')
    expect(overlay).not.toBeNull()
    // `@click.self` 只认打在遮罩本身上的点击：子节点的点击不会冒泡到这里，
    // 所以这里必须直接 dispatch 在 overlay 上，点按钮的对照用例在上面那组里。
    overlay?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(await second).toBe(false)
  })

  it('已经有一个弹窗在等用户时，后来的请求立刻 false，不叠第二层遮罩', async () => {
    mountDialog()
    const dialog = useDialogStore()

    const held = dialog.askConfirm('先弹出的那一个', '用户还没决定。')
    await flushPromises()
    expect(bodyEl('.ui-overlay')).not.toBeNull()

    expect(await dialog.askConfirm('后到的那一个', '不该出现')).toBe(false)
    // 前一个仍然在等，且屏幕上只有一层遮罩。
    expect(dialog.pending?.title).toBe('先弹出的那一个')
    expect(document.body.querySelectorAll('.ui-overlay')).toHaveLength(1)

    dialog.settle(true)
    expect(await held).toBe(true)
  })

  it('`close()` 会把悬着的 Promise 结算掉（登出/强拆路由时的兜底）', async () => {
    mountDialog()
    const dialog = useDialogStore()

    const pending = dialog.askConfirm('有人要关掉界面', '不能让它永远悬着。')
    await flushPromises()
    dialog.close()
    expect(await pending).toBe(false)
    expect(dialog.pending).toBeNull()
  })
})

describe('原生弹层不许回来（守则 C6）', () => {
  it('六处调用点换完后，弹窗全程没有碰过 window.confirm / window.alert', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => undefined)

    mountDialog()
    const dialog = useDialogStore()

    const pending = dialog.askConfirm('删除这份简历？', '删除后可在回收站找回。')
    await flushPromises()
    await click(bodyEl('[data-testid="app-dialog-confirm"]'))
    expect(await pending).toBe(true)

    expect(confirmSpy).toHaveBeenCalledTimes(0)
    expect(alertSpy).toHaveBeenCalledTimes(0)
  })
})
