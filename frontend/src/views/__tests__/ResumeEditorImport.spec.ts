/**
 * 简历编辑页的「导入 PDF 简历」用例（第 28 轮，`ADR-031` 的前端半边）。
 *
 * 这一页只管四件事，用例就只钉这四件：
 *
 * 1. **入口的形状**：一个按钮 + 一个隐藏的 `<input type="file" accept="application/pdf">`。
 *    按钮不是 `<label>`：`label` 会跟着文件框一起被 Tab 到，两个焦点停在同一件事上。
 * 2. **dirty 先拦一次确认**（老板指令原文「导入将覆盖当前未保存的修改，是否继续？」）。
 *    弹窗由 `ConfirmDialog` 全站唯一那一份渲染，这里不在 `<AppLayout>` 里挂它，
 *    所以用例直接读 `dialog.pending` 再 `settle()`——测的是「这一页有没有把请求
 *    挡在确认之后」，不是弹窗自己（那是 `ConfirmDialog.spec.ts` 的活）。
 * 3. **解析期间的样子**：遮罩出现、按钮禁用、按钮文字换成「正在解析 PDF…」。
 * 4. **结果上屏**：成功给「仍需点保存」那句话 + 截断/丢弃的账；失败进 `role="alert"`，
 *    其中只有 `AI_KEY_MISSING` 那条带「点此配置 AI 密钥」的链接。
 *
 * **第 30 轮（`ADR-033`）之后导入变成「上传 → 轮询 → 回填」**：上传只回 202 + `task_id`，
 * 结果与失败原因都在 `GET /tasks/{id}` 的终态里。所以本文件 mock 了 `@/api/tasks`，
 * 并按新契约补了一条「进度条读服务端的 `stage` / `progress`」——
 * 那是老板点名的「上传后显示正在提取文字，可能有进度条」那句话的机械判据。
 *
 * 表单本身（`ResumeEditor`）与预览、辅助面板都被 stub：它们各自的用例在别的文件里，
 * 这一页不需要重复证明它们能渲染。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, type Pinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setDeepSeekKey } from '@/api/client'
import type { PdfImportResult, ProfileUpsertRequest, TaskStatus } from '@/api/types'
import { useDialogStore } from '@/stores/dialog'
import { useResumeStore } from '@/stores/resume'
import ResumeEditorView from '@/views/ResumeEditorView.vue'

const importResumePdf = vi.hoisted(() => vi.fn())
const getTask = vi.hoisted(() => vi.fn())

vi.mock('@/api/resumes', () => ({ importResumePdf }))
vi.mock('@/api/tasks', () => ({
  getTask,
  downloadTaskFile: vi.fn(),
  TASK_POLL_INTERVAL_MS: 0,
  TASK_POLL_BUDGET_MS: 60_000,
}))
vi.mock('@/api/profiles', () => ({
  listProfiles: vi.fn().mockResolvedValue([]),
  getMyProfile: vi.fn().mockResolvedValue(null),
  saveMyProfile: vi.fn(),
}))

const FILE = new File(['%PDF-1.4 fake'], 'resume.pdf', { type: 'application/pdf' })

function importedResult(): PdfImportResult {
  const profile = {
    full_name: '张三',
    contact: '13800138000',
    target_role: '算法工程师',
    skills: [{ name: 'Python' }],
    experiences: [],
    educations: [],
    certificates: [],
    portfolio_links: [],
    city_preferences: [],
  } satisfies ProfileUpsertRequest
  return {
    profile,
    meta: {
      pages_total: 4,
      pages_used: 3,
      text_chars: 900,
      text_truncated: false,
      prompt_version: 'v1',
      source: 'text_layer',
      dropped: {
        skills: 4,
        experiences: 0,
        educations: 1,
        contact_discarded: true,
        text_clamped: false,
      },
    },
  }
}

/** 上传的 202（`ADR-033` §2.6）：四格，没有 `profile`。 */
const QUEUED = {
  task_id: 'task0000000000000000000000000000',
  kind: 'import_pdf' as const,
  state: 'pending' as const,
  created_at: '2026-11-16T12:00:00+08:00',
}

/** 轮询到 `complete`（结果就是旧的 `PdfImportResult`）。 */
function completedTask(result: PdfImportResult): TaskStatus {
  return {
    ...QUEUED,
    state: 'complete',
    stage: null,
    progress: 100,
    finished_at: '2026-11-16T12:00:05+08:00',
    result,
    error: null,
  }
}

/** 轮询到 `failed`：HTTP 200，失败在 `error` 里。 */
function failedTask(code: string, message: string): TaskStatus {
  return {
    ...QUEUED,
    state: 'failed',
    stage: 'extracting',
    progress: 35,
    finished_at: '2026-11-16T12:00:03+08:00',
    result: null,
    error: { code, message, retryable: false },
  }
}

/** 一次成功导入（上传 202 + 轮询 complete）。 */
function stubImport(result: PdfImportResult): void {
  importResumePdf.mockResolvedValue(QUEUED)
  getTask.mockResolvedValue(completedTask(result))
}

/** 一次失败的导入：**上传成功**、任务失败（这是新契约里最容易写错的组合）。 */
function stubFailure(code: string, message: string): void {
  importResumePdf.mockResolvedValue(QUEUED)
  getTask.mockResolvedValue(failedTask(code, message))
}

function mountView() {
  return mount(ResumeEditorView, {
    global: {
      plugins: [pinia],
      stubs: { ResumeEditor: true, ResumePreview: true, StylePanel: true, SkeletonList: true },
    },
  })
}

/** 让 `onMounted` 走「已加载过」那条快路径：本文件不测首次装载。 */
function markLoaded() {
  const store = useResumeStore()
  store.loadedFromServer = true
  return store
}

/** jsdom 不给 `<input type=file>` 赋值 `files`，只能把这一格定义上去（Vitest 通行做法）。 */
function pickFile(wrapper: ReturnType<typeof mountView>, file: File | null): void {
  const input = wrapper.get('[data-testid="import-pdf-input"]').element as HTMLInputElement
  Object.defineProperty(input, 'files', { value: file ? [file] : [], configurable: true })
  wrapper.get('[data-testid="import-pdf-input"]').trigger('change')
}

/**
 * 一个文件级 pinia：`markLoaded()` 要在 `mount()` **之前**就能拿到 store
 * （挂载之后再改 `loadedFromServer` 已经晚了一步 —— `onMounted` 早跑完了）。
 */
let pinia: Pinia

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
  setDeepSeekKey('sk-view-test')
  importResumePdf.mockReset()
  getTask.mockReset()
  stubImport(importedResult())
})

describe('入口的形状', () => {
  it('一个按钮 + 一个只收 PDF 的隐藏文件框', () => {
    markLoaded()
    const wrapper = mountView()

    const button = wrapper.get('[data-testid="import-pdf-button"]')
    expect(button.text()).toBe('导入 PDF 简历')

    const input = wrapper.get('[data-testid="import-pdf-input"]').element as HTMLInputElement
    expect(input.getAttribute('accept')).toBe('application/pdf')
    expect(input.type).toBe('file')
  })

  it('点按钮打开的是那个文件框，而不是直接发请求', () => {
    markLoaded()
    const wrapper = mountView()
    const clickSpy = vi
      .spyOn(HTMLInputElement.prototype, 'click')
      .mockImplementation(function (this: HTMLInputElement) {
        // 只拦文件选择框那一下；其它 input 的 click 行为不动。
        if (this.getAttribute('data-testid') !== 'import-pdf-input') this.dispatchEvent(new MouseEvent('click'))
      })

    wrapper.get('[data-testid="import-pdf-button"]').trigger('click')

    expect(clickSpy).toHaveBeenCalledTimes(1)
    const clicked = clickSpy.mock.instances[0] as HTMLInputElement | undefined
    expect(clicked?.getAttribute('data-testid')).toBe('import-pdf-input')
    expect(importResumePdf).not.toHaveBeenCalled()
    clickSpy.mockRestore()
  })
})

describe('dirty 先拦一次确认', () => {
  it('草稿是干净的：不弹窗，直接上传', async () => {
    const store = markLoaded()
    expect(store.dirty).toBe(false)
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    expect(useDialogStore().pending).toBeNull()
    expect(importResumePdf).toHaveBeenCalledTimes(1)
  })

  it('有未保存的修改：先弹「导入将覆盖当前未保存的修改」，确认之后才上传', async () => {
    const store = markLoaded()
    // 必须走 store 的方法：`dirty` 只在 `markContentChanged()` 的矩阵里判定，
    // 直接改 `draft` 的字段不会置脏——那样这里测的就不是 dirty 那条分支了。
    store.updateProfileField('full_name', '改过但没保存')
    const dialog = useDialogStore()
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    expect(dialog.pending).not.toBeNull()
    expect(dialog.pending?.title).toContain('导入将覆盖当前未保存的修改')
    // 请求必须还堵在确认后面——这一步是这条用例的全部意义。
    expect(importResumePdf).not.toHaveBeenCalled()

    dialog.settle(true)
    await flushPromises()

    expect(importResumePdf).toHaveBeenCalledTimes(1)
  })

  it('取消就停住：不发请求、不动表单、也不留提示', async () => {
    const store = markLoaded()
    store.updateProfileField('full_name', '改过但没保存')
    const before = JSON.stringify(store.draft)
    const dialog = useDialogStore()
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()
    dialog.settle(false)
    await flushPromises()

    expect(importResumePdf).not.toHaveBeenCalled()
    expect(JSON.stringify(store.draft)).toBe(before)
    expect(wrapper.find('[data-testid="import-feedback"]').exists()).toBe(false)
  })

  it('没选文件（对话框被取消）时什么都不做', async () => {
    markLoaded()
    const wrapper = mountView()

    pickFile(wrapper, null)
    await flushPromises()

    expect(importResumePdf).not.toHaveBeenCalled()
    expect(useDialogStore().pending).toBeNull()
  })
})

describe('解析期间的样子', () => {
  it('遮罩出现、按钮禁用、按钮文字换成「正在解析 PDF…」', async () => {
    markLoaded()
    let resolveImport: ((value: typeof QUEUED) => void) | undefined
    importResumePdf.mockImplementation(() => new Promise<typeof QUEUED>((resolve) => (resolveImport = resolve)))
    getTask.mockResolvedValue(completedTask(importedResult()))
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    expect(wrapper.find('[data-testid="import-mask"]').exists()).toBe(true)
    const button = wrapper.get('[data-testid="import-pdf-button"]')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toBe('正在解析 PDF…')

    resolveImport?.(QUEUED)
    await flushPromises()

    expect(wrapper.find('[data-testid="import-mask"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="import-pdf-button"]').text()).toBe('导入 PDF 简历')
  })

  it('进度条读服务端的 `stage` / `progress`（「正在提取文字…」不是前端编的）', async () => {
    // 第二次轮询挂在半空：这时屏幕上必须已经是服务端给的那一档。
    let release: ((value: TaskStatus) => void) | undefined
    getTask
      .mockResolvedValueOnce({
        ...QUEUED,
        state: 'running',
        stage: 'extracting',
        progress: 35,
      })
      .mockImplementationOnce(() => new Promise<TaskStatus>((resolve) => (release = resolve)))
    markLoaded()
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    const bar = wrapper.get('[data-testid="import-progress"]')
    expect(bar.attributes('role')).toBe('progressbar')
    expect(bar.attributes('aria-valuenow')).toBe('35')
    expect(wrapper.get('.import-mask__text').text()).toBe('正在提取文字…')

    release?.({ ...completedTask(importedResult()) })
    await flushPromises()
    expect(wrapper.find('[data-testid="import-mask"]').exists()).toBe(false)
  })
})

describe('结果上屏', () => {
  it('成功：表单填上、给一句「仍需点保存」，并把读了哪几页与丢了几条说出来', async () => {
    const store = markLoaded()
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    expect(store.draft.full_name).toBe('张三')
    expect(store.dirty).toBe(true)
    const feedback = wrapper.get('[data-testid="import-feedback"]')
    expect(feedback.text()).toContain('保存修改')
    expect(feedback.attributes('role')).toBe('status')

    const notes = wrapper.get('[data-testid="import-notes"]').text()
    expect(notes).toContain('共 4 页')
    expect(notes).toContain('只读了前 3 页')
    expect(notes).toContain('4 条技能')
    expect(notes).toContain('1 段教育经历')
    expect(notes).toContain('手机号')
    // 技术构成不进界面：字符数与 Prompt 版本号只在数据里，不念给用户听。
    expect(notes).not.toContain('v1')
    expect(notes).not.toContain('900')
  })

  it('干净导入（没有任何截断与丢弃）时不画那条账', async () => {
    markLoaded()
    stubImport({
      profile: importedResult().profile,
      meta: {
        pages_total: 1,
        pages_used: 1,
        text_chars: 200,
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
      },
    })
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    expect(wrapper.find('[data-testid="import-notes"]').exists()).toBe(false)
  })

  it('OCR 来源的成功响应：界面与文本层来源逐字一致（`ADR-031` §5 第 11 条）', async () => {
    // 两份响应只差 `meta.source`。前端**不**因为「这是认回来的」就换提示、换颜色、
    // 或者多画一个说明——那会把「同一条路径的两种来源」变成用户面前的两件事。
    const ocrResult = importedResult()
    ocrResult.meta.source = 'ocr'
    markLoaded()
    stubImport(ocrResult)
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    expect(wrapper.get('[data-testid="import-feedback"]').text()).toContain('保存修改')
    expect(wrapper.get('[data-testid="import-notes"]').text()).toContain('只读了前 3 页')
    // 来源这个事实留在数据里（排查找得到），但不作为第二套界面文案出现。
    expect(useResumeStore().importedMeta?.source).toBe('ocr')
    expect(wrapper.get('[data-testid="import-notes"]').text()).not.toContain('ocr')
  })

  it('扫描件失败：错误进 `role="alert"`，句子用的是后端那句能动手的话', async () => {
    markLoaded()
    stubFailure('PDF_INVALID', '这份文件没识别到文字，请上传含文字层的 PDF，或在下面手动填写档案。')
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    const feedback = wrapper.get('[data-testid="import-feedback"]')
    expect(feedback.attributes('role')).toBe('alert')
    expect(feedback.text()).toContain('没识别到文字')
    // 不是 Key 的问题，就不该给「点此配置 AI 密钥」。
    expect(wrapper.find('[data-testid="ai-key-hint"]').exists()).toBe(false)
  })

  it('没配 Key 失败：错误句子下面那一行「点此配置 AI 密钥」必须出现', async () => {
    markLoaded()
    stubFailure('AI_KEY_MISSING', '请先在「AI 设置」中配置 DeepSeek API Key。')
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    expect(wrapper.get('[data-testid="import-feedback"]').attributes('role')).toBe('alert')
    expect(wrapper.find('[data-testid="ai-key-hint"]').exists()).toBe(true)
  })

  it('AI 超时失败：说清「表单没被改动」，并且不给配 Key 的链接', async () => {
    markLoaded()
    stubFailure('AI_SERVICE_UNAVAILABLE', 'AI 服务当前不可用，本次请求未被保存，您的资料不受影响。请稍后重试。')
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    // 这句话来自后端 `ErrorMessage.AI_UNAVAILABLE`（受信目录），前端不再叠一句同义话。
    expect(wrapper.get('[data-testid="import-feedback"]').text()).toContain('资料不受影响')
    expect(wrapper.find('[data-testid="ai-key-hint"]').exists()).toBe(false)
  })

  it('换一次文件再导：上一次的结果文字先清掉（不留两条互相矛盾的提示）', async () => {
    markLoaded()
    const wrapper = mountView()
    pickFile(wrapper, FILE)
    await flushPromises()
    expect(wrapper.get('[data-testid="import-feedback"]').text()).toContain('保存修改')

    wrapper.get('[data-testid="import-pdf-button"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="import-feedback"]').exists()).toBe(false)
  })
})
