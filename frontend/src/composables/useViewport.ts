/**
 * 视口断点（守则 L2）——全站**唯一**一份宽度令牌，也是唯一允许出现「按宽度换组件」的地方。
 *
 * 为什么要单独一个模块，而不是直接在 CSS 里写数字：CSS 的 `@media` 至今读不了
 * `var(--bp-*)`（媒体查询在样式计算之前求值，自定义属性那时还不存在），所以
 * 「同一组值只写一次」在跨语言这一层只能靠**一条扫描断言**兑现：
 * `src/__tests__/layoutGuards.spec.ts` 会把 `src/**` 里每一个 `@media (max|min-width: Npx)`
 * 的 N 与本文件的 `BREAKPOINTS` 对照，出现表外数值即红。这样 CSS 与 JS 读的是同一张表，
 * 只是一个是查、一个是引。
 *
 * 四个值的来历（取证见守则 L2 行，本批只收口不改值）：
 * - `compact: 760`  窄屏结构差异（顶栏「更多」菜单、各页单列）；
 * - `wide: 1100`    宽屏结构差异（简历工作区三列 / 两列、岗位列表侧栏）；
 * - `dialog: 768`   Element 弹窗铺满视口的阈值（`styles.css`）；
 * - `paper: 720`    A4 纸面缩放阈值（`ResumePreview.vue`）。
 *
 * L2 的行为分工也在这里落地：**结构差异**（同一棵树换个形状）留在 CSS 媒体查询里；
 * 只有**换组件**才允许 `matchMedia`，而 `matchMedia` 的宽度查询只许从本文件发出。
 * `stores/theme.ts` 里那两处 `matchMedia('(prefers-color-scheme: dark)')` 不在 L2 的射程内：
 * 它查的是配色偏好，不是视口宽度，与「断点」无关，收进来只会让本文件变成主题 store 的一部分。
 *
 * 本批（批次 F2）交付的是**收口点本身**：批次 F3 的抽屉 / 侧栏 / 工作区切换器才是第一个
 * 需要「换组件」的消费者。在那之前 `useViewport()` 没有生产调用方，所以它自带单测
 * （`src/composables/__tests__/useViewport.spec.ts`），不留一格「写了但没人验」的死码。
 */

import { ref, type Ref } from 'vue'

/** 宽度断点（px）。CSS 与 JS 共用的那一组值，新增一格必须同时改扫描断言。 */
export const BREAKPOINTS = {
  compact: 760,
  wide: 1100,
  dialog: 768,
  paper: 720,
} as const

export type BreakpointKey = keyof typeof BREAKPOINTS

/**
 * 宽度媒体查询语句。`compact` 是「不超过」，`wide` 是「至少」——
 * 两者不是同一条查询的正反面（761–1099 这段两头都不沾），所以各自写死方向，
 * 不给调用方留「自己拼个 min/max」的口子。
 */
export const WIDTH_MEDIA_QUERIES = {
  compact: `(max-width: ${BREAKPOINTS.compact}px)`,
  wide: `(min-width: ${BREAKPOINTS.wide}px)`,
} as const satisfies Record<string, string>

/** 同一条查询在全进程内只订阅一次：断点不会在运行期变化，没有退订的必要。 */
const liveQueries = new Map<string, Ref<boolean>>()

function hasMatchMedia(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
}

/**
 * 把一条媒体查询包成一个响应式布尔。
 *
 * 老 Safari（<14）只有 `addListener`，`theme.ts` 已经踩过一次，这里同样两路都试；
 * 两条都没有时不抛错、只保持 `false`——按 L2，宽度只该决定形状，决定错了也只是窄屏形状，
 * 不该让整页白屏。
 */
function mediaState(query: string): Ref<boolean> {
  const existing = liveQueries.get(query)
  if (existing) return existing

  // `matchMedia` 只调**一次**：环境里那个 `MediaQueryList` 既是初始答案，也是订阅对象。
  // 调两次在真浏览器上只是浪费，在测试里会把「一条查询只订阅一次」这条判据数歪。
  const list = hasMatchMedia() ? window.matchMedia(query) : null
  const state = ref(list?.matches ?? false)
  if (list) {
    const onChange = (event: MediaQueryListEvent): void => {
      state.value = event.matches
    }
    if (typeof list.addEventListener === 'function') {
      list.addEventListener('change', onChange)
    } else if (typeof (list as unknown as LegacyMediaQueryList).addListener === 'function') {
      ;(list as unknown as LegacyMediaQueryList).addListener(onChange)
    }
  }
  liveQueries.set(query, state)
  return state
}

/** 只声明 `addListener` 这一支老接口，避免把整份 `MediaQueryList` 抄一遍。 */
interface LegacyMediaQueryList {
  addListener(listener: (event: MediaQueryListEvent) => void): void
}

export interface Viewport {
  /** 窄屏（≤ `compact`）。 */
  readonly isCompact: Ref<boolean>
  /** 宽屏（≥ `wide`）。 */
  readonly isWide: Ref<boolean>
}

/**
 * 当前视口落在哪一档。组件里只许用它问宽度，不许自己 `matchMedia`。
 */
export function useViewport(): Viewport {
  return {
    isCompact: mediaState(WIDTH_MEDIA_QUERIES.compact),
    isWide: mediaState(WIDTH_MEDIA_QUERIES.wide),
  }
}
