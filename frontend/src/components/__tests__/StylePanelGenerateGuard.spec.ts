/**
 * 「生成前先保存」这一处的确认（批次 F1a/F1b 交界的第 5 处调用点）。
 *
 * 只钉三件事，别的都交给它对应的单测：
 *
 * 1. **取消 = 一次请求都不发**（原来这里是 `window.confirm`，取消与确认共用同一段代码，
 *    接线写错就会「取消也照样生成」，而生成会覆盖纸面上那一版）。
 * 2. 确认后是**先保存再生成**，顺序不能反：生成读的是已保存的那一版。
 * 3. 取消时那句提示仍然出现（守则 C3：停手也要说清下一步）。
 *
 * `validateForGenerate()` 这里直接桩成「通过」：本文件验的是弹窗接线，不是必填校验；
 * 校验本身在 `stores/__tests__/resume.spec.ts` 那一组里逐条钉着。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useResumeStore } from '@/stores/resume'

let pinia: Pinia

function bodyText(selector: string): string {
  return document.body.querySelector(selector)?.textContent ?? ''
}

async function bodyClick(selector: string): Promise<void> {
  const el = document.body.querySelector(selector)
  if (!el) throw new Error(`找不到弹窗按钮：${selector}`)
  ;(el as HTMLElement).click()
  await flushPromises()
}

async function mountPanel() {
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(
    // StylePanel 只依赖 store，不依赖路由；直接挂它比挂整个编辑器便宜，
    // 而这一处判断的全部证据就在它自己模板里那颗「生成简历」按钮上。
    (await import('@/components/StylePanel.vue')).default,
    {
      attachTo,
      global: {
        plugins: [pinia],
        // StylePanel 里那两个润色弹窗用了全局注册的 element-plus 组件，而本用例只测
        // 「生成」这一条链路。桩掉它们而不是装整个 element-plus：不然日志里全是
        // 「Failed to resolve component」，真正的失败会被淹掉。
        stubs: { 'el-dialog': true, 'el-button': true, 'el-input-number': true },
      },
    },
  )
  mount(ConfirmDialog, { global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('生成前的未保存拦截走页面内弹窗（批次 F1b）', () => {
  function stubGeneratePath() {
    const store = useResumeStore()
    vi.spyOn(store, 'validateForGenerate').mockReturnValue(null)
    const saveSpy = vi.spyOn(store, 'save').mockResolvedValue({} as never)
    const generateSpy = vi
      .spyOn(store, 'generate')
      .mockResolvedValue({ resume_id: 'resume-under-test' } as never)
    store.dirty = true
    return { store, saveSpy, generateSpy }
  }

  it('点「生成简历」先问；取消就一次请求都不发，并留下一句下一步', async () => {
    const { saveSpy, generateSpy } = stubGeneratePath()
    const wrapper = await mountPanel()

    await wrapper.find('.generate-button').trigger('click')
    await flushPromises()

    expect(bodyText('[data-testid="app-dialog"]')).toContain('生成读的是已经保存的那一版')
    expect(saveSpy).toHaveBeenCalledTimes(0)
    expect(generateSpy).toHaveBeenCalledTimes(0)

    await bodyClick('[data-testid="app-dialog-cancel"]')
    expect(saveSpy).toHaveBeenCalledTimes(0)
    expect(generateSpy).toHaveBeenCalledTimes(0)
    expect(wrapper.find('.feedback').text()).toContain('已取消生成')
  })

  it('确认后先保存、再生成（反面对照：确认这条路真的走到底）', async () => {
    const { saveSpy, generateSpy } = stubGeneratePath()
    const wrapper = await mountPanel()

    await wrapper.find('.generate-button').trigger('click')
    await flushPromises()
    await bodyClick('[data-testid="app-dialog-confirm"]')

    expect(saveSpy).toHaveBeenCalledTimes(1)
    expect(generateSpy).toHaveBeenCalledTimes(1)
  })

  it('没有未保存修改时直接生成，不打扰用户', async () => {
    const { saveSpy, generateSpy, store } = stubGeneratePath()
    store.dirty = false
    const wrapper = await mountPanel()

    await wrapper.find('.generate-button').trigger('click')
    await flushPromises()

    expect(bodyText('[data-testid="app-dialog"]')).toBe('')
    expect(saveSpy).toHaveBeenCalledTimes(0)
    expect(generateSpy).toHaveBeenCalledTimes(1)
  })
})
