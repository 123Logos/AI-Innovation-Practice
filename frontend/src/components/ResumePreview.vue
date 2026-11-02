<script setup lang="ts">
import { computed } from 'vue'

import { useResumeStore } from '@/stores/resume'

const store = useResumeStore()

/**
 * 后端 `TemplateResumeGenerator` 返回的是完整 HTML 文档（含 `<html><head>`），
 * 直接塞进纸面会带上不需要的文档级标签，所以只取 `<body>` 内部内容。
 */
const generatedBody = computed(() => {
  const html = store.generatedHtml
  if (!html) return ''
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  return parsed.body.innerHTML
})

const generatedResumeId = computed(() => store.generatedResumeId?.slice(0, 8) ?? '')

function onBlockInput(event: Event, apply: (value: string) => void): void {
  apply((event.currentTarget as HTMLElement).innerText)
}
</script>

<template>
  <section class="preview-column">
    <div class="preview-toolbar">
      <span>{{ store.isGeneratingResultVisible ? '已生成简历' : '实时预览' }}</span>
      <div class="toolbar-right">
        <span v-if="generatedResumeId" class="code">#{{ generatedResumeId }}</span>
        <span class="page-count">A4 · 1 页</span>
        <button
          v-if="store.isGeneratingResultVisible"
          class="text-button"
          type="button"
          @click="store.showLivePreview"
        >
          回到实时预览
        </button>
      </div>
    </div>

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
          @input="onBlockInput($event, (value) => store.updateProfileField('full_name', value))"
        >{{ store.draft.full_name || '姓名' }}</h1>
        <p class="subtitle">
          <span
            contenteditable="true"
            spellcheck="false"
            @input="onBlockInput($event, (value) => store.updateProfileField('major', value))"
          >{{ store.draft.major || '专业' }}</span>
          <span class="divider">·</span>
          <span
            contenteditable="true"
            spellcheck="false"
            @input="onBlockInput($event, (value) => store.updateProfileField('grade', value))"
          >{{ store.draft.grade || '年级' }}</span>
        </p>
        <p
          class="contact"
          contenteditable="true"
          spellcheck="false"
          @input="onBlockInput($event, (value) => store.updateProfileField('contact', value))"
        >{{ store.draft.contact || '联系方式' }}</p>
      </header>

      <section class="paper-section">
        <h2>技能</h2>
        <div class="skill-list">
          <span v-for="(skill, index) in store.draft.skills" :key="index" class="skill-item">
            <span
              contenteditable="true"
              spellcheck="false"
              @input="onBlockInput($event, (value) => store.updateSkill(index, 'name', value))"
            >{{ skill.name || '技能' }}</span>
            <em class="rating-badge">{{ skill.self_rating }}/5</em>
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
              @input="onBlockInput($event, (value) => store.updateExperience(index, 'title', value))"
            >{{ experience.title || '项目名称' }}</h3>
            <span class="date">
              {{ experience.start_date || '开始' }} — {{ experience.end_date || '至今' }}
            </span>
          </div>
          <p
            contenteditable="true"
            spellcheck="false"
            @input="onBlockInput($event, (value) => store.updateExperience(index, 'description', value))"
          >{{ experience.description }}</p>
          <p
            contenteditable="true"
            spellcheck="false"
            @input="onBlockInput($event, (value) => store.updateExperience(index, 'responsibility', value))"
          >{{ experience.responsibility }}</p>
        </article>
      </section>
    </article>
  </section>
</template>

<style scoped>
.preview-column { min-width: 0; }
.preview-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; color: #687875; font-size: 12px; }
.toolbar-right { display: flex; align-items: center; gap: 12px; }
.page-count, .date, .code { font-family: "SF Mono", Consolas, monospace; }
.code { color: #a9b6b3; }
.text-button { border: 0; padding: 0; color: #527873; background: transparent; cursor: pointer; font-size: 12px; }
.paper { aspect-ratio: 210 / 297; min-height: 720px; overflow: auto; padding: 56px 52px; color: #263330; background: #fff; box-shadow: 0 14px 36px #2633301c; }
.paper-header { border-bottom: 2px solid #6b8f8a; padding-bottom: 24px; }
.paper-header h1 { margin: 0 0 8px; font-size: 32px; font-weight: 700; }
.subtitle { margin: 4px 0; color: #52635f; font-size: 14px; }
.divider { padding: 0 4px; }
.paper-header .contact { margin: 4px 0; color: #52635f; font-family: "SF Mono", Consolas, monospace; font-size: 12px; }
.paper-section { margin-top: 28px; }
.paper-section h2 { margin: 0 0 14px; color: #527873; font-size: 13px; letter-spacing: .14em; }
.skill-list { display: flex; flex-wrap: wrap; gap: 10px 18px; }
.skill-item { display: inline-flex; align-items: baseline; gap: 6px; border-bottom: 1px solid #b8c8c4; padding-bottom: 3px; font-size: 13px; }
.rating-badge { color: #879692; font-size: 10px; font-style: normal; font-family: "SF Mono", Consolas, monospace; }
.paper-experience { margin-bottom: 20px; }
.experience-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.experience-heading h3 { margin: 0; font-size: 15px; }
.date { color: #687875; font-size: 11px; white-space: nowrap; }
.paper-experience p { margin: 6px 0 0; color: #52635f; font-size: 12px; line-height: 1.6; }
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

@media (prefers-color-scheme: dark) {
  .paper { color: #263330; background: #fff; }
  .subtitle, .paper-experience p, .date, .paper-header .contact { color: #52635f; }
}
@media print {
  .preview-toolbar { display: none; }
  .paper { min-height: 0; box-shadow: none; margin: 0; }
}
</style>
