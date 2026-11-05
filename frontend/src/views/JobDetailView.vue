<script setup lang="ts">
/**
 * 岗位详情页（议题 3.B）。
 *
 * 三条硬约束：
 * 1. **过期标记只看后端 `is_expired`**：凡 `deadline < 今天` 一律显示「已过期」，
 *    不区分查看者身份、也不绑定列表的 `include_expired` 参数（2.G：判据在 service 层
 *    用应用时钟算，前端再实现一遍必然漂移）。
 * 2. **`origin` 徽标只有两态**，文案固定为「可回溯原文」/「未标注出处」，
 *    **不出现「AI 提取」字样**（v7：这一列只证明「这句话在 JD 原文里出现过」，
 *    不证明是谁写的；本轮全部条目都是手填 → 恒为 `unverified`）。
 * 3. **`evidence_text` 为 NULL 时整块不渲染**：不空着一条展开区、也不写「无依据」占位。
 *
 * 「你已具备」是只读比对（2.E）：与当前登录用户默认简历的技能名做归一化等值比较，
 * 不写表、不打分、不排序。
 *
 * 只有创建者能看到「发布 / 下架 / 删除」——后端对非创建者一律 404（1.F），
 * 摆一个必然失败的按钮比不摆更糟。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import AppTopBar from '@/components/AppTopBar.vue'
import { isApiError } from '@/api'
import {
  EMPLOYMENT_TYPE_LABELS,
  JOB_ORIGIN_BADGES,
  JOB_SKILL_TYPE_LABELS,
  JOB_STATUS_LABELS,
  useJobStore,
} from '@/stores/job'
import { formatDate, formatDateTime } from '@/utils/datetime'

const store = useJobStore()
const route = useRoute()
const router = useRouter()

const loadError = ref('')
const actionError = ref('')
const actionNotice = ref('')
/** 正在执行发布/下架/删除时锁住这一组按钮。 */
const actionBusy = ref<'publish' | 'unpublish' | 'delete' | null>(null)

/** JD 超过这么长就先折叠；600 字大约是手机上 12–14 行，够看清「这是不是我要的岗位」。 */
const COLLAPSE_THRESHOLD = 600
const expanded = ref(false)

const job = computed(() => store.detail)
const isLongDescription = computed(() => (job.value?.description.length ?? 0) > COLLAPSE_THRESHOLD)
const descriptionText = computed(() => {
  const text = job.value?.description ?? ''
  if (!isLongDescription.value || expanded.value) return text
  return `${text.slice(0, COLLAPSE_THRESHOLD)}…`
})

/** 只有创建者才有的操作区；比对用的是 JWT 里的用户 id 与 `created_by_user_id`。 */
const isCreator = computed(() => store.isCreatorOf(job.value))

function employmentLabel(value: string | null): string {
  if (!value) return ''
  return EMPLOYMENT_TYPE_LABELS[value as keyof typeof EMPLOYMENT_TYPE_LABELS] ?? value
}

/** 技能分类标签：未分类（NULL）返回空串，调用方据此不渲染徽标。 */
function skillTypeLabel(value: string | null): string {
  if (!value) return ''
  return JOB_SKILL_TYPE_LABELS[value as keyof typeof JOB_SKILL_TYPE_LABELS] ?? value
}

/**
 * 出处徽标：两态固定文案（「可回溯原文」/「未标注出处」），**不含任何「AI」字样**。
 * 未知值（后端将来加第三种）一律按「未标注出处」显示 —— 说不出出处就是未标注，
 * 反过来把它说成「可回溯」才是真错误。
 */
function originBadge(origin: string): string {
  return JOB_ORIGIN_BADGES[origin as keyof typeof JOB_ORIGIN_BADGES] ?? JOB_ORIGIN_BADGES.unverified
}

/**
 * 薪资区间展示。后端是 `DECIMAL(12,2)` 的**字符串**（`"8000.00"`），
 * 这里只去掉多余的 `.00`，不做数值运算——避免把「库里存了什么」看成被改过。
 */
function trimAmount(value: string): string {
  return value.replace(/\.00$/, '')
}

const salaryLabel = computed(() => {
  const current = job.value
  if (!current) return ''
  const min = current.salary_min ? trimAmount(current.salary_min) : ''
  const max = current.salary_max ? trimAmount(current.salary_max) : ''
  if (min && max) return `${min} – ${max} 元`
  if (min) return `${min} 元起`
  if (max) return `最高 ${max} 元`
  return ''
})

function describeError(error: unknown, fallback: string): string {
  if (isApiError(error) && error.message) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}

async function load(): Promise<void> {
  const jobId = String(route.params.id ?? '')
  if (!jobId) return
  loadError.value = ''
  // 两件事互不依赖：详情读不到「你已具备」只是少标几个，不该拖慢主内容。
  try {
    await store.loadDetail(jobId)
  } catch (error) {
    loadError.value = isApiError(error) && error.status === 404
      ? '这个岗位不存在、是别人的草稿，或已经在回收站里。'
      : describeError(error, '读取岗位详情失败，请稍后重试。')
  }
}

async function loadMine(): Promise<void> {
  try {
    await store.loadMySkillNames()
  } catch {
    // 「你已具备」是增益信息：拉不到就一条都不标，不能因为它让详情页报错。
  }
}

async function handlePublish(): Promise<void> {
  const current = job.value
  if (!current || actionBusy.value) return
  actionBusy.value = 'publish'
  actionError.value = ''
  actionNotice.value = ''
  try {
    await store.publish(current.id)
    actionNotice.value = '已发布，任何登录用户都能在列表里看到它。'
  } catch (error) {
    actionError.value = describeError(error, '发布失败，请稍后重试。')
  } finally {
    actionBusy.value = null
  }
}

async function handleUnpublish(): Promise<void> {
  const current = job.value
  if (!current || actionBusy.value) return
  actionBusy.value = 'unpublish'
  actionError.value = ''
  actionNotice.value = ''
  try {
    await store.unpublish(current.id)
    actionNotice.value = '已下架回草稿，现在只有你自己看得到。'
  } catch (error) {
    actionError.value = describeError(error, '下架失败，请稍后重试。')
  } finally {
    actionBusy.value = null
  }
}

async function handleDelete(): Promise<void> {
  const current = job.value
  if (!current || actionBusy.value) return
  const confirmed = window.confirm('删除后可在「我的删除」里找回，确认删除？')
  if (!confirmed) return
  actionBusy.value = 'delete'
  actionError.value = ''
  try {
    await store.remove(current.id)
    await router.push({ name: 'job-board' })
  } catch (error) {
    actionError.value = describeError(error, '删除失败，请稍后重试。')
  } finally {
    actionBusy.value = null
  }
}

onMounted(() => {
  void load()
  void loadMine()
})
</script>

<template>
  <div class="app-shell">
    <AppTopBar />

    <main class="detail-page">
      <div class="page-heading">
        <div class="heading-main">
          <p class="eyebrow">岗位详情</p>
        </div>
        <div class="heading-actions">
          <RouterLink class="text-link" :to="{ name: 'job-board' }">← 返回列表</RouterLink>
          <RouterLink class="text-link" :to="{ name: 'job-trash' }">我的删除</RouterLink>
        </div>
      </div>

      <p v-if="loadError" class="feedback feedback--error" role="alert">{{ loadError }}</p>
      <p v-else-if="store.detailLoading" class="feedback">正在加载岗位…</p>
      <p v-else-if="!job" class="feedback">没有可显示的内容。</p>

      <template v-else>
        <section class="detail-card">
          <h1 class="detail-title">{{ job.title }}</h1>
          <p class="detail-company">{{ job.company_name }}</p>

          <div class="badge-row">
            <span class="badge">{{ JOB_STATUS_LABELS[job.status] }}</span>
            <span v-if="employmentLabel(job.employment_type)" class="badge">
              {{ employmentLabel(job.employment_type) }}
            </span>
            <span v-if="job.is_expired" class="badge badge--expired">已过期</span>
          </div>

          <dl class="meta-grid">
            <div class="meta-item">
              <dt>工作地点</dt>
              <dd>{{ job.location || '未填写' }}</dd>
            </div>
            <div class="meta-item">
              <dt>截止时间</dt>
              <dd>
                {{ job.deadline ? formatDate(job.deadline) || job.deadline : '长期有效' }}
                <span v-if="job.is_expired" class="meta-flag">已过期</span>
              </dd>
            </div>
            <div class="meta-item">
              <dt>薪资区间</dt>
              <dd>{{ salaryLabel || '未填写' }}</dd>
            </div>
            <div class="meta-item">
              <dt>经验要求</dt>
              <dd>{{ job.experience_required || '未填写' }}</dd>
            </div>
            <div class="meta-item">
              <dt>录入时间</dt>
              <dd>{{ formatDateTime(job.created_at) || job.created_at }}</dd>
            </div>
          </dl>

          <div v-if="isCreator" class="owner-actions">
            <button
              v-if="job.status === 'draft'"
              class="row-button row-button--primary"
              type="button"
              :disabled="actionBusy !== null || store.submitting"
              @click="handlePublish"
            >
              {{ actionBusy === 'publish' ? '发布中…' : '发布到广场' }}
            </button>
            <button
              v-else
              class="row-button"
              type="button"
              :disabled="actionBusy !== null || store.submitting"
              @click="handleUnpublish"
            >
              {{ actionBusy === 'unpublish' ? '下架中…' : '下架回草稿' }}
            </button>
            <button
              class="row-button row-button--danger"
              type="button"
              :disabled="actionBusy !== null || store.submitting"
              @click="handleDelete"
            >
              {{ actionBusy === 'delete' ? '删除中…' : '删除' }}
            </button>
          </div>

          <p v-if="actionError" class="feedback feedback--error" role="alert">{{ actionError }}</p>
          <p v-if="actionNotice" class="feedback feedback--ok" role="status">{{ actionNotice }}</p>
        </section>

        <section class="detail-block">
          <h2 class="block-title">岗位描述（JD 原文）</h2>
          <p class="description">{{ descriptionText }}</p>
          <button
            v-if="isLongDescription"
            class="expand-button"
            type="button"
            :aria-expanded="expanded"
            @click="expanded = !expanded"
          >
            {{ expanded ? '收起全文' : '展开全文' }}
          </button>
        </section>

        <section class="detail-block">
          <h2 class="block-title">岗位要求的技能</h2>
          <p class="block-hint">
            「可回溯原文」表示这条技能能在这份 JD 原文里逐字找到；
            「未标注出处」表示当前没有可回溯的依据。
          </p>

          <p v-if="!job.skills.length" class="empty">这个岗位还没有填写技能要求。</p>

          <ul v-else class="skill-list">
            <li v-for="skill in job.skills" :key="skill.id" class="skill-item">
              <div class="skill-head">
                <span class="skill-name">{{ skill.name }}</span>
                <span v-if="skillTypeLabel(skill.type)" class="badge badge--soft">
                  {{ skillTypeLabel(skill.type) }}
                </span>
                <span class="badge badge--origin">{{ originBadge(skill.origin) }}</span>
                <span v-if="store.matchesMySkill(skill.name)" class="badge badge--mine">你已具备</span>
              </div>
              <!-- `evidence_text` 为 NULL（本轮恒为 NULL）时整块不渲染：不留下空的展开区。 -->
              <p v-if="skill.evidence_text" class="skill-evidence">原文依据：{{ skill.evidence_text }}</p>
            </li>
          </ul>
        </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; background: var(--ui-canvas); }
.detail-page { width: min(880px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.page-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.heading-main { min-width: 0; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: 12px; letter-spacing: .12em; }
.heading-actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; gap: 8px 14px; }
.text-link { color: var(--theme-primary); font-size: 13px; text-decoration: none; white-space: nowrap; }
.text-link:hover { text-decoration: underline; }

.feedback { margin: 0 0 12px; color: var(--ui-ink-faint); font-size: 13px; line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }
.feedback--ok { color: var(--theme-primary-deep); }

.detail-card { display: grid; gap: 12px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 20px 22px; background: var(--ui-surface); }
.detail-title { margin: 0; color: var(--ui-ink); font-size: 20px; font-weight: 650; }
.detail-company { margin: 0; color: var(--ui-ink-muted); font-size: 13px; }
.badge-row { display: flex; flex-wrap: wrap; gap: 6px; }
.badge { border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 2px 7px; color: var(--ui-ink-muted); background: var(--ui-surface-soft); font-size: 11px; line-height: 1.5; white-space: nowrap; }
.badge--soft { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); background: var(--theme-bg-light); }
.badge--expired { border-color: var(--ui-danger-border); color: var(--ui-danger); background: var(--ui-danger-soft); }
/* 出处徽标刻意用中性色：它不是成就，只是一个「能不能回溯」的事实。 */
.badge--origin { border-style: dashed; color: var(--ui-ink-faint); }
.badge--mine { border-color: var(--theme-primary); color: var(--theme-primary-strong); background: var(--theme-bg-light); font-weight: 600; }

.meta-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px 18px; margin: 0; }
.meta-item { min-width: 0; }
.meta-item dt { color: var(--ui-ink-faint); font-size: 11px; letter-spacing: .04em; }
.meta-item dd { margin: 4px 0 0; color: var(--ui-ink); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.meta-flag { margin-left: 6px; color: var(--ui-danger); font-size: 11px; }

.owner-actions { display: flex; flex-wrap: wrap; gap: 10px; border-top: 1px solid var(--ui-surface-sunken); padding-top: 14px; }

.detail-block { margin-top: 16px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 18px 22px; background: var(--ui-surface); }
.block-title { margin: 0 0 10px; color: var(--ui-ink); font-size: 14px; font-weight: 650; }
.block-hint { margin: 0 0 12px; color: var(--ui-ink-faint); font-size: 11px; line-height: 1.5; }
/* JD 是用户粘进来的原文：保留换行，但不允许它撑破容器（长 URL / 长英文单词）。 */
.description { margin: 0; color: var(--ui-ink); font-size: 13px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.expand-button { margin-top: 10px; border: 0; padding: 0; color: var(--theme-primary); background: none; cursor: pointer; font: inherit; font-size: 12px; text-decoration: underline; }
.expand-button:hover { color: var(--theme-primary-strong); }

.skill-list { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.skill-item { display: grid; gap: 6px; border-bottom: 1px solid var(--ui-surface-sunken); padding-bottom: 10px; }
.skill-item:last-child { border-bottom: 0; padding-bottom: 0; }
.skill-head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.skill-name { color: var(--ui-ink); font-size: 13px; font-weight: 600; }
.skill-evidence { margin: 0; border-left: 2px solid var(--theme-rule-soft); padding-left: 8px; color: var(--ui-ink-faint); font-size: 12px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.empty { margin: 0; color: var(--ui-ink-faint); font-size: 13px; }

.row-button { height: 32px; border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 0 14px; color: var(--theme-primary-deep); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 12px; white-space: nowrap; }
.row-button:hover:not(:disabled) { background: var(--theme-bg-hover); }
.row-button:disabled { opacity: .5; cursor: progress; }
.row-button--primary { border-color: var(--theme-primary); color: var(--theme-on-primary); background: var(--theme-primary); }
.row-button--primary:hover:not(:disabled) { border-color: var(--theme-primary-hover); background: var(--theme-primary-hover); }
.row-button--danger { border-color: var(--ui-danger-border); color: var(--ui-danger); }
.row-button--danger:hover:not(:disabled) { border-color: var(--ui-danger); background: var(--ui-danger-soft); }

@media (max-width: 760px) {
  .detail-page { padding: 20px 16px 48px; }
  .page-heading { flex-direction: column; }
  .heading-actions { justify-content: flex-start; }
}
@media print {
  .detail-page { display: none; }
}
</style>
