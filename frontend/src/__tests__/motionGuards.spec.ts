/**
 * 动效守则的源码级闸门（批次 F4 · 守则 M1 / M3 / T2）。
 *
 * 为什么读源码而不是挂载后断言：M1 的判据本身就是**写法**——「时长与缓动必须取自令牌」。
 * jsdom 没有布局引擎，`getComputedStyle` 给不出过渡时长，挂载测不出「这里写死了 .15s」；
 * 而 `prefers-reduced-motion` 在 jsdom 里根本不匹配。能判它们的只有扫描。
 *
 * 与 `layoutGuards.spec.ts` 的分工：那份管布局（L1–L5），这份管动效与令牌（M1/M3/T2）。
 * 两份都只查**已经写进守则、且批次出口判据要求机械可查**的东西；
 * 守则 §9 的 **G4（裸 hex / 裸 px 字号）仍未获批**，所以这里**不查字号与色值**——
 * 借一条已批准的 vitest 步骤把待拍的闸偷偷开起来是越界（G5 的先例写在 layoutGuards 文件头）。
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const SRC_ROOT = resolve(process.cwd(), 'src')

if (!readdirSync(SRC_ROOT, { withFileTypes: true }).some((entry) => entry.name === 'components')) {
  throw new Error(`扫描根目录不像 frontend/src：${SRC_ROOT}`)
}

interface SourceFile {
  rel: string
  text: string
}

function walk(dir: string, out: SourceFile[] = []): SourceFile[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const absolute = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name === '__tests__') continue
      walk(absolute, out)
      continue
    }
    if (!/\.(?:ts|vue|css)$/.test(entry.name)) continue
    out.push({
      rel: absolute.slice(SRC_ROOT.length + 1).split('\\').join('/'),
      text: readFileSync(absolute, 'utf8'),
    })
  }
  return out
}

const sources = walk(SRC_ROOT)
const components = sources.filter((source) => source.rel.endsWith('.vue'))
const tokens = sources.find((source) => source.rel === 'styles.css')
if (!tokens) throw new Error('找不到 styles.css：令牌层是这一批的地基，没有它下面全是空检')

/** 组件 `<style>` 段：判据管的是样式声明，注释里提一句 `.15s` 不算写死了一个时长。 */
function styleOf(text: string): string {
  return [...text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)]
    .map((match) => match[1])
    .join('\n')
    .replace(/\/\*[\s\S]*?\*\//g, '')
}

/** `transition:` / `animation:` 的声明值（到分号或右花括号为止）。 */
function motionDeclarations(text: string): string[] {
  return [...text.matchAll(/(?:^|[;{])\s*(?:transition|animation)\s*:\s*([^;}]+)/g)].map((match) =>
    (match[1] ?? '').trim(),
  )
}

const BARE_DURATION = /(?<![\w-])\d*\.?\d+m?s\b/

describe('守则 T2：三族令牌先建在 styles.css', () => {
  it('动效 / 间距 / 字号三族都在 :root 有声明（少一族，下面的消费判据就是空检）', () => {
    for (const name of [
      '--motion-fast',
      '--motion-base',
      '--motion-slow',
      '--motion-ease',
      '--motion-shift',
      '--space-1',
      '--space-9',
      '--text-xs',
      '--text-base',
      '--text-4xl',
    ]) {
      expect(tokens.text, `styles.css 缺少令牌 ${name}`).toContain(`${name}:`)
    }
  })

  it('组件里引用的 --motion-* / --space-* / --text-* 都在 :root 声明过', () => {
    const declared = new Set([...tokens.text.matchAll(/(--(?:motion|space|text)-[\w-]+)\s*:/g)].map((m) => m[1]))
    const missing: string[] = []
    for (const source of components) {
      for (const match of styleOf(source.text).matchAll(/var\((--(?:motion|space|text)-[\w-]+)/g)) {
        const name = match[1] as string
        if (!declared.has(name)) missing.push(`${source.rel} → ${name}`)
      }
    }
    expect(missing).toEqual([])
  })
})

describe('守则 M1：时长与缓动只从令牌来', () => {
  it('正向护栏：组件里真的有人在用动效令牌（否则下一条会因为全仓没人写过渡而假绿）', () => {
    const users = components.filter((source) => /var\(--motion-/.test(styleOf(source.text)))
    expect(users.length).toBeGreaterThanOrEqual(4)
  })

  it('任何组件的 transition / animation 里都不许出现字面时长', () => {
    const offenders: string[] = []
    for (const source of components) {
      for (const value of motionDeclarations(styleOf(source.text))) {
        if (BARE_DURATION.test(value)) offenders.push(`${source.rel} → ${value}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('进入 / 退出真的接上了 <Transition>，而不是只有令牌没有动效', () => {
    const users = components
      .filter((source) => /<Transition\s+name="ui-(?:fade|slide-right|slide-left)/.test(source.text))
      .map((source) => source.rel)
    /*
     * 抽屉 / 弹层 / 页面切换都要在：抽屉与外框、AI 设置弹窗与账户抽屉（顶栏）、
     * 确认弹窗、页面内容。
     * 2026-11-14 起抽屉走 `ui-slide-right`（水平滑入），所以判据从「只认 ui-fade」
     * 放宽到这三个前缀——否则「改了过渡名」会被当成「没有过渡」。
     * `WorkspaceSwitcher.vue` 已删除（模块切换改成二级导航条），不再出现在名单里。
     */
    expect(users).toEqual(
      expect.arrayContaining([
        'components/AppLayout.vue',
        'components/AppTopBar.vue',
        'components/ConfirmDialog.vue',
      ]),
    )
    // 共享的那份过渡 CSS 必须真的用令牌，且位移不超过 M1 的 8px 上限。
    expect(tokens.text).toContain('var(--fade-duration, var(--motion-slow))')
    expect(tokens.text).toContain('translateY(var(--motion-shift))')
    const shift = /--motion-shift:\s*(\d+)px/.exec(tokens.text)
    expect(shift, 'styles.css 里找不到 --motion-shift 的像素值').not.toBeNull()
    expect(Number(shift?.[1])).toBeLessThanOrEqual(8)
  })
})

describe('守则 M2：加载点先占位后填充', () => {
  it('骨架的呼吸来自令牌，且尺寸由调用方传入（组件自己不写死高度）', () => {
    expect(tokens.text).toContain('animation: var(--skeleton-pulse)')
    const skeleton = sources.find((source) => source.rel === 'components/SkeletonList.vue')
    expect(skeleton, '找不到骨架组件').toBeDefined()
    // 高度只从 props 来：组件样式里不许出现写死的 px 高度。
    expect(styleOf(skeleton?.text ?? '')).not.toMatch(/height:\s*\d+px/)
    expect(skeleton?.text).toContain('--row-h-compact')
  })

  it('六个加载点都换成了骨架，不再有「只有文字」的加载中提示', () => {
    const textOnly: string[] = []
    let skeletonLabels = 0
    for (const source of components) {
      const template = source.text.split('<template>')[1] ?? ''
      for (const line of template.split('\n')) {
        if (!line.includes('正在加载')) continue
        if (/label="正在加载/.test(line)) skeletonLabels += 1
        else textOnly.push(`${source.rel} → ${line.trim()}`)
      }
    }
    // 正向护栏：真的接上了加载骨架，否则「没有纯文字提示」会因为全仓没人写加载提示而假绿。
    // 批次 B11a（FR-07）新增两张面试页（列表 / 详情）的加载骨架，故从 6 增至 8；
    // 批次 B9（FR-04）新增能力测评页，8 → 9。
    expect(skeletonLabels).toBe(9)
    expect(textOnly).toEqual([])
  })
})

describe('守则 M3：减弱动效只有一个开关', () => {
  it('styles.css 里那个开关把位移归零、把骨架呼吸停掉', () => {
    const block = /@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*?)\n\}/.exec(tokens.text)
    expect(block, 'styles.css 没有 prefers-reduced-motion 分支').not.toBeNull()
    expect(block?.[1]).toContain('--motion-shift: 0px')
    expect(block?.[1]).toContain('--skeleton-pulse: none')
  })

  it('组件里不许再各写一个 prefers-reduced-motion 分支', () => {
    // 只看 `<style>` 段：组件注释里提一句这个媒体查询是在解释开关在哪，不是又写了一个分支。
    const offenders = components
      .filter((source) => /prefers-reduced-motion/.test(styleOf(source.text)))
      .map((source) => source.rel)
    expect(offenders).toEqual([])
  })
})