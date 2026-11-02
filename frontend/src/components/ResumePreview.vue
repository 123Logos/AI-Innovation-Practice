<script setup lang="ts">
import { computed } from 'vue'

import { useResumeStore } from '@/stores/resume'

const store = useResumeStore()

/**
 * 后端 `TemplateResumeGenerator` 返回的是完整 HTML 文档（含 `<html><head>`），
 * 直接塞进纸面会带上不需要的文档级标签，所以只取 `<body>` 内部内容。
 *
 * 这里只做「取 body」，不再对文本做任何改写：
 * 技能熟练度已从后端模板中彻底移除，落库的 HTML 本身是干净的，
 * 前端若再剥离一次，反而会掩盖后端回归问题。
 */
const generatedBody = computed(() => {
  const html = store.generatedHtml
  if (!html) return ''
  return new DOMParser().parseFromString(html, 'text/html').body.innerHTML
})

const generatedResumeId = computed(() => store.generatedResumeId?.slice(0, 8) ?? '')

/** A4 纸上的月份展示：草稿存 `YYYY-MM`，与后端生成器的 `2021.09` 保持一致。 */
function toDisplayMonth(value: string): string {
  if (!value) return ''
  return value.slice(0, 7).replace('-', '.')
}

/**
 * 教育经历按入学时间倒序（最近一段在前），与后端生成器 `sorted(..., reverse=True)` 同序，
 * 保证「实时预览」和「已生成简历」两套渲染的视觉顺序一致。
 */
const sortedEducations = computed(() =>
  [...store.draft.educations].sort((left, right) => right.edu_start.localeCompare(left.edu_start)),
)

/**
 * 纸面编辑只在**失焦时**回写 store。
 *
 * 若在 `@input` 里回写，Pinia 一变、Vue 就重渲染这段文本节点，
 * 浏览器会丢失原生 Selection，光标被强制归零（表现为「四张三」）。
 * 改用 `@blur` 后，编辑过程中 store 不变、DOM 不被 patch，光标完全交给浏览器管。
 *
 * 副作用可接受：左侧表单要等失焦后才同步。点击「保存档案」「生成简历」
 * 会先触发 blur，所以提交时读到的一定是最新内容。
 */
function onBlockCommit(event: Event, apply: (value: string) => void): void {
  const element = event.currentTarget as HTMLElement | null
  if (!element) return
  apply(element.innerText)
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
          contenteditable="true"
          spellcheck="false"
          @blur="onBlockCommit($event, (value) => store.updateProfileField('full_name', value))"
        >{{ store.draft.full_name || '姓名' }}</h1>
        <p class="subtitle">
          <span
            contenteditable="true"
            spellcheck="false"
            @blur="onBlockCommit($event, (value) => store.updateProfileField('major', value))"
          >{{ store.draft.major || '专业' }}</span>
        </p>
        <p
          class="contact"
          contenteditable="true"
          spellcheck="false"
          @blur="onBlockCommit($event, (value) => store.updateProfileField('contact', value))"
        >{{ store.draft.contact || '联系方式' }}</p>
      </header>

      <section v-if="sortedEducations.length" class="paper-section">
        <h2>教育经历</h2>
        <article
          v-for="(education, index) in sortedEducations"
          :key="`edu-${index}`"
          class="paper-experience"
        >
          <h3 class="education-school">{{ education.school || '学校名称' }}</h3>
          <p class="education-meta">
            {{ education.major || '专业' }} ｜ {{ education.degree }} ｜
            {{ toDisplayMonth(education.edu_start) || '入学时间' }} —
            {{ toDisplayMonth(education.edu_end) || '至今' }}
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
              @blur="onBlockCommit($event, (value) => store.updateSkill(index, 'name', value))"
            >{{ skill.name || '技能' }}</span>
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
              contenteditable="true"
              spellcheck="false"
              @blur="onBlockCommit($event, (value) => store.updateExperience(index, 'title', value))"
            >{{ experience.title || '项目名称' }}</h3>
            <span class="date">
              {{ experience.start_date || '开始' }} — {{ experience.end_date || '至今' }}
            </span>
          </div>
          <p
            contenteditable="true"
            spellcheck="false"
            @blur="onBlockCommit($event, (value) => store.updateExperience(index, 'description', value))"
          >{{ experience.description }}</p>
          <p
            contenteditable="true"
            spellcheck="false"
            @blur="onBlockCommit($event, (value) => store.updateExperience(index, 'responsibility', value))"
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
.paper { aspect-ratio: 210 / 297; min-height: 720px; overflow: auto; padding: 56px 52px; color: #263330; background: #fff; box-shadow: 0 14px 36px #2633301c; }
.paper-header { border-bottom: 2px solid #6b8f8a; padding-bottom: 24px; }
.paper-header h1 { margin: 0 0 8px; font-size: 32px; font-weight: 700; }
.subtitle { margin: 4px 0; color: #52635f; font-size: 14px; }
.paper-header .contact { margin: 4px 0; color: #52635f; font-family: "SF Mono", Consolas, monospace; font-size: 12px; }
.paper-section { margin-top: 28px; }
.paper-section h2 { margin: 0 0 14px; color: #527873; font-size: 13px; letter-spacing: .14em; }
.skill-list { display: flex; flex-wrap: wrap; gap: 10px 18px; }
.skill-item { display: inline-flex; align-items: baseline; gap: 6px; border-bottom: 1px solid #b8c8c4; padding-bottom: 3px; font-size: 13px; }
.paper-experience { margin-bottom: 20px; }
/* 教育区块：学校做标题，专业/学历/起止同一行，与后端生成器的 HTML 结构对齐。 */
.education-school { margin: 0; font-size: 15px; }
.experience-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.experience-heading h3 { margin: 0; font-size: 15px; }
.date { color: #687875; font-size: 11px; white-space: nowrap; }
.paper-experience p { margin: 6px 0 0; color: #52635f; font-size: 12px; line-height: 1.6; }
/* 失焦回写后文本节点被重绘，靠 pre-wrap 保住用户敲的换行。 */
.paper-experience p[contenteditable] { white-space: pre-wrap; }
[contenteditable="true"] { outline: 0; }
[contenteditable="true"]:focus { background: #eaf1ef; }

/* 后端模板产出的 HTML 没有 scoped 属性，必须用 :deep() 才能命中。 */
.paper--generated :deep(h1) { margin: 0 0 12px; border-bottom: 2px solid #6b8f8a; padding-bottom: 20px; font-size: 30px; font-weight: 700; }
.paper--generated :deep(section) { margin-top: 28px; }
.paper--generated :deep(section > h2) { margin: 0 0 14px; color: #527873; font-size: 13px; letter-spacing: .14em; }
.paper--generated :deep(section > p) { margin: 0; color: #52635f; font-size: 13px; line-height: 1.6; }
.paper--generated :deep(ul) { display: flex; flex-wrap: wrap; gap: 10px 18px; margin: 0; padding: 0; list-style: none; }
.paper--generated :deep(li) { border-bottom: 1px solid #b8c8c4; padding-bottom: 3px; font-size: 13px; }
.paper--generated :deep(article) { margin-bottom: 20px; }
.paper--generated :deep(article h3) { margin: 0; font-size: 15px; }
.paper--generated :deep(article p) { margin: 6px 0 0; color: #52635f; font-size: 12px; line-height: 1.6; }

/* 窄屏：按钮变宽后单行放不下，工具栏改为两行，按钮独占整行（仍满足「撑满」）。 */
@media (max-width: 720px) {
  .preview-toolbar { flex-wrap: wrap; row-gap: 10px; }
  .toolbar-right { flex: 1 0 100%; justify-content: space-between; }
  .preview-button { flex: 1 1 100%; max-width: none; }
}

@media (prefers-color-scheme: dark) {
  .paper { color: #263330; background: #fff; }
  .subtitle, .paper-experience p, .date, .paper-header .contact { color: #52635f; }
  .mode-badge { border-color: #527873; border-left-color: #8fb3ab; color: #e4eeec; background: #2f4340; }
  .mode-badge__dot { background: #8fb3ab; }
  .mode-badge--generated { border-color: #6b8f8a; border-left-color: #d7e5e1; color: #f0f6f4; background: #3d5b56; }
  .mode-badge--generated .mode-badge__dot { background: #d7e5e1; }
  .preview-button { border-color: #6b8f8a; color: #eaf1ef; background: #3d5b56; }
  .preview-button:hover { background: #4a6c66; }
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
