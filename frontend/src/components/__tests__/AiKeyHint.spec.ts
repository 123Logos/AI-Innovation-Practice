/**
 * 「👉 点此配置 AI 密钥」那行链接（2026-11-15 第 25 轮，老板指令第 5 条）。
 *
 * 两组判据：
 * 1. **什么时候出现**——只有「凭据没配 / 凭据无效」这两种原因才给链接。超时、上游挂了、
 *    输出解析不了都不是用户去设置里能修好的，给了只会让他改一把本来没坏的 Key。
 * 2. **点了做什么**——把 `stores/ui.ts` 的 `settingsOpen` 打开。这里验的是「它走全局状态」，
 *    而不是「它 emit 了一个事件让调用方去弹」：四个视图离顶栏隔着一整个外框。
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import AiKeyHint from '@/components/AiKeyHint.vue'
import { useUiStore } from '@/stores/ui'

beforeEach(() => {
  setActivePinia(createPinia())
})

function mountHint(reason: string | null) {
  const wrapper = mount(AiKeyHint, { props: { reason } })
  return { wrapper, ui: useUiStore() }
}

describe('只在「凭据」这一类原因上出现', () => {
  it('四种码都认：HTTP 错误码与匹配/面试的降级键各两种', () => {
    for (const reason of ['AI_KEY_MISSING', 'AI_KEY_INVALID', 'key_missing', 'key_invalid']) {
      const { wrapper } = mountHint(reason)
      expect(wrapper.find('[data-testid="ai-key-hint"]').exists(), reason).toBe(true)
      expect(wrapper.text()).toContain('点此配置 AI 密钥')
    }
  })

  it('其它原因一律不画：超时 / 上游 / 输出不可解析 / 空', () => {
    for (const reason of ['timeout', 'upstream', 'bad_output', 'AI_SERVICE_UNAVAILABLE', null]) {
      const { wrapper } = mountHint(reason)
      expect(wrapper.find('[data-testid="ai-key-hint"]').exists(), String(reason)).toBe(false)
    }
  })
})

describe('点了直接打开设置弹窗（跨组件，不靠逐层抛事件）', () => {
  it('点一下 → `settingsOpen` 变真', async () => {
    const { wrapper, ui } = mountHint('AI_KEY_MISSING')
    expect(ui.settingsOpen).toBe(false)

    await wrapper.get('[data-testid="ai-key-hint"]').trigger('click')

    expect(ui.settingsOpen).toBe(true)
  })
})
