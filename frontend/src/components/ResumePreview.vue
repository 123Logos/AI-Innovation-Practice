<script setup lang="ts">
import { storeToRefs } from 'pinia'

import { useResumeStore } from '@/stores/resume'

const store = useResumeStore()
const { resume } = storeToRefs(store)

function updateBlock(event: Event, update: (value: string) => void) {
  update((event.currentTarget as HTMLElement).innerText)
}
</script>

<template>
  <section class="preview-column">
    <div class="preview-toolbar">
      <span>实时预览</span>
      <span class="page-count">A4 · 1 页</span>
    </div>
    <article class="paper" aria-label="简历预览">
      <header class="paper-header">
        <h1
          contenteditable="true"
          spellcheck="false"
          @input="updateBlock($event, (value) => store.updateProfile('name', value))"
        >{{ resume.profile.name }}</h1>
        <p
          contenteditable="true"
          spellcheck="false"
          @input="updateBlock($event, (value) => store.updateProfile('major', value))"
        >{{ resume.profile.major }} · {{ resume.profile.grade }}</p>
        <p
          class="contact"
          contenteditable="true"
          spellcheck="false"
          @input="updateBlock($event, (value) => store.updateProfile('contact', value))"
        >{{ resume.profile.contact }}</p>
      </header>

      <section class="paper-section">
        <h2>技能</h2>
        <div class="skill-list">
          <span
            v-for="(skill, index) in resume.skills"
            :key="index"
            contenteditable="true"
            spellcheck="false"
            @input="updateBlock($event, (value) => store.updateSkill(index, value))"
          >{{ skill }}</span>
        </div>
      </section>

      <section class="paper-section">
        <h2>项目经历</h2>
        <article v-for="(experience, index) in resume.experiences" :key="index" class="paper-experience">
          <div class="experience-heading">
            <h3
              contenteditable="true"
              spellcheck="false"
              @input="updateBlock($event, (value) => store.updateExperience(index, 'title', value))"
            >{{ experience.title }}</h3>
            <span class="date">{{ experience.start }} — {{ experience.end || '至今' }}</span>
          </div>
          <p
            contenteditable="true"
            spellcheck="false"
            @input="updateBlock($event, (value) => store.updateExperience(index, 'description', value))"
          >{{ experience.description }}</p>
          <p
            contenteditable="true"
            spellcheck="false"
            @input="updateBlock($event, (value) => store.updateExperience(index, 'duty', value))"
          >{{ experience.duty }}</p>
        </article>
      </section>
    </article>
  </section>
</template>

<style scoped>
.preview-column { min-width: 0; }
.preview-toolbar { display: flex; justify-content: space-between; margin-bottom: 12px; color: #687875; font-size: 12px; }
.page-count, .date { font-family: "SF Mono", Consolas, monospace; }
.paper { aspect-ratio: 210 / 297; min-height: 720px; overflow: auto; padding: 56px 52px; color: #263330; background: #fff; box-shadow: 0 14px 36px #2633301c; }
.paper-header { border-bottom: 2px solid #6b8f8a; padding-bottom: 24px; }
.paper-header h1 { margin: 0 0 8px; font-size: 32px; font-weight: 700; }
.paper-header p { margin: 4px 0; color: #52635f; font-size: 14px; }
.paper-header .contact { font-family: "SF Mono", Consolas, monospace; font-size: 12px; }
.paper-section { margin-top: 28px; }
.paper-section h2 { margin: 0 0 14px; color: #527873; font-size: 13px; letter-spacing: .14em; }
.skill-list { display: flex; flex-wrap: wrap; gap: 8px 18px; }
.skill-list span { border-bottom: 1px solid #b8c8c4; padding-bottom: 3px; font-size: 13px; }
.paper-experience { margin-bottom: 20px; }
.experience-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.experience-heading h3 { margin: 0; font-size: 15px; }
.date { color: #687875; font-size: 11px; white-space: nowrap; }
.paper-experience p { margin: 6px 0 0; color: #52635f; font-size: 12px; line-height: 1.6; }
[contenteditable="true"] { outline: 0; }
[contenteditable="true"]:focus { background: #eaf1ef; }
@media (prefers-color-scheme: dark) {
  .paper { color: #263330; background: #fff; }
  .paper-header p, .paper-experience p, .date { color: #52635f; }
}
@media print {
  .preview-toolbar { display: none; }
  .paper { min-height: 0; box-shadow: none; margin: 0; }
}
</style>
