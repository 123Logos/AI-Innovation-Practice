/**
 * 全局换肤引擎（D5.2 补充）。
 *
 * 为什么是「纯函数模块」而不是 Pinia store：
 * 换肤是**副作用**（改 `document.documentElement` 的内联变量），没有需要被
 * 模板订阅的状态。做成 store 只会多一层「谁是当前主题」的重复来源——
 * `stores/resume.ts` 的 `draft.theme` 已经是唯一事实来源，这里只负责把它画出来。
 *
 * 变量分两组，**不要混用**：
 * - `--theme-*` → Web UI（按钮 / 链接 / 选中态 / 输入框聚焦框 / 顶栏）
 * - `--paper-*` → A4 纸面（由 `ResumePreview.vue` 维护）
 * 纸张是会被打印的独立介质：它不该带上 Web UI 的浅色底，也不该被暗色模式影响。
 *
 * 色值全部由预设的 `accent` / `rule` **离线可推**地派生，不引入任何自定义色输入：
 * 用户只能从 8 套里选，所以对比度在构建期就是确定的（见 `document_style.py`）。
 */

/** 与 `prefers-color-scheme: dark` 下各组件的底色同源（顶栏 / 卡片都用这个深墨绿）。 */
const DARK_SURFACE = '#1b2421'
/** 暗色模式下主色按钮上的文字：不用纯白，避免在浅主色上过曝。 */
const DARK_ON_PRIMARY = '#12201d'
const WHITE = '#ffffff'

type Rgb = [number, number, number]

function hexToRgb(hex: string): Rgb {
  const value = hex.replace('#', '')
  const full =
    value.length === 3
      ? value
          .split('')
          .map((char) => char + char)
          .join('')
      : value
  return [
    Number.parseInt(full.slice(0, 2), 16),
    Number.parseInt(full.slice(2, 4), 16),
    Number.parseInt(full.slice(4, 6), 16),
  ]
}

function rgbToHex([r, g, b]: Rgb): string {
  const clamp = (channel: number): string =>
    Math.max(0, Math.min(255, Math.round(channel))).toString(16).padStart(2, '0')
  return `#${clamp(r)}${clamp(g)}${clamp(b)}`
}

/**
 * 线性插值：`weight` 是**第二个颜色**的占比。
 * `mix(accent, WHITE, 0.9)` = 九成白 + 一成主色（极浅底色）。
 */
function mix(from: string, to: string, weight: number): string {
  const a = hexToRgb(from)
  const b = hexToRgb(to)
  const w = Math.max(0, Math.min(1, weight))
  return rgbToHex([
    a[0] + (b[0] - a[0]) * w,
    a[1] + (b[1] - a[1]) * w,
    a[2] + (b[2] - a[2]) * w,
  ])
}

/** sRGB 相对亮度（WCAG 2.x 定义），用于算对比度。 */
function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((channel) => {
    const c = channel / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }) as Rgb
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrastRatio(a: string, b: string): number {
  const la = luminance(a)
  const lb = luminance(b)
  const [hi, lo] = la > lb ? [la, lb] : [lb, la]
  return (hi + 0.05) / (lo + 0.05)
}

/**
 * 朝白色逐步提亮，直到与 `background` 的对比度达到 `minRatio`。
 *
 * 为什么不写死「提亮 45%」：8 套预设的 `accent` 明度差得很远（墨黑 13:1、
 * 橄榄 7.5:1），同一个比例提亮后落在暗底上的对比度也差得远。用循环兜底，
 * 每套主题都恰好「够亮」，不会有一套在暗色下糊掉。
 */
function lightenUntil(hex: string, background: string, minRatio: number, maxSteps = 30): string {
  let result = hex
  for (let step = 0; step < maxSteps && contrastRatio(result, background) < minRatio; step += 1) {
    result = mix(result, WHITE, 0.06)
  }
  return result
}

/** 主题色对：只认这两个值，其余派生量算出来。 */
export interface ThemePalette {
  accent: string
  rule: string
}

/**
 * 生成一组 CSS 变量。
 *
 * 明色：`accent` 本身就是 ≥4.5:1 的深色，直接用作主色，白色文字压在上面。
 * 暗色：同一份 `accent` 在深底上会糊掉（酒红 8.35:1 是对白纸而言，对深墨绿只有
 * 2:1 出头），所以主色改成「提亮到对比度达标」，按钮文字同时翻成深色。
 */
function buildVariables(palette: ThemePalette, dark: boolean): Record<string, string> {
  const { accent, rule } = palette

  if (!dark) {
    const primary = accent
    return {
      '--theme-primary': primary,
      '--theme-primary-hover': mix(primary, '#000000', 0.12),
      '--theme-primary-deep': mix(primary, '#000000', 0.22),
      // 用于「主色文字」：压到白底上必须 ≥7:1，比按钮底色再深一档才不刺眼。
      '--theme-primary-strong': mix(primary, '#000000', 0.34),
      '--theme-rule': rule,
      // 原代码里的 `#9fbab4` / `#c2d2ce` 一类「浅主色边框」（chip hover、标签描边）
      '--theme-rule-soft': mix(rule, WHITE, 0.45),
      '--theme-bg-light': mix(primary, WHITE, 0.9),
      // hover / active 的底色：比 bg-light 再深一档（原 `#c5e0d7` 那一类）
      '--theme-bg-strong': mix(primary, WHITE, 0.82),
      '--theme-bg-hover': mix(primary, WHITE, 0.95),
      '--theme-fade': `rgba(${hexToRgb(primary).join(', ')}, 0.13)`,
      '--theme-on-primary': WHITE,

      // Element Plus：`light-N` = 主色向「背景色」混合 N×10%
      '--el-color-primary': primary,
      '--el-color-primary-light-3': mix(primary, WHITE, 0.3),
      '--el-color-primary-light-5': mix(primary, WHITE, 0.5),
      '--el-color-primary-light-7': mix(primary, WHITE, 0.7),
      '--el-color-primary-light-8': mix(primary, WHITE, 0.8),
      '--el-color-primary-light-9': mix(primary, WHITE, 0.9),
      '--el-color-primary-dark-2': mix(primary, '#000000', 0.2),
    }
  }

  const primary = lightenUntil(mix(accent, WHITE, 0.45), DARK_ON_PRIMARY, 4.5)
  const ruleOnDark = lightenUntil(mix(rule, WHITE, 0.3), DARK_SURFACE, 3)
  return {
    '--theme-primary': primary,
    '--theme-primary-hover': mix(primary, WHITE, 0.12),
    '--theme-primary-deep': mix(primary, WHITE, 0.22),
    '--theme-primary-strong': mix(primary, WHITE, 0.34),
    '--theme-rule': ruleOnDark,
    '--theme-rule-soft': mix(ruleOnDark, DARK_SURFACE, 0.45),
    // 暗底上的浅色块：以深底为基，只掺一点主色，避免整块发灰绿
    '--theme-bg-light': mix(primary, DARK_SURFACE, 0.82),
    '--theme-bg-strong': mix(primary, DARK_SURFACE, 0.7),
    '--theme-bg-hover': mix(primary, DARK_SURFACE, 0.9),
    '--theme-fade': `rgba(${hexToRgb(primary).join(', ')}, 0.22)`,
    '--theme-on-primary': DARK_ON_PRIMARY,

    // 暗色下 `light-N` 要朝**深底**混合，否则 light-9 会变成一块刺眼的白斑
    '--el-color-primary': primary,
    '--el-color-primary-light-3': mix(primary, DARK_SURFACE, 0.3),
    '--el-color-primary-light-5': mix(primary, DARK_SURFACE, 0.5),
    '--el-color-primary-light-7': mix(primary, DARK_SURFACE, 0.7),
    '--el-color-primary-light-8': mix(primary, DARK_SURFACE, 0.8),
    '--el-color-primary-light-9': mix(primary, DARK_SURFACE, 0.9),
    '--el-color-primary-dark-2': mix(primary, WHITE, 0.2),
  }
}

let currentPalette: ThemePalette | null = null
let mediaWatcher: MediaQueryList | null = null

/**
 * 「最后应用过的配色」。
 *
 * 主题本身跟简历走（存在 `student_profiles.theme`），但有些页面拿不到简历上下文：
 * 回收站、登录页、或者首屏数据还没回来的那几百毫秒。没有这份缓存，
 * 刷新一次就会从酒红跳回青绿——用户会以为设置丢了。
 *
 * 存的是**解析后的色值**而不是 theme id：`theme.ts` 不认识 `ThemeId`
 * （那是 `resume.ts` 的领域），存 id 只会把依赖倒过来。
 */
const PALETTE_STORAGE_KEY = 'ui-theme-palette'

function persistPalette(palette: ThemePalette): void {
  try {
    window.localStorage.setItem(PALETTE_STORAGE_KEY, JSON.stringify(palette))
  } catch {
    // 隐私模式写不进去：只影响刷新后的首屏，不能因此中断换肤。
  }
}

function readStoredPalette(): ThemePalette | null {
  try {
    const raw = window.localStorage.getItem(PALETTE_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<ThemePalette>
    if (typeof parsed.accent !== 'string' || typeof parsed.rule !== 'string') return null
    return { accent: parsed.accent, rule: parsed.rule }
  } catch {
    return null
  }
}

function paint(): void {
  if (!currentPalette || typeof document === 'undefined') return
  const dark =
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches
      : false
  const variables = buildVariables(currentPalette, dark)
  const root = document.documentElement.style
  for (const [name, value] of Object.entries(variables)) {
    root.setProperty(name, value)
  }
  // 让 CSS 里的 `@media (prefers-color-scheme: dark)` 与 JS 的判定永远同源：
  // 一旦哪天改成「手动暗色开关」，这里就是唯一要改的地方。
  document.documentElement.dataset.themeMode = dark ? 'dark' : 'light'
}

/**
 * 应用一套主题。幂等，可以随便多调。
 *
 * 只注册一次 `prefers-color-scheme` 监听：用户在系统里切深浅色时，
 * 已经选好的主题要跟着重新推导一遍（否则主色还停在明色版本，暗底上糊掉）。
 */
export function applyTheme(palette: ThemePalette): void {
  currentPalette = palette
  persistPalette(palette)
  paint()
  if (mediaWatcher || typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return
  }
  mediaWatcher = window.matchMedia('(prefers-color-scheme: dark)')
  mediaWatcher.addEventListener('change', paint)
}

/**
 * 首屏恢复：在 `main.ts` 挂载应用之前调用。
 *
 * 必须早于 Vue 渲染，否则「回收站/登录页」会先画成青绿再跳成上次的配色，
 * 一次可见的闪色比没有这个功能更糟。
 */
export function initTheme(): void {
  if (typeof window === 'undefined') return
  const stored = readStoredPalette()
  if (stored) applyTheme(stored)
}

/** 供测试 / 调试：当前主题在某个模式下会算出哪些变量。 */
export function previewVariables(palette: ThemePalette, dark: boolean): Record<string, string> {
  return buildVariables(palette, dark)
}
