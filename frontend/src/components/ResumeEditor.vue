<script setup lang="ts">
import { ref } from 'vue'

import { isApiError } from '@/api'
import { EXPERIENCE_LIMIT, SKILL_LIMIT, useResumeStore } from '@/stores/resume'

const store = useResumeStore()

const feedback = ref<{ tone: 'ok' | 'error'; text: string } | null>(null)

type ProfileField = 'full_name' | 'major' | 'grade' | 'contact'

function onProfileInput(field: ProfileField, event: Event): void {
  store.updateProfileField(field, (event.target as HTMLInputElement).value)
}

function onSkillName(index: number, event: Event): void {
  store.updateSkill(index, 'name', (event.target as HTMLInputElement).value)
}

function onSkillRating(index: number, event: Event): void {
  store.updateSkill(index, 'self_rating', Number((event.target as HTMLSelectElement).value))
}

function onExperienceInput(
  index: number,
  field: 'title' | 'start_date' | 'end_date' | 'description' | 'responsibility',
  event: Event,
): void {
  store.updateExperience(index, field, (event.target as HTMLInputElement | HTMLTextAreaElement).value)
}

async function handleSave(): Promise<void> {
  feedback.value = null
  const invalid = store.validate()
  if (invalid) {
    feedback.value = { tone: 'error', text: invalid }
    return
  }
  try {
    await store.save()
    feedback.value = { tone: 'ok', text: '档案已保存到数据库。' }
  } catch (error) {
    feedback.value = {
      tone: 'error',
      text: isApiError(error) ? error.message : '保存失败，请稍后重试。',
    }
  }
}
</script>

<template>
  <aside class="editor-panel">
    <div class="panel-heading">
      <p class="eyebrow">简历编辑</p>
      <h1>完善你的简历</h1>
      <p class="panel-intro">填写后点击「保存档案」落库，中央预览会实时同步。</p>
    </div>

    <section class="form-section">
      <h2>基本信息</h2>
      <label class="field">
        <span>姓名</span>
        <input :value="store.draft.full_name" @input="onProfileInput('full_name', $event)" />
      </label>
      <label class="field">
        <span>专业</span>
        <input :value="store.draft.major" @input="onProfileInput('major', $event)" />
      </label>
      <div class="field-row">
        <label class="field">
          <span>年级</span>
          <input :value="store.draft.grade" @input="onProfileInput('grade', $event)" />
        </label>
        <label class="field">
          <span>联系方式</span>
          <input :value="store.draft.contact" @input="onProfileInput('contact', $event)" />
        </label>
      </div>
    </section>

    <section class="form-section">
      <div class="section-title">
        <h2>技能</h2>
        <span class="counter">{{ store.draft.skills.length }} / {{ SKILL_LIMIT.max }}</span>
      </div>
      <div v-for="(skill, index) in store.draft.skills" :key="index" class="skill-row">
        <input
          :value="skill.name"
          :aria-label="`技能 ${index + 1}`"
          placeholder="如 Python"
          @input="onSkillName(index, $event)"
        />
        <label class="rating">
          <span class="visually-hidden">技能 {{ index + 1 }} 自评</span>
          <select :value="skill.self_rating" @change="onSkillRating(index, $event)">
            <option v-for="score in 5" :key="score" :value="score">{{ score }}</option>
          </select>
          <span class="rating-unit">/5</span>
        </label>
        <button
          class="remove-button"
          type="button"
          :disabled="!store.canRemoveSkill"
          :aria-label="`删除技能 ${index + 1}`"
          @click="store.removeSkill(index)"
        >
          ×
        </button>
      </div>
      <button class="text-button" type="button" :disabled="!store.canAddSkill" @click="store.addSkill">
        + 新增技能
      </button>
      <p class="hint">技能需 {{ SKILL_LIMIT.min }}–{{ SKILL_LIMIT.max }} 条，名称不可重复。</p>
    </section>

    <section class="form-section">
      <div class="section-title">
        <h2>项目经历</h2>
        <span class="counter">{{ store.draft.experiences.length }} / {{ EXPERIENCE_LIMIT.max }}</span>
      </div>
      <article
        v-for="(experience, index) in store.draft.experiences"
        :key="index"
        class="experience-form"
      >
        <div class="section-title">
          <span class="item-index">0{{ index + 1 }}</span>
          <button
            class="remove-button"
            type="button"
            :disabled="!store.canRemoveExperience"
            @click="store.removeExperience(index)"
          >
            ×
          </button>
        </div>
        <label class="field">
          <span>项目名称</span>
          <input
            :value="experience.title"
            @input="onExperienceInput(index, 'title', $event)"
          />
        </label>
        <div class="field-row">
          <label class="field">
            <span>开始日期</span>
            <input
              type="date"
              :value="experience.start_date"
              @input="onExperienceInput(index, 'start_date', $event)"
            />
          </label>
          <label class="field">
            <span>结束日期（留空为「至今」）</span>
            <input
              type="date"
              :value="experience.end_date"
              @input="onExperienceInput(index, 'end_date', $event)"
            />
          </label>
        </div>
        <label class="field">
          <span>项目描述</span>
          <textarea
            :value="experience.description"
            @input="onExperienceInput(index, 'description', $event)"
          />
        </label>
        <label class="field">
          <span>个人职责</span>
          <textarea
            :value="experience.responsibility"
            @input="onExperienceInput(index, 'responsibility', $event)"
          />
        </label>
      </article>
      <button
        class="text-button"
        type="button"
        :disabled="!store.canAddExperience"
        @click="store.addExperience"
      >
        + 新增经历
      </button>
      <p class="hint">经历需 {{ EXPERIENCE_LIMIT.min }}–{{ EXPERIENCE_LIMIT.max }} 条。</p>
    </section>

    <section class="save-section">
      <button class="save-button" type="button" :disabled="store.saving" @click="handleSave">
        {{ store.saving ? '保存中…' : '保存档案' }}
      </button>
      <p v-if="feedback" :class="['feedback', `feedback--${feedback.tone}`]">{{ feedback.text }}</p>
      <p v-else-if="store.lastSavedAt" class="feedback">上次保存：{{ store.lastSavedAt }}</p>
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
.counter { color: #879692; font-family: "SF Mono", Consolas, monospace; font-size: 11px; }
.field { display: grid; gap: 6px; min-width: 0; }
.field span { color: #687875; font-size: 12px; }
input, textarea, select { width: 100%; border: 1px solid #d6dfdc; border-radius: 0; padding: 9px 10px; color: #243330; background: #fff; font: inherit; font-size: 13px; outline: 0; }
input:focus, textarea:focus, select:focus { border-color: #6b8f8a; box-shadow: 0 0 0 2px #6b8f8a22; }
textarea { min-height: 58px; resize: vertical; line-height: 1.45; }
.field-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.text-button { justify-self: start; border: 0; padding: 0; color: #527873; background: transparent; cursor: pointer; font-size: 12px; }
.text-button:disabled { color: #a9b6b3; cursor: not-allowed; }
.skill-row { display: grid; grid-template-columns: minmax(0, 1fr) 84px 20px; align-items: center; gap: 8px; }
.rating { display: flex; align-items: center; gap: 4px; }
.rating select { padding: 9px 4px; text-align: center; }
.rating-unit { color: #879692; font-size: 11px; }
.remove-button { border: 0; padding: 0 4px; color: #9a5e58; background: transparent; cursor: pointer; font-size: 18px; line-height: 1; }
.remove-button:disabled { color: #c9d2d0; cursor: not-allowed; }
.experience-form { display: grid; gap: 10px; border-top: 1px solid #dde5e2; padding-top: 12px; }
.item-index { color: #6b8f8a; font-family: "SF Mono", Consolas, monospace; font-size: 12px; }
.hint { color: #879692; font-size: 11px; line-height: 1.5; }
.save-section { display: grid; gap: 8px; border-top: 1px solid #dde5e2; padding-top: 16px; }
.save-button { width: 100%; border: 0; padding: 11px; color: #fff; background: #6b8f8a; cursor: pointer; font: inherit; font-size: 13px; }
.save-button:hover:not(:disabled) { background: #527873; }
.save-button:disabled { opacity: .6; cursor: progress; }
.feedback { font-size: 12px; line-height: 1.5; }
.feedback--ok { color: #3f7a5f; }
.feedback--error { color: #9a5e58; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (prefers-color-scheme: dark) {
  .editor-panel { color: #e8eae9; }
  .panel-intro, .field span, .hint { color: #b7c3c0; }
  input, textarea, select { border-color: #52635f; color: #e8eae9; background: #263330; }
  .save-section, .experience-form { border-color: #52635f; }
  .counter, .rating-unit { color: #8ea09c; }
}
@media print {
  .editor-panel { display: none; }
}
</style>
