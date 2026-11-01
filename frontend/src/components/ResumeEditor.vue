<script setup lang="ts">
import { storeToRefs } from 'pinia'

import { useResumeStore } from '@/stores/resume'

const store = useResumeStore()
const { resume } = storeToRefs(store)

function updateProfile(field: 'name' | 'major' | 'grade' | 'contact', event: Event) {
  store.updateProfile(field, (event.target as HTMLInputElement).value)
}

function updateExperience(index: number, field: 'title' | 'start' | 'end', event: Event) {
  store.updateExperience(index, field, (event.target as HTMLInputElement).value)
}

function updateExperienceText(index: number, field: 'description' | 'duty', event: Event) {
  store.updateExperience(index, field, (event.target as HTMLTextAreaElement).value)
}
</script>

<template>
  <aside class="editor-panel">
    <div class="panel-heading">
      <p class="eyebrow">简历编辑</p>
      <h1>完善你的简历</h1>
      <p class="panel-intro">填写左侧信息，中央预览会同步更新。</p>
    </div>

    <section class="form-section">
      <h2>基本信息</h2>
      <label class="field">
        <span>姓名</span>
        <input :value="resume.profile.name" @input="updateProfile('name', $event)" />
      </label>
      <label class="field">
        <span>专业</span>
        <input :value="resume.profile.major" @input="updateProfile('major', $event)" />
      </label>
      <div class="field-row">
        <label class="field">
          <span>年级</span>
          <input :value="resume.profile.grade" @input="updateProfile('grade', $event)" />
        </label>
        <label class="field">
          <span>联系方式</span>
          <input :value="resume.profile.contact" @input="updateProfile('contact', $event)" />
        </label>
      </div>
    </section>

    <section class="form-section">
      <div class="section-title">
        <h2>技能</h2>
        <button class="text-button" type="button" @click="store.addSkill">新增</button>
      </div>
      <div v-for="(skill, index) in resume.skills" :key="index" class="skill-row">
        <input
          :value="skill"
          :aria-label="`技能 ${index + 1}`"
          @input="store.updateSkill(index, ($event.target as HTMLInputElement).value)"
        />
        <button
          class="remove-button"
          type="button"
          :aria-label="`删除技能 ${index + 1}`"
          @click="store.removeSkill(index)"
        >
          ×
        </button>
      </div>
    </section>

    <section class="form-section">
      <div class="section-title">
        <h2>项目经历</h2>
        <button class="text-button" type="button" @click="store.addExperience">新增</button>
      </div>
      <article v-for="(experience, index) in resume.experiences" :key="index" class="experience-form">
        <div class="section-title">
          <span class="item-index">0{{ index + 1 }}</span>
          <button class="remove-button" type="button" @click="store.removeExperience(index)">×</button>
        </div>
        <label class="field">
          <span>项目名称</span>
          <input :value="experience.title" @input="updateExperience(index, 'title', $event)" />
        </label>
        <div class="field-row">
          <label class="field">
            <span>开始</span>
            <input :value="experience.start" @input="updateExperience(index, 'start', $event)" />
          </label>
          <label class="field">
            <span>结束</span>
            <input :value="experience.end" @input="updateExperience(index, 'end', $event)" />
          </label>
        </div>
        <label class="field">
          <span>项目描述</span>
          <textarea :value="experience.description" @input="updateExperienceText(index, 'description', $event)" />
        </label>
        <label class="field">
          <span>个人职责</span>
          <textarea :value="experience.duty" @input="updateExperienceText(index, 'duty', $event)" />
        </label>
      </article>
    </section>
  </aside>
</template>

<style scoped>
.editor-panel { min-width: 0; }
.panel-heading { margin-bottom: 20px; }
.eyebrow { margin: 0 0 8px; color: #6b8f8a; font-size: 12px; letter-spacing: .12em; }
h1, h2, p { margin: 0; }
h1 { font-size: 24px; font-weight: 650; }
.panel-intro { margin-top: 8px; color: #687875; font-size: 13px; line-height: 1.5; }
.form-section { display: grid; gap: 10px; margin-bottom: 20px; }
.form-section h2 { font-size: 13px; font-weight: 650; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.field { display: grid; gap: 6px; min-width: 0; }
.field span { color: #687875; font-size: 12px; }
input, textarea { width: 100%; border: 1px solid #d6dfdc; border-radius: 0; padding: 9px 10px; color: #243330; background: #fff; font: inherit; font-size: 13px; outline: 0; }
input:focus, textarea:focus { border-color: #6b8f8a; box-shadow: 0 0 0 2px #6b8f8a22; }
textarea { min-height: 58px; resize: vertical; line-height: 1.45; }
.field-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.text-button { border: 0; padding: 0; color: #527873; background: transparent; cursor: pointer; font-size: 12px; }
.skill-row { display: flex; gap: 8px; }
.remove-button { border: 0; padding: 0 4px; color: #9a5e58; background: transparent; cursor: pointer; font-size: 18px; line-height: 1; }
.experience-form { display: grid; gap: 10px; border-top: 1px solid #dde5e2; padding-top: 12px; }
.item-index { color: #6b8f8a; font-family: "SF Mono", Consolas, monospace; font-size: 12px; }
@media (prefers-color-scheme: dark) {
  .editor-panel { color: #e8eae9; }
  .panel-intro, .field span { color: #b7c3c0; }
  input, textarea { border-color: #52635f; color: #e8eae9; background: #263330; }
}
</style>
