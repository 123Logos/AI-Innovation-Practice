<script setup lang="ts">
/**
 * 岗位回收站（议题 3.B 的 v7 补条）。
 *
 * 为什么单独一个视图而不是塞进简历的 `/trash`：两个回收站混在一层会让人分不清
 * 「我删掉的到底是简历还是岗位」——所以顶栏那个「回收站」继续只表示简历，
 * 岗位的入口是 `/jobs` 列表页顶部的「我的删除」。
 *
 * 为什么这一页必须存在：补正轮已经把 `PUT /jobs/{job_id}/restore` 接上了 HTTP，
 * 前端没有入口它就是一条死码端点。
 *
 * 后端只提供「恢复」，没有彻底删除岗位（库里是软删列，物理清理由服务端负责），
 * 所以这里**不摆**「彻底删除」按钮——摆了必然失败。
 *
 * 行内不链到详情页：`GET /jobs/{id}` 的可读谓词排除了软删行（`get_readable`），
 * 从回收站点进详情必然 404。要看内容就先恢复。
 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { isApiError } from '@/api'
import { EMPLOYMENT_TYPE_LABELS, JOB_STATUS_LABELS, useJobStore } from '@/stores/job'
import { formatDate, formatDateTime } from '@/utils/datetime'
import SkeletonList from '@/components/SkeletonList.vue'

const store = useJobStore()

const loadError = ref('')
const actionError = ref('')
const actionNotice = ref('')
/** 正在恢复的那一行：只有它自己禁用，别让整页按钮一起灰。 */
const busyId = ref<string | null>(null)

function describeError(error: unknown, fallback: string): string {
  if (isApiError(error) && error.message) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}

function employmentLabel(value: string | null): string {
  if (!value) return ''
  return EMPLOYMENT_TYPE_LABELS[value as keyof typeof EMPLOYMENT_TYPE_LABELS] ?? value
}

async function load(): Promise<void> {
  loadError.value = ''
  try {
    await store.loadTrash()
  } catch (error) {
    loadError.value = describeError(error, '读取岗位回收站失败，请刷新页面重试。')
  }
}

async function handleRestore(jobId: string, title: string): Promise<void> {
  if (busyId.value) return
  busyId.value = jobId
  actionError.value = ''
  actionNotice.value = ''
  try {
    await store.restore(jobId)
    actionNotice.value = `已恢复「${title}」，回到列表就能看到它。`
  } catch (error) {
    actionError.value = describeError(error, '恢复失败，请稍后重试。')
  } finally {
    busyId.value = null
  }
}

onMounted(load)
</script>

<template>
  <main class="trash-page">
    <div class="trash-heading">
      <div>
        <p class="eyebrow">岗位库</p>
        <h1>岗位回收站</h1>
        <p class="intro">
          这里只有你删除的<strong>岗位</strong>。简历的回收站在别处，两者不混在一起。
          恢复后岗位回到列表，状态（草稿 / 已发布）保持删除前的样子。
        </p>
      </div>
      <RouterLink class="back-link" :to="{ name: 'job-board' }">返回岗位列表</RouterLink>
    </div>

    <p v-if="loadError" class="feedback feedback--error" role="alert">{{ loadError }}</p>
    <!-- 稳定态实测：回收站行 1280 档 71px、375 档 140px（窄屏动作换行），行之间只有分隔线。 -->
    <SkeletonList
      v-else-if="store.trashLoading"
      label="正在加载岗位回收站…"
      :heights="[71, 71, 71]"
      :heights-compact="[140, 140, 140]"
    />

    <p v-if="actionError" class="feedback feedback--error" role="alert">{{ actionError }}</p>
    <p v-if="actionNotice" class="feedback feedback--ok" role="status">{{ actionNotice }}</p>

    <section v-if="!loadError && !store.trashLoading" class="trash-panel">
      <p v-if="!store.trash.length" class="empty">这里还没有被删除的岗位。</p>

      <ul v-else class="trash-list">
        <li v-for="item in store.trash" :key="item.id" class="trash-row">
          <div class="trash-main">
            <p class="trash-title">{{ item.title }}</p>
            <p class="trash-meta">
              <span>{{ item.company_name }}</span>
              <span class="dot">·</span>
              <span>{{ JOB_STATUS_LABELS[item.status] }}</span>
              <span v-if="employmentLabel(item.employment_type)" class="dot">·</span>
              <span v-if="employmentLabel(item.employment_type)">
                {{ employmentLabel(item.employment_type) }}
              </span>
              <template v-if="item.deadline">
                <span class="dot">·</span>
                <span>截止 {{ formatDate(item.deadline) }}</span>
              </template>
              <span class="dot">·</span>
              <span>删除于 {{ formatDateTime(item.deleted_at) || '—' }}</span>
            </p>
          </div>
          <div class="trash-actions">
            <button
              class="row-button"
              type="button"
              :disabled="busyId === item.id"
              @click="handleRestore(item.id, item.title)"
            >
              {{ busyId === item.id ? '恢复中…' : '恢复' }}
            </button>
          </div>
        </li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.trash-page { width: min(880px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.trash-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: 12px; letter-spacing: .12em; }
h1 { margin: 0; color: var(--ui-ink); font-size: 22px; font-weight: 650; }
.intro { margin: 8px 0 0; max-width: 62ch; color: var(--ui-ink-muted); font-size: 13px; line-height: 1.6; }
.intro strong { color: var(--ui-ink); }
.back-link { flex: 0 0 auto; color: var(--theme-primary); font-size: 13px; text-decoration: none; white-space: nowrap; }
.back-link:hover { text-decoration: underline; }
.feedback { margin: 0 0 12px; font-size: 13px; line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }
.feedback--ok { color: var(--theme-primary-deep); }
.trash-panel { border: 1px solid var(--ui-border); border-radius: var(--ui-radius); background: var(--ui-surface); }
.empty { margin: 0; padding: 40px 24px; color: var(--ui-ink-faint); font-size: 13px; text-align: center; }
.trash-list { margin: 0; padding: 0; list-style: none; }
.trash-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--ui-surface-sunken); padding: 16px 20px; }
.trash-row:last-child { border-bottom: 0; }
.trash-main { min-width: 0; }
.trash-title { margin: 0; overflow: hidden; color: var(--ui-ink); font-size: 14px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.trash-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 6px 0 0; color: var(--ui-ink-faint); font-size: 12px; }
.dot { color: var(--ui-border); }
.trash-actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.row-button { height: 32px; border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 0 14px; color: var(--theme-primary-deep); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 12px; white-space: nowrap; }
.row-button:hover:not(:disabled) { background: var(--theme-bg-hover); }
.row-button:disabled { opacity: .6; cursor: progress; }
@media (max-width: 760px) {
  .trash-page { padding: 20px 16px 48px; }
  .trash-heading { flex-direction: column; }
  .trash-row { flex-direction: column; align-items: stretch; }
  .trash-actions { justify-content: flex-end; }
}
@media print {
  .trash-page { display: none; }
}
</style>
