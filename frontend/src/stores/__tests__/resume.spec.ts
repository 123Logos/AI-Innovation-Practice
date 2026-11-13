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
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ProfileRead, SectionId } from '@/api/types'
import { DEFAULT_LAYOUT_SECTIONS, DEFAULT_THEME, MULTIVALUE_LIMIT, useResumeStore } from '@/stores/resume'

/**
 * 给 `save()` 用例造桩：只关心它发出的 PUT 请求体，不真正打后端。
 * 这里只实现本用例用到的两个方法，其余留空函数——store 的其它路径不进本文件。
 */
const profilesApi = vi.hoisted(() => ({
  listProfiles: vi.fn(),
  getProfile: vi.fn(),
  createProfile: vi.fn(),
  updateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  setDefaultProfile: vi.fn(),
  renameProfile: vi.fn(),
  listTrashProfiles: vi.fn(),
  restoreProfile: vi.fn(),
  purgeProfile: vi.fn(),
}))
vi.mock('@/api/profiles', () => profilesApi)

import { useAuthStore } from '@/stores/auth'

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
    // 五段（ADR-015 §2.2：`LAYOUT_VERSION` 2 起证书与作品各占一节）。
    // 这份清单与后端 `DEFAULT_SECTIONS` 的逐字比对在 `tests/test_profile_contract.py`，
    // 这里显式写死是为了让「默认顺序被谁悄悄改了」在本仓也留一处人眼可见的记录。
    expect(store.layoutSections.map((section) => section.title)).toEqual([
      '教育经历',
      '技能',
      '项目经历',
      '证书',
      '作品链接',
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
   * 不是「直接顶到端点」——所以断言写成「与相邻那一位交换」，而不是写死一串 id：
   * 区块从 3 段变 5 段（ADR-015 §2.2）时，写死的那串会红在一个与被测语义无关的地方。
   * 两条用例都同时覆盖「单次只换一格」和「连点能到端点」，避免哪天被改成
   * 「一步到位」而没人发现（那会让用户没法把中间模块挪一格）。
   */
  it('moveSectionUp 单次只挪一格，连点能把最后一项顶到最前面', () => {
    const store = useResumeStore()
    const before = sectionIds()
    const lastId = last(before)

    store.moveSectionUp(lastId)
    // 一次点击只和前面一位交换，其余保持不动。
    const expected = [...before]
    const tail = expected.length - 1
    ;[expected[tail - 1], expected[tail]] = [expected[tail]!, expected[tail - 1]!]
    expect(sectionIds()).toEqual(expected)
    expect(store.dirty).toBe(true)

    // 连点 N-1 次才到底：证明每次只挪一格，而不是一步到位。
    for (let index = before.length - 2; index > 0; index -= 1) {
      store.moveSectionUp(lastId)
    }
    expect(sectionIds()[0]).toBe(lastId)
  })

  it('moveSectionDown 单次只挪一格，连点能把第一项挪到最后面', () => {
    const store = useResumeStore()
    const before = sectionIds()
    const firstId = before[0]!

    // 边界：第一项再上移不应改变顺序，也不该把草稿弄脏。
    store.moveSectionUp(firstId)
    expect(sectionIds()).toEqual(before)
    expect(store.dirty).toBe(false)

    store.moveSectionDown(firstId)
    const expected = [...before]
    ;[expected[0], expected[1]] = [expected[1]!, expected[0]!]
    expect(sectionIds()).toEqual(expected)

    // 继续连点到尾：每一次都只跨过一位。
    for (let index = 1; index < before.length - 1; index += 1) {
      store.moveSectionDown(firstId)
    }
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

  it('缺 target_role 键的老草稿必须仍能恢复（可选新字段不得进判据）', () => {
    // `DRAFT_VERSION` 仍是 1，所以「上一版前端存的草稿」就是这份少一个键的对象。
    // 把可选新字段写进 `isProfileDraft` 会让这类草稿整体判废：用户看到的不是
    // 「这一格没填」，而是整条「上次没保存的修改」横幅连同其它未保存内容一起消失。
    // `JSON.stringify` 会丢掉值为 `undefined` 的键，正好构造出缺键的老形状；
    // 同时改掉 `full_name`，让它与当前草稿**有差异**，否则判等分支会先把草稿当残留清掉，
    // 用例就会因为错误的原因通过。
    const store = useResumeStore()
    seedLocalDraft({ full_name: '上一次没点保存的名字', target_role: undefined })
    const seeded = JSON.parse(localStorage.getItem(DRAFT_KEY)!).data as Record<string, unknown>
    expect(Object.keys(seeded)).not.toContain('target_role')

    store.detectLocalDraft(OWNER_ID)

    expect(store.pendingDraft).not.toBeNull()

    store.restoreDraft()
    expect(store.draft.full_name).toBe('上一次没点保存的名字')
    // 缺键由 normalizeDraft 补成空串，而不是 undefined：模板直接绑定它。
    expect(store.draft.target_role).toBe('')
  })
})

describe('useResumeStore 三格（ADR-015 §5）', () => {
  /**
   * 钉住「脏判定必须把三格算进去，且空行不算内容」。
   * 加一条空证书不该亮「保存修改」（否则用户每进一次页面都看到未保存），
   * 填了才算；再改回空行必须能判回未修改——这就是 comparableOf 投影空行剔除的意义。
   */
  it('只加一条证书（其它都没动）使 dirty 变 true，改成空行后回到 false（空行不进判据）', () => {
    const store = useResumeStore()
    expect(store.dirty).toBe(false)

    store.addCertificate()
    // 空证书行被 comparableOf 丢弃，与基线（空）一致 → 不脏。
    expect(store.dirty).toBe(false)

    store.updateCertificate(0, 'name', '大学英语六级')
    expect(store.dirty).toBe(true)

    store.updateCertificate(0, 'name', '')
    expect(store.dirty).toBe(false)
  })

  /**
   * 钉住 `save()` 发出的请求体确实带上三格、键名与后端契约一致、空行被剔除。
   * mock 手法照 `resume-switch-guard.spec.ts`：`vi.hoisted` 造桩 + `vi.mock('@/api/profiles')`。
   */
  it('save 发出的 payload 含 certificates / portfolio_links / city_preferences 且内容正确', async () => {
    const store = useResumeStore()
    const auth = useAuthStore()
    auth.user = {
      id: 'user-under-test',
      username: 'tester',
      email: null,
      phone: null,
      created_at: '2026-11-10T10:00:00+08:00',
    }

    store.addCertificate()
    store.updateCertificate(0, 'name', '大学英语六级')
    store.updateCertificate(0, 'issuer', '考试中心')
    store.updateCertificate(0, 'issued_on', '2024-06-15')
    // 第二条空证书：后端要求 name 至少 1 字，发空串会 422，必须被剔除。
    store.addCertificate()

    store.addPortfolioLink()
    store.updatePortfolioLink(0, 'label', '技术博客')
    store.updatePortfolioLink(0, 'url', 'https://example.com')

    store.addCityPreference()
    store.updateCityPreference(0, '上海')

    store.currentProfileId = 'profile-x'
    store.savedProfile = profileFixture('profile-x')

    let captured: Record<string, unknown> | null = null
    profilesApi.updateProfile.mockImplementation((_id: string, payload: unknown) => {
      captured = payload as Record<string, unknown>
      return Promise.resolve(profileFixture('profile-x'))
    })

    await store.save()

    expect(captured).not.toBeNull()
    expect(Object.keys(captured!)).toEqual(
      expect.arrayContaining(['certificates', 'portfolio_links', 'city_preferences']),
    )
    // 空证书行已剔除，只留下一条有效证书；issuer 非空、issued_on 透传给后端。
    expect(captured!.certificates).toEqual([
      { name: '大学英语六级', issuer: '考试中心', issued_on: '2024-06-15' },
    ])
    expect(captured!.portfolio_links).toEqual([
      { label: '技术博客', url: 'https://example.com' },
    ])
    expect(captured!.city_preferences).toEqual([{ city: '上海' }])
  })

  /**
   * 钉住三格与技能 8 条同口径的上限：到 `MULTIVALUE_LIMIT.max`（5）就加不进去，
   * 避免用户删到超限才发现被后端 422。
   */
  it('证书超过 5 条时加不进去（MULTIVALUE_LIMIT.max）', () => {
    const store = useResumeStore()
    for (let index = 0; index < 7; index += 1) store.addCertificate()
    expect(store.draft.certificates.length).toBe(MULTIVALUE_LIMIT.max)
    expect(store.canAddCertificate).toBe(false)
  })
})

/** 一份形状完整的档案：`save()` 之后会 `applyDraft(draftFromProfile(profile))`，缺字段就当场炸。 */
function profileFixture(id: string): ProfileRead {
  return {
    id,
    user_id: 'user-under-test',
    title: '我的简历',
    is_default: true,
    deleted_at: null,
    paper_size: 'A4',
    theme: DEFAULT_THEME,
    layout_config: { version: 1, sections: [...DEFAULT_LAYOUT_SECTIONS] },
    full_name: '张三',
    email: null,
    grade: '2027',
    contact: '13800000000',
    target_role: null,
    created_at: '2026-11-10T10:00:00+08:00',
    updated_at: '2026-11-10T10:00:00+08:00',
    skills: [],
    experiences: [],
    educations: [],
    certificates: [],
    portfolio_links: [],
    city_preferences: [],
  }
}
