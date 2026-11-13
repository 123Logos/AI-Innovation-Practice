/**
 * `ResumePreview.vue` 的单测（Phase 2 需求池 §一「纸面内容编辑回写机制」的验收尾巴）。
 *
 * 这个组件是用户盯得最久的一个，却一个用例都没有：池子第 3 项的验收
 * 「纸面上改一处文字 → 刷新页面后仍在；改动与编辑区表单双向一致」到今天只有手动复现步骤。
 * 本文件把其中能被自动化的部分钉住，五个 describe 各自对应一条用户可见行为：
 * 保存按钮的两态、失焦回写与双向一致、点保存恰好一次 PUT、保存失败不静默、重新挂载后还在。
 *
 * 三件桩与它们各自的边界：
 *
 * 1. **axios 的 `defaults.adapter` 换成一只内存小服务器**（同 `JobDetailView.spec.ts` 的做法，
 *    统一信封 `{ data, meta }`）。它不是「返回固定 JSON 的 mock」，而是会**接住 PUT 的内容**、
 *    并在下一次 GET 时原样吐回来——否则最后那条「重新挂载后还在」测的只是 Pinia 自己的记忆。
 *    真实 DeepSeek / 真实 MySQL 都不在本文件范围内（ADR-009/010 与纸面编辑无关）。
 * 2. **`ResizeObserver` 桩**：`ResumePreview` 用它量纸面高度算页数。jsdom 没有这个类，
 *    而组件在 `onMounted` 与 `watch(paperRef)` 里各 new 一次，不桩就直接抛。
 *    桩成「什么都不量」是刻意的：本文件断言的全是文字与按钮，页数归 `StylePanel` 那侧管。
 * 3. **`innerText` 垫片**：jsdom 至今没实现 `innerText`（它需要布局引擎），
 *    而纸面失焦回写读的就是 `element.innerText`。垫片按「读 textContent / 写 textContent」
 *    实现，对本文件的单句文字与浏览器语义一致；**真实浏览器的 Selection、
 *    粘贴换行、`white-space: pre-wrap` 下的 innerText 差异不在本文件的覆盖面里**，
 *    这条已写进交付说明的「未解决问题」。
 *
 * 纸面编辑必须靠「改 DOM 再派发 blur」来测，不能在测试里直接调 `store.updateExperience()`：
 * 后者只证明 store 能用，而这条切片坏掉的方式恰恰是「DOM → store」那一段断了。
 */

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import {
  AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'

import { authHttp, http } from '@/api/client'
import type { ApiErrorBody } from '@/api/types'
import ResumeEditor from '@/components/ResumeEditor.vue'
import ResumePreview from '@/components/ResumePreview.vue'
import { useAuthStore } from '@/stores/auth'
import { useResumeStore, PX_PER_MM } from '@/stores/resume'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

const PROFILE_ID = 'profile-under-test'
const OWNER_ID = 'user-under-test'

/** 纸面上原本写着的那句话。 */
const ORIGINAL_DESCRIPTION = '用 PyTorch 训练了一个推荐模型，离线评估靠人工核对。'
/** 用户在纸面上敲进去替换它的那句话——本文件所有断言都围着这一句转。 */
const NEW_DESCRIPTION = '把离线评估从人工核对改成自动化脚本，评估周期从两天缩到二十分钟。'

/* ------------------------------ jsdom 缺的两样 ------------------------------ */

/** 见文件头第 2 条：组件只关心「能 new、能 observe、能 disconnect」，量到的尺寸一律是 0。 */
class StubResizeObserver {
  constructor(_callback: unknown) {}

  observe(): void {}

  unobserve(): void {}

  disconnect(): void {}
}

;(globalThis as unknown as Record<string, unknown>).ResizeObserver = StubResizeObserver

/** 见文件头第 3 条：`innerText` ↔ `textContent` 的代理，只在本文件的 document 上生效。 */
Object.defineProperty(HTMLElement.prototype, 'innerText', {
  configurable: true,
  get(): string {
    return this.textContent ?? ''
  },
  set(value: string) {
    this.textContent = value
  },
})

/* ------------------------------ 内存小服务器 ------------------------------ */

interface Call {
  method: string
  url: string
  /** axios 在交给 adapter 之前已经序列化过，这里看到的就是真实出线的字节。 */
  body: string | null
}

let calls: Call[] = []
let savedAdapters: Array<[AxiosInstance, AxiosInstance['defaults']['adapter']]> = []
/** 下一次 `PUT /profiles/{id}` 要不要失败，以及失败成什么状态码（0 = 不失败）。 */
let failSaveWith = 0
let pinia: Pinia
const mounted: VueWrapper[] = []

/**
 * 「库里那一份」：`PUT` 改它、`GET` 读它。
 * 形状照后端 `ProfileRead`（`src/api/types.ts`），字段值必须过得了 `validateForSave()`，
 * 否则点了保存会被前端自检拦下来，用例其实在验一条根本没发出去的网络请求。
 */
let serverProfile: Record<string, unknown>

function freshServerProfile(): Record<string, unknown> {
  return {
    id: PROFILE_ID,
    user_id: OWNER_ID,
    title: '我的简历',
    is_default: true,
    deleted_at: null,
    paper_size: 'A4',
    theme: 'forest',
    layout_config: {
      version: 1,
      sections: [
        { id: 'educations', title: '教育经历' },
        { id: 'skills', title: '技能' },
        { id: 'experiences', title: '项目经历' },
      ],
    },
    full_name: '张三',
    email: 'zhangsan@example.com',
    grade: '',
    contact: '13800000000',
    created_at: '2026-11-01T10:00:00+08:00',
    updated_at: '2026-11-01T10:00:00+08:00',
    skills: [{ id: 'skill-1', name: 'Python' }],
    experiences: [
      {
        id: 'exp-1',
        title: '校园推荐系统',
        start_date: '2024-03-01',
        end_date: '2024-08-31',
        description: ORIGINAL_DESCRIPTION,
        responsibility: '负责数据清洗与模型训练。',
      },
    ],
    educations: [
      {
        id: 'edu-1',
        school: '示例大学',
        major: '人工智能',
        degree: '本科',
        edu_start: '2021-09-01',
        edu_end: '2025-06-01',
      },
    ],
  }
}

/** 后端 `ProfileSummary`（列表行）：`save()` 结尾会刷一次列表。 */
function summaryRow(): Record<string, unknown> {
  return {
    id: PROFILE_ID,
    title: serverProfile.title,
    is_default: true,
    full_name: serverProfile.full_name,
    created_at: serverProfile.created_at,
    updated_at: serverProfile.updated_at,
    deleted_at: null,
  }
}

/**
 * `PUT` 的落库：整份替换（与后端 `update_profile` 的子表全量替换一致），
 * 子表 id 由「服务端」重新编号，这样前端永远不会以为改错了行。
 */
function persistUpsert(payload: Record<string, unknown>): void {
  const numbered = (rows: unknown, prefix: string): unknown[] =>
    (Array.isArray(rows) ? rows : []).map((row, index) => ({
      ...(row as Record<string, unknown>),
      id: `${prefix}-${index + 1}`,
    }))

  serverProfile = {
    ...serverProfile,
    ...payload,
    skills: numbered(payload.skills, 'skill'),
    experiences: numbered(payload.experiences, 'exp'),
    educations: numbered(payload.educations, 'edu'),
    updated_at: '2026-11-07T12:00:00+08:00',
  }
}

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

/** 后端统一错误信封（`app/core/errors.py`）：`{ error: {code,message,...}, meta }`。 */
function failure(config: InternalAxiosRequestConfig, status: number, message: string) {
  const body: ApiErrorBody = {
    error: {
      code: status >= 500 ? 'SERVER_ERROR' : 'CLIENT_ERROR',
      message,
      details: {},
      retryable: status >= 500,
    },
    meta: { request_id: 'req-resume-preview-test' },
  }
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, envelope(config, body, status))
}

function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  const rawBody = typeof config.data === 'string' ? config.data : null
  calls.push({ method, url, body: rawBody })

  if (method === 'get' && url === '/profiles') {
    return Promise.resolve(
      envelope(config, { data: { profiles: [summaryRow()] }, meta: { request_id: 'r' } }),
    )
  }

  if (method === 'get' && url === `/profiles/${PROFILE_ID}`) {
    return Promise.resolve(
      envelope(config, { data: { profile: serverProfile }, meta: { request_id: 'r' } }),
    )
  }

  if (method === 'put' && url === `/profiles/${PROFILE_ID}`) {
    if (failSaveWith !== 0) {
      // 刻意**不**落库：用例要靠这条区分「前端把失败演成已保存」与「如实失败」。
      return Promise.reject(
        failure(config, failSaveWith, '保存失败：这份简历正在被另一个标签页编辑。'),
      )
    }
    persistUpsert(JSON.parse(rawBody ?? '{}') as Record<string, unknown>)
    return Promise.resolve(
      envelope(config, { data: { profile: serverProfile }, meta: { request_id: 'r' } }),
    )
  }

  return Promise.reject(failure(config, 404, `未桩住的请求：${method.toUpperCase()} ${url}`))
}

/* --------------------------------- 挂载 ---------------------------------- */

/**
 * 编辑区 + 纸面**挂在同一棵树上**：这就是用户实际看到的那一屏。
 * 「纸面改了、表单还是旧的」这类错位，只有两者同时在场才测得出来。
 *
 * 用 `h()` 而不是 `template` 字符串：vitest 走的是 Vue 的运行时构建（没有模板编译器），
 * 字符串 template 在这里根本渲染不出来。
 */
const Workspace = defineComponent({
  setup() {
    return () => h('div', [h(ResumeEditor), h(ResumePreview)])
  },
})

async function mountWorkspace(): Promise<VueWrapper> {
  const attachTo = document.createElement('div')
  document.body.appendChild(attachTo)
  const wrapper = mount(Workspace, { attachTo, global: { plugins: [pinia] } })
  mounted.push(wrapper)
  // 真实编辑器视图在 `onMounted` 里拉数据；这里复刻这一步，
  // 让 store 的 `currentProfileId` / `savedProfile` 与纸面渲染都来自 GET 响应。
  await useResumeStore().loadFromServer()
  await flushPromises()
  return wrapper
}

/** 纸面上第一段经历的「项目描述」。 */
function paperDescription(wrapper: VueWrapper) {
  return wrapper.get('.experience-description')
}

/**
 * 模拟用户在纸面上改一处文字：改 DOM 文本 → 派发 blur（组件只在失焦时回写，
 * 见 `ResumePreview.vue` 的 `commitText`：在 `@input` 里回写会把光标打回开头）。
 */
async function editOnPaper(wrapper: VueWrapper, value: string): Promise<void> {
  const node = paperDescription(wrapper)
  ;(node.element as HTMLElement).innerText = value
  await node.trigger('blur')
  await flushPromises()
}

/**
 * 编辑区里按标签找到的那个输入框。
 * 为什么不按下标取 `findAll('textarea')[0]`：表单里 textarea 的先后顺序是排版细节，
 * 加一个字段就会错位，而下标错位的用例照样会绿（假绿）。标签文字才是用户与测试共同的锚点。
 */
function formFieldByLabel(wrapper: VueWrapper, label: string) {
  const field = wrapper
    .findAll('label.field')
    .find((item) => item.find('span').exists() && item.find('span').text() === label)
  if (!field) throw new Error(`编辑区找不到标签「${label}」`)
  return field.find('textarea')
}

function saveButton(wrapper: VueWrapper) {
  return wrapper.get('.save-button')
}

/** 只统计打到这份简历上的写请求，用来数「恰好一次」。 */
function putCalls(): Call[] {
  return calls.filter((call) => call.method === 'put' && call.url === `/profiles/${PROFILE_ID}`)
}

function profileCreates(): Call[] {
  return calls.filter((call) => call.method === 'post' && call.url === '/profiles')
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  sessionStorage.clear()
  document.body.innerHTML = ''
  calls = []
  failSaveWith = 0
  serverProfile = freshServerProfile()

  savedAdapters = [
    [http, http.defaults.adapter],
    [authHttp, authHttp.defaults.adapter],
  ]
  http.defaults.adapter = fakeAdapter
  authHttp.defaults.adapter = fakeAdapter

  // `resolveOwnerId()` 回落 auth store；没有它就等于「不知道这是谁的简历」，
  // 草稿格与保存目标都会走进还没落库的空白骨架分支。
  useAuthStore().user = {
    id: OWNER_ID,
    username: 'tester',
    email: null,
    phone: null,
    created_at: '2026-11-01T10:00:00+08:00',
  }
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.innerHTML = ''
  for (const [instance, adapter] of savedAdapters) {
    instance.defaults.adapter = adapter
  }
  savedAdapters = []
})

describe('保存按钮的两态', () => {
  it('刚进页面没有改动：写「已保存」并且点不动', async () => {
    const wrapper = await mountWorkspace()

    const button = saveButton(wrapper)
    expect(button.text()).toBe('已保存')
    expect(button.attributes('disabled')).toBeDefined()
    expect(useResumeStore().dirty).toBe(false)
    // 反向护栏：这句话不是「组件没渲染出来」——纸面上确实带着库里那句原文。
    expect(paperDescription(wrapper).text()).toBe(ORIGINAL_DESCRIPTION)
  })

  it('纸面上改了一处文字：立刻变「保存修改」，并且真的可以点', async () => {
    const wrapper = await mountWorkspace()
    await editOnPaper(wrapper, NEW_DESCRIPTION)

    const button = saveButton(wrapper)
    expect(button.text()).toBe('保存修改')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(useResumeStore().dirty).toBe(true)
  })
})

describe('纸面编辑的回写与双向一致（池子 §一 第 3 项的验收口径）', () => {
  it('失焦后：改动进了 store 的 draft，纸面留着新句子', async () => {
    const wrapper = await mountWorkspace()
    await editOnPaper(wrapper, NEW_DESCRIPTION)

    expect(useResumeStore().draft.experiences[0]?.description).toBe(NEW_DESCRIPTION)
    expect(paperDescription(wrapper).text()).toBe(NEW_DESCRIPTION)
  })

  it('双向一致：编辑区「项目描述」那个输入框显示的是同一句话', async () => {
    const wrapper = await mountWorkspace()
    await editOnPaper(wrapper, NEW_DESCRIPTION)

    const textarea = formFieldByLabel(wrapper, '项目描述')
    // 断言的是 DOM 的 value，不是 store 的字段：表单只绑 `:value`，
    // 真出现「store 变了、表单没跟着重渲染」时，读字段是查不出来的。
    expect((textarea.element as HTMLTextAreaElement).value).toBe(NEW_DESCRIPTION)
    // 只动了描述：同段的职责与标题一个字节都不该被牵连。
    expect((formFieldByLabel(wrapper, '个人职责').element as HTMLTextAreaElement).value).toBe(
      '负责数据清洗与模型训练。',
    )
  })

  it('反方向也一致：在编辑区改描述，纸面上同一处跟着变成那句话', async () => {
    const wrapper = await mountWorkspace()

    const textarea = formFieldByLabel(wrapper, '项目描述')
    await textarea.setValue(NEW_DESCRIPTION)
    await flushPromises()

    expect(paperDescription(wrapper).text()).toBe(NEW_DESCRIPTION)
    expect(useResumeStore().draft.experiences[0]?.description).toBe(NEW_DESCRIPTION)
    expect(useResumeStore().dirty).toBe(true)
  })
})

describe('点「保存修改」', () => {
  it('恰好发出一次 PUT /profiles/{id}，请求体带着刚改的那句话，成功后按钮退回「已保存」', async () => {
    const wrapper = await mountWorkspace()
    await editOnPaper(wrapper, NEW_DESCRIPTION)

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(putCalls()).toHaveLength(1)
    // 库里一份简历就在，所以不该走「先 POST 建一份再 PUT」那条兜底分支。
    expect(profileCreates()).toEqual([])

    const payload = JSON.parse(putCalls()[0]?.body ?? '{}') as {
      experiences?: { description?: string }[]
    }
    expect(payload.experiences?.[0]?.description).toBe(NEW_DESCRIPTION)

    const button = saveButton(wrapper)
    expect(button.text()).toBe('已保存')
    expect(button.attributes('disabled')).toBeDefined()
    expect(useResumeStore().dirty).toBe(false)
    expect(useResumeStore().saving).toBe(false)
  })

  it('保存成功后内存里的小服务器也真的接住了这句（不是只改了前端状态）', async () => {
    const wrapper = await mountWorkspace()
    await editOnPaper(wrapper, NEW_DESCRIPTION)
    await saveButton(wrapper).trigger('click')
    await flushPromises()

    const stored = serverProfile.experiences as { description: string }[]
    expect(stored[0]?.description).toBe(NEW_DESCRIPTION)
  })
})

describe('保存失败绝不静默（项目红线在组件上的落点）', () => {
  it('PUT 返回 500：按钮不变成「已保存」，用户看得见原因，打的字还在纸面与表单里', async () => {
    failSaveWith = 500
    const wrapper = await mountWorkspace()
    await editOnPaper(wrapper, NEW_DESCRIPTION)

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    // 1) 状态没被演成成功。
    const button = saveButton(wrapper)
    expect(button.text()).toBe('保存修改')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(useResumeStore().dirty).toBe(true)
    expect(useResumeStore().saving).toBe(false)

    // 2) 失败原因看得见，而且是后端那句话（不是通用「请求失败」糊过去）。
    const alert = wrapper.get('.save-error')
    expect(alert.attributes('role')).toBe('alert')
    expect(alert.text()).toContain('保存失败')
    expect(alert.text()).toContain('保存失败：这份简历正在被另一个标签页编辑。')

    // 3) 用户打的字一个都没丢。
    expect(useResumeStore().draft.experiences[0]?.description).toBe(NEW_DESCRIPTION)
    expect(paperDescription(wrapper).text()).toBe(NEW_DESCRIPTION)
    expect((formFieldByLabel(wrapper, '项目描述').element as HTMLTextAreaElement).value).toBe(
      NEW_DESCRIPTION,
    )

    // 4) 反向护栏：服务端那份确实没被改脏——「失败」是真的失败，不是回了个假错误码。
    const stored = serverProfile.experiences as { description: string }[]
    expect(stored[0]?.description).toBe(ORIGINAL_DESCRIPTION)
  })

  it('失败之后把同一个按钮再点一次还能救回来（不是进入死状态）', async () => {
    failSaveWith = 500
    const wrapper = await mountWorkspace()
    await editOnPaper(wrapper, NEW_DESCRIPTION)
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(saveButton(wrapper).text()).toBe('保存修改')

    failSaveWith = 0
    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(saveButton(wrapper).text()).toBe('已保存')
    expect(putCalls()).toHaveLength(2)
    expect(wrapper.find('.save-error').exists()).toBe(false)
    expect(useResumeStore().dirty).toBe(false)
  })
})

describe('「刷新页面后仍在」', () => {
  /**
   * 这一条是**重新挂载**模拟，不是真刷新：jsdom 里没法重载页面。
   * 做法是「卸掉整棵树 + 换一只全新的 Pinia（内存草稿随之消失）+ 重新拉一次数据」，
   * 于是新 store 唯一的信息来源就是那只内存小服务器。
   *
   * 它证明了：PUT 的请求体里确实带着那句改动、GET → draft → 纸面/表单这条读路径会把它画出来。
   * 它证明不了：MySQL 真的把那行写进去了、以及后端 `update_profile` 的子表全量替换没吞字段
   * ——那要集成测试（`tests/`）或人眼验收。已如实写进交付说明的「未解决问题」。
   */
  it('卸载并用全新 store 重新挂载后，纸面与表单仍是那句话', async () => {
    const wrapper = await mountWorkspace()
    await editOnPaper(wrapper, NEW_DESCRIPTION)
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(saveButton(wrapper).text()).toBe('已保存')

    wrapper.unmount()
    const readCallsBefore = calls.filter((call) => call.method === 'get').length

    pinia = createPinia()
    setActivePinia(pinia)
    useAuthStore().user = {
      id: OWNER_ID,
      username: 'tester',
      email: null,
      phone: null,
      created_at: '2026-11-01T10:00:00+08:00',
    }

    const rebuilt = await mountWorkspace()

    // 反向护栏：新 store 的数据是从网络读回来的，不是上一条用例残留的内存。
    expect(calls.filter((call) => call.method === 'get').length).toBeGreaterThan(readCallsBefore)

    expect(useResumeStore().draft.experiences[0]?.description).toBe(NEW_DESCRIPTION)
    expect(paperDescription(rebuilt).text()).toBe(NEW_DESCRIPTION)
    expect((formFieldByLabel(rebuilt, '项目描述').element as HTMLTextAreaElement).value).toBe(
      NEW_DESCRIPTION,
    )
    // 重新挂载后「库里就是这一版」，所以不该被判定成有未保存的修改。
    expect(useResumeStore().dirty).toBe(false)
    expect(saveButton(rebuilt).text()).toBe('已保存')
  })
})

/**
 * 证书与作品（B4 / ADR-015 §2.1、§5 ②）。
 *
 * 这两段是 2026-11-12 补的：**后端生成器早就会渲染，实时预览一句都没有**。
 * 用户填了证书、点保存也成功了（库里真有那一行），纸面上却一格都没有——
 * 他唯一的结论只能是「没保存上」，而不是「预览没实现」。
 *
 * 所以这组用例的判据不是「store 里有没有数据」，而是**纸面 DOM 上有没有那几格**：
 * 库 → draft 这一段一直是通的，断的恰恰是 draft → 纸面。
 */
describe('证书与作品：实时预览必须与生成物同构', () => {
  beforeEach(() => {
    serverProfile = {
      ...freshServerProfile(),
      certificates: [
        { id: 'cert-1', name: '大学英语六级', issuer: '教育部教育考试院', issued_on: '2024-06-01' },
      ],
      portfolio_links: [{ id: 'link-1', label: '技术博客', url: 'https://example.com/blog' }],
    }
  })

  it('档案里有几格，纸面上就逐格画出来（含月份格式与链接 href）', async () => {
    const wrapper = await mountWorkspace()

    expect(wrapper.get('.certificate-name').text()).toBe('大学英语六级')
    expect(wrapper.get('.certificate-issuer').text()).toBe('教育部教育考试院')
    // 纸面只到月：与后端 `_format_month` / 教育经历同一格式，不是 `2024-06-01`。
    expect(wrapper.get('.certificate-date').text()).toBe('2024.06')

    expect(wrapper.get('.portfolio-label').text()).toBe('技术博客')
    const anchor = wrapper.get('.portfolio-url')
    expect(anchor.text()).toBe('https://example.com/blog')
    expect(anchor.attributes('href')).toBe('https://example.com/blog')
  })

  it('两个段都受排版顺序控制：改顺序纸面跟着变', async () => {
    const wrapper = await mountWorkspace()
    const store = useResumeStore()
    // 把作品挪到证书前面——顺序来自 `layout_config`，不是模板里写死的位置。
    store.moveSectionUp('portfolio')
    await flushPromises()

    const headings = wrapper.findAll('.paper-section > h2').map((node) => node.text())
    expect(headings.indexOf('作品链接')).toBeLessThan(headings.indexOf('证书'))
  })

  it('没填证书时不画一个空标题（空模块不占纸面）', async () => {
    serverProfile = freshServerProfile()
    const wrapper = await mountWorkspace()
    expect(wrapper.find('.certificate-name').exists()).toBe(false)
    expect(wrapper.find('.portfolio-label').exists()).toBe(false)
  })
})

/**
 * 一页边界预警（第 15 轮裁定，`docs/ROADMAP.md` §6 第 5 条）。
 *
 * 裁定的原文是「在预览区高度等于一页 A4 的位置画一条明显的虚线，旁边写上
 * 『建议单页，此处后内容可能被 HR 忽略』」——**不截断、不缩字**。
 * 所以本组用例要钉住的不是「线好看不好看」，而是三件会被做坏的事：
 *
 * 1. 内容只有一页时**别画**：那时线和纸张底边重合，是一条没有信息量的边线；
 * 2. 真的超过一页时**必须画**，且文案逐字来自裁定（改成「超过一页」就等于把
 *    「HR 可能不看」这个真正该权衡的理由弄丢了）；
 * 3. 它挂在 stage 上而不是纸面内部——进了 `.paper` 会进 `scrollHeight`（页数算歪），
 *    也会混进 contenteditable 与 `v-html` 生成物里。
 *
 * 这里量高度靠临时改写 `HTMLElement.prototype.scrollHeight`：jsdom 没有布局引擎，
 * `scrollHeight` 恒为 0，不垫这一刀组件永远只会算出「1 页」，用例会对着一个
 * 从不出现的元素断言 `toBe(false)` 而假绿。
 */
describe('一页边界预警', () => {
  /** A4 一页的真实高度（px）。纸面按 mm 排版，量出来的高度也是这个坐标系。 */
  const A4_ONE_PAGE_PX = 297 * PX_PER_MM

  /**
   * 让 `.paper` 量到指定高度；传 `null` 恢复 jsdom 的默认（恒 0）。
   *
   * 只拦 `.paper` 自己的 `scrollHeight`：其它元素一概返回 0，
   * 免得顺手把某个看不见的容器也「量」成一个假高度。
   */
  function stubPaperHeight(px: number | null): void {
    if (px === null) {
      Reflect.deleteProperty(HTMLElement.prototype, 'scrollHeight')
      return
    }
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get(this: HTMLElement): number {
        return this.classList?.contains('paper') ? px : 0
      },
    })
  }

  afterEach(() => stubPaperHeight(null))

  it('内容在一页以内：不画线、不写提示（不制造没信息量的焦虑）', async () => {
    stubPaperHeight(A4_ONE_PAGE_PX * 0.6)
    const wrapper = await mountWorkspace()

    expect(wrapper.find('.paper-page-guard').exists()).toBe(false)
    // 反向护栏：纸面确实渲染了，不是「整个组件没出来」才找不到这条线。
    expect(paperDescription(wrapper).text()).toBe(ORIGINAL_DESCRIPTION)
    expect(wrapper.get('.page-count').text()).toContain('1 页')
  })

  it('超过一页：画一条线，提示逐字来自裁定，纸面内容一个字都没被裁掉', async () => {
    stubPaperHeight(A4_ONE_PAGE_PX * 1.3)
    const wrapper = await mountWorkspace()

    const guard = wrapper.get('.paper-page-guard')
    expect(guard.text()).toBe('建议单页，此处后内容可能被 HR 忽略')
    // 它得能被读屏读到，否则这句话只对看得见颜色的人成立。
    expect(guard.attributes('role')).toBe('note')
    expect(wrapper.get('.page-count').text()).toContain('2 页')

    // 裁定是「纸可以拉长成两页」，所以内容必须原样在纸上，没有被截掉半句。
    expect(paperDescription(wrapper).text()).toBe(ORIGINAL_DESCRIPTION)
  })

  it('线挂在 stage 上、不挂在纸面里（否则会进 scrollHeight，也会混进可编辑区）', async () => {
    stubPaperHeight(A4_ONE_PAGE_PX * 1.3)
    const wrapper = await mountWorkspace()

    expect(wrapper.find('.paper-stage > .paper-page-guard').exists()).toBe(true)
    expect(wrapper.find('.paper .paper-page-guard').exists()).toBe(false)
  })
})
