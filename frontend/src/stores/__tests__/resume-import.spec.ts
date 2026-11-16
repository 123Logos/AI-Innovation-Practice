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
 * **第 30 轮（`ADR-033`）之后多了一层**：导入是「上传 → 轮询 → 回填」，
 * 所以 `@/api/resumes`（上传）与 `@/api/tasks`（轮询）都被 mock。上面三条约定一条没变，
 * 变的只是「结果从哪来」：`importResumePdf` 只回 `task_id`，结果在 `getTask` 的终态里。
 * `TASK_POLL_INTERVAL_MS` 也一并 mock 成 0：轮询间隔在测试里没有意义，
 * 留着 1500ms 只会让每个用例白等一秒半。
 */

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, clearDeepSeekKey, setDeepSeekKey } from '@/api/client'
import type { PdfImportResult, ProfileUpsertRequest, TaskStatus } from '@/api/types'
import { TaskFailedError } from '@/composables/usePollTask'
import { useResumeStore } from '@/stores/resume'

const importResumePdf = vi.hoisted(() => vi.fn())
const getTask = vi.hoisted(() => vi.fn())
const saveMyProfile = vi.hoisted(() => vi.fn())

vi.mock('@/api/resumes', () => ({ importResumePdf }))
vi.mock('@/api/profiles', () => ({ saveMyProfile }))
vi.mock('@/api/tasks', () => ({
  getTask,
  downloadTaskFile: vi.fn(),
  TASK_POLL_INTERVAL_MS: 0,
  TASK_POLL_BUDGET_MS: 60_000,
}))

/** 上传的 202 响应（`ADR-033` §2.6）：只有四格，没有 `profile`。 */
function queuedTask(taskId = 'task0000000000000000000000000000'): Record<string, string> {
  return {
    task_id: taskId,
    kind: 'import_pdf',
    state: 'pending',
    created_at: '2026-11-16T12:00:00+08:00',
  }
}

/** 轮询到 `complete`：结果在 `result` 里，形状就是旧的 `PdfImportResult`。 */
function completedTask(result: PdfImportResult): TaskStatus {
  return {
    task_id: 'task0000000000000000000000000000',
    kind: 'import_pdf',
    state: 'complete',
    stage: null,
    progress: 100,
    created_at: '2026-11-16T12:00:00+08:00',
    finished_at: '2026-11-16T12:00:05+08:00',
    result,
    error: null,
  }
}

/** 轮询到 `failed`：**HTTP 200**，失败在 `error` 里（这是新契约最容易写错的一格）。 */
function failedTask(code: string, message: string): TaskStatus {
  return {
    task_id: 'task0000000000000000000000000000',
    kind: 'import_pdf',
    state: 'failed',
    stage: 'extracting',
    progress: 35,
    created_at: '2026-11-16T12:00:00+08:00',
    finished_at: '2026-11-16T12:00:03+08:00',
    result: null,
    error: { code, message, retryable: false },
  }
}

/** 一次成功的导入：上传回 202、轮询到 complete 且带着这份结果。 */
function stubImport(result: PdfImportResult): void {
  importResumePdf.mockResolvedValue(queuedTask())
  getTask.mockResolvedValue(completedTask(result))
}

/** 一次失败的导入：上传成功、任务失败。 */
function stubImportFailure(code: string, message: string): void {
  importResumePdf.mockResolvedValue(queuedTask())
  getTask.mockResolvedValue(failedTask(code, message))
}

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
      source: 'text_layer',
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
  importResumePdf.mockReset()
  getTask.mockReset()
  saveMyProfile.mockReset()
  stubImport(resultFixture())
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
    let resolveUpload: ((value: Record<string, string>) => void) | undefined
    importResumePdf.mockImplementation(
      () => new Promise<Record<string, string>>((resolve) => (resolveUpload = resolve)),
    )
    getTask.mockResolvedValue(completedTask(resultFixture()))

    const pending = store.importPdf(new File(['%PDF-1.4'], 'resume.pdf'))
    expect(store.importing).toBe(true)
    resolveUpload?.(queuedTask())
    await pending

    expect(store.importing).toBe(false)
  })

  it('来历留在 store 里供横幅显示（读了 3/4 页 + 丢弃计数）', async () => {
    const store = seedDraft()
    stubImport(
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
    stubImport(resultFixture(profileFixture({ skills: [{ name: 'python' }, { name: 'SQL' }] })))

    await store.importPdf(new File(['%PDF-1.4'], 'resume.pdf'))

    // 判定键与写侧同源（NFKC + casefold + 去空白），所以 `python` 认得出原来那行 `Python`。
    expect(store.draft.skills[0]).toMatchObject({ name: 'python', source: 'assessment', last_used_on: '2025-03' })
    // 新出现的那条没有任何来历可继承，落基准档。
    expect(store.draft.skills[1]).toMatchObject({ name: 'SQL', source: 'self_reported', last_used_on: '' })
  })

  it('导入失败时表单一个字段都不动，横幅也不出现', async () => {
    const store = seedDraft()
    // 失败从**任务终态**来（HTTP 200 + `state=failed`），不是 422 响应体。
    stubImportFailure('PDF_INVALID', '这份 PDF 里读不出文字。')
    const before = JSON.stringify(store.draft)

    const failure = await store.importPdf(new File(['x'], 'scan.pdf')).catch((e) => e)
    expect(failure).toBeInstanceOf(TaskFailedError)
    expect(failure.code).toBe('PDF_INVALID')

    expect(JSON.stringify(store.draft)).toBe(before)
    expect(store.importing).toBe(false)
    expect(store.importedMeta).toBeNull()
  })

  it('第二次导入失败时，上一次那份账不留在场', async () => {
    const store = seedDraft()
    await store.importPdf(new File(['%PDF-1.4'], 'ok.pdf'))
    expect(store.importedMeta).not.toBeNull()
    stubImportFailure('AI_SERVICE_UNAVAILABLE', 'AI 服务当前不可用。')

    const failure = await store.importPdf(new File(['%PDF-1.4'], 'second.pdf')).catch((e) => e)
    expect(failure).toBeInstanceOf(TaskFailedError)

    // 新一次的请求还没成功，横幅说的就必须是「这一次」——上一次的成功记录不该继续挂着。
    expect(store.importedMeta).toBeNull()
  })
})

describe('OCR 与文本层两条路径表现一致（`ADR-031` §5 第 11 条）', () => {
  it('只差 `meta.source` 的两份响应，回填结果、dirty、请求次数逐字相同', async () => {
    const store = seedDraft()
    const profile = profileFixture()

    stubImport(resultFixture(profile, { source: 'text_layer' }))
    await store.importPdf(new File(['%PDF-1.4'], 'a.pdf'))
    const fromTextLayer = JSON.stringify({ draft: store.draft, dirty: store.dirty })
    const textLayerCalls = importResumePdf.mock.calls.length

    store.reset()
    seedDraft()
    stubImport(resultFixture(profile, { source: 'ocr' }))
    await store.importPdf(new File(['%PDF-1.4'], 'b.pdf'))
    const fromOcr = JSON.stringify({ draft: store.draft, dirty: store.dirty })

    // 前端不因为「这是认出来的」就演第二套：该填的填、该脏的脏、该存的一律不存。
    expect(fromOcr).toBe(fromTextLayer)
    expect(importResumePdf).toHaveBeenCalledTimes(textLayerCalls + 1)
    expect(saveMyProfile).not.toHaveBeenCalled()
    // 但来源这个事实留在数据里，日志与排查找得到它。
    expect(store.importedMeta?.source).toBe('ocr')
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
