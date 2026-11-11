/**
 * 共享多行输入框的行为单测（守则 F1，批次 F5）。
 *
 * jsdom 没有布局引擎：`scrollHeight` 恒为 0、`getComputedStyle` 不给边框宽度。
 * 所以这里**把这两样当输入喂进去**，验的是组件拿到尺寸之后的那条算法
 * （塌回 auto → 量内容 → 加边框 → 与下限取大），不是「浏览器排得对不对」——
 * 后者归 R2 的真浏览器实测（375 / 1280 两档，数值记在 `docs/ROADMAP.md` 的 F5 行）。
 *
 * `ResizeObserver` 用一份**记录型**桩替掉 setup.ts 里那份不回调的：
 * 「宽度没变就不重量」这条判据必须能手动送尺寸才验得了，
 * 而它正是「改高度 → 触发观察 → 再改高度」那个自激环的唯一掐断点。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AutoTextarea from '@/components/AutoTextarea.vue'

class RecordingResizeObserver {
  static instances: RecordingResizeObserver[] = []

  callback: ResizeObserverCallback
  targets: Element[] = []
  disconnects = 0

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    RecordingResizeObserver.instances.push(this)
  }

  observe(target: Element): void {
    this.targets.push(target)
  }

  unobserve(): void {}

  disconnect(): void {
    this.disconnects += 1
  }

  /** 手动送一次观察结果：宽度由用例给，jsdom 自己量不出来。 */
  deliver(width: number): void {
    const entry = { contentRect: { width } } as unknown as ResizeObserverEntry
    this.callback([entry], this as unknown as ResizeObserver)
  }
}

;(globalThis as unknown as Record<string, unknown>).ResizeObserver = RecordingResizeObserver

/** 内容需要多高（px），以及每次被读时元素上的 height 是什么——用来证明「先塌回 auto」。 */
interface Content {
  px: number
  reads: string[]
}

function stubContent(el: HTMLTextAreaElement, px: number): Content {
  const content: Content = { px, reads: [] }
  Object.defineProperty(el, 'scrollHeight', {
    configurable: true,
    get: () => {
      content.reads.push(el.style.height)
      return content.px
    },
  })
  return content
}

function mountArea(props: Record<string, unknown> = {}, attrs: Record<string, unknown> = {}) {
  const wrapper = mount(AutoTextarea, {
    props: { modelValue: '', ...props },
    attrs,
    attachTo: document.body,
  })
  const el = wrapper.element as HTMLTextAreaElement
  // border-box 下 scrollHeight 不含边框，组件要把这两条加回来。jsdom 的 getComputedStyle
  // 连内联的 border-width 都不回读（实测返回空串），所以边框也当输入喂进去。
  vi.spyOn(window, 'getComputedStyle').mockReturnValue({
    borderTopWidth: '1px',
    borderBottomWidth: '1px',
  } as CSSStyleDeclaration)
  return { wrapper, el }
}

beforeEach(() => {
  RecordingResizeObserver.instances.length = 0
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('AutoTextarea', () => {
  it('输入把整段文本交出去，并把高度撑到内容那么高（含边框）', async () => {
    const { wrapper, el } = mountArea({ minHeight: 58 })
    const content = stubContent(el, 120)

    el.value = '把招聘页的职责与要求原样粘进来'
    await wrapper.trigger('input')

    expect(wrapper.emitted('update:modelValue')).toEqual([['把招聘页的职责与要求原样粘进来']])
    expect(el.style.height).toBe('122px')
    // 量的那一刻 height 必须是 auto，否则量到的是「现在有多高」而不是「内容要多高」。
    expect(content.reads[content.reads.length - 1]).toBe('auto')
  })

  it('删字会缩回去，不是一直保持在最长的那一次', async () => {
    const { wrapper, el } = mountArea()
    const content = stubContent(el, 120)
    el.value = '一段很长的描述'
    await wrapper.trigger('input')
    expect(el.style.height).toBe('122px')

    content.px = 40
    el.value = '短'
    await wrapper.trigger('input')

    expect(el.style.height).toBe('42px')
    expect(content.reads.every((height) => height === 'auto')).toBe(true)
  })

  it('内容比下限小时由下限托底（JD 那 300px 是地板不是天花板）', async () => {
    const { wrapper, el } = mountArea({ minHeight: 300 })
    stubContent(el, 100)

    el.value = '只有一行'
    await wrapper.trigger('input')

    expect(el.style.height).toBe('300px')
  })

  it('外部改值也重量：接受润色 / 恢复草稿 / 编辑态回填都不经过 input', async () => {
    const { wrapper, el } = mountArea({ minHeight: 58 })
    const content = stubContent(el, 120)

    content.px = 260
    await wrapper.setProps({ modelValue: '后端把整段描述换掉了' })

    expect(el.style.height).toBe('262px')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('只有宽度变了才重量：高度是自己写的，逐次回量就是自激环', () => {
    const { el } = mountArea({ minHeight: 58 })
    const content = stubContent(el, 120)
    const observer = RecordingResizeObserver.instances[0]
    expect(observer?.targets).toEqual([el])

    const afterMount = content.reads.length
    observer?.deliver(720)
    const afterFirstWidth = content.reads.length
    expect(afterFirstWidth).toBe(afterMount + 1)

    observer?.deliver(720)
    expect(content.reads.length).toBe(afterFirstWidth)

    observer?.deliver(343)
    expect(content.reads.length).toBe(afterFirstWidth + 1)
  })

  it('卸载时退订，不留一个还在观察已卸载节点的观察器', () => {
    const { wrapper } = mountArea()
    const observer = RecordingResizeObserver.instances[0]
    expect(RecordingResizeObserver.instances).toHaveLength(1)

    wrapper.unmount()

    expect(observer?.disconnects).toBe(1)
  })

  it('maxlength / placeholder / data-testid 落在同一个 textarea 上（JD 的字数上限靠它）', () => {
    const { el } = mountArea(
      { modelValue: '' },
      { maxlength: 8000, placeholder: '把招聘页的职责与要求原样粘进来', 'data-testid': 'jd-input' },
    )

    expect(el.tagName).toBe('TEXTAREA')
    expect(el.getAttribute('maxlength')).toBe('8000')
    expect(el.getAttribute('placeholder')).toBe('把招聘页的职责与要求原样粘进来')
    expect(el.getAttribute('data-testid')).toBe('jd-input')
  })
})