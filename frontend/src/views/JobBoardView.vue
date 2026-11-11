<script setup lang="ts">
/**
 * 岗位列表（议题 3.B）：卡片式 + `keyword`/`status` 筛选 + 分页。
 *
 * 三条形状约束照 ADR，不自行发挥：
 * - 卡片只放企业名 + 岗位名 + 工作方式标签 + 截止时间 + 技能前 6 条。
 *   技能数与「不带 JD 原文」都是后端保证的（`JobSummary.skills` 的 `max_length=6`），
 *   前端不需要再 slice 一次去防「后端忘了截」——真忘了截也该在后端修。
 * - 375px 单列；≥1100px 两列，点卡片进**独立详情页**而不是右侧抽屉。
 * - 「我的删除」是本页顶部的一条文字链接：岗位回收站**不占顶栏一级入口**
 *   （顶栏那个「回收站」是简历的，两个混在一层会让人分不清删掉的是什么，v7 裁定）。
 *
 * 本轮没有「AI 提取」相关的一切形态：列表页只有「录入岗位」这一个动作入口。
 *
 * 数据全在 `stores/job.ts`（含筛选条件），所以从详情页返回时筛选与页码都还在。
 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { isApiError } from '@/api'
import type { jobsApi as JobsContract } from '@/api'
import { EMPLOYMENT_TYPE_LABELS, JOB_STATUS_LABELS, useJobStore } from '@/stores/job'
import { formatDate } from '@/utils/datetime'
import SkeletonList from '@/components/SkeletonList.vue'

const store = useJobStore()

type JobStatus = JobsContract.JobStatus

const loadError = ref('')
/** 搜索框的输入值：按「搜索」或回车才写进 store 的筛选条件，不逐字发请求。 */
const keywordDraft = ref(store.filter.keyword)

/** 筛选项只有两个（议题 3.C）：薪资/地点/经验都存而不筛。 */
const STATUS_FILTERS: JobStatus[] = ['published', 'draft']

function describeError(error: unknown, fallback: string): string {
  if (isApiError(error) && error.message) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}

async function load(): Promise<void> {
  loadError.value = ''
  try {
    await store.loadJobs(1)
  } catch (error) {
    loadError.value = describeError(error, '读取岗位列表失败，请刷新页面重试。')
  }
}

async function submitKeyword(): Promise<void> {
  loadError.value = ''
  try {
    await store.setKeyword(keywordDraft.value)
  } catch (error) {
    loadError.value = describeError(error, '搜索失败，请稍后重试。')
  }
}

async function clearKeyword(): Promise<void> {
  keywordDraft.value = ''
  await submitKeyword()
}

async function pickStatus(status: JobStatus): Promise<void> {
  if (store.filter.status === status) return
  loadError.value = ''
  try {
    await store.setStatusFilter(status)
  } catch (error) {
    loadError.value = describeError(error, '切换筛选失败，请稍后重试。')
  }
}

async function toggleExpired(): Promise<void> {
  loadError.value = ''
  try {
    await store.toggleIncludeExpired()
  } catch (error) {
    loadError.value = describeError(error, '切换筛选失败，请稍后重试。')
  }
}

async function goPage(targetPage: number): Promise<void> {
  loadError.value = ''
  try {
    await store.goPage(targetPage)
  } catch (error) {
    loadError.value = describeError(error, '翻页失败，请稍后重试。')
  }
}

/** 工作方式的中文标签；库里存的是枚举串，未知值原样显示（宁可多显示也不静默丢字段）。 */
function employmentLabel(value: string | null): string {
  if (!value) return ''
  return EMPLOYMENT_TYPE_LABELS[value as keyof typeof EMPLOYMENT_TYPE_LABELS] ?? value
}

onMounted(load)
</script>

<template>
  <main class="board-page">
    <div class="board-heading">
      <div>
        <p class="eyebrow">岗位库</p>
        <h1>岗位列表</h1>
        <p class="intro">
          由使用者手工录入的岗位与岗位要求。看到合适的岗位，可以在「录入岗位」里补充新的。
        </p>
      </div>
      <div class="heading-actions">
        <RouterLink class="primary-link" :to="{ name: 'job-create' }">录入岗位</RouterLink>
        <!-- v7 裁定：岗位回收站的入口在这里，不在顶栏。 -->
        <RouterLink class="text-link" :to="{ name: 'job-trash' }">我的删除</RouterLink>
        <RouterLink class="text-link" :to="{ name: 'resume-editor' }">返回简历</RouterLink>
      </div>
    </div>

    <section class="filter-bar" aria-label="岗位筛选">
      <form class="keyword-form" @submit.prevent="submitKeyword">
        <label class="field-label" for="job-keyword">关键词</label>
        <div class="keyword-row">
          <input
            id="job-keyword"
            v-model="keywordDraft"
            class="keyword-input"
            type="search"
            maxlength="200"
            placeholder="企业名或岗位名"
          />
          <button class="row-button" type="submit">搜索</button>
          <button
            v-if="store.filter.keyword"
            class="row-button row-button--ghost"
            type="button"
            @click="clearKeyword"
          >
            清除
          </button>
        </div>
      </form>

      <div class="filter-group">
        <span class="field-label">范围</span>
        <div class="chip-row" role="group" aria-label="岗位状态">
          <button
            v-for="status in STATUS_FILTERS"
            :key="status"
            class="filter-chip"
            :class="{ 'filter-chip--active': store.filter.status === status }"
            type="button"
            @click="pickStatus(status)"
          >
            {{ JOB_STATUS_LABELS[status] }}
          </button>
        </div>
        <label class="expired-toggle">
          <input
            type="checkbox"
            :checked="store.filter.includeExpired"
            @change="toggleExpired"
          />
          <span>含已过期</span>
        </label>
      </div>

      <p v-if="store.filter.status === 'draft'" class="filter-hint">
        草稿只有创建者自己看得到；发布后才会出现在「已发布」里。
      </p>
    </section>

    <p v-if="loadError" class="feedback feedback--error" role="alert">{{ loadError }}</p>
    <!-- 稳定态实测：卡片 1280 档 158px、375 档 187px；≥1100 两列，列间距 14px（`.job-grid`）。 -->
    <SkeletonList
      v-else-if="store.loading"
      label="正在加载岗位…"
      :heights="[158, 158, 158, 158]"
      :heights-compact="[187, 187, 187, 187]"
      :columns="2"
      :gap="14"
    />
    <p v-else-if="!store.jobs.length" class="feedback">
      {{ store.filter.keyword ? '没有匹配的岗位，换个关键词试试。' : '这里还没有岗位。' }}
    </p>

    <template v-else>
      <ul class="job-grid">
        <li v-for="job in store.jobs" :key="job.id">
          <RouterLink class="job-card" :to="{ name: 'job-detail', params: { id: job.id } }">
            <div class="card-head">
              <p class="card-company">{{ job.company_name }}</p>
              <h2 class="card-title">{{ job.title }}</h2>
              <div class="card-tags">
                <span v-if="employmentLabel(job.employment_type)" class="tag">
                  {{ employmentLabel(job.employment_type) }}
                </span>
                <span v-if="job.status === 'draft'" class="tag tag--draft">草稿</span>
                <span v-if="job.is_expired" class="tag tag--expired">已过期</span>
              </div>
            </div>

            <p class="card-deadline">
              {{ job.deadline ? `截止 ${formatDate(job.deadline) || job.deadline}` : '长期有效' }}
            </p>

            <ul v-if="job.skills.length" class="card-skills">
              <li v-for="skill in job.skills" :key="skill.id" class="skill-chip">
                {{ skill.name }}
              </li>
            </ul>
            <p v-else class="card-empty-skills">还没填写技能要求</p>
          </RouterLink>
        </li>
      </ul>

      <nav class="pager" aria-label="分页">
        <button
          class="row-button row-button--ghost"
          type="button"
          :disabled="!store.hasPrevPage || store.loading"
          @click="goPage(store.page - 1)"
        >
          上一页
        </button>
        <span class="pager-info">第 {{ store.page }} / {{ store.totalPages }} 页 · 共 {{ store.total }} 条</span>
        <button
          class="row-button row-button--ghost"
          type="button"
          :disabled="!store.hasNextPage || store.loading"
          @click="goPage(store.page + 1)"
        >
          下一页
        </button>
      </nav>
    </template>
  </main>
</template>

<style scoped>
/* 页面外壳与内缩沿用 `RecycleBinView` 那一套（同一个产品不该有两套页边距）。 */
.board-page { width: min(1180px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.board-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .12em; }
h1 { margin: 0; color: var(--ui-ink); font-size: var(--text-4xl); font-weight: 650; }
.intro { margin: 8px 0 0; max-width: 60ch; color: var(--ui-ink-muted); font-size: var(--text-base); line-height: 1.6; }
.heading-actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 10px 14px; }

/* 主入口与两个文字链接的层级：录入是动作，回收站与返回是导航。 */
.primary-link { display: inline-flex; align-items: center; height: 32px; border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 0 14px; color: var(--theme-on-primary); background: var(--theme-primary); font-size: var(--text-sm); text-decoration: none; white-space: nowrap; }
.primary-link:hover { background: var(--theme-primary-hover); }
.text-link { color: var(--theme-primary); font-size: var(--text-base); text-decoration: none; white-space: nowrap; }
.text-link:hover { text-decoration: underline; }

.filter-bar { display: grid; gap: 14px; margin-bottom: 18px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 14px 16px; background: var(--ui-surface); }
.field-label { color: var(--ui-ink-faint); font-size: var(--text-xs); letter-spacing: .04em; }
.keyword-form { display: grid; gap: 6px; }
.keyword-row { display: flex; flex-wrap: wrap; gap: 8px; }
.keyword-input { flex: 1 1 200px; min-width: 0; height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 10px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: var(--text-base); outline: 0; }
.keyword-input:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
.filter-group { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; }
.chip-row { display: flex; flex-wrap: wrap; gap: 6px; }
.filter-chip { height: 26px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 0 10px; color: var(--ui-ink-muted); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-xs); white-space: nowrap; }
.filter-chip:hover { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); }
.filter-chip--active { border-color: var(--theme-primary); color: var(--theme-primary-strong); background: var(--theme-bg-light); font-weight: 600; }
.expired-toggle { display: inline-flex; align-items: center; gap: 6px; color: var(--ui-ink-muted); font-size: var(--text-sm); cursor: pointer; }
.expired-toggle input { margin: 0; accent-color: var(--theme-primary); }
.filter-hint { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-xs); line-height: 1.5; }

.feedback { margin: 0 0 14px; color: var(--ui-ink-faint); font-size: var(--text-base); line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }

/* 375px 单列；≥1100px 两列（议题 3.B 的断点，中间宽度维持单列以免卡片被压扁）。 */
.job-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; margin: 0; padding: 0; list-style: none; }
@media (min-width: 1100px) {
  .job-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

.job-card { display: grid; gap: 10px; height: 100%; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 16px 18px; color: inherit; background: var(--ui-surface); text-decoration: none; }
.job-card:hover { border-color: var(--theme-rule-soft); background: var(--ui-surface-soft); }
.card-head { display: grid; gap: 4px; }
.card-company { margin: 0; color: var(--ui-ink-muted); font-size: var(--text-sm); }
.card-title { margin: 0; color: var(--ui-ink); font-size: var(--text-lg); font-weight: 650; }
.card-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.tag { border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 1px 6px; color: var(--ui-ink-muted); background: var(--ui-surface-soft); font-size: var(--text-xs); line-height: 1.5; white-space: nowrap; }
.tag--draft { border-color: var(--ui-warning-border); color: var(--ui-warning); background: var(--ui-warning-soft); }
.tag--expired { border-color: var(--ui-danger-border); color: var(--ui-danger); background: var(--ui-danger-soft); }
.card-deadline { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-sm); }
.card-skills { display: flex; flex-wrap: wrap; gap: 6px; margin: 0; padding: 0; list-style: none; }
.skill-chip { border: 1px solid var(--theme-rule-soft); border-radius: var(--ui-radius); padding: 2px 7px; color: var(--theme-primary-deep); background: var(--theme-bg-light); font-size: var(--text-xs); line-height: 1.5; }
.card-empty-skills { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-xs); }

.pager { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 12px; margin-top: 22px; }
.pager-info { color: var(--ui-ink-faint); font-size: var(--text-sm); }
.row-button { height: 32px; border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 0 14px; color: var(--theme-primary-deep); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-sm); white-space: nowrap; }
.row-button:hover:not(:disabled) { background: var(--theme-bg-hover); }
.row-button:disabled { opacity: .5; cursor: not-allowed; }
.row-button--ghost { border-color: var(--ui-border-strong); color: var(--ui-ink-muted); }

@media (max-width: 760px) {
  .board-page { padding: 20px 16px 48px; }
  .board-heading { flex-direction: column; }
  .heading-actions { justify-content: flex-start; }
}
@media print {
  .board-page { display: none; }
}
</style>
