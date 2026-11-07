<script setup lang="ts">
import {
  DEGREE_OPTIONS,
  EDUCATION_LIMIT,
  EXPERIENCE_LIMIT,
  SKILL_LIMIT,
  useResumeStore,
} from '@/stores/resume'
import type { EducationDraft } from '@/stores/resume'
import { currentMonthValue, todayIsoDate } from '@/utils/datetime'

const store = useResumeStore()

/**
 * **开始**时间的 `max`：不允许选未来。
 *
 * 只挂在入学时间 / 项目开始日期上；毕业时间与项目结束日期刻意不加 `max`——
 * 应届生要填「预计毕业时间」，项目也可能「预计结束」，卡在过去会把这两类
 * 真实场景直接堵死（体验反馈修复）。
 *
 * 与 `validateForGenerate()` 里的同口径校验互补——浏览器原生控件先挡住，
 * 手改 DOM / 贴值绕过时再由生成前校验与后端 422 兜底。
 * 取值按东八区（见 `utils/datetime.ts`），与后端 `shanghai_today()` 一致。
 */
const maxMonth = currentMonthValue()
const maxDate = todayIsoDate()

type ProfileField = 'full_name' | 'email' | 'contact'

function onProfileInput(field: ProfileField, event: Event): void {
  store.updateProfileField(field, (event.target as HTMLInputElement).value)
}

function onSkillName(index: number, event: Event): void {
  store.updateSkill(index, (event.target as HTMLInputElement).value)
}

function onEducationInput(index: number, field: keyof EducationDraft, event: Event): void {
  const target = event.target as HTMLInputElement | HTMLSelectElement
  store.updateEducation(index, field, target.value)
}

function onExperienceInput(
  index: number,
  field: 'title' | 'start_date' | 'end_date' | 'description' | 'responsibility',
  event: Event,
): void {
  store.updateExperience(index, field, (event.target as HTMLInputElement | HTMLTextAreaElement).value)
}
</script>

<template>
  <aside class="editor-panel">
    <div class="panel-heading">
      <p class="eyebrow">简历编辑</p>
      <h1>完善你的简历</h1>
      <p class="panel-intro">改动会实时同步到中央预览，点预览区上方的「保存修改」才落库。</p>
    </div>

    <section class="form-section">
      <h2>基本信息</h2>
      <label class="field">
        <span>姓名 <b class="required">*</b></span>
        <input :value="store.draft.full_name" @input="onProfileInput('full_name', $event)" />
      </label>
      <label class="field">
        <span>手机号码 <b class="required">*</b></span>
        <input
          type="tel"
          inputmode="numeric"
          maxlength="11"
          :value="store.draft.contact"
          placeholder="请输入 11 位手机号"
          @input="onProfileInput('contact', $event)"
        />
      </label>
      <label class="field">
        <span>简历邮箱</span>
        <input
          type="email"
          :value="store.draft.email"
          placeholder="选填，会显示在简历的联系方式旁"
          @input="onProfileInput('email', $event)"
        />
      </label>
      <p class="hint">带 <b class="required">*</b> 的项在「生成简历」时才校验；随时可保存草稿。</p>
    </section>

    <section class="form-section">
      <div class="section-title">
        <h2>教育经历</h2>
        <span class="counter">{{ store.draft.educations.length }} / {{ EDUCATION_LIMIT.max }}</span>
      </div>
      <article
        v-for="(education, index) in store.draft.educations"
        :key="`edu-${index}`"
        class="education-form"
      >
        <div class="section-title">
          <span class="item-index">0{{ index + 1 }}</span>
          <button
            class="remove-button"
            type="button"
            :disabled="!store.canRemoveEducation"
            :aria-label="`删除第 ${index + 1} 段教育经历`"
            @click="store.removeEducation(index)"
          >
            ×
          </button>
        </div>
        <label class="field">
          <span>学校 <b class="required">*</b></span>
          <input
            :value="education.school"
            placeholder="如 清华大学"
            @input="onEducationInput(index, 'school', $event)"
          />
        </label>
        <div class="field-row">
          <label class="field">
            <span>专业 <b class="required">*</b></span>
            <input
              :value="education.major"
              placeholder="如 计算机科学与技术"
              @input="onEducationInput(index, 'major', $event)"
            />
          </label>
          <label class="field">
            <span>学历 <b class="required">*</b></span>
            <select
              :value="education.degree"
              @change="onEducationInput(index, 'degree', $event)"
            >
              <option v-for="degree in DEGREE_OPTIONS" :key="degree" :value="degree">
                {{ degree }}
              </option>
            </select>
          </label>
        </div>
        <div class="field-row">
          <label class="field">
            <span>入学时间 <b class="required">*</b></span>
            <input
              type="month"
              :value="education.edu_start"
              :max="maxMonth"
              @input="onEducationInput(index, 'edu_start', $event)"
            />
          </label>
          <label class="field">
            <span>毕业时间（留空为「至今」）</span>
            <input
              type="month"
              :value="education.edu_end"
              :min="education.edu_start || undefined"
              @input="onEducationInput(index, 'edu_end', $event)"
            />
          </label>
        </div>
      </article>
      <button
        class="text-button"
        type="button"
        :disabled="!store.canAddEducation"
        @click="store.addEducation"
      >
        + 添加教育经历
      </button>
      <p class="hint">
        最多 {{ EDUCATION_LIMIT.max }} 段，按入学时间倒序展示；生成简历时至少需要 1 段完整的。
      </p>
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
      <p class="hint">最多 {{ SKILL_LIMIT.max }} 条，名称不可重复；可先存草稿。</p>
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
              :max="maxDate"
              @input="onExperienceInput(index, 'start_date', $event)"
            />
          </label>
          <label class="field">
            <span>结束日期（留空为「至今」）</span>
            <input
              type="date"
              :value="experience.end_date"
              :min="experience.start_date || undefined"
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
      <p class="hint">最多 {{ EXPERIENCE_LIMIT.max }} 条。</p>
    </section>
  </aside>
</template>

<style scoped>
.editor-panel { min-width: 0; }
.panel-heading { margin-bottom: 20px; }
.eyebrow { margin: 0 0 8px; color: var(--theme-rule); font-size: 12px; letter-spacing: .12em; }
h1, h2, p { margin: 0; }
h1 { font-size: 24px; font-weight: 650; }
.panel-intro { margin-top: 8px; color: var(--ui-ink-muted); font-size: 13px; line-height: 1.5; }
.form-section { display: grid; gap: 10px; margin-bottom: 20px; }
.form-section h2 { font-size: 13px; font-weight: 650; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.counter { color: var(--ui-ink-faint); font-family: "SF Mono", Consolas, monospace; font-size: 11px; }
.field { display: grid; gap: 6px; min-width: 0; }
.field span { color: var(--ui-ink-muted); font-size: 12px; }
/* 圆角与 Element Plus 侧（纸张下拉、润色弹窗里的输入框）统一到 `--ui-radius`。 */
input, textarea, select { width: 100%; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 9px 10px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: 13px; outline: 0; }
input:focus, textarea:focus, select:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
textarea { min-height: 58px; resize: vertical; line-height: 1.45; }
.field-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.text-button { justify-self: start; border: 0; padding: 0; color: var(--theme-primary); background: transparent; cursor: pointer; font-size: 12px; }
.text-button:disabled { color: var(--el-text-color-disabled); cursor: not-allowed; }
.skill-row { display: grid; grid-template-columns: minmax(0, 1fr) 20px; align-items: center; gap: 8px; }
.remove-button { border: 0; padding: 0 4px; color: var(--ui-danger); background: transparent; cursor: pointer; font-size: 18px; line-height: 1; }
.remove-button:disabled { color: var(--ui-border); cursor: not-allowed; }
.experience-form, .education-form { display: grid; gap: 10px; border-top: 1px solid var(--ui-border); padding-top: 12px; }
.required { color: var(--ui-danger); font-weight: 400; }
.item-index { color: var(--theme-rule); font-family: "SF Mono", Consolas, monospace; font-size: 12px; }
.hint { color: var(--ui-ink-faint); font-size: 11px; line-height: 1.5; }
@media print {
  .editor-panel { display: none; }
}
</style>
