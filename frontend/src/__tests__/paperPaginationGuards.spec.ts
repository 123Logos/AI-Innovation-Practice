/**
 * 纸面分页规则的源码级闸门（第 15 轮裁定，`docs/ROADMAP.md` §6 第 5 条）。
 *
 * 为什么必须读源码：`page-break-inside: avoid` 是**分页媒体**的属性，
 * jsdom 既不排版也不分页，挂载后断言只能证明「样式表字符串里有这几个字母」，
 * 证明不了它落在哪个块、哪个媒体查询、有没有被 scoped 加了一层属性选择器。
 * 而下面每一条都可能单独把功能弄坏：
 *
 * 1. **落在 scoped 块里** → 选择器变成 `.paper .paper-experience[data-v-xxx]`，
 *    `v-html` 的生成物没有那个属性，于是「实时预览不被腰斩、导出稿照样腰斩」。
 *    这条最阴，因为它看起来完全正常，只有打印出来才发现。
 * 2. **落在 `@media print` 外面** → 现在没副作用（`break-*` 在屏幕上本就不生效），
 *    但它会诱导下一个人把别的打印声明也往上挪，最终一半屏幕一半打印。
 * 3. **只钉到整节**（`.paper-section`）→ 一节本身超过一页时，浏览器忽略这条限制、
 *    把整节推到下一页，第一页留一大片空白。所以判据要求钉在「一条内容」这一级。
 * 4. **被删掉** → 全仓今天一条都没有过，删掉不会有任何测试变红，只有用户的简历变丑。
 *
 * 与 G4（`scripts/check_design_tokens.py`）的分工：那条闸判「纸面块的裸 px 是否豁免」，
 * 它不关心分页属性在不在。这里只判「规则在不在、在哪」，两条互不替代。
 *
 * 判据一律跑在**剔掉注释之后**的文本上：注释里写满 `page-break-inside: avoid`
 * 不算数——G1 那次「注释里的标记解锁了检查」已经踩过一模一样的坑。
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

/** 用 `process.cwd()` 而不是 `import.meta.url`：vitest 在 jsdom 下把 URL 报成 `http://`（同 `layoutGuards.spec.ts`）。 */
const PREVIEW_PATH = resolve(process.cwd(), 'src', 'components', 'ResumePreview.vue')

interface StyleBlock {
  /** `<style scoped>` 出来的块，选择器会被加一层 `[data-v-*]`。 */
  scoped: boolean
  css: string
}

/** 注释剔干净：`/* … *\/` 里的字面量不是规则。 */
function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

/**
 * 取 `<style>` 块，**只认行首**的顶层标签。
 *
 * 不能用 `<style([^>]*)>` 一路扫：`ResumePreview.vue` 第 170 行的脚本注释里就有
 * 一对 `<style>` 字面量（讲 `@page` 注入），正则会把注释当成开标签、把整段
 * scoped 块吞进一个「非 scoped」块里——本闸第一次做反证时正是被它蒙绿的
 * （把纸面块改成 scoped，判「非 scoped」的那条仍然绿）。SFC 的顶层标签本就
 * 必须从行首开始，按行锚定既够用又能把注释里那些字面量挡在外面。
 */
function styleBlocks(text: string): StyleBlock[] {
  const blocks: StyleBlock[] = []
  const open = /^<style\b([^>]*)>[ \t]*$/gm
  let match: RegExpExecArray | null = open.exec(text)
  while (match !== null) {
    const start = match.index + match[0].length
    const close = /^<\/style>[ \t]*$/gm
    close.lastIndex = start
    const end = close.exec(text)
    blocks.push({
      scoped: /\bscoped\b/.test(match[1] ?? ''),
      css: stripComments(text.slice(start, end ? end.index : text.length)),
    })
    if (!end) break
    open.lastIndex = end.index + end[0].length
    match = open.exec(text)
  }
  return blocks
}

/**
 * 取出所有 `@media print` 的**块内文本**（不含查询本身）。
 *
 * 只认 `print` / `only print`：`screen, print` 这类一半屏幕一半打印的查询
 * 不算「打印作用域」，G4 的判据里已经把这条写死，这里保持一致的口径。
 */
function printBodies(css: string): string[] {
  const bodies: string[] = []
  for (const match of css.matchAll(/@media\s+([^{]+)\{/g)) {
    const query = (match[1] ?? '').trim().toLowerCase()
    if (query !== 'print' && query !== 'only print') continue
    let cursor = (match.index ?? 0) + match[0].length
    let depth = 1
    const start = cursor
    while (cursor < css.length && depth > 0) {
      if (css[cursor] === '{') depth += 1
      else if (css[cursor] === '}') depth -= 1
      cursor += 1
    }
    // depth 没归零说明花括号不配对，宁可把整段交出去让断言失败，也不要静默跳过。
    bodies.push(css.slice(start, depth === 0 ? cursor - 1 : css.length))
  }
  return bodies
}

const source = readFileSync(PREVIEW_PATH, 'utf8')
const blocks = styleBlocks(source)

/** 生成物（`v-html`）只受非 scoped 块约束，所以打印规则必须在这几个块里找。 */
const globalPrintBodies = blocks.filter((block) => !block.scoped).flatMap((block) => printBodies(block.css))
const allPrintBodies = blocks.flatMap((block) => printBodies(block.css))

describe('纸面打印分页规则（第 15 轮裁定）', () => {
  it('解析器本身不空转：文件里确实有 print 块，且非 scoped 的那一份在场', () => {
    // 反向护栏：文件改了路径、`<style>` 写法变了，本文件必须红，而不是对着空数组全绿。
    // 精确到 2 块：SFC 里就是「scoped 的界面 chrome」+「非 scoped 的纸面」两块，
    // 多出来一块就说明解析器又把脚本注释里的 `<style>` 字面量当成标签了。
    expect(blocks).toHaveLength(2)
    expect(blocks.filter((block) => !block.scoped)).toHaveLength(1)
    expect(allPrintBodies.length).toBeGreaterThan(0)
    expect(globalPrintBodies.length).toBeGreaterThan(0)
  })

  it('段落级防腰斩写在非 scoped 的 @media print 里，且钉到「一条内容」这一级', () => {
    const body = globalPrintBodies.join('\n')

    expect(body).toContain('page-break-inside: avoid')
    // 标准属性与兼容别名都写：只写一个会在某一边静默失效。
    expect(body).toContain('break-inside: avoid')
    // 经历、项目、证书、作品四段共用这个容器类（模板里两段 `v-for` 复用了它）。
    // 判据必须要求类名后面紧跟逗号或左花括号：只写 `toContain('.paper .paper-experience')`
    // 会被 `.paper .paper-experienceX` 这类手滑字符串前缀蒙过去（本闸第一次红就是被它蒙的）。
    expect(body).toMatch(/\.paper \.paper-experience\s*(?:,|\{)/)
    // 整节不能设 avoid：一节超过一页时会把整节推到下一页，第一页空一大片。
    expect(body).not.toMatch(/\.paper \.paper-section\s*(?:,|\{)[\s\S]{0,80}break-inside/)
  })

  it('段标题不留在页尾（标题与它的内容分居两页是最常见的那种丑）', () => {
    const body = globalPrintBodies.join('\n')
    expect(body).toContain('.paper .paper-section > h2')
    expect(body).toContain('break-after: avoid')
    expect(body).toContain('page-break-after: avoid')
  })

  it('预警线不上纸：打印时必须隐藏，否则会被 HR 当成简历的一部分', () => {
    const body = allPrintBodies.join('\n')
    expect(body).toMatch(/\.paper-page-guard\s*\{\s*display:\s*none/)
  })

  it('这条闸认的是规则、不是注释里的字（先用一个反证钉住剔除这一步）', () => {
    // 反证：把判据要的那几个词全塞进注释里，剔完之后必须一个都不剩。
    // 没有这一步，上面的断言无法区分「规则真的在」与「注释里提到过」。
    const probe = '/* page-break-inside: avoid; break-after: avoid; */ .x { color: red }'
    expect(probe).toContain('page-break-inside')
    expect(stripComments(probe)).not.toContain('page-break-inside')
    expect(stripComments(probe)).not.toContain('break-after')
    // 现场也自证一次：这份文件确实有注释被剔掉（剔 0 个字符等于没剔）。
    const raw = readFileSync(PREVIEW_PATH, 'utf8')
    expect(raw.length).toBeGreaterThan(stripComments(raw).length)
  })
})
