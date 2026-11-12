<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { isApiError } from '@/api'
import type { PaperSize } from '@/api/types'
import { PX_PER_MM, useResumeStore } from '@/stores/resume'
import { formatDate, formatMonth, parseDateInput, parseMonthInput } from '@/utils/datetime'

const store = useResumeStore()

/**
 * 后端 `TemplateResumeGenerator` 返回的是完整 HTML 文档（含 `<html><head>`），
 * 直接塞进纸面会带上不需要的文档级标签，所以只取 `<body>` 内部内容。
 *
 * 这里只做「取 body」，不再对文本做任何改写：生成的 HTML 与下方模板使用
 * 同一套类名（`.paper-header` / `.paper-section` / `.education-school` …），
 * 版式由同一个非 scoped 样式块统一负责，所以「实时预览」和「已生成简历」
 * 长得完全一样，打印出来也就是所见的那样。
 */
const generatedBody = computed(() => {
  const html = store.generatedHtml
  if (!html) return ''
  return new DOMParser().parseFromString(html, 'text/html').body.innerHTML
})

const generatedResumeId = computed(() => store.generatedResumeId?.slice(0, 8) ?? '')

/* ------------------------------ 纸张与缩放（D5.1） ------------------------------ */

/**
 * P2 方案（ADR-007 §4.2）：`.paper` 永远按真实 mm 排版，屏幕放不下就整体缩放。
 *
 * 这不只是为了「好看」——它是页数能算对的前提：
 * 1. 屏幕与打印共用同一套 mm 坐标，字号 px 在两边占纸宽的比例一致（所见即所得）；
 * 2. 于是「内容高度」可以除以「一张纸的高度」得到真实页数。
 *
 * 缩放走 `transform`，**不参与排版**，所以量出来的高度与 zoom 无关，
 * 缩放变化时不必重量。这正是它比「改宽度/字号」更可靠的地方。
 */
const paperRef = ref<HTMLElement | null>(null)
const frameRef = ref<HTMLElement | null>(null)
/** 预览列可用宽度（px），由 ResizeObserver 维护。 */
const columnWidth = ref(0)
/** `.paper` 内容真实高度（未缩放 px）。 */
const contentHeight = ref(0)

const onePageWidth = computed(() => store.paperSpec.widthMm * PX_PER_MM)
const onePageHeight = computed(() => store.paperSpec.heightMm * PX_PER_MM)

/**
 * 显示倍率 = 列宽 / 一张纸的宽度。
 *
 * 不设 1 的上限：容器更宽时按比例放大，纸面始终铺满预览列（与改造前观感一致）。
 * 封顶 1 会让宽屏下左右各留一大片空白，看起来像「没加载完整」。
 */
const zoom = computed(() => {
  if (!columnWidth.value || !onePageWidth.value) return 1
  return Math.max(0.2, columnWidth.value / onePageWidth.value)
})

/** 真实页数：内容高度 ÷ 一张纸的高度，向上取整。 */
const pageCount = computed(() => {
  const height = contentHeight.value || onePageHeight.value
  // 减 1px：`scrollHeight` 是整数像素，内容刚好铺满时可能多出不到 1px，
  // 不减就会把「正好一页」算成两页。
  return Math.max(1, Math.ceil((height - 1) / onePageHeight.value))
})

/**
 * stage 的尺寸是「缩放后的可见尺寸」，必须显式给：
 * 纸面的布局宽度是 210mm（794px）而显示宽度是 794×zoom，
 * 让父容器按缩放后的大小占位，才不会在窄列里撑出横向滚动条。
 */
const stageStyle = computed<Record<string, string>>(() => ({
  width: `${onePageWidth.value * zoom.value}px`,
  height: `${Math.max(contentHeight.value, onePageHeight.value) * zoom.value}px`,
  // 三个变量挂在 stage 上，纸面与水印一起继承（水印也要跟着缩放）。
  '--paper-zoom': String(zoom.value),
  '--paper-width': `${store.paperSpec.widthMm}mm`,
  '--paper-height': `${store.paperSpec.heightMm}mm`,
}))

const paperMeta = computed(() => `${store.paperSpec.label} · ${pageCount.value} 页`)

/**
 * 主题色下发（D5.2）。
 *
 * 只挂在**实时预览**那张纸上：`.paper` 里声明了 8 个变量的默认值（= forest），
 * 这里把当前预设的 `accent` / `rule` 覆盖上去，纸面样式统一用 `var(--paper-*)`，
 * 于是「换主题」只是换两个变量，不需要任何 DOM 重排。
 *
 * 为什么「已生成简历」那张纸不绑：它的配色是生成时**内联进 HTML** 的
 * （`resume_generator.py` 的 `.paper-doc` 包装层）。生成物应当忠实反映
 * 「生成那一刻是什么样」；改主题后 store 会自动切回实时预览并提示重新生成。
 */
const paperThemeStyle = computed<Record<string, string>>(() => ({
  '--paper-accent': store.themeSpec.accent,
  '--paper-rule': store.themeSpec.rule,
}))

function measurePaper(): void {
  const element = paperRef.value
  if (!element) return
  contentHeight.value = element.scrollHeight
}

watch(pageCount, (value) => store.setPageCount(value), { immediate: true })

/** 容器宽度：窗口缩放、侧栏变化都会改它，必须观察而不是只读一次。 */
let frameObserver: ResizeObserver | null = null
/** 纸面高度：内容增删、换纸张、切到生成结果都会改它。 */
let paperObserver: ResizeObserver | null = null

/**
 * 纸面元素会被 `v-if` 换掉（实时预览 ↔ 生成结果），所以观察器绑在 `paperRef` 上：
 * 元素一变就重新 observe，否则量到的永远是上一个已卸载的 DOM。
 */
watch(
  paperRef,
  (element) => {
    paperObserver?.disconnect()
    if (!element) return
    paperObserver = new ResizeObserver(measurePaper)
    paperObserver.observe(element)
    measurePaper()
  },
  { flush: 'post' },
)

/* 换纸张时 `min-height` 变了、内容可能没变，ResizeObserver 不一定触发，补量一次。 */
watch(
  () => store.draft.paper_size,
  () => {
    void nextTick(measurePaper)
  },
)

onMounted(() => {
  if (frameRef.value) {
    frameObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0
      if (width > 0) columnWidth.value = width
    })
    frameObserver.observe(frameRef.value)
  }
  void nextTick(measurePaper)
})

/* --------------------------- 打印：@page 动态注入 --------------------------- */

/*
 * `@page { size: … }` **不接受 CSS 变量**（ADR-007 §4.4）：`size: var(--x)` 是无效值，
 * 浏览器会整条忽略。所以只能在切换纸张时改写一段 `<style>` 的 textContent。
 *
 * `margin: 0` 必须显式写：不写就是「浏览器默认页边距 + 纸内 padding」两层叠加，
 * 打印出来四周多一圈白、屏幕上却没有——这是「预览和打印不一样」最常见的原因。
 * 代价是用户若在打印对话框里勾了「页眉页脚」，页眉会压在纸内边距里（只能靠提示）。
 */
const PAGE_RULE_STYLE_ID = 'resume-print-page-rule'

function applyPrintPageRule(size: PaperSize): void {
  let element = document.getElementById(PAGE_RULE_STYLE_ID) as HTMLStyleElement | null
  if (!element) {
    element = document.createElement('style')
    element.id = PAGE_RULE_STYLE_ID
    document.head.appendChild(element)
  }
  element.textContent = `@page { size: ${size}; margin: 0; }`
}

watch(() => store.draft.paper_size, applyPrintPageRule, { immediate: true })

onBeforeUnmount(() => {
  frameObserver?.disconnect()
  paperObserver?.disconnect()
  // 这条规则是全局的：不摘掉会跟着留到其它路由的打印里。
  document.getElementById(PAGE_RULE_STYLE_ID)?.remove()
})

/**
 * 全站唯一的「保存」入口（D3 方案 C）。
 *
 * 纸面编辑与左侧表单都只写 Pinia，`dirty` 为真即代表「改了但没落库」。
 * 这里不再拆「保存档案 / 保存修改」两种语义——都只是把当前草稿整体
 * `PUT /profiles/me`，成功后 `store.save()` 内部会 `applyDraft()` 把
 * `dirty` 归零，按钮自然从高亮的「保存修改」退回灰底的「已保存」。
 */
const saveError = ref('')

const saveLabel = computed(() => {
  if (store.saving) return '保存中…'
  return store.dirty ? '保存修改' : '已保存'
})

/**
 * `details[0].loc` 形如 `["body", "educations", 0, "edu_start"]`：
 * 去掉 `body` 这一段后拼成 `educations.0.edu_start`，正好能指名到具体输入框。
 */
function firstInvalidField(details: unknown): string {
  if (!Array.isArray(details) || details.length === 0) return ''
  const first: unknown = details[0]
  if (!first || typeof first !== 'object') return ''
  const loc = (first as { loc?: unknown }).loc
  if (!Array.isArray(loc)) return ''
  return loc.filter((part: unknown) => part !== 'body').join('.')
}

/**
 * 保存失败的提示文案。
 *
 * 保存 422 排查轮：原先只显示 `error.message`（T6 统一文案「请求参数校验失败。」），
 * 到底哪个字段不合法完全看不出来，只能去翻 Network 面板。现在 422 时把后端的
 * `details` **完整打到控制台**，页面上则带上出错字段的路径。
 * 非 422 的 `ApiError` 与 `validateForSave()` 抛出的普通 Error 都直接用人话消息。
 */
function describeSaveError(error: unknown): string {
  if (isApiError(error)) {
    if (error.status === 422) {
      console.error('[保存档案] 后端返回 422，details：', error.details)
      const field = firstInvalidField(error.details)
      return field
        ? `保存失败：字段「${field}」未通过后端校验，请检查后重试。`
        : '保存失败：请求参数未通过后端校验，请检查后重试。'
    }
    return `保存失败：${error.message}`
  }
  if (error instanceof Error) return `保存失败：${error.message}`
  return '保存失败，请稍后重试。'
}

async function handleSave(): Promise<void> {
  // 保存中重复点击、或在「已保存」状态下点击，都直接忽略。
  if (store.saving || !store.dirty) return
  saveError.value = ''
  try {
    await store.save()
  } catch (error) {
    saveError.value = describeSaveError(error)
  }
}

/**
 * 教育经历按入学时间倒序（最近一段在前），与后端生成器 `sorted(..., reverse=True)` 同序。
 *
 * 排序会打乱数组下标，所以这里把「原始下标」一起带出来——
 * 纸面上编辑完要按原始下标回写 store，否则会改错行。
 */
const displayEducations = computed(() =>
  store.draft.educations
    .map((education, index) => ({ education, index }))
    .sort((left, right) => right.education.edu_start.localeCompare(left.education.edu_start)),
)

/** 教育经历入学/毕业时间的展示形态：`2021-09` → `2021.09`，空值由调用方给占位。 */
function displayMonth(value: string): string {
  return formatMonth(value)
}

/**
 * 纸面编辑只在**失焦时**回写 store。
 *
 * 若在 `@input` 里回写，Pinia 一变、Vue 就重渲染这段文本节点，
 * 浏览器会丢失原生 Selection，光标被强制归零（表现为「四张三」）。
 * 改用 `@blur` 后，编辑过程中 store 不变、DOM 不被 patch，光标完全交给浏览器管。
 *
 * `apply` 返回「该值对应的规范展示文本」：日期被用户敲成 `2021.9` 这类写法时，
 * 回写后要把 DOM 文本也纠正成 `2021.09`，否则纸面会留下用户的原始输入。
 * 内容为空时置空串，交给 CSS 的 `data-placeholder` 显示灰色占位。
 */
function commitText(event: Event, current: string, apply: (value: string) => string): void {
  const element = event.currentTarget as HTMLElement | null
  if (!element) return
  const value = element.innerText.trim()
  const display = value === current.trim() ? current : apply(value)
  if (element.innerText !== display) {
    element.innerText = display
  }
}

/** 教育经历的月份单元格：能识别就写回 store，识别不了就还原成原值。 */
function commitEducationMonth(
  index: number,
  field: 'edu_start' | 'edu_end',
  event: Event,
): void {
  const education = store.draft.educations[index]
  if (!education) return
  const current = displayMonth(education[field]) || (field === 'edu_end' ? '至今' : '入学时间')
  commitText(event, current, (value) => {
    // 「至今」等价于清空 edu_end；入学时间不允许空。
    const parsed = value === '' || value === '至今' ? '' : parseMonthInput(value)
    if (parsed === null) return current
    if (field === 'edu_start' && parsed === '') return current
    store.updateEducation(index, field, parsed)
    return displayMonth(parsed) || (field === 'edu_end' ? '至今' : '入学时间')
  })
}

/**
 * 证书的颁发日期（失焦回写）。
 *
 * 纸面只到月（与生成器 `_format_month` 同口径，`2023.12`），而库里是完整 DATE：
 * 用户敲 `2023.12` 时补成当月 1 号写入，与表单 `type="date"` 的取值格式一致。
 * 识别不了就还原成原值——**不猜**用户的意图，也不把半截输入写进库。
 */
function commitCertificateDate(index: number, event: Event): void {
  const certificate = store.draft.certificates[index]
  if (!certificate) return
  const current = displayMonth(certificate.issued_on) || '颁发日期'
  commitText(event, current, (value) => {
    if (value === '') {
      store.updateCertificate(index, 'issued_on', '')
      return '颁发日期'
    }
    const parsed = parseMonthInput(value)
    if (parsed === null) return current
    store.updateCertificate(index, 'issued_on', `${parsed}-01`)
    return displayMonth(`${parsed}-01`)
  })
}

/**
 * 作品链接的 `href` 兜底：只放行 http/https，其余返回 `undefined`（渲染成无 `href` 的 `<a>`）。
 *
 * 写侧 `validateForSave()` 与后端 `PortfolioLinkInput` 都会拦，这里是第三道——
 * 因为纸面是 `v-html` 之外唯一把用户输入直接塞进属性名的地方，草稿可能停在任何半截状态。
 */
function safeHref(url: string): string | undefined {
  return /^https?:\/\//i.test(url.trim()) ? url.trim() : undefined
}

/**
 * 纸面上编辑项目经历的起止时间（失焦回写）。
 *
 * 纸面按「年.月.日」展示（与后端生成器 `_format_day` 同口径），store 里也是
 * 完整 ISO 日期（`2024-03-15`），所以这里只做「用户输入 → ISO」的解析：
 *
 * - 写完整日期（`2024.03.15` / `2024-03-15` / `2024/3/15`）→ 原样落库；
 * - 只写年月（`2024.03`）→ 由 `parseDateInput` 补 `-01`，兼容旧习惯；
 * - 解析不了 → 视为放弃修改，还原原展示文本；
 * - 结束时间被删空或写成「至今」→ `end_date` 置空串，渲染层显示「至今」；
 * - 开始时间被删空 → 同样视为放弃修改（开始时间是必填锚点）。
 */
function updateExpDate(
  event: Event,
  index: number,
  field: 'start_date' | 'end_date',
): void {
  const experience = store.draft.experiences[index]
  if (!experience) return
  const fallback = field === 'end_date' ? '至今' : '开始'
  const current = formatDate(experience[field]) || fallback
  commitText(event, current, (value) => {
    if (value === '' || value === '至今') {
      // 只有结束时间可以用「空」表达「至今」，开始时间缺失时保持原值。
      if (field !== 'end_date') return current
      store.updateExperience(index, field, '')
      return fallback
    }
    const parsed = parseDateInput(value)
    if (parsed === null) return current
    store.updateExperience(index, field, parsed)
    return formatDate(parsed) || fallback
  })
}
</script>

<template>
  <section class="preview-column">
    <div class="preview-toolbar">
      <span
        class="mode-badge"
        :class="{ 'mode-badge--generated': store.isGeneratingResultVisible }"
      >
        <span class="mode-badge__dot" aria-hidden="true"></span>
        {{ store.isGeneratingResultVisible ? '已生成简历' : '实时预览' }}
      </span>
      <div class="toolbar-save">
        <p v-if="saveError" class="save-error" role="alert">{{ saveError }}</p>
        <button
          class="save-button"
          :class="{ 'save-button--dirty': store.dirty }"
          type="button"
          :disabled="store.saving || !store.dirty"
          @click="handleSave"
        >
          <span v-if="store.dirty && !store.saving" class="save-button__dot" aria-hidden="true"></span>
          {{ saveLabel }}
        </button>
      </div>
      <div class="toolbar-right">
        <span v-if="generatedResumeId" class="code">#{{ generatedResumeId }}</span>
        <span class="page-count">{{ paperMeta }}</span>
        <button
          v-if="store.isGeneratingResultVisible"
          class="preview-button"
          type="button"
          @click="store.showLivePreview"
        >
          回到实时预览
        </button>
      </div>
    </div>

    <div ref="frameRef" class="paper-frame">
      <!--
        stage 负责「缩放后的占位尺寸」，纸面在里面按真实 mm 排版后整体 scale。
        外层不裁剪：多页内容要让 stage 跟着变高（页数覆盖层是 D5.4 的事）。
      -->
      <div class="paper-stage" :style="stageStyle">
        <article
          v-if="store.isGeneratingResultVisible"
          ref="paperRef"
          class="paper paper--generated"
          aria-label="已生成的简历"
          v-html="generatedBody"
        />

        <article v-else ref="paperRef" class="paper" aria-label="简历预览" :style="paperThemeStyle">
          <header class="paper-header">
            <h1
              class="resume-name"
              contenteditable="true"
              spellcheck="false"
              data-placeholder="姓名"
              @blur="
                commitText($event, store.draft.full_name, (value) => {
                  store.updateProfileField('full_name', value)
                  return value
                })
              "
            >{{ store.draft.full_name }}</h1>
            <p class="contact">
              <span
                class="contact-phone"
                contenteditable="true"
                spellcheck="false"
                data-placeholder="手机号"
                @blur="
                  commitText($event, store.draft.contact, (value) => {
                    store.updateProfileField('contact', value)
                    return value
                  })
                "
              >{{ store.draft.contact }}</span>
              <span class="contact-separator">·</span>
              <span
                class="contact-email"
                contenteditable="true"
                spellcheck="false"
                data-placeholder="邮箱（选填）"
                @blur="
                  commitText($event, store.draft.email, (value) => {
                    store.updateProfileField('email', value)
                    return value
                  })
                "
              >{{ store.draft.email }}</span>
            </p>
          </header>

          <!--
            排版（D5.3）：五个 `<section>` 不再按模板位置固定顺序，而是按
            `store.layoutSections`（= 后端解析后的 `layout_config`）逐个匹配渲染。
            标题取 layout 里的自定义标题，空标题回落默认值。

            为什么用 `v-for` + `v-if` 五连而不是「动态组件」：
            每段内部有十几处 `contenteditable` 与失焦回写，拆成子组件会多一层
            props/emit，也容易把「纸面编辑」的原始下标搞错（教育经历还要按入学时间排序）。
            这里只是把「外层顺序」交出去，内部保持原样。

            `v-else-if` 链必须紧邻（中间不能插注释），否则 Vue 编译报错——所以两段新模块的
            说明都写在这里，链内一个字都不插。

            证书 / 作品（B4 / ADR-015 §2.1、§5 ②）是 2026-11-12 补上的两段。缺它们的后果
            正是老板报的那个 bug：表单里填了证书、保存也成功了（库里真有那一行），但纸面上一格
            都没有——实时预览当时只认 educations / skills / experiences 三段，而后端生成器
            早就渲染了五段。DOM 与 `TemplateResumeGenerator` 逐字对应（`.certificate-name` /
            `.certificate-meta` / `.portfolio-label` / `.portfolio-url`，连
            `<span class="certificate-divider"> ｜ </span>` 的位置都一样），否则「实时预览」与
            「已生成简历」会长得不一样。

            作品那枚链接在预览里点不动（`@click.prevent`）：纸面是用来看的，点一下就离开编辑器
            还得重新等加载；真正导出/投递的那份 HTML 里它照常可点。`href` 走 `safeHref()`，
            写侧虽然只认 http/https，草稿可能停在半截输入上，这里再挡一道。
          -->
          <template v-for="section in store.layoutSections" :key="section.id">
            <section
              v-if="section.id === 'educations' && displayEducations.length"
              class="paper-section"
            >
              <h2>{{ store.sectionTitle(section) }}</h2>
              <article
                v-for="row in displayEducations"
                :key="`edu-${row.index}`"
                class="paper-experience"
              >
                <h3
                  class="education-school"
                  contenteditable="true"
                  spellcheck="false"
                  data-placeholder="学校名称"
                  @blur="
                    commitText($event, row.education.school, (value) => {
                      store.updateEducation(row.index, 'school', value)
                      return value
                    })
                  "
                >{{ row.education.school }}</h3>
                <p class="education-meta">
                  <span
                    class="edu-major"
                    contenteditable="true"
                    spellcheck="false"
                    data-placeholder="专业"
                    @blur="
                      commitText($event, row.education.major, (value) => {
                        store.updateEducation(row.index, 'major', value)
                        return value
                      })
                    "
                  >{{ row.education.major }}</span>
                  <span class="edu-divider"> ｜ </span>
                  <span class="edu-degree">{{ row.education.degree }}</span>
                  <span class="edu-divider"> ｜ </span>
                  <span
                    class="edu-start"
                    contenteditable="true"
                    spellcheck="false"
                    data-placeholder="入学时间"
                    @blur="commitEducationMonth(row.index, 'edu_start', $event)"
                  >{{ displayMonth(row.education.edu_start) }}</span>
                  <span class="edu-divider"> — </span>
                  <span
                    class="edu-end"
                    contenteditable="true"
                    spellcheck="false"
                    data-placeholder="至今"
                    @blur="commitEducationMonth(row.index, 'edu_end', $event)"
                  >{{ displayMonth(row.education.edu_end) || '至今' }}</span>
                </p>
              </article>
            </section>

            <section
              v-else-if="section.id === 'skills' && store.draft.skills.length"
              class="paper-section"
            >
              <h2>{{ store.sectionTitle(section) }}</h2>
              <div class="skill-list">
                <span v-for="(skill, index) in store.draft.skills" :key="index" class="skill-item">
                  <span
                    contenteditable="true"
                    spellcheck="false"
                    data-placeholder="技能"
                    @blur="
                      commitText($event, skill.name, (value) => {
                        store.updateSkill(index, value)
                        return value
                      })
                    "
                  >{{ skill.name }}</span>
                </span>
              </div>
            </section>

            <section
              v-else-if="section.id === 'experiences' && store.draft.experiences.length"
              class="paper-section"
            >
              <h2>{{ store.sectionTitle(section) }}</h2>
              <article
                v-for="(experience, index) in store.draft.experiences"
                :key="index"
                class="paper-experience"
              >
                <div class="experience-heading">
                  <h3
                    class="experience-title"
                    contenteditable="true"
                    spellcheck="false"
                    data-placeholder="项目名称"
                    @blur="
                      commitText($event, experience.title, (value) => {
                        store.updateExperience(index, 'title', value)
                        return value
                      })
                    "
                  >{{ experience.title }}</h3>
                  <!--
                    时间拆成「开始 / 分隔符 / 结束」三段独立节点，各自可点编辑。
                    外层仍用 .date，保证与后端生成器的 `<span class="date">…</span>` 视觉一致。
                  -->
                  <span class="date date-range">
                    <span
                      class="date-start"
                      contenteditable="true"
                      spellcheck="false"
                      data-placeholder="开始"
                      @blur="updateExpDate($event, index, 'start_date')"
                    >{{ formatDate(experience.start_date) }}</span>
                    <span class="date-divider"> — </span>
                    <span
                      class="date-end"
                      contenteditable="true"
                      spellcheck="false"
                      data-placeholder="至今"
                      @blur="updateExpDate($event, index, 'end_date')"
                    >{{ formatDate(experience.end_date) || '至今' }}</span>
                  </span>
                </div>
                <p
                  class="experience-description"
                  contenteditable="true"
                  spellcheck="false"
                  @blur="
                    commitText($event, experience.description, (value) => {
                      store.updateExperience(index, 'description', value)
                      return value
                    })
                  "
                >{{ experience.description }}</p>
                <p
                  class="experience-responsibility"
                  contenteditable="true"
                  spellcheck="false"
                  @blur="
                    commitText($event, experience.responsibility, (value) => {
                      store.updateExperience(index, 'responsibility', value)
                      return value
                    })
                  "
                >{{ experience.responsibility }}</p>
              </article>
            </section>

            <section
              v-else-if="section.id === 'certificates' && store.draft.certificates.length"
              class="paper-section"
            >
              <h2>{{ store.sectionTitle(section) }}</h2>
              <article
                v-for="(certificate, index) in store.draft.certificates"
                :key="`certificate-${index}`"
                class="paper-experience"
              >
                <h3
                  class="certificate-name"
                  contenteditable="true"
                  spellcheck="false"
                  data-placeholder="证书名称"
                  @blur="
                    commitText($event, certificate.name, (value) => {
                      store.updateCertificate(index, 'name', value)
                      return value
                    })
                  "
                >{{ certificate.name }}</h3>
                <p
                  v-if="certificate.issuer.trim() || certificate.issued_on"
                  class="certificate-meta"
                >
                  <span
                    v-if="certificate.issuer.trim()"
                    class="certificate-issuer"
                    contenteditable="true"
                    spellcheck="false"
                    @blur="
                      commitText($event, certificate.issuer, (value) => {
                        store.updateCertificate(index, 'issuer', value)
                        return value
                      })
                    "
                  >{{ certificate.issuer }}</span>
                  <span
                    v-if="certificate.issuer.trim() && certificate.issued_on"
                    class="certificate-divider"
                  > ｜ </span>
                  <span
                    v-if="certificate.issued_on"
                    class="certificate-date"
                    contenteditable="true"
                    spellcheck="false"
                    @blur="commitCertificateDate(index, $event)"
                  >{{ displayMonth(certificate.issued_on) }}</span>
                </p>
              </article>
            </section>

            <section
              v-else-if="section.id === 'portfolio' && store.draft.portfolio_links.length"
              class="paper-section"
            >
              <h2>{{ store.sectionTitle(section) }}</h2>
              <article
                v-for="(link, index) in store.draft.portfolio_links"
                :key="`portfolio-${index}`"
                class="paper-experience"
              >
                <h3
                  class="portfolio-label"
                  contenteditable="true"
                  spellcheck="false"
                  data-placeholder="作品名称"
                  @blur="
                    commitText($event, link.label, (value) => {
                      store.updatePortfolioLink(index, 'label', value)
                      return value
                    })
                  "
                >{{ link.label }}</h3>
                <p class="portfolio-meta">
                  <a
                    class="portfolio-url"
                    :href="safeHref(link.url)"
                    target="_blank"
                    rel="noopener noreferrer"
                    contenteditable="true"
                    spellcheck="false"
                    data-placeholder="链接地址"
                    @click.prevent
                    @blur="
                      commitText($event, link.url, (value) => {
                        store.updatePortfolioLink(index, 'url', value)
                        return value
                      })
                    "
                  >{{ link.url }}</a>
                </p>
              </article>
            </section>
          </template>
        </article>

        <!-- 水印：绝对定位在纸张右下角，pointer-events:none 不干扰纸面编辑。 -->
        <span class="paper-watermark" aria-hidden="true">职途AI</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.preview-column { min-width: 0; }
.preview-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; color: var(--ui-ink-muted); font-size: var(--text-sm); }
/* 右侧占满剩余宽度，让「回到实时预览」能撑开（任务 2）。 */
.toolbar-right { display: flex; flex: 1; align-items: center; justify-content: flex-end; gap: 12px; }
.page-count, .date, .code { font-family: "SF Mono", Consolas, monospace; }
.code { color: var(--ui-ink-faint); }
/* 任务 3：状态标识要能一眼看到——16px / 700 / 深墨绿 / 浅底标签。 */
.mode-badge {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--theme-rule-soft);
  border-left: 4px solid var(--theme-primary);
  border-radius: var(--ui-radius);
  padding: 7px 14px;
  color: var(--theme-primary-strong);
  background: var(--theme-bg-light);
  font-size: var(--text-lg-plus);
  font-weight: 700;
  letter-spacing: .02em;
  line-height: 1.25;
  white-space: nowrap;
}
.mode-badge__dot { width: 8px; height: 8px; border-radius: 50%; background: var(--theme-primary); }
.mode-badge--generated { border-color: var(--theme-rule); border-left-color: var(--theme-primary-deep); color: var(--theme-primary-strong); background: var(--theme-bg-light); }
.mode-badge--generated .mode-badge__dot { background: var(--theme-primary-deep); }
/*
 * 唯一保存入口。两种状态刻意做出明显反差：
 * dirty → 低饱和青绿底 + 琥珀色小圆点（「有事没做完」的警示感）；
 * !dirty → 灰底灰字并禁用，文字变「已保存」，让「没有待办」这件事无需点击即可确认。
 */
.toolbar-save { display: flex; flex: 0 0 auto; align-items: center; gap: 10px; }
.save-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 9px 18px;
  color: var(--ui-ink-faint);
  background: var(--ui-surface-sunken);
  cursor: not-allowed;
  font: inherit;
  font-size: var(--text-base);
  font-weight: 600;
  white-space: nowrap;
}
.save-button--dirty {
  border-color: var(--theme-primary);
  color: var(--theme-primary-strong);
  background: var(--theme-bg-light);
  box-shadow: 0 0 0 3px var(--theme-fade);
  cursor: pointer;
}
.save-button--dirty:hover:not(:disabled) { background: var(--theme-bg-strong); }
.save-button--dirty:active:not(:disabled) { background: var(--theme-bg-strong); }
/* 保存中：保留高亮外观（否则会闪一下灰），只把光标换成等待态。 */
.save-button--dirty:disabled { cursor: progress; opacity: .8; }
.save-button:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.save-button__dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ui-warning-border); }
.save-error { max-width: 220px; margin: 0; color: var(--ui-danger); font-size: var(--text-sm); line-height: 1.4; }
/*
 * 任务 2：尺寸对齐 StylePanel 的 .generate-button（padding 11px / font-size 13px），
 * 并靠 flex:1 撑满工具栏右侧剩余空间，视觉重量与「生成简历」同档。
 */
.preview-button {
  display: inline-flex;
  flex: 1 1 auto;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-width: 180px;
  /* 上限避免在宽屏下拉成超宽扁条，同时仍明显大于旧版。 */
  max-width: 280px;
  border: 1px solid var(--theme-rule);
  border-radius: var(--ui-radius);
  padding: 11px 20px;
  color: var(--theme-primary-deep);
  background: var(--theme-bg-light);
  cursor: pointer;
  font: inherit;
  font-size: var(--text-base);
  font-weight: 600;
  white-space: nowrap;
}
.preview-button:hover { border-color: var(--theme-primary); background: var(--theme-bg-hover); }
.preview-button:active { background: var(--theme-bg-strong); }
.preview-button:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.paper-frame { position: relative; }
/*
 * 缩放容器（D5.1 · P2）：
 * - 自己的 width / height 由 `stageStyle` 按「纸张 mm × zoom」内联算出；
 * - `--paper-zoom` / `--paper-width` / `--paper-height` 从这里下发给纸面与水印，
 *   所以纸面只需要 `transform: scale(var(--paper-zoom))`，不需要知道自己被缩了多少。
 */
.paper-stage { position: relative; margin: 0 auto; }
.paper-watermark {
  position: absolute;
  /* 跟着纸面一起缩放：绝对定位以「未缩放的 stage 坐标」为基准，
     但视觉尺寸要乘 zoom，否则纸缩小后水印会显得越来越大。 */
  right: calc(14px * var(--paper-zoom, 1));
  bottom: calc(10px * var(--paper-zoom, 1));
  z-index: 1;
  color: #b0b0b0;
  font-size: calc(10px * var(--paper-zoom, 1));
  line-height: 1;
  letter-spacing: .08em;
  opacity: .5;
  pointer-events: none;
  user-select: none;
}
/*
 * 纸张本身：**按真实物理尺寸排版**（mm），屏幕上的大小只由 `transform` 决定。
 *
 * 用 `min-height` 而不是 `height`：内容超过一页时要让它自然长高，
 * 否则多出来的内容会被裁掉（打印也只有一页）。页数由 `scrollHeight` 量出来。
 */
.paper {
  /*
   * 必须显式声明：`width: 210mm` 只有配上 border-box 才等于「含内边距的纸宽」。
   * 全局 `* { box-sizing: border-box }` 虽然已经设了，但纸张的物理尺寸是页数
   * 计算的基准，不能依赖一条随时可能被改的全局重置（实测 content-box 会把
   * 210mm 变成 210mm + 26mm，页数直接算错）。
   */
  box-sizing: border-box;
  width: var(--paper-width, 210mm);
  min-height: var(--paper-height, 297mm);
  padding: 14mm 13mm;
  transform: scale(var(--paper-zoom, 1));
  transform-origin: top left;
  /*
   * 纸面配色（D5.2）：这 8 个变量的默认值就是 forest，与 D5.1 之前的观感逐像素相同。
   *
   * - 实时预览：父层把 `--paper-accent` / `--paper-rule` 以内联样式覆盖成当前预设；
   * - 已生成简历：生成物自带 `.paper-doc` 内联变量，优先级更高。
   * 两条路径用同一组变量名，所以下面那套规则只需要维护一份。
   */
  --paper-accent: #527873;
  --paper-rule: #6b8f8a;
  --paper-ink: #263330;
  --paper-body: #52635f;
  --paper-muted: #687875;
  --paper-faint: #8fa5a1;
  --paper-line: #b8c8c4;
  --paper-focus: #eaf1ef;
  color: var(--paper-ink);
  background: #fff;
  box-shadow: 0 14px 36px #2633301c;
}

/* 窄屏：按钮变宽后单行放不下，工具栏改为两行，按钮独占整行（仍满足「撑满」）。 */
@media (max-width: 720px) {
  .preview-toolbar { flex-wrap: wrap; row-gap: 10px; }
  /* 窄屏一行放不下三组，让保存按钮与状态徽标各占一半，仍保持「一眼可见」。 */
  .mode-badge { flex: 1 1 auto; justify-content: center; }
  .toolbar-save { flex: 1 1 100%; }
  .save-button { flex: 1 1 auto; justify-content: center; }
  .save-error { flex: 1 1 100%; max-width: none; text-align: center; }
  .toolbar-right { flex: 1 0 100%; justify-content: space-between; }
  .preview-button { flex: 1 1 100%; max-width: none; }
}

@media print {
  .preview-toolbar { display: none; }
  /*
   * 打印必须**取消缩放**：stage 上的 width/height 是屏幕像素（内联样式），
   * 不覆盖就会把整页塞进一个固定像素盒子里。`!important` 是覆盖内联值的唯一办法。
   */
  .paper-stage { width: auto !important; height: auto !important; margin: 0; }
  .paper {
    width: var(--paper-width);
    /* 让内容按页流下去：固定 min-height 会强制第一页留白、后续页错位。 */
    min-height: 0;
    transform: none;
    box-shadow: none;
    margin: 0;
  }
  /*
   * 水印必须打印出来：显式给色并关掉浏览器的省墨降色（print-color-adjust），
   * 否则淡灰在部分浏览器会被优化成不可见。
   */
  .paper-watermark {
    right: 6mm;
    bottom: 4mm;
    color: #b0b0b0;
    font-size: 10px;
    opacity: .5;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
}
</style>

<!--
  纸面文档样式：**不加 scoped**。
  实时预览的子节点带 scoped 属性，后端 v-html 出来的不一样，
  只有全局选择器能让两者共用一套规则——这正是「所见即所得」的实现方式。
  所有选择器都以 `.paper` 开头，不会污染页面其他区域。
-->
<style>
.paper .paper-header { border-bottom: 2px solid var(--paper-rule); padding-bottom: 24px; }
.paper .paper-header h1,
.paper .resume-name { margin: 0 0 8px; font-size: 32px; font-weight: 700; line-height: 1.2; }
.paper .contact { margin: 4px 0 0; color: var(--paper-body); font-family: "SF Mono", Consolas, monospace; font-size: 12px; }
.paper .contact-separator { padding: 0 6px; color: var(--paper-faint); }
.paper .paper-section { margin-top: 28px; }
.paper .paper-section > h2 { margin: 0 0 14px; color: var(--paper-accent); font-size: 13px; font-weight: 650; letter-spacing: .14em; }
.paper .skill-list { display: flex; flex-wrap: wrap; gap: 10px 18px; }
.paper .skill-item { display: inline-flex; align-items: baseline; gap: 6px; border-bottom: 1px solid var(--paper-line); padding-bottom: 3px; font-size: 13px; }
.paper .paper-experience { margin-bottom: 20px; }
.paper .paper-experience:last-child { margin-bottom: 0; }
.paper .education-school,
.paper .experience-title { margin: 0; font-size: 15px; font-weight: 650; }
/*
 * 证书 / 作品（B4）：h3 的字号只对教育、项目两格定过，这两格不补会掉回浏览器默认的
 * 大号 h3 + 外边距，同一张纸上两种标题大小。分隔符与网址的色值也在这里给，
 * 否则网址会变成默认的蓝色下划线链接，和整页的墨绿配色打架。
 */
.paper .certificate-name,
.paper .portfolio-label { margin: 0; font-size: 15px; font-weight: 650; }
.paper .certificate-meta,
.paper .portfolio-meta { margin: 6px 0 0; color: var(--paper-body); font-size: 12px; line-height: 1.6; }
.paper .certificate-divider { color: var(--paper-faint); }
/* 长网址在窄纸上必须能折行，否则一条 URL 会把整页撑出横向滚动条。 */
.paper .portfolio-url { color: var(--paper-accent); text-decoration: none; word-break: break-all; }
.paper .education-meta { margin: 6px 0 0; color: var(--paper-body); font-size: 12px; line-height: 1.6; }
.paper .experience-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.paper .date { color: var(--paper-muted); font-family: "SF Mono", Consolas, monospace; font-size: 11px; white-space: nowrap; }
/* 时间拆成三段可编辑节点后，分隔符要淡一点，避免和数字抢视线。 */
.paper .date-divider { color: var(--paper-faint); }
.paper .paper-experience p { margin: 6px 0 0; color: var(--paper-body); font-size: 12px; line-height: 1.6; }
/* 失焦回写后文本节点被重绘，靠 pre-wrap 保住用户敲的换行。 */
.paper [contenteditable] { outline: 0; }
.paper [contenteditable]:focus { background: var(--paper-focus); }
.paper .experience-description[contenteditable],
.paper .experience-responsibility[contenteditable] { white-space: pre-wrap; }
/* 空单元格用灰色占位，不用把占位文字写进 value（否则会被人当成内容保存下来）。 */
.paper [contenteditable]:empty::before {
  content: attr(data-placeholder);
  color: var(--paper-line);
  pointer-events: none;
}
</style>
