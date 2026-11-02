<script setup lang="ts">
import { computed } from 'vue'

import { useResumeStore } from '@/stores/resume'
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
      <div class="toolbar-right">
        <span v-if="generatedResumeId" class="code">#{{ generatedResumeId }}</span>
        <span class="page-count">A4 · 1 页</span>
        <button
          v-if="store.isGeneratingResultVisible"
          class="preview-button"
          type="button"
          @click="store.showLivePreview"
        >
          <span class="preview-button__icon" aria-hidden="true">↺</span>
          回到实时预览
        </button>
      </div>
    </div>

    <div class="paper-frame">
      <article
        v-if="store.isGeneratingResultVisible"
        class="paper paper--generated"
        aria-label="已生成的简历"
        v-html="generatedBody"
      />

      <article v-else class="paper" aria-label="简历预览">
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

        <section v-if="displayEducations.length" class="paper-section">
          <h2>教育经历</h2>
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

        <section class="paper-section">
          <h2>技能</h2>
          <div class="skill-list">
            <span v-for="(skill, index) in store.draft.skills" :key="index" class="skill-item">
              <span
                contenteditable="true"
                spellcheck="false"
                data-placeholder="技能"
                @blur="
                  commitText($event, skill.name, (value) => {
                    store.updateSkill(index, 'name', value)
                    return value
                  })
                "
              >{{ skill.name }}</span>
            </span>
          </div>
        </section>

        <section class="paper-section">
          <h2>项目经历</h2>
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
      </article>

      <!-- 水印：绝对定位在纸张右下角，pointer-events:none 不干扰纸面编辑。 -->
      <span class="paper-watermark" aria-hidden="true">职途AI</span>
    </div>
  </section>
</template>

<style scoped>
.preview-column { min-width: 0; }
.preview-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; color: #687875; font-size: 12px; }
/* 右侧占满剩余宽度，让「回到实时预览」能撑开（任务 2）。 */
.toolbar-right { display: flex; flex: 1; align-items: center; justify-content: flex-end; gap: 12px; }
.page-count, .date, .code { font-family: "SF Mono", Consolas, monospace; }
.code { color: #a9b6b3; }
/* 任务 3：状态标识要能一眼看到——16px / 700 / 深墨绿 / 浅底标签。 */
.mode-badge {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  border: 1px solid #9fbab4;
  border-left: 4px solid #527873;
  padding: 7px 14px;
  color: #2f4f4a;
  background: #edf4f2;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: .02em;
  line-height: 1.25;
  white-space: nowrap;
}
.mode-badge__dot { width: 8px; height: 8px; border-radius: 50%; background: #527873; }
.mode-badge--generated { border-color: #6b8f8a; border-left-color: #3f635e; color: #26433e; background: #dceae6; }
.mode-badge--generated .mode-badge__dot { background: #3f635e; }
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
  border: 1px solid #6b8f8a;
  border-radius: 0;
  padding: 11px 20px;
  color: #3f635e;
  background: #e8f0ee;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}
.preview-button:hover { border-color: #527873; background: #d5e5e1; }
.preview-button:active { background: #c6dbd6; }
.preview-button:focus-visible { outline: 2px solid #6b8f8a; outline-offset: 2px; }
.preview-button__icon { font-size: 16px; line-height: 1; }
.paper-frame { position: relative; }
.paper-watermark {
  position: absolute;
  right: 14px;
  bottom: 10px;
  z-index: 1;
  color: #b0b0b0;
  font-size: 10px;
  line-height: 1;
  letter-spacing: .08em;
  opacity: .5;
  pointer-events: none;
  user-select: none;
}
/* 纸张本身：实时预览与生成结果都用 `.paper`，所以放在 scoped 里没问题。 */
.paper { aspect-ratio: 210 / 297; min-height: 720px; overflow: auto; padding: 56px 52px; color: #263330; background: #fff; box-shadow: 0 14px 36px #2633301c; }

/* 窄屏：按钮变宽后单行放不下，工具栏改为两行，按钮独占整行（仍满足「撑满」）。 */
@media (max-width: 720px) {
  .preview-toolbar { flex-wrap: wrap; row-gap: 10px; }
  .toolbar-right { flex: 1 0 100%; justify-content: space-between; }
  .preview-button { flex: 1 1 100%; max-width: none; }
}

@media print {
  .preview-toolbar { display: none; }
  .paper { min-height: 0; box-shadow: none; margin: 0; }
  /*
   * 水印必须打印出来：显式给色并关掉浏览器的省墨降色（print-color-adjust），
   * 否则淡灰在部分浏览器会被优化成不可见。
   */
  .paper-watermark {
    right: 6mm;
    bottom: 4mm;
    color: #b0b0b0;
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
.paper .paper-header { border-bottom: 2px solid #6b8f8a; padding-bottom: 24px; }
.paper .paper-header h1,
.paper .resume-name { margin: 0 0 8px; font-size: 32px; font-weight: 700; line-height: 1.2; }
.paper .contact { margin: 4px 0 0; color: #52635f; font-family: "SF Mono", Consolas, monospace; font-size: 12px; }
.paper .contact-separator { padding: 0 6px; color: #8fa5a1; }
.paper .paper-section { margin-top: 28px; }
.paper .paper-section > h2 { margin: 0 0 14px; color: #527873; font-size: 13px; font-weight: 650; letter-spacing: .14em; }
.paper .skill-list { display: flex; flex-wrap: wrap; gap: 10px 18px; }
.paper .skill-item { display: inline-flex; align-items: baseline; gap: 6px; border-bottom: 1px solid #b8c8c4; padding-bottom: 3px; font-size: 13px; }
.paper .paper-experience { margin-bottom: 20px; }
.paper .paper-experience:last-child { margin-bottom: 0; }
.paper .education-school,
.paper .experience-title { margin: 0; font-size: 15px; font-weight: 650; }
.paper .education-meta { margin: 6px 0 0; color: #52635f; font-size: 12px; line-height: 1.6; }
.paper .experience-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.paper .date { color: #687875; font-family: "SF Mono", Consolas, monospace; font-size: 11px; white-space: nowrap; }
/* 时间拆成三段可编辑节点后，分隔符要淡一点，避免和数字抢视线。 */
.paper .date-divider { color: #8fa5a1; }
.paper .paper-experience p { margin: 6px 0 0; color: #52635f; font-size: 12px; line-height: 1.6; }
/* 失焦回写后文本节点被重绘，靠 pre-wrap 保住用户敲的换行。 */
.paper [contenteditable] { outline: 0; }
.paper [contenteditable]:focus { background: #eaf1ef; }
.paper .experience-description[contenteditable],
.paper .experience-responsibility[contenteditable] { white-space: pre-wrap; }
/* 空单元格用灰色占位，不用把占位文字写进 value（否则会被人当成内容保存下来）。 */
.paper [contenteditable]:empty::before {
  content: attr(data-placeholder);
  color: #b8c8c4;
  pointer-events: none;
}
</style>
