/**
 * PDF 导入的 store 单测（第 28 轮，`ADR-031` 2.1 / 2.4）。
 *
 * 钉的是这条路径的三个「只有前端能证明」的约定：
 *
 * 1. **导入即改写草稿，但绝不落库**。服务端本来就不写库（纯计算端点），
 *    所以「不保存」这件事在这一侧只剩一个证明点：store 自己有没有偷偷 PUT。
 *    一旦有人顺手接上 `save()`，AI 的产出就会在用户没看过的情况下变成库里的正式档案，
 *    而 `ADR-031` 2.1 的全部理由就是给这件事留一个回退点。
 * 2. **只覆盖 AI 真读过的那几格**。响应里的 `certificates` / `portfolio_links` /
 *    `city_preferences` 是空数组（后端写侧的默认值），照单全收等于
 *    「导一份 PDF 就把用户的证书清空」；`email` 不在导入契约里，同理。
 * 3. **技能的 `source` 按名字继承**。`assessment` 那一档是标准化测评写的
 *    （`ADR-027` §2.6），导入若把它降回自评，用户点一次保存就把 B9 的证据抹掉了。
 *
 * `@/api/resumes` 与 `@/api/profiles` 整体被 mock：本文件验的是 store 的编排，不是 axios。
 */

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, clearDeepSeekKey, setDeepSeekKey } from '@/api/client'
import type { PdfImportResult, ProfileUpsertRequest } from '@/api/types'
import { useResumeStore } from '@/stores/resume'

const importResumePdf = vi.hoisted(() => vi.fn())
const saveMyProfile = vi.hoisted(() => vi.fn())

vi.mock('@/api/resumes', () => ({ importResumePdf }))
vi.mock('@/api/profiles', () => ({ saveMyProfile }))

const TEST_KEY = 'sk-import-test'

function profileFixture(overrides: Partial<ProfileUpsertRequest> = {}): ProfileUpsertRequest {
  return {
    full_name: '张三',
    contact: '13800138000',
    target_role: '算法工程师',
    skills: [{ name: 'Python' }, { name: 'PyTorch' }],
    experiences: [
      {
        title: '某公司 算法实习',
        start_date: '2024-06-01',
        end_date: '2024-09-01',
        description: '参与推荐模型离线评估。',
        responsibility: '负责特征工程。',
      },
    ],
    educations: [
      {
        school: '中国计量大学',
        major: '人工智能',
        degree: '本科',
        edu_start: '2021-09-01',
        edu_end: '2025-06-01',
      },
    ],
    // 后端 `ProfileUpsertRequest` 的默认值：响应里一定带着三张空表。
    certificates: [],
    portfolio_links: [],
    city_preferences: [],
    ...overrides,
  }
}

function resultFixture(
  profile: ProfileUpsertRequest = profileFixture(),
  overrides: Partial<PdfImportResult['meta']> = {},
): PdfImportResult {
  return {
    profile,
    meta: {
      pages_total: 4,
      pages_used: 3,
      text_chars: 1200,
      text_truncated: false,
      prompt_version: 'v1',
      dropped: {
        skills: 0,
        experiences: 0,
        educations: 0,
        contact_discarded: false,
        text_clamped: false,
      },
      ...overrides,
    },
  }
}

/** 铺一份「与服务端一致」的草稿：`dirty` 从干净状态开始，才测得出导入有没有置脏。 */
function seedDraft() {
  const store = useResumeStore()
  store.draft.full_name = ''
  store.draft.contact = ''
  store.draft.email = 'zhangsan@example.com'
  store.draft.target_role = ''
  store.draft.skills = []
  store.draft.experiences = []
  store.draft.educations = []
  store.draft.certificates = [{ name: 'CET-6', issuer: '', issued_on: '' }]
  store.draft.portfolio_links = [{ label: '博客', url: 'https://example.com' }]
  store.draft.city_preferences = [{ city: '杭州' }]
  return store
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  localStorage.clear()
  setDeepSeekKey(TEST_KEY)
  importResumePdf.mockReset().mockResolvedValue(resultFixture())
  saveMyProfile.mockReset()
})

describe('导入即改写草稿，但不落库', () => {
  it('成功后各格填上、dirty 由假变真，且一个保存请求都没发', async () => {
    // 故意不 seed：起点必须是「与空档案一致」的干净草稿，
    // 否则 `dirty` 一开始就是真，那条断言就只是恒绿。
    const store = useResumeStore()
    expect(store.dirty).toBe(false)

    await store.importPdf(new File(['%PDF-1.4'], 'resume.pdf'))

    expect(store.draft.full_name).toBe('张三')
    expect(store.draft.contact).toBe('13800138000')
    expect(store.draft.target_role).toBe('算法工程师')
    expect(store.draft.skills.map((skill) => skill.name)).toEqual(['Python', 'PyTorch'])
    expect(store.draft.experiences[0]?.title).toBe('某公司 算法实习')
    expect(store.draft.educations[0]?.school).toBe('中国计量大学')
    expect(store.dirty).toBe(true)
    // 这条是本文件存在的理由：导入之后库里必须还是空的，除非用户自己点保存。
    expect(saveMyProfile).not.toHaveBeenCalled()
  })

  it('教育经历按前端的月份格式落地（`<input type="month">` 不接受带日的串）', async () => {
    const store = seedDraft()

    await store.importPdf(new File(['%PDF-1.4'], 'resume.pdf'))

    const education = store.draft.educations[0]
    expect(education?.edu_start).toBe('2021-09')
    expect(education?.edu_end).toBe('2025-06')
    // 经历那侧相反：它的输入框是 `type="date"`，原样保留带日的串。
    expect(store.draft.experiences[0]?.start_date).toBe('2024-06-01')
    expect(store.draft.experiences[0]?.end_date).toBe('2024-09-01')
  })

  it('请求进行中 `importing` 为真、结束后为假（遮罩与按钮 disable 都读它）', async () => {
    const store = seedDraft()
    let resolveFirst: ((value: PdfImportResult) => void) | undefined
    importResumePdf.mockImplementation(
      () => new Promise<PdfImportResult>((resolve) => (resolveFirst = resolve)),
    )

    const pending = store.importPdf(new File(['%PDF-1.4'], 'resume.pdf'))
    expect(store.importing).toBe(true)
    resolveFirst?.(resultFixture())
    await pending

    expect(store.importing).toBe(false)
  })

  it('来历留在 store 里供横幅显示（读了 3/4 页 + 丢弃计数）', async () => {
    const store = seedDraft()
    importResumePdf.mockResolvedValue(
      resultFixture(profileFixture(), {
        dropped: { skills: 4, experiences: 0, educations: 1, contact_discarded: true, text_clamped: false },
      }),
    )

    await store.importPdf(new File(['%PDF-1.4'], 'resume.pdf'))

    expect(store.importedMeta).toMatchObject({
      pages_total: 4,
      pages_used: 3,
      prompt_version: 'v1',
      dropped: { skills: 4, educations: 1, contact_discarded: true },
    })
  })

  it('换账号 / 重新装载时导入横幅一起清掉（挂着上一份的账就是假话）', async () => {
    const store = seedDraft()
    await store.importPdf(new File(['%PDF-1.4'], 'resume.pdf'))
    expect(store.importedMeta).not.toBeNull()

    store.reset()

    expect(store.importedMeta).toBeNull()
  })
})

describe('只覆盖 AI 真读过的那几格', () => {
  it('邮箱、证书、作品链接、期望城市与排版配色一律原样保留', async () => {
    const store = seedDraft()
    store.draft.paper_size = 'Letter'
    store.draft.theme = 'navy'
    const before = JSON.stringify({
      email: store.draft.email,
      certificates: store.draft.certificates,
      portfolio_links: store.draft.portfolio_links,
      city_preferences: store.draft.city_preferences,
      paper_size: store.draft.paper_size,
      theme: store.draft.theme,
      layout_config: store.draft.layout_config,
    })

    await store.importPdf(new File(['%PDF-1.4'], 'resume.pdf'))

    // 响应里这三张表是空数组（`ProfileUpsertRequest` 的默认值），照单全收就会清空它们。
    expect(profileFixture().certificates).toEqual([])
    const after = JSON.stringify({
      email: store.draft.email,
      certificates: store.draft.certificates,
      portfolio_links: store.draft.portfolio_links,
      city_preferences: store.draft.city_preferences,
      paper_size: store.draft.paper_size,
      theme: store.draft.theme,
      layout_config: store.draft.layout_config,
    })
    expect(after).toBe(before)
  })

  it('同名技能继承原来的来源与最近使用（测评写下的证据不被导入抹掉）', async () => {
    const store = seedDraft()
    store.draft.skills = [
      { name: 'Python', source: 'assessment', last_used_on: '2025-03' },
      { name: 'Excel', source: 'teacher_confirmed', last_used_on: '' },
    ]
    importResumePdf.mockResolvedValue(
      resultFixture(profileFixture({ skills: [{ name: 'python' }, { name: 'SQL' }] })),
    )

    await store.importPdf(new File(['%PDF-1.4'], 'resume.pdf'))

    // 判定键与写侧同源（NFKC + casefold + 去空白），所以 `python` 认得出原来那行 `Python`。
    expect(store.draft.skills[0]).toMatchObject({ name: 'python', source: 'assessment', last_used_on: '2025-03' })
    // 新出现的那条没有任何来历可继承，落基准档。
    expect(store.draft.skills[1]).toMatchObject({ name: 'SQL', source: 'self_reported', last_used_on: '' })
  })

  it('导入失败时表单一个字段都不动，横幅也不出现', async () => {
    const store = seedDraft()
    importResumePdf.mockRejectedValue(new ApiError('这份 PDF 里读不出文字。', 422, 'PDF_INVALID'))
    const before = JSON.stringify(store.draft)

    await expect(store.importPdf(new File(['x'], 'scan.pdf'))).rejects.toBeInstanceOf(ApiError)

    expect(JSON.stringify(store.draft)).toBe(before)
    expect(store.importing).toBe(false)
    expect(store.importedMeta).toBeNull()
  })

  it('第二次导入失败时，上一次那份账不留在场', async () => {
    const store = seedDraft()
    await store.importPdf(new File(['%PDF-1.4'], 'ok.pdf'))
    expect(store.importedMeta).not.toBeNull()
    importResumePdf.mockRejectedValue(new ApiError('AI 服务当前不可用。', 422, 'AI_SERVICE_UNAVAILABLE'))

    await expect(store.importPdf(new File(['%PDF-1.4'], 'second.pdf'))).rejects.toBeInstanceOf(ApiError)

    // 新一次的请求还没成功，横幅说的就必须是「这一次」——上一次的成功记录不该继续挂着。
    expect(store.importedMeta).toBeNull()
  })
})

describe('没配 Key 时不发请求', () => {
  it('当场抛 AI_KEY_MISSING，且不产生任何一次上传', async () => {
    const store = seedDraft()
    clearDeepSeekKey()

    const error = await store.importPdf(new File(['%PDF-1.4'], 'resume.pdf')).catch((e) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('AI_KEY_MISSING')
    // 这条断言的实际意义：用户不该在传完 4 MB 之后才被告知没配 Key。
    expect(importResumePdf).not.toHaveBeenCalled()
    expect(store.importing).toBe(false)
  })
})
