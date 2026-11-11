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
 * 七个值的来历（取证见守则 L2 行）：
 * - `compact: 760`  窄屏结构差异（顶栏账户入口换字、各页单列）；
 * - `wide: 1100`    宽屏**形状**差异（岗位列表两列卡片、骨架屏列数），只被 CSS 消费；
 * - `dialog: 768`   Element 弹窗铺满视口的阈值（`styles.css`）；
 * - `paper: 720`    A4 纸面缩放阈值（`ResumePreview.vue`）；
 * - `paperThreeColumn: 1434`  简历工作区开三列的**容器**阈值（老板 2026-11-11 裁定，
 *   `ROADMAP.md` §6 第 8 条）。它不是拍的整数，是算出来的：纸面 A4 原大 794 +
 *   左列 280 + 右列 200 + 两个间距 64 + 左右内缩 96 = 1434。低于它一律两列，
 *   因为同一宽度下三列永远比两列窄一列——「屏幕更宽、简历更小」就是这么来的。
 * - `sidebar: 1440`  常驻简历左栏登场的**视口**阈值（同一条裁定的第二次落地，
 *   `ROADMAP.md` §6b 第 3 条）。左栏原来跟着 `wide: 1100` 出现，于是 1024→1120
 *   纸面从 629px 掉到 485px：又是一次「屏幕更宽、简历更小」，只是这次吃掉宽度的是左栏。
 *   **收窄左栏救不了它**：要在 1120 保住 629px 纸面，左栏只能剩 96px（1105 可用
 *   − 56 内缩 − 24 间距 − 300 编辑列下限 − 629 纸面），那个宽度放不下简历名 + 默认徽标 + 两个动作。
 *   所以走裁定给的第二条路——抬阈值，而且抬到**纸面已经封顶**的那一档：
 *   794 + 240（左栏）+ 24（间距）+ 56（内缩）+ 15（滚动条）= 1429 是算术下限，
 *   取 1440 留 11px 余量给更宽的系统滚动条。≥1440 时纸面在有无左栏两种形态下都是 794px，
 *   登场那一刻不再缩水；1100–1439 这段改用**已经存在的**右侧抽屉（`ResumeDrawer.vue`），
 *   列表照样够得着，只是不常驻。
 * - `paperTwoColumn: 1174`  简历工作区从「单列堆叠」换成「表单 + 纸面并排」的**容器**阈值。
 *   同一条底线量出来的第三处：单列时纸面独占整行（760 档 713px），并排时要分给表单
 *   300 + 间距 24 + 内缩 56，于是 1024 档反而只剩 629px——比 760 还小。
 *   并排只有在「纸面已经封顶 794、且分完还剩 794」时才不缩水：794 + 300 + 24 + 56 = 1174。
 *   低于它一律单列堆叠、整列居中封顶 A4 原大，所以 841–1188 这段看到的是 1:1 的纸，
 *   代价是表单在纸面上方而不是左边（要滚动才看得到预览）。这是那条底线的直接后果，
 *   不是顺手改的排版。
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
  paperThreeColumn: 1434,
  paperTwoColumn: 1174,
  sidebar: 1440,
} as const

export type BreakpointKey = keyof typeof BREAKPOINTS

/**
 * 宽度媒体查询语句。`compact` 是「不超过」，`wide` / `sidebar` 是「至少」——
 * 它们不是同一条查询的正反面（761–1099 这段两头都不沾），所以各自写死方向，
 * 不给调用方留「自己拼个 min/max」的口子。
 */
export const WIDTH_MEDIA_QUERIES = {
  compact: `(max-width: ${BREAKPOINTS.compact}px)`,
  wide: `(min-width: ${BREAKPOINTS.wide}px)`,
  sidebar: `(min-width: ${BREAKPOINTS.sidebar}px)`,
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
  /** 宽屏形状（≥ `wide`）。 */
  readonly isWide: Ref<boolean>
  /** 宽到能让简历左栏常驻而不缩纸面（≥ `sidebar`）。 */
  readonly isSidebar: Ref<boolean>
}

/**
 * 当前视口落在哪一档。组件里只许用它问宽度，不许自己 `matchMedia`。
 */
export function useViewport(): Viewport {
  return {
    isCompact: mediaState(WIDTH_MEDIA_QUERIES.compact),
    isWide: mediaState(WIDTH_MEDIA_QUERIES.wide),
    isSidebar: mediaState(WIDTH_MEDIA_QUERIES.sidebar),
  }
}
