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
 * 表单本身（`ResumeEditor`）与预览、辅助面板都被 stub：它们各自的用例在别的文件里，
 * 这一页不需要重复证明它们能渲染。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, type Pinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, setDeepSeekKey } from '@/api/client'
import type { PdfImportResult, ProfileUpsertRequest } from '@/api/types'
import { useDialogStore } from '@/stores/dialog'
import { useResumeStore } from '@/stores/resume'
import ResumeEditorView from '@/views/ResumeEditorView.vue'

const importResumePdf = vi.hoisted(() => vi.fn())

vi.mock('@/api/resumes', () => ({ importResumePdf }))
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
  importResumePdf.mockReset().mockResolvedValue(importedResult())
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
    let resolveImport: ((value: PdfImportResult) => void) | undefined
    importResumePdf.mockImplementation(() => new Promise<PdfImportResult>((resolve) => (resolveImport = resolve)))
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    expect(wrapper.find('[data-testid="import-mask"]').exists()).toBe(true)
    const button = wrapper.get('[data-testid="import-pdf-button"]')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toBe('正在解析 PDF…')

    resolveImport?.(importedResult())
    await flushPromises()

    expect(wrapper.find('[data-testid="import-mask"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="import-pdf-button"]').text()).toBe('导入 PDF 简历')
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
    importResumePdf.mockResolvedValue({
      profile: importedResult().profile,
      meta: {
        pages_total: 1,
        pages_used: 1,
        text_chars: 200,
        text_truncated: false,
        prompt_version: 'v1',
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

  it('扫描件失败：错误进 `role="alert"`，句子用的是后端那句能动手的话', async () => {
    markLoaded()
    importResumePdf.mockRejectedValue(
      new ApiError('这份 PDF 里读不出文字（可能是扫描件或加密文件）。', 422, 'PDF_INVALID'),
    )
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    const feedback = wrapper.get('[data-testid="import-feedback"]')
    expect(feedback.attributes('role')).toBe('alert')
    expect(feedback.text()).toContain('读不出文字')
    // 不是 Key 的问题，就不该给「点此配置 AI 密钥」。
    expect(wrapper.find('[data-testid="ai-key-hint"]').exists()).toBe(false)
  })

  it('没配 Key 失败：错误句子下面那一行「点此配置 AI 密钥」必须出现', async () => {
    markLoaded()
    importResumePdf.mockRejectedValue(
      new ApiError('请先在「AI 设置」中配置 DeepSeek API Key。', 422, 'AI_KEY_MISSING'),
    )
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    expect(wrapper.get('[data-testid="import-feedback"]').attributes('role')).toBe('alert')
    expect(wrapper.find('[data-testid="ai-key-hint"]').exists()).toBe(true)
  })

  it('AI 超时失败：说清「表单没被改动」，并且不给配 Key 的链接', async () => {
    markLoaded()
    importResumePdf.mockRejectedValue(
      new ApiError('AI 服务当前不可用，本次请求未被保存，您的资料不受影响。请稍后重试。', 422, 'AI_SERVICE_UNAVAILABLE'),
    )
    const wrapper = mountView()

    pickFile(wrapper, FILE)
    await flushPromises()

    expect(wrapper.get('[data-testid="import-feedback"]').text()).toContain('本次没有改动表单')
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
