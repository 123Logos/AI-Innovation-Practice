/**
 * 设置弹窗（2026-11-15 第 25 轮，老板指令第 4 条）。
 *
 * 它接走了原来顶栏里那个「AI 设置」弹窗，所以三条老规矩必须继续成立（ADR-009 / `client.ts`）：
 * Key 只进 `sessionStorage`、打开时**不回填**已存的值、保存后立刻清空输入框。
 * 另外两条是这一轮新加的：开合走 `stores/ui.ts`（任何页面都能唤起），
 * 两个大弹窗互斥。
 *
 * 查询走 `document`：弹窗 Teleport 到 body，`wrapper.find()` 看不见它。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import SettingsModal from '@/components/SettingsModal.vue'
import { useUiStore } from '@/stores/ui'

const KEY_STORAGE = 'deepseek-api-key'
const mounted: VueWrapper[] = []

function mountModal() {
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(SettingsModal, { attachTo })
  mounted.push(wrapper)
  return { wrapper, ui: useUiStore() }
}

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
  sessionStorage.clear()
})

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
})

const dialog = (): Element | null => document.querySelector('[data-testid="settings-modal"]')

describe('开合走全局状态', () => {
  it('`settingsOpen` 为假时不渲染；打开后是一个带 `aria-modal` 的对话框', async () => {
    const { ui } = mountModal()
    expect(dialog()).toBeNull()

    ui.openSettings()
    await flushPromises()

    expect(dialog()).not.toBeNull()
    const panel = document.querySelector('[role="dialog"][aria-modal="true"]')
    expect(panel?.getAttribute('aria-labelledby')).toBe('settings-heading')
    expect(document.querySelector('#settings-heading')?.textContent).toBe('设置')
  })

  it('「关闭」按钮与点遮罩都只关自己；点面板内部不关', async () => {
    const { ui } = mountModal()
    ui.openSettings()
    await flushPromises()

    // 点面板内部：不关（`@click.self` 在遮罩上，面板不是遮罩）。
    ;(document.querySelector('.ui-panel') as HTMLElement).click()
    await flushPromises()
    expect(ui.settingsOpen).toBe(true)

    ;(document.querySelector('[data-testid="settings-close"]') as HTMLElement).click()
    await flushPromises()
    expect(ui.settingsOpen).toBe(false)
  })

  it('两个大弹窗互斥：开着资料弹窗时打开设置，资料那格自动落下来', async () => {
    const { ui } = mountModal()
    ui.openProfile()
    expect(ui.profileOpen).toBe(true)

    ui.openSettings()

    expect(ui.settingsOpen).toBe(true)
    expect(ui.profileOpen).toBe(false)
  })
})

describe('AI 密钥（从顶栏搬过来的那一段，规矩不变）', () => {
  it('默认停在 AI 页签，并说明当前有没有填', async () => {
    const { ui } = mountModal()
    ui.openSettings()
    await flushPromises()

    const label = document.querySelector('.ai-settings-status')?.textContent?.trim()
    expect(label).toBe('这个标签页里还没填')
    // 另一个页签是「其他设置」的占位，不放禁用控件（守则 L5）。
    const tabs = [...document.querySelectorAll('.settings-tab')].map((tab) =>
      tab.textContent?.trim(),
    )
    expect(tabs).toEqual(['AI 密钥', '其他设置'])
  })

  it('保存：写进 sessionStorage、状态翻转、输入框清空', async () => {
    const { ui } = mountModal()
    ui.openSettings()
    await flushPromises()

    const input = document.querySelector('.modal-field input') as HTMLInputElement
    input.value = 'sk-test-123456'
    input.dispatchEvent(new Event('input'))
    await flushPromises()
    ;(document.querySelector('.modal-button--primary') as HTMLElement).click()
    await flushPromises()

    expect(sessionStorage.getItem(KEY_STORAGE)).toBe('sk-test-123456')
    expect(document.querySelector('.ai-settings-status')?.textContent?.trim()).toBe(
      '这个标签页里已经填好了',
    )
    // 屏幕上看不到完整 Key：保存后立刻清空。
    expect((document.querySelector('.modal-field input') as HTMLInputElement).value).toBe('')
    expect(document.querySelector('.modal-notice')?.textContent).toContain('已保存')
  })

  it('空着点保存：校验提示上屏，不写入', async () => {
    const { ui } = mountModal()
    ui.openSettings()
    await flushPromises()
    ;(document.querySelector('.modal-button--primary') as HTMLElement).click()
    await flushPromises()

    expect(document.querySelector('.modal-error')?.textContent).toContain('这里还空着')
    expect(sessionStorage.getItem(KEY_STORAGE)).toBeNull()
  })

  it('重新打开时不回填已存的值（肩窥 / 录屏都不该看到明文）', async () => {
    const { ui } = mountModal()
    sessionStorage.setItem(KEY_STORAGE, 'sk-already-saved')
    ui.openSettings()
    await flushPromises()

    expect(document.querySelector('.ai-settings-status')?.textContent?.trim()).toBe(
      '这个标签页里已经填好了',
    )
    expect((document.querySelector('.modal-field input') as HTMLInputElement).value).toBe('')

    // 清除 Key：状态回到「还没填」，存储里也没了。
    const buttons = [...document.querySelectorAll('.modal-button')] as HTMLElement[]
    buttons.find((button) => button.textContent?.trim() === '清除 Key')?.click()
    await flushPromises()
    expect(sessionStorage.getItem(KEY_STORAGE)).toBeNull()
  })
})
