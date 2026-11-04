/**
 * `useResumeStore` 核心单测（前端测试基建第一轮）。
 *
 * 这里只钉住三条**最容易静默坏掉**的链路，不去复刻组件渲染：
 *
 * 1. 新草稿必须等于服务端默认值（配色 `forest`、排版默认顺序）。它错位的后果不是
 *    报错，而是「用户刚进编辑器就被判成有未保存修改」，或者保存一次就把配色/顺序
 *    悄悄改成了别的值。
 * 2. 改配色、调顺序必须**同时**做三件事：写 `draft`、置 `dirty`、同步全局换肤。
 *    少任何一件都会表现为「点了没反应」或「改了没落库」。
 * 3. 本地草稿的「与服务器是否一致」判定（`comparableOf` 投影）必须把 `theme` 与
 *    `layout_config` 算进去。漏算的后果很隐蔽：只调了顺序的草稿会被判成「已保存」，
 *    恢复横幅不弹，用户以为自己的调整丢了。
 *
 * `comparableOf` 是模块内私有函数，**没有为了测试去导出它**（业务代码本轮不该改）。
 * 它唯一的公开入口是 `detectLocalDraft()`：这里用「本地草稿 vs 当前草稿」的判定
 * 结果反证投影口径，测的是行为而不是实现细节。
 */

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import type { SectionId } from '@/api/types'
import { DEFAULT_LAYOUT_SECTIONS, DEFAULT_THEME, useResumeStore } from '@/stores/resume'

/** 用例里的「已登录用户」，只用于拼草稿 key。 */
const OWNER_ID = 'user-under-test'

/**
 * 本地草稿 key：与 store 内部 `draftStorageKey(owner, profileId)` 同源。
 * 当前没有已落库的简历（`profileId === null`），那一格用 `new` 占位。
 * 刻意在这里重写一遍而不是导出常量：key 规则一旦变了，这个用例应当**直接失败**，
 * 提醒改规模的人「草稿兼容性也是契约」。
 */
const DRAFT_KEY = `resume_draft:${OWNER_ID}:new`

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

/** 当前草稿里的模块 id 顺序。 */
function sectionIds(): SectionId[] {
  return useResumeStore().layoutSections.map((section) => section.id)
}

/** `Array.prototype.at` 需要 ES2022 的 lib；项目 tsconfig 停在 ES2020，这里手取下标。 */
function last<T>(items: readonly T[]): T {
  return items[items.length - 1]!
}

/** 往 `localStorage` 塞一份「上次没保存」的草稿，可覆盖任意字段。 */
function seedLocalDraft(patch: Record<string, unknown> = {}): void {
  const store = useResumeStore()
  // JSON 往返：`draft` 是 reactive 代理，直接存进去会丢掉原型、也带上响应式痕迹。
  const data = JSON.parse(JSON.stringify(store.draft)) as Record<string, unknown>
  localStorage.setItem(
    DRAFT_KEY,
    JSON.stringify({ version: 1, saved_at: Date.now(), data: { ...data, ...patch } }),
  )
}

describe('useResumeStore 初始状态', () => {
  it('新草稿取服务端默认的配色、纸张与排版，且不处于「已修改」状态', () => {
    const store = useResumeStore()

    expect(store.draft.theme).toBe(DEFAULT_THEME)
    expect(store.draft.theme).toBe('forest')
    expect(store.draft.paper_size).toBe('A4')

    // 顺序与标题都必须与后端 `DEFAULT_SECTIONS` 逐字一致（契约测试两端比对）。
    expect(store.layoutSections.map((section) => section.id)).toEqual(
      DEFAULT_LAYOUT_SECTIONS.map((section) => section.id),
    )
    expect(store.layoutSections.map((section) => section.title)).toEqual([
      '教育经历',
      '技能',
      '项目经历',
    ])

    expect(store.dirty).toBe(false)
  })
})

describe('useResumeStore 配色', () => {
  it('updateTheme 改 draft、置 dirty，并把新主色写进全局 CSS 变量', () => {
    const store = useResumeStore()
    const root = document.documentElement
    const before = root.style.getPropertyValue('--theme-primary')

    store.updateTheme('navy')

    expect(store.draft.theme).toBe('navy')
    expect(store.themeSpec.id).toBe('navy')
    expect(store.dirty).toBe(true)

    // 全局换肤是「改配色」的一半功能：按钮、选中态、Element Plus 主色都读这个变量。
    const after = root.style.getPropertyValue('--theme-primary')
    expect(after).not.toBe('')
    expect(after).not.toBe(before)
  })

  it('updateTheme 传当前主题视为没有修改（不置 dirty、不覆盖提示）', () => {
    const store = useResumeStore()

    store.updateTheme(DEFAULT_THEME)

    expect(store.draft.theme).toBe(DEFAULT_THEME)
    expect(store.dirty).toBe(false)
  })
})

describe('useResumeStore 模块调序', () => {
  /*
   * `moveSection*` 的语义是「挪一格」（对应面板上的 ↑/↓ 单次点击），
   * 不是「直接顶到端点」——3 个模块时，末项要顶到首位得点两次 ↑。
   * 两条用例都同时覆盖「单次只换一格」和「连点能到端点」，避免哪天被改成
   * 「一步到位」而没人发现（那会让用户没法把中间模块挪一格）。
   */
  it('moveSectionUp 单次只挪一格，连点能把最后一项顶到最前面', () => {
    const store = useResumeStore()
    const lastId = last(sectionIds())

    store.moveSectionUp(lastId)
    // 一次点击只和前面一位交换：[educations, experiences, skills]
    expect(sectionIds()).toEqual(['educations', 'experiences', 'skills'])
    expect(store.dirty).toBe(true)

    store.moveSectionUp(lastId)
    expect(sectionIds()).toEqual(['experiences', 'educations', 'skills'])
    expect(sectionIds()[0]).toBe(lastId)
  })

  it('moveSectionDown 单次只挪一格，连点能把第一项挪到最后面', () => {
    const store = useResumeStore()
    const firstId = sectionIds()[0]!

    // 边界：第一项再上移不应改变顺序，也不该把草稿弄脏。
    store.moveSectionUp(firstId)
    expect(sectionIds()[0]).toBe(firstId)
    expect(store.dirty).toBe(false)

    store.moveSectionDown(firstId)
    // [skills, educations, experiences]
    expect(sectionIds()).toEqual(['skills', 'educations', 'experiences'])

    store.moveSectionDown(firstId)
    expect(sectionIds()).toEqual(['skills', 'experiences', 'educations'])
    expect(last(sectionIds())).toBe(firstId)
    expect(store.dirty).toBe(true)
  })
})

describe('useResumeStore 草稿一致性投影（comparableOf 口径）', () => {
  it('本地草稿与当前草稿完全一致时，判等并清掉残留草稿', () => {
    const store = useResumeStore()
    seedLocalDraft()

    store.detectLocalDraft(OWNER_ID)

    expect(store.pendingDraft).toBeNull()
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull()
  })

  it('只有 theme 不同也必须判为「有差异」，草稿要留着等用户恢复', () => {
    const store = useResumeStore()
    const otherTheme = store.draft.theme === 'navy' ? 'olive' : 'navy'
    seedLocalDraft({ theme: otherTheme })

    store.detectLocalDraft(OWNER_ID)

    expect(store.pendingDraft).not.toBeNull()
    expect(store.pendingDraft?.data.theme).toBe(otherTheme)
    expect(localStorage.getItem(DRAFT_KEY)).not.toBeNull()
  })

  it('只有 layout_config 不同（顺序被调过）也必须判为「有差异」', () => {
    const store = useResumeStore()
    const reversed = [...store.layoutSections].reverse().map((section) => ({ ...section }))
    seedLocalDraft({
      layout_config: { version: 1, sections: reversed },
    })

    store.detectLocalDraft(OWNER_ID)

    expect(store.pendingDraft).not.toBeNull()
    expect(store.pendingDraft?.data.layout_config.sections.map((section) => section.id)).toEqual(
      reversed.map((section) => section.id),
    )
  })
})
