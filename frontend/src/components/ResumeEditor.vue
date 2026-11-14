<script setup lang="ts">
import {
  DEGREE_OPTIONS,
  EDUCATION_LIMIT,
  EXPERIENCE_LIMIT,
  MULTIVALUE_LIMIT,
  SKILL_LIMIT,
  SKILL_SOURCE_OPTIONS,
  useResumeStore,
} from '@/stores/resume'
import type {
  CertificateDraft,
  EducationDraft,
  PortfolioLinkDraft,
} from '@/stores/resume'
import { currentMonthValue, todayIsoDate } from '@/utils/datetime'

import AutoTextarea from './AutoTextarea.vue'

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

type ProfileField = 'full_name' | 'email' | 'contact' | 'target_role'

function onProfileInput(field: ProfileField, event: Event): void {
  store.updateProfileField(field, (event.target as HTMLInputElement).value)
}

function onSkillName(index: number, event: Event): void {
  store.updateSkill(index, (event.target as HTMLInputElement).value)
}

function onSkillSource(index: number, event: Event): void {
  store.updateSkillSource(index, (event.target as HTMLSelectElement).value)
}

function onSkillLastUsed(index: number, event: Event): void {
  store.updateSkillLastUsed(index, (event.target as HTMLInputElement).value)
}

function onEducationInput(index: number, field: keyof EducationDraft, event: Event): void {
  const target = event.target as HTMLInputElement | HTMLSelectElement
  store.updateEducation(index, field, target.value)
}

function onExperienceInput(
  index: number,
  field: 'title' | 'start_date' | 'end_date',
  event: Event,
): void {
  store.updateExperience(index, field, (event.target as HTMLInputElement).value)
}

/** 两个多行字段走共享组件，拿到的已经是字符串，不再从 `event.target` 上抠值。 */
function onExperienceText(index: number, field: 'description' | 'responsibility', value: string): void {
  store.updateExperience(index, field, value)
}

function onCertificateInput(
  index: number,
  field: keyof CertificateDraft,
  event: Event,
): void {
  store.updateCertificate(index, field, (event.target as HTMLInputElement).value)
}

function onPortfolioInput(
  index: number,
  field: keyof PortfolioLinkDraft,
  event: Event,
): void {
  store.updatePortfolioLink(index, field, (event.target as HTMLInputElement).value)
}

function onCityInput(index: number, event: Event): void {
  store.updateCityPreference(index, (event.target as HTMLInputElement).value)
}
</script>

<template>
  <aside class="editor-panel">
    <div class="panel-heading">
      <p class="eyebrow">简历编辑</p>
      <h1>完善你的简历</h1>
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
      <label class="field">
        <span>目标岗位</span>
        <input
          type="text"
          maxlength="200"
          :value="store.draft.target_role"
          placeholder="选填，如「算法实习生」；填了才能算这一项匹配"
          @input="onProfileInput('target_role', $event)"
        />
      </label>
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
    </section>

    <section class="form-section">
      <div class="section-title">
        <h2>证书</h2>
        <span class="counter">{{ store.draft.certificates.length }} / {{ MULTIVALUE_LIMIT.max }}</span>
      </div>
      <article
        v-for="(certificate, index) in store.draft.certificates"
        :key="`certificate-${index}`"
        class="education-form"
      >
        <div class="section-title">
          <span class="item-index">0{{ index + 1 }}</span>
          <button
            class="remove-button"
            type="button"
            :disabled="!store.canRemoveCertificate"
            :aria-label="`删除第 ${index + 1} 条证书`"
            @click="store.removeCertificate(index)"
          >
            ×
          </button>
        </div>
        <label class="field">
          <span>证书名称 <b class="required">*</b></span>
          <input
            :value="certificate.name"
            placeholder="如 大学英语六级"
            @input="onCertificateInput(index, 'name', $event)"
          />
        </label>
        <div class="field-row">
          <label class="field">
            <span>颁发机构</span>
            <input
              :value="certificate.issuer"
              placeholder="选填，如 教育部考试中心"
              @input="onCertificateInput(index, 'issuer', $event)"
            />
          </label>
          <label class="field">
            <span>颁发日期</span>
            <input
              type="date"
              :value="certificate.issued_on"
              @input="onCertificateInput(index, 'issued_on', $event)"
            />
          </label>
        </div>
      </article>
      <button
        class="text-button"
        type="button"
        :disabled="!store.canAddCertificate"
        @click="store.addCertificate"
      >
        + 添加证书
      </button>
    </section>

    <section class="form-section">
      <div class="section-title">
        <h2>作品链接</h2>
        <span class="counter">{{ store.draft.portfolio_links.length }} / {{ MULTIVALUE_LIMIT.max }}</span>
      </div>
      <article
        v-for="(link, index) in store.draft.portfolio_links"
        :key="`portfolio-${index}`"
        class="education-form"
      >
        <div class="section-title">
          <span class="item-index">0{{ index + 1 }}</span>
          <button
            class="remove-button"
            type="button"
            :disabled="!store.canRemovePortfolioLink"
            :aria-label="`删除第 ${index + 1} 条作品链接`"
            @click="store.removePortfolioLink(index)"
          >
            ×
          </button>
        </div>
        <label class="field">
          <span>作品名称 <b class="required">*</b></span>
          <input
            :value="link.label"
            placeholder="如 个人技术博客"
            @input="onPortfolioInput(index, 'label', $event)"
          />
        </label>
        <label class="field">
          <span>作品链接 <b class="required">*</b></span>
          <input
            type="url"
            :value="link.url"
            placeholder="https://"
            @input="onPortfolioInput(index, 'url', $event)"
          />
        </label>
      </article>
      <button
        class="text-button"
        type="button"
        :disabled="!store.canAddPortfolioLink"
        @click="store.addPortfolioLink"
      >
        + 添加作品链接
      </button>
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
        <div class="skill-meta">
          <!--
            `assessment` 那一档不渲染下拉：它是标准化测评提交时服务端写的，
            客户端无权改动。渲染成下拉就等于给用户一个「撤回证据」的按钮，
            而且选它会被后端 422 拒掉（ADR-027 §2.2）。这里只是如实告诉他出处。
          -->
          <p v-if="skill.source === 'assessment'" class="skill-source-static">
            来源：已通过标准化测评（由测评记录决定，不能在这里改）
          </p>
          <label v-else class="field">
            <span>来源</span>
            <select
              :value="skill.source"
              :aria-label="`技能 ${index + 1} 的来源`"
              @change="onSkillSource(index, $event)"
            >
              <option v-for="option in SKILL_SOURCE_OPTIONS" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>最近使用（选填）</span>
            <input
              type="month"
              :value="skill.last_used_on"
              :max="maxMonth"
              :aria-label="`技能 ${index + 1} 的最近使用月份`"
              @input="onSkillLastUsed(index, $event)"
            />
          </label>
        </div>
      </div>
      <button class="text-button" type="button" :disabled="!store.canAddSkill" @click="store.addSkill">
        + 新增技能
      </button>
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
          <AutoTextarea
            :model-value="experience.description"
            :min-height="58"
            @update:model-value="onExperienceText(index, 'description', $event)"
          />
        </label>
        <label class="field">
          <span>个人职责</span>
          <AutoTextarea
            :model-value="experience.responsibility"
            :min-height="58"
            @update:model-value="onExperienceText(index, 'responsibility', $event)"
          />
        </label>
        <!--
          接受润色之后这一段的目标与截断标记（ADR-018 §2.5 / §6）。
          超出字数跟着当前文字实时算，删到目标以内这行自己消失。
        -->
        <p v-if="store.polishNoteFor(index)" class="polish-note" role="status">
          {{ store.polishNoteFor(index) }}
        </p>
      </article>
      <button
        class="text-button"
        type="button"
        :disabled="!store.canAddExperience"
        @click="store.addExperience"
      >
        + 新增经历
      </button>
    </section>

    <section class="form-section">
      <div class="section-title">
        <h2>期望城市</h2>
        <span class="counter">{{ store.draft.city_preferences.length }} / {{ MULTIVALUE_LIMIT.max }}</span>
      </div>
      <div v-for="(city, index) in store.draft.city_preferences" :key="`city-${index}`" class="skill-row">
        <input
          :value="city.city"
          :aria-label="`期望城市 ${index + 1}`"
          placeholder="如 上海"
          @input="onCityInput(index, $event)"
        />
        <button
          class="remove-button"
          type="button"
          :disabled="!store.canRemoveCityPreference"
          :aria-label="`删除期望城市 ${index + 1}`"
          @click="store.removeCityPreference(index)"
        >
          ×
        </button>
      </div>
      <button
        class="text-button"
        type="button"
        :disabled="!store.canAddCityPreference"
        @click="store.addCityPreference"
      >
        + 添加期望城市
      </button>
    </section>
  </aside>
</template>

<style scoped>
.editor-panel { min-width: 0; }
.panel-heading { margin-bottom: 20px; }
.eyebrow { margin: 0 0 8px; color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .12em; }
h1, h2, p { margin: 0; }
h1 { font-size: var(--text-5xl); font-weight: 650; }
.form-section { display: grid; gap: 10px; margin-bottom: 20px; }
.form-section h2 { font-size: var(--text-base); font-weight: 650; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.counter { color: var(--ui-ink-faint); font-family: "SF Mono", Consolas, monospace; font-size: var(--text-xs); }
.field { display: grid; gap: 6px; min-width: 0; }
.field span { color: var(--ui-ink-muted); font-size: var(--text-sm); }
/* 圆角与 Element Plus 侧（纸张下拉、润色弹窗里的输入框）统一到 `--ui-radius`。 */
input, textarea, select { width: 100%; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 9px 10px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: var(--text-base); outline: 0; }
input:focus, textarea:focus, select:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
/* 高度归 AutoTextarea（守则 F1）：这里只留行高，下限 58px 由调用点的 `min-height` 传进去。 */
textarea { line-height: 1.45; }
.field-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
/* 润色超限 / 截断：琥珀色细条，与「未保存」同色系——它要用户动手，不是报错。 */
.polish-note { margin: 0; border-left: 3px solid var(--ui-warning-border); padding-left: 8px; color: var(--ui-ink-muted); font-size: var(--text-sm); line-height: 1.6; }
.text-button { justify-self: start; border: 0; padding: 0; color: var(--theme-primary); background: transparent; cursor: pointer; font-size: var(--text-sm); }
.text-button:disabled { color: var(--el-text-color-disabled); cursor: not-allowed; }
/* 技能一行：名称 + 删除在第一条线，来源与最近使用缩进在第二条线
   （375px 宽下四列并排会把「教师确认」压成省略号，那比多一行更糟）。 */
.skill-row { display: grid; grid-template-columns: minmax(0, 1fr) 20px; align-items: center; gap: 8px; }
.skill-meta { display: grid; grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.skill-source-static { color: var(--ui-ink-muted); font-size: var(--text-sm); line-height: 1.5; }
.remove-button { border: 0; padding: 0 4px; color: var(--ui-danger); background: transparent; cursor: pointer; font-size: var(--text-2xl); line-height: 1; }
.remove-button:disabled { color: var(--ui-border); cursor: not-allowed; }
.experience-form, .education-form { display: grid; gap: 10px; border-top: 1px solid var(--ui-border); padding-top: 12px; }
.required { color: var(--ui-danger); font-weight: 400; }
.item-index { color: var(--theme-rule); font-family: "SF Mono", Consolas, monospace; font-size: var(--text-sm); }
@media print {
  .editor-panel { display: none; }
}
</style>
