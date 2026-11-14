/**
 * 布局守则的源码级闸门（批次 F2 · 守则 L1 / L2 / L4 / L5）。
 *
 * 为什么是「读源码」而不是「挂载后断言」：这三条的判据本身就是**写法**。
 * jsdom 不跑媒体查询（`AppTopBar.spec.ts` 文件头同一句话），挂载测不出第五个断点；
 * 而「顶栏被摆了两次」在页面里恰恰长得跟一次一样。能判它们的只有扫描 + 一条挂载断言，
 * 挂载那一半在 `src/components/__tests__/AppLayout.spec.ts`，本文件是另一半。
 *
 * 与 `scripts/check_route_reachability.py`（G5）的分工：那条查「未挂路由的视图文件」，
 * 属于**还没批进 CI 的闸**（`docs/ROADMAP.md` §6 第 2 条待老板拍），所以它不进本文件——
 * 把待拍的闸借一条已批准的 vitest 步骤偷偷开起来，是越界，不是补位。
 * 本文件只查已经白纸黑字批准在 CI 里的东西（守则 §9 G6：vitest 这道闸本来就在跑）。
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { APP_MODULES } from '@/config/modules'
import { BREAKPOINTS } from '@/composables/useViewport'
import router from '@/router'

/**
 * 扫描根目录。
 *
 * 不用 `import.meta.url`：vitest 在 jsdom 环境里把模块 URL 报成 `http://`，
 * `fileURLToPath` 当场抛「URL must be of scheme file」（实测）。`process.cwd()` 在
 * vitest 与 CI 里都是 `frontend/`（`npm run test` / `working-directory: frontend`），
 * 而目录不对时宁可在这里立刻炸掉，也不要让下面每条断言对着一个空文件列表假绿。
 */
const SRC_ROOT = resolve(process.cwd(), 'src')

if (!readdirSync(SRC_ROOT, { withFileTypes: true }).some((entry) => entry.name === 'components')) {
  throw new Error(`扫描根目录不像 frontend/src：${SRC_ROOT}`)
}

interface SourceFile {
  /** 相对 `frontend/src` 的路径，斜杠统一成正斜杠，报错才读得懂。 */
  rel: string
  text: string
}

function walk(dir: string, out: SourceFile[] = []): SourceFile[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const absolute = join(dir, entry.name)
    if (entry.isDirectory()) {
      // 测试目录里的桩合法地要写 `matchMedia`、`@media` 之类，扫它只会自缚手脚。
      if (entry.name === '__tests__') continue
      walk(absolute, out)
      continue
    }
    if (!/\.(?:ts|vue|css)$/.test(entry.name)) continue
    out.push({
      // 相对 `frontend/src`、斜杠统一成正斜杠：断言里的期望值才写得干净，报错才读得懂。
      rel: absolute.slice(SRC_ROOT.length + 1).split('\\').join('/'),
      text: readFileSync(absolute, 'utf8'),
    })
  }
  return out
}

const sources = walk(SRC_ROOT)

function file(rel: string): SourceFile {
  const found = sources.find((source) => source.rel === rel)
  if (!found) throw new Error(`找不到源文件：${rel}`)
  return found
}

/** 去掉 HTML 注释：判据管的是**渲染出来的标记**，注释里提一句模块名不算摆了一个入口。 */
function withoutHtmlComments(text: string): string {
  return text.replace(/<!--[\s\S]*?-->/g, '')
}

/** SFC 的 `<template>` 段（不含注释以外的东西一律不解析，够用且不会误伤 `<style>`）。 */
function templateOf(text: string): string {
  const match = /<template>([\s\S]*)<\/template>/.exec(text)
  return match?.[1] ?? ''
}

/**
 * 被路由真正引用的视图（从 `router/index.ts` 的 import 段读）。
 *
 * 只对这些文件提要求：没挂路由的视图由 G5 那条脚本负责，不在这里判。
 * 批次 F7 之前 `views/DashboardView.vue` 不在这份名单里（它是守则 §11 的规划期资产、
 * 自带 `app-shell`）；F7 给它挂了 `/dashboard` 与 `/welcome` 两条路由，它从此与其余视图
 * 同一条尺子——判据见下面 L1 那组用例里「必须包含它」那条。
 */
function routedViewFiles(): string[] {
  const imports = [...file('router/index.ts').text.matchAll(/from '@\/views\/([\w.-]+\.vue)'/g)]
  return imports.map((match) => `views/${match[1]}`)
}

describe('守则 L1：一个布局，一处定义', () => {
  it('顶栏只被 AppLayout 摆一次', () => {
    const placers = sources.filter((source) => source.text.includes('<AppTopBar'))

    expect(placers.map((source) => source.rel)).toEqual(['components/AppLayout.vue'])
  })

  it('AppLayout 自己就是那一处：一个外框 + 一个顶栏 + 一个出口', () => {
    const layout = withoutHtmlComments(templateOf(file('components/AppLayout.vue').text))

    expect(layout.match(/class="app-shell"/g) ?? []).toHaveLength(1)
    expect(layout.match(/<AppTopBar/g) ?? []).toHaveLength(1)
    expect(layout.match(/<RouterView/g) ?? []).toHaveLength(1)
  })

  it('被路由引用的 14 个视图里既不摆顶栏，也不自带 app-shell 外框', () => {
    const views = [...new Set(routedViewFiles())]
    // 反向护栏：路由表读空了上面几条也会绿，所以先确认真的读到了 14 个视图
    // （12 个带外框的 + 登录 + 注册；`job-create` 与 `job-edit` 共用一个组件，
    //  所以是 14 不是 15）。原始 8 → B5 加「组织管理」= 9 → B11a 加列表/详情 = 11
    // → B9 加「能力测评」= 12 → B10a 加「学情看板」= 13 → F7 加「工作台」= 14。
    // 这个数字是**合并后**重算的：两边各自的版本（9 / 10 / 11 / 12 / 13）都不是最终值。
    expect(views).toHaveLength(14)
    // 批次 F7（2026-11-14）起 DashboardView 挂了两条路由，所以它被这条用例覆盖了：
    // 它现在必须像其他视图一样「只渲染内容区」，外框归 `AppLayout`（登录态）/
    // 自己的 `.dashboard-page` 铺底（访客态）。这条断言从「不许包含」翻成「必须包含」，
    // 翻的就是 F7 的账：以前它自带 `app-shell`，正因为不在这份名单里才没人管。
    expect(views).toContain('views/DashboardView.vue')

    for (const rel of views) {
      const text = withoutHtmlComments(templateOf(file(rel).text))
      expect(text, `${rel} 不许再手工摆顶栏`).not.toContain('<AppTopBar')
      expect(text, `${rel} 不许再自带外框`).not.toContain('app-shell')
    }
  })

  it('视图的 scoped 样式里也没有第二份 .app-shell 规则', () => {
    for (const rel of routedViewFiles()) {
      expect(file(rel).text, `${rel} 的外框样式应归 AppLayout`).not.toContain('.app-shell')
    }
  })
})

describe('守则 L2：断点只用同一组值', () => {
  // `number` 而不是表推导出的字面量联合：扫描到的宽度是任意数字，
  // 「在不在表里」正是本组用例要判的事，把它写进类型里等于提前把答案塞给断言。
  const allowed = new Set<number>(Object.values(BREAKPOINTS))

  it('扫描真的读到了带宽度查询的文件（否则下一条是空检）', () => {
    // `@container` 与 `@media` 一起数：批次 F3 第三片把简历工作区的两档改成了容器查询
    // （内容区加了常驻左栏之后，视口宽度不再等于可用宽度），只数 `@media` 会漏掉它们。
    const withWidthQuery = sources.filter((source) =>
      /@(?:media|container)[^{]*\((?:max|min)-width:/.test(source.text),
    )
    expect(withWidthQuery.length).toBeGreaterThanOrEqual(5)
  })

  it('每一个宽度查询的数值都来自 BREAKPOINTS 表（@media 与 @container 同一把尺）', () => {
    for (const source of sources) {
      for (const match of source.text.matchAll(
        /@(?:media|container)[^{]*?\((?:max|min)-width:\s*(\d+)px\)/g,
      )) {
        const value = Number(match[1])
        expect(
          allowed.has(value),
          `${source.rel} 用了表外的断点 ${value}px；要加先加进 composables/useViewport.ts`,
        ).toBe(true)
      }
    }
  })

  it('容器查询真的有人用（否则上一条对 @container 的那半是空检）', () => {
    const users = sources
      .filter((source) => /@container[^{]*\((?:max|min)-width:/.test(source.text))
      .map((source) => source.rel)
    expect(users).toContain('views/ResumeEditorView.vue')
    // 有查询就得有容器：名字对不上时浏览器会静默不匹配，页面退回三列并溢出。
    expect(file('components/AppLayout.vue').text).toContain('container-name: app-content')
  })

  /**
   * 老板 2026-11-11 裁定（`ROADMAP.md` §6 第 8 条）：「绝不能接受屏幕更宽，简历更小」。
   * 真像素归 CDP 实测（11 档数值在 PR 里），这里钉住**兑现它的那三条写法**——
   * 它们任何一条被改回去，纸面就会重新在宽屏上缩水。
   */
  it('两列时纸面封顶 A4 原大，三列只在算出来的富余阈值上开', () => {
    const view = file('views/ResumeEditorView.vue').text
    const css = file('styles.css').text

    // ① 两列是常态，纸面列有上限；多出来的宽度给编辑列而不是把纸越拉越大。
    expect(view).toContain('grid-template-columns: minmax(300px, 1fr) minmax(0, var(--paper-a4-width))')
    expect(css).toContain('--paper-a4-width: 794px')
    // ② 三列的阈值来自表，且是「至少」而不是「不超过」——方向写反就变成窄屏开三列。
    expect(view).toContain(`@container app-content (min-width: ${BREAKPOINTS.paperThreeColumn}px)`)
    // ③ 三列的左右列按裁定压缩，省下的宽度全给纸面。
    expect(view).toContain('grid-template-columns: 280px minmax(0, 1fr) 200px')
    // ④ 阈值必须是算出来的那个数，不是随手拍的整数。
    expect(BREAKPOINTS.paperThreeColumn).toBe(794 + 280 + 200 + 64 + 96)
  })

  it('单列堆叠换并排的那一档同样不许缩纸面（§6b 第 3 条量出来的第三处）', () => {
    const view = file('views/ResumeEditorView.vue').text

    // 基线是单列居中封顶 A4：760 档纸面独占整行 713px，若 761 就换并排，
    // 1024 档反而只剩 629px——比更窄的屏幕还小。
    expect(view).toContain(
      'grid-template-columns: minmax(0, var(--paper-a4-width)); justify-content: center',
    )
    expect(view).toContain(`@container app-content (min-width: ${BREAKPOINTS.paperTwoColumn}px)`)
    // 阈值 = 纸面 794 + 表单下限 300 + 间距 24 + 内缩 56：分完刚好还剩一张原大 A4。
    expect(BREAKPOINTS.paperTwoColumn).toBe(794 + 300 + 24 + 56)
    // 三档的方向必须递增，写反任何一档都会让宽屏退回窄形态。
    expect(BREAKPOINTS.paperTwoColumn).toBeLessThan(BREAKPOINTS.paperThreeColumn)
  })

  it('常驻左栏只在纸面已封顶的那一档登场（同一条裁定的第二次落地，§6b 第 3 条）', () => {
    const layout = file('components/AppLayout.vue').text

    // 开关必须问 `sidebar` 那一格而不是 `wide`：后者是 CSS 的宽屏形状档（岗位卡片两列），
    // 跟着它登场就是 1024→1120 纸面 629→485 那次缩水。
    expect(layout).toContain('const { isSidebar } = useViewport()')
    expect(layout).toContain('v-if="isSidebar"')
    expect(layout).toContain('v-if="!isSidebar && resumesOpen"')
    expect(layout).not.toContain('isWide')
    // 阈值不低于算术下限：794 纸面 + 240 左栏 + 24 间距 + 56 内缩 + 15 滚动条 = 1429。
    expect(BREAKPOINTS.sidebar).toBeGreaterThanOrEqual(794 + 240 + 24 + 56 + 15)
    // 左栏宽度就是算式里那个 240：改宽它必须一起抬阈值，否则纸面又开始缩水。
    expect(layout).toContain('flex: 0 0 240px')
  })

  it('宽度相关的 matchMedia 只出现在 composables/useViewport.ts 一处', () => {
    // 正向护栏：收口点自己得真的在用 `matchMedia`，否则「别处都没有」这条会因为
    // 全仓一处都不存在而假绿（`useViewport.ts` 被删空也会绿，那是要拦的）。
    expect(file('composables/useViewport.ts').text).toContain('window.matchMedia(query)')

    const offenders = sources
      .filter((source) => source.rel !== 'composables/useViewport.ts')
      .filter((source) => /matchMedia\s*\(/.test(source.text))
      // `theme.ts` 那两处查的是 `prefers-color-scheme`：配色偏好不是视口宽度，不在 L2 射程内。
      .filter((source) => /matchMedia\s*\(\s*['"`][^'"()]*width/.test(source.text))
      .map((source) => source.rel)

    expect(offenders).toEqual([])
  })
})

describe('守则 L3：浮层的「点外面关闭 / Esc 关闭」只有一处实现', () => {
  const BASE = 'components/OverlayPanel.vue'

  it('正向护栏：基座自己确实实现了这两件事（否则下面两条会因为全仓没人做而假绿）', () => {
    const base = file(BASE).text
    expect(base).toContain("document.addEventListener('pointerdown'")
    expect(base).toContain('el.contains(target)')
    expect(base).toContain("event.key === 'Escape'")
    // 「点自己的触发按钮不算外面」也在基座这一处：认的是 aria-controls 的反向链接。
    // 少了它，两个抽屉会叠在一起（调用方各自豁免自己的话，别的浮层就看不见那一下了）。
    expect(base).toContain("getAttribute('aria-controls')")
  })

  it('除基座外，任何组件都不许再自己写「点外面关闭」', () => {
    const offenders = sources
      .filter((source) => source.rel !== BASE)
      .filter((source) => /contains\(\s*(?:event|e)\.target/.test(source.text))
      .map((source) => source.rel)

    expect(offenders).toEqual([])
  })

  it('除基座外，谁都不许再挂 document 级 pointerdown 监听', () => {
    const offenders = sources
      .filter((source) => source.rel !== BASE)
      .filter((source) => /addEventListener\(\s*["']pointerdown["']/.test(source.text))
      .map((source) => source.rel)

    expect(offenders).toEqual([])
  })
})


describe('守则 L4：导航条目只有一张表', () => {
  it('模块描述符的形状只存在于 config/modules.ts', () => {
    const declarers = sources
      .filter((source) => /routeName:/.test(source.text))
      .map((source) => source.rel)

    expect(declarers).toEqual(['config/modules.ts'])
  })

  it('顶栏不再承载模块导航：既没有写死的模块名，也没有第二份模块数组', () => {
    const source = file('components/AppTopBar.vue')
    const template = withoutHtmlComments(templateOf(source.text))

    /*
     * 2026-11-14（UI 收尾第 3/4 条）：顶栏上那两个文字入口（「岗位库」「组织管理」）
     * 与 Logo 右侧的切换器都删掉了，模块切换只有顶栏下方的 `ModuleNav.vue` 一处。
     * 判据随之翻面：以前是「顶栏渲染的名字必须来自表」，现在是「顶栏一个模块名都不许有」
     * ——任何名字出现在这里都说明有人把入口又摆回来了。这条比原来更强，不是放宽。
     */
    for (const module of APP_MODULES) {
      expect(template, `${module.name} 不该出现在顶栏模板里`).not.toContain(module.name)
    }
    expect(source.text).not.toContain("from '@/config/modules'")
    expect(template).not.toContain('v-for="module in APP_MODULES"')
  })

  /**
   * 这一条刻意**不**写成「视图里不许出现模块名」：`JobBoardView.vue` / `JobTrashView.vue`
   * 的 `<p class="eyebrow">岗位库</p>` 是页面小标题，不是导航条目，扫它会误伤。
   * L4 的「只有一张表」由上面两条合起来钉住：能当导航用的描述符形状（带 `routeName`）
   * 全仓只有一处声明，而顶栏渲染的那几个名字在标记里没有字面量。
   *
   * 批次 F7 起 `views/DashboardView.vue` 是**第三个**消费者：它把登录态/访客态的模块入口
   * 从 `APP_MODULES` 读出来渲染成卡片，原来那份硬编码数组（曾经的 L4 违例、§11 登记在册）
   * 已删除。第三个消费者在这里显式记账，是因为「消费者名单」本身就是这条守则的判据：
   * 名单外的文件一旦 import 那张表，说明有人开始抄第二份数组了。
   */
  it('除二级导航外，没有任何组件把模块表再抄一份成自己的数组', () => {
    const consumers = sources
      .filter((source) => /from '@\/config\/modules'/.test(source.text))
      .map((source) => source.rel)

    /*
     * 两个消费者，都是**读表**而不是抄表（2026-11-14 起）：
     * - `components/ModuleNav.vue`：顶栏下方的二级导航，模块入口的唯一一处；
     * - `views/DashboardView.vue`：工作台首屏的入口卡片。
     * 顶栏与工作区切换器都已退出这份名单（切换器组件已删除）。
     * 再多一个消费者要在这里显式记账——名单本身就是这条守则的判据：
     * 名单外的文件一旦 import 那张表，说明有人开始抄第二份数组了。
     */
    expect(consumers).toEqual(['components/ModuleNav.vue', 'views/DashboardView.vue'])
  })

  it('二级导航的页签文案也一律来自表，模板里没有写死的模块名', () => {
    const source = file('components/ModuleNav.vue')
    const template = withoutHtmlComments(templateOf(source.text))

    for (const module of APP_MODULES) {
      expect(template, `${module.name} 不该以字面量出现在导航模板里`).not.toContain(module.name)
    }
    // 正向：它确实在遍历那张表，否则上面那条会因为「什么都没渲染」而假绿。
    expect(template).toContain('v-for="item in APP_MODULES"')
    expect(template).toContain(':to="{ name: item.routeName }"')
  })
})

describe('守则 L5：不进没有实现的入口', () => {
  it('表不为空，且每一项的 routeName 在真实路由表里解析得到', () => {
    expect(APP_MODULES.length).toBeGreaterThan(0)

    for (const module of APP_MODULES) {
      expect(router.hasRoute(module.routeName), `${module.key} 指向的路由不存在`).toBe(true)
    }
  })

  it('每一项的登录条件与路由记录上的 meta 一致（两份表不许各说各话）', () => {
    for (const module of APP_MODULES) {
      const record = router.getRoutes().find((route) => route.name === module.routeName)
      expect(record, `${module.routeName} 没有路由记录`).toBeDefined()
      expect(record?.meta.requiresAuth, `${module.routeName} 的 requiresAuth 与表不一致`).toBe(
        module.requiresAuth,
      )
    }
  })
})
