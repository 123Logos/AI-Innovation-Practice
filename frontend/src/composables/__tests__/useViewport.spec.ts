/**
 * `useViewport` 的单测（批次 F2 · 守则 L2）。
 *
 * 批次 F2 里**没有生产调用方**（第一个要用「换组件」的是批次 F3 的抽屉 / 切换器），
 * 所以本文件就是它现在的验收：没有这份测试，它就会变成「写了但没人验」的死码，
 * 而这恰恰是 AGENTS.md §7.1 那条红线针对的东西。
 *
 * 每条用例都重新 `import` 一次模块：`useViewport.ts` 里那条
 * 「同一条媒体查询全进程只订阅一次」的缓存是模块级的，
 * 不复位就会让第二条用例读到第一条用例留下的那一份假 `matchMedia`。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

interface FakeMediaQueryList {
  readonly media: string
  matches: boolean
  addEventListener?: (type: 'change', listener: (event: { matches: boolean }) => void) => void
  removeEventListener?: (type: 'change', listener: (event: { matches: boolean }) => void) => void
  addListener?: (listener: (event: { matches: boolean }) => void) => void
}

type ViewportModule = typeof import('@/composables/useViewport')

/** 本条用例里被创建出来的查询列表，按 media 串索引。 */
let lists: Map<string, FakeMediaQueryList>
/** 被调用过几次 `matchMedia`——去重判据就读这一格。 */
let matchMediaCalls: string[]

function installMatchMedia(initial: Record<string, boolean>): void {
  lists = new Map()
  matchMediaCalls = []
  window.matchMedia = ((query: string) => {
    matchMediaCalls.push(query)
    const existing = lists.get(query)
    if (existing) return existing as unknown as MediaQueryList

    const list: FakeMediaQueryList = {
      media: query,
      matches: initial[query] ?? false,
      addEventListener: (_type, listener) => {
        listeners.set(query, listener)
      },
    }
    lists.set(query, list)
    return list as unknown as MediaQueryList
  }) as unknown as typeof window.matchMedia
}

/** 每条查询注册的 change 监听（只留最后一个，够用了）。 */
let listeners: Map<string, (event: { matches: boolean }) => void>

/** 只支持老 Safari 的 `addListener`，用来验那条回落分支。 */
function installLegacyMatchMedia(matches: boolean): void {
  lists = new Map()
  listeners = new Map()
  matchMediaCalls = []
  window.matchMedia = ((query: string) => {
    matchMediaCalls.push(query)
    const list: FakeMediaQueryList = {
      media: query,
      matches,
      addListener: (listener) => {
        listeners.set(query, listener)
      },
    }
    lists.set(query, list)
    return list as unknown as MediaQueryList
  }) as unknown as typeof window.matchMedia
}

let nativeMatchMedia: typeof window.matchMedia | undefined

beforeEach(() => {
  // jsdom 本身不实现 `matchMedia`，这里的 `undefined` 是**起点**而不是桩：
  // 「环境里没有 matchMedia 也不许抛」那条用例测的就是这个真实状态。
  nativeMatchMedia = window.matchMedia
  listeners = new Map()
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: nativeMatchMedia,
  })
})

async function load(): Promise<ViewportModule> {
  vi.resetModules()
  return import('@/composables/useViewport')
}

describe('断点表本身（L2 的「同一组值」）', () => {
  it('七个值就是那七个：760 / 1100 / 768 / 720 / 1434 / 1174 / 1440，多一格少一格都要显式改这里', async () => {
    const { BREAKPOINTS } = await load()

    // 第五格是老板 2026-11-11 裁定加的（`ROADMAP.md` §6 第 8 条）：简历工作区开三列的
    // **容器**阈值。它不是拍的整数——794（A4 原大）+ 280 + 200 + 64（两个间距）
    // + 96（左右内缩）= 1434，低于它一律两列，免得「屏幕更宽、简历更小」。
    // 第六格是同一条裁定的第二次落地（§6b 第 3 条）：常驻左栏登场的**视口**阈值。
    // 算术下限 1429 = 794 + 240（左栏）+ 24（间距）+ 56（内缩）+ 15（滚动条），
    // 取 1440 留余量；低于它左栏不登场，否则纸面会在登场那一刻缩水。
    expect(BREAKPOINTS).toEqual({
      compact: 760,
      wide: 1100,
      dialog: 768,
      paper: 720,
      paperThreeColumn: 1434,
      paperTwoColumn: 1174,
      sidebar: 1440,
    })
    expect(BREAKPOINTS.paperThreeColumn).toBe(794 + 280 + 200 + 64 + 96)
    // 第七格：单列堆叠换并排的容器阈值。并排要分给表单 300 + 间距 24 + 内缩 56，
    // 所以只有纸面已封顶 794 且分完还剩 794 时才不缩水（760→1024 那次 713→629 就是它）。
    expect(BREAKPOINTS.paperTwoColumn).toBe(794 + 300 + 24 + 56)
    expect(BREAKPOINTS.sidebar).toBeGreaterThanOrEqual(794 + 240 + 24 + 56 + 15)
  })

  it('媒体查询串由表推导，不允许与表各写一份数字', async () => {
    const { BREAKPOINTS, WIDTH_MEDIA_QUERIES } = await load()

    expect(WIDTH_MEDIA_QUERIES).toEqual({
      compact: `(max-width: ${BREAKPOINTS.compact}px)`,
      wide: `(min-width: ${BREAKPOINTS.wide}px)`,
      sidebar: `(min-width: ${BREAKPOINTS.sidebar}px)`,
    })
  })
})

describe('useViewport 的宽度判定', () => {
  it('初始 matches 直接反映在响应式值上，且三条查询各订阅一次', async () => {
    installMatchMedia({
      '(max-width: 760px)': true,
      '(min-width: 1100px)': false,
      '(min-width: 1440px)': false,
    })
    const { useViewport } = await load()

    const viewport = useViewport()
    expect(viewport.isCompact.value).toBe(true)
    expect(viewport.isWide.value).toBe(false)
    expect(viewport.isSidebar.value).toBe(false)
    expect(matchMediaCalls).toEqual([
      '(max-width: 760px)',
      '(min-width: 1100px)',
      '(min-width: 1440px)',
    ])
  })

  it('1100–1439 是独立的一档：宽屏形状成立，但左栏不常驻', async () => {
    // 这一档是 2026-11-11 裁定划出来的（§6b 第 3 条）。两档「至少」不是同一件事，
    // 合成一个布尔就会让 1280 的笔记本重新被 240px 左栏把纸面挤小。
    installMatchMedia({ '(min-width: 1100px)': true, '(min-width: 1440px)': false })
    const { useViewport } = await load()

    const viewport = useViewport()
    expect(viewport.isWide.value).toBe(true)
    expect(viewport.isSidebar.value).toBe(false)
  })

  it('视口跨档时值跟着变——这是 F3 换组件唯一能依赖的东西', async () => {
    installMatchMedia({ '(max-width: 760px)': false, '(min-width: 1100px)': false })
    const { useViewport } = await load()

    const viewport = useViewport()
    expect(viewport.isCompact.value).toBe(false)

    const onChange = listeners.get('(max-width: 760px)')
    expect(onChange).toBeTypeOf('function')
    onChange?.({ matches: true })
    expect(viewport.isCompact.value).toBe(true)
  })

  /**
   * 去重这条不是洁癖：`useViewport()` 会被多个组件各调一次，
   * 每次都新订阅就会让监听器数量随组件数增长，而断点查询只有三条。
   */
  it('多处调用共用同一份状态，且同一条查询只向环境订阅一次', async () => {
    installMatchMedia({
      '(max-width: 760px)': true,
      '(min-width: 1100px)': true,
      '(min-width: 1440px)': true,
    })
    const { useViewport } = await load()

    const first = useViewport()
    const second = useViewport()

    expect(first.isCompact).toBe(second.isCompact)
    expect(first.isWide).toBe(second.isWide)
    expect(first.isSidebar).toBe(second.isSidebar)
    expect(matchMediaCalls).toEqual([
      '(max-width: 760px)',
      '(min-width: 1100px)',
      '(min-width: 1440px)',
    ])
  })

  it('老 Safari（只有 addListener）也能收到变化', async () => {
    installLegacyMatchMedia(false)
    const { useViewport } = await load()

    const viewport = useViewport()
    expect(viewport.isCompact.value).toBe(false)

    listeners.get('(max-width: 760px)')?.({ matches: true })
    expect(viewport.isCompact.value).toBe(true)
  })

  /**
   * 环境没有 `matchMedia` 时保持 `false`：按 L2 的分工，宽度只决定形状，
   * 决定错了顶多拿到宽屏那一档，不该让整页白屏。
   */
  it('环境不提供 matchMedia 时不抛错，值退化为「非窄屏」', async () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: undefined,
    })
    const { useViewport } = await load()

    expect(() => useViewport()).not.toThrow()
    expect(useViewport().isCompact.value).toBe(false)
  })
})
