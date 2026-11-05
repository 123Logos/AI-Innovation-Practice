<script setup lang="ts">
/**
 * 岗位录入页（议题 3.B）。**整页而不是弹窗**：JD 最长 8000 字，
 * 而 Phase 3.2 定的弹窗在 ≤768px 是 `calc(100% - 24px)`，长表单塞进去
 * 只会得到一条很高的内部滚动条，比整页更难填。
 *
 * 本轮的链路是「粘贴 JD → **手填**技能 → 保存岗位」：
 * 没有「AI 提取」按钮，也没有它的置灰/禁用/「即将上线」形态——
 * `POST /jobs/extract-skills` 属于下一轮（C 轮），这一轮连端点都还不存在。
 *
 * 表单与请求体的分工：本文件只持有原始输入（空串 = 没填），
 * 「空串转 null」「技能只发 name/type」「提交前的 422 自检」全在 `stores/job.ts`。
 * 因此这里没有一处直接调 API，也就没有第二份 fetch 逻辑。
 */
import { computed, reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import AppTopBar from '@/components/AppTopBar.vue'
import { isApiError } from '@/api'
import {
  DESCRIPTION_LIMIT,
  EMPLOYMENT_TYPE_OPTIONS,
  JOB_SKILL_TYPE_OPTIONS,
  SKILL_LIMIT,
  TEXT_LIMIT,
  createEmptyJobForm,
  createEmptySkillRow,
  useJobStore,
} from '@/stores/job'

const store = useJobStore()
const router = useRouter()

const form = reactive(createEmptyJobForm())
const formError = ref('')

/** 技能行数到达后端上限就不再给「添加技能」，免得用户填完才发现被 422。 */
const canAddSkill = computed(() => form.skills.length < SKILL_LIMIT)

/** 已填了名字的行数：计数器展示用，空行不算「一条技能」。 */
const filledSkillCount = computed(
  () => form.skills.filter((row) => row.name.trim() !== '').length,
)

const descriptionLength = computed(() => form.description.length)

function addSkillRow(): void {
  if (!canAddSkill.value) return
  form.skills.push(createEmptySkillRow())
}

function removeSkillRow(index: number): void {
  // 至少留一行空行：区段整个消失会让「还能加技能」这件事变得看不见。
  if (form.skills.length <= 1) return
  form.skills.splice(index, 1)
}

function describeError(error: unknown, fallback: string): string {
  if (isApiError(error) && error.message) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}

async function submit(): Promise<void> {
  if (store.submitting) return
  formError.value = ''
  try {
    const created = await store.createJob({ ...form, skills: form.skills.map((row) => ({ ...row })) })
    // 存完直接进详情页：用户最想知道的是「我刚填的那些真的进去了吗」。
    await router.push({ name: 'job-detail', params: { id: created.id } })
  } catch (error) {
    formError.value = describeError(error, '保存岗位失败，请检查后重试。')
  }
}

async function cancel(): Promise<void> {
  await router.push({ name: 'job-board' })
}
</script>

<template>
  <div class="app-shell">
    <AppTopBar />

    <main class="create-page">
      <div class="page-heading">
        <div>
          <p class="eyebrow">岗位库</p>
          <h1>录入岗位</h1>
          <p class="intro">
            把招聘信息原文粘进来，再手工补上岗位要求的技能。保存后是「草稿」，
            只有你自己看得到；确认无误后可在详情页发布到广场。
          </p>
        </div>
        <RouterLink class="text-link" :to="{ name: 'job-board' }">← 返回列表</RouterLink>
      </div>

      <form class="job-form" @submit.prevent="submit">
        <section class="form-block">
          <h2 class="block-title">岗位基本信息</h2>
          <div class="field-grid">
            <label class="field">
              <span>企业名称 <em class="required">必填</em></span>
              <input
                v-model="form.company_name"
                type="text"
                :maxlength="TEXT_LIMIT.company"
                placeholder="如：示例科技"
              />
            </label>

            <label class="field">
              <span>岗位名称 <em class="required">必填</em></span>
              <input
                v-model="form.title"
                type="text"
                :maxlength="TEXT_LIMIT.title"
                placeholder="如：后端开发工程师"
              />
            </label>

            <label class="field">
              <span>工作地点</span>
              <input
                v-model="form.location"
                type="text"
                :maxlength="TEXT_LIMIT.location"
                placeholder="如：上海（选填）"
              />
            </label>

            <label class="field">
              <span>工作方式</span>
              <select v-model="form.employment_type">
                <option value="">未指定</option>
                <option v-for="[value, label] in EMPLOYMENT_TYPE_OPTIONS" :key="value" :value="value">
                  {{ label }}
                </option>
              </select>
            </label>

            <label class="field">
              <span>经验要求</span>
              <input
                v-model="form.experience_required"
                type="text"
                :maxlength="TEXT_LIMIT.experience"
                placeholder="如：3 年以上后端经验（选填）"
              />
            </label>

            <label class="field">
              <span>截止时间</span>
              <input v-model="form.deadline" type="date" />
              <small class="field-hint">留空表示长期有效；填过去的日期也可以（补录用过的岗位）。</small>
            </label>

            <label class="field">
              <span>薪资下限（元）</span>
              <input v-model="form.salary_min" type="text" inputmode="decimal" placeholder="如：3000" />
            </label>

            <label class="field">
              <span>薪资上限（元）</span>
              <input v-model="form.salary_max" type="text" inputmode="decimal" placeholder="如：6000" />
            </label>
          </div>
          <p class="field-hint">
            地点、薪资、经验都存下来备查，本轮列表不提供按它们筛选。
          </p>
        </section>

        <section class="form-block">
          <h2 class="block-title">岗位描述（JD 原文）</h2>
          <label class="field">
            <span>原文 <em class="required">必填</em></span>
            <textarea
              v-model="form.description"
              class="description-input"
              rows="14"
              :maxlength="DESCRIPTION_LIMIT"
              placeholder="把招聘页的职责与要求原样粘进来"
            ></textarea>
            <small class="field-hint">{{ descriptionLength }} / {{ DESCRIPTION_LIMIT }} 字</small>
          </label>
        </section>

        <section class="form-block">
          <div class="block-head">
            <h2 class="block-title">岗位要求的技能</h2>
            <span class="counter">{{ filledSkillCount }} / {{ SKILL_LIMIT }}</span>
          </div>

          <ul class="skill-list">
            <li v-for="(row, index) in form.skills" :key="index" class="skill-row">
              <input
                v-model="row.name"
                class="skill-name"
                type="text"
                :maxlength="TEXT_LIMIT.skillName"
                placeholder="如：Python / 沟通能力强"
              />
              <select v-model="row.type" class="skill-type">
                <option v-for="[value, label] in JOB_SKILL_TYPE_OPTIONS" :key="value" :value="value">
                  {{ label }}
                </option>
              </select>
              <button
                class="row-button row-button--ghost"
                type="button"
                :disabled="form.skills.length <= 1"
                @click="removeSkillRow(index)"
              >
                删除
              </button>
            </li>
          </ul>

          <button class="row-button add-skill" type="button" :disabled="!canAddSkill" @click="addSkillRow">
            添加技能
          </button>
          <p class="field-hint">
            一行一个技能；留空的行不会被保存。同名技能（大小写不同也算）会被后端拒掉。
          </p>
        </section>

        <p v-if="formError" class="feedback feedback--error" role="alert">{{ formError }}</p>

        <div class="form-actions">
          <button class="row-button row-button--ghost" type="button" :disabled="store.submitting" @click="cancel">
            取消
          </button>
          <button class="row-button row-button--primary" type="submit" :disabled="store.submitting">
            {{ store.submitting ? '保存中…' : '保存岗位' }}
          </button>
        </div>
      </form>
    </main>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; background: var(--ui-canvas); }
.create-page { width: min(880px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.page-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: 12px; letter-spacing: .12em; }
h1 { margin: 0; color: var(--ui-ink); font-size: 22px; font-weight: 650; }
.intro { margin: 8px 0 0; max-width: 62ch; color: var(--ui-ink-muted); font-size: 13px; line-height: 1.6; }
.text-link { flex: 0 0 auto; color: var(--theme-primary); font-size: 13px; text-decoration: none; white-space: nowrap; }
.text-link:hover { text-decoration: underline; }

.job-form { display: grid; gap: 18px; }
.form-block { border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 18px 20px; background: var(--ui-surface); }
.block-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.block-title { margin: 0 0 14px; color: var(--ui-ink); font-size: 14px; font-weight: 650; }
.counter { color: var(--ui-ink-faint); font-size: 12px; }

/* 两列字段在宽屏上并排，窄屏自动回落单列：不写死断点像素，靠 `auto-fit` + 最小列宽。 */
.field-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px 18px; }
.field { display: grid; gap: 6px; }
.field > span { color: var(--ui-ink-muted); font-size: 12px; }
.required { margin-left: 4px; color: var(--ui-danger); font-size: 11px; font-style: normal; }
.field input,
.field select,
.field textarea { width: 100%; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 8px 10px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: 13px; outline: 0; }
.field input,
.field select { height: 32px; padding: 0 10px; }
.field textarea { padding: 10px; line-height: 1.6; }
.field input:focus,
.field select:focus,
.field textarea:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
.field-hint { margin: 8px 0 0; color: var(--ui-ink-faint); font-size: 11px; line-height: 1.5; }
.description-input { min-height: 300px; resize: vertical; }

.skill-list { display: grid; gap: 8px; margin: 0 0 12px; padding: 0; list-style: none; }
.skill-row { display: flex; flex-wrap: wrap; gap: 8px; }
.skill-name { flex: 1 1 220px; min-width: 0; height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 10px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: 13px; outline: 0; }
.skill-type { flex: 0 0 auto; min-width: 108px; height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 8px; color: var(--ui-ink-muted); background: var(--ui-surface); font: inherit; font-size: 12px; outline: 0; }
.skill-name:focus, .skill-type:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
.add-skill { width: 100%; }

.feedback { margin: 0; color: var(--ui-ink-faint); font-size: 13px; line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }
.form-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 10px; }

.row-button { height: 32px; border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 0 14px; color: var(--theme-primary-deep); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 12px; white-space: nowrap; }
.row-button:hover:not(:disabled) { background: var(--theme-bg-hover); }
.row-button:disabled { opacity: .5; cursor: not-allowed; }
.row-button--ghost { border-color: var(--ui-border-strong); color: var(--ui-ink-muted); }
.row-button--primary { border-color: var(--theme-primary); color: var(--theme-on-primary); background: var(--theme-primary); }
.row-button--primary:hover:not(:disabled) { border-color: var(--theme-primary-hover); background: var(--theme-primary-hover); }

@media (max-width: 760px) {
  .create-page { padding: 20px 16px 48px; }
  .page-heading { flex-direction: column; }
  .skill-type { flex: 1 1 100%; }
}
@media print {
  .create-page { display: none; }
}
</style>
