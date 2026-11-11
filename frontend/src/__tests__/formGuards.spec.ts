/**
 * 表单守则的源码级闸门（批次 F5 · 守则 F1 / F2，以及 §9 G2 的 textarea 半边）。
 *
 * 为什么是「读源码」而不是「挂载后断言」：F1 的判据本身就是**写法**——
 * 「`<textarea>` 只允许出现在共享组件内」。挂载之后 DOM 里长得一模一样，
 * 一个视图自己写的 `<textarea>` 和共享组件渲染出来的那个，从外面看不出区别。
 *
 * 三条正向判据缺一不可（只留禁令会把闸变成摆设）：
 * - 只禁不查「还在用」，删掉三个字段就能全绿；
 * - 只查组件不查豁免名单，名单一长这条闸就退回成建议；
 * - 只查代码不查注释豁免，一行 `<!-- eslint-disable -->` 就能把它绕过去。
 *
 * 真正的「规则会不会响」由探针取证（临时放一个直写 `<textarea>` 的 .vue 进 views/ 与
 * components/，`npx eslint` 两处当场红，输出记在 `docs/ROADMAP.md` 的 F5 行），
 * 本文件锁的是**这份配置不被悄悄改软**。分工同 `layoutGuards.spec.ts` 文件头那一段。
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const FRONTEND_ROOT = process.cwd()
const SRC_ROOT = resolve(FRONTEND_ROOT, 'src')

if (!readdirSync(SRC_ROOT, { withFileTypes: true }).some((entry) => entry.name === 'components')) {
  throw new Error(`扫描根目录不像 frontend/src：${SRC_ROOT}`)
}

/** 全站唯一允许直写 `<textarea>` 的文件，也是 ESLint 豁免名单里唯一的一项。 */
const SHARED = 'components/AutoTextarea.vue'

interface SourceFile {
  rel: string
  text: string
}

function walk(dir: string, out: SourceFile[] = []): SourceFile[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const absolute = join(dir, entry.name)
    if (entry.isDirectory()) {
      // 用例里合法地要写桩模板、要造 DOM，扫它只会自缚手脚（同 layoutGuards）。
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

function file(rel: string): SourceFile {
  const found = sources.find((source) => source.rel === rel)
  if (!found) throw new Error(`找不到源文件：${rel}`)
  return found
}

/** 注释里提一句标签名不算写了一个标签；判据管的是渲染出来的东西。 */
function withoutComments(text: string): string {
  return text.replace(/<!--[\s\S]*?-->/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
}

function countOf(text: string, needle: RegExp): number {
  return [...withoutComments(text).matchAll(needle)].length
}

describe('守则 F1：多行输入只有一个实现', () => {
  it('全 src 只有共享组件里出现 <textarea>', () => {
    const writers = sources
      .filter((source) => countOf(source.text, /<textarea[\s/>]/g) > 0)
      .map((source) => source.rel)
    expect(writers).toEqual([SHARED])
  })

  it('三个调用点真的换成了共享组件，而不是把字段删掉了', () => {
    const editor = file('components/ResumeEditor.vue').text
    const jobForm = file('views/JobCreateView.vue').text
    expect(countOf(editor, /<AutoTextarea[\s/>]/g)).toBe(2)
    expect(countOf(jobForm, /<AutoTextarea[\s/>]/g)).toBe(1)
    // 下限由调用点给：58px（两行）与 300px（JD 的粘贴靶子）都是原来量出来的值。
    expect(countOf(editor, /:min-height="58"/g)).toBe(2)
    expect(countOf(jobForm, /:min-height="300"/g)).toBe(1)
  })

  it('ESLint 那半边开着，且 src 层的豁免名单恰好只有共享组件', () => {
    const config = readFileSync(resolve(FRONTEND_ROOT, 'eslint.config.js'), 'utf8')
    expect(config).toMatch(/'vue\/no-restricted-syntax':\s*\[\s*'error'/)
    expect(config).toContain('VElement[name=\'textarea\']')

    const srcExemptions = [...config.matchAll(/ignores:\s*\[([^\]]*)\]/g)]
      .map((match) => match[1] ?? '')
      .filter((body) => body.includes('src/'))
    expect(srcExemptions).toHaveLength(1)
    const listed = (srcExemptions[0] ?? '')
      .split(',')
      .map((item) => item.trim().replace(/^'|'$/g, ''))
      .filter(Boolean)
    expect(listed).toEqual([`src/${SHARED}`])
  })

  it('src 里一处注释豁免都没有（老板 F5 硬约束：不许用注释换绿）', () => {
    const disabled = sources
      .filter((source) => /eslint-disable/.test(source.text))
      .map((source) => source.rel)
    expect(disabled).toEqual([])
  })
})

describe('守则 F2：自适应不许侵犯分页', () => {
  it('共享组件不碰纸面：既不读 --paper-*，也不认 contenteditable', () => {
    const shared = withoutComments(file(SHARED).text)
    expect(shared).not.toContain('--paper-')
    expect(shared).not.toContain('contenteditable')
    expect(shared).not.toContain('.paper')
  })

  it('纸面仍是原来那一条测量通道：scrollHeight 一处 + 两个 ResizeObserver', () => {
    const paper = withoutComments(file('components/ResumePreview.vue').text)
    expect(countOf(paper, /contentHeight\.value = element\.scrollHeight/g)).toBe(1)
    expect(countOf(paper, /new ResizeObserver\(/g)).toBe(2)
  })
})