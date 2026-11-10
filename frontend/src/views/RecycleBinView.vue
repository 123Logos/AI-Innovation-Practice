<script setup lang="ts">
/**
 * 回收站（D4.2）：列出软删除的简历，可恢复或彻底删除。
 *
 * 为什么做成独立路由而不是弹窗：
 * - 列表要展示标题 + 删除时间 + 两个操作，挤在下拉里会很难点；
 * - 用户需要「过几天再来清」的路径，独立地址可收藏、可刷新、可前进后退。
 *
 * 数据由 `store` 统一持有（`trashProfiles`）：恢复 / 彻底删除后，
 * store 会同时刷新「档案库列表」，回到编辑器就能立刻看到变化。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import AppTopBar from '@/components/AppTopBar.vue'
import { isApiError } from '@/api'
import { useResumeStore } from '@/stores/resume'
import { useDialogStore } from '@/stores/dialog'

const store = useResumeStore()
const dialog = useDialogStore()

const loadError = ref('')
const actionError = ref('')
const actionNotice = ref('')
/** 正在执行恢复 / 彻底删除的那一行，用于禁用按钮并显示进度。 */
const busyId = ref<string | null>(null)

const items = computed(() => store.trashProfiles)

/**
 * 后端 `deleted_at` 是东八区 ISO 串（`2026-11-03T20:12:00+08:00`）。
 *
 * 这里不用 `toLocaleString`：它会按浏览器时区二次换算，
 * 对带着 `+08:00` 的串做本地化等于「再偏移一次」，只有中国时区的用户看起来才对。
 * 直接截取日期与时分既没有时区风险，也和页面其他时间的写法一致。
 */
function formatDeletedAt(value: string | null): string {
  if (!value) return '—'
  const date = value.slice(0, 10)
  const time = value.slice(11, 16)
  return time ? `${date} ${time}` : date
}

function describeError(error: unknown, fallback: string): string {
  if (isApiError(error) && error.message) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}

async function load(): Promise<void> {
  loadError.value = ''
  try {
    await store.loadTrash()
  } catch (error) {
    loadError.value = describeError(error, '读取回收站失败，请刷新页面重试。')
  }
}

async function handleRestore(profileId: string, title: string): Promise<void> {
  if (busyId.value) return
  busyId.value = profileId
  actionError.value = ''
  actionNotice.value = ''
  try {
    await store.restoreProfile(profileId)
    actionNotice.value = `已恢复「${title}」，可以回到编辑器继续编辑。`
  } catch (error) {
    actionError.value = describeError(error, '恢复失败，请稍后重试。')
  } finally {
    busyId.value = null
  }
}

async function handlePurge(profileId: string, title: string): Promise<void> {
  if (busyId.value) return
  // 不可逆操作必须二次确认，文案直接把后果说清楚。
  const confirmed = await dialog.askConfirm(
    '彻底删除这份简历？',
    '这一步不可逆：回收站里的这一份会永久消失，恢复不了。',
    { subject: title, confirmLabel: '确认彻底删除', tone: 'danger' },
  )
  if (!confirmed) return

  busyId.value = profileId
  actionError.value = ''
  actionNotice.value = ''
  try {
    await store.purgeProfile(profileId)
    actionNotice.value = `已彻底删除「${title}」。`
  } catch (error) {
    actionError.value = describeError(error, '彻底删除失败，请稍后重试。')
  } finally {
    busyId.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="app-shell">
    <AppTopBar />

    <main class="trash-page">
      <div class="trash-heading">
        <div>
          <p class="eyebrow">简历管理</p>
          <h1>回收站</h1>
          <p class="intro">
            删除的简历会在这里保留 30 天，期间可以随时恢复；超过 30 天由服务端自动清理。
          </p>
        </div>
        <RouterLink class="back-link" :to="{ name: 'resume-editor' }">← 返回编辑器</RouterLink>
      </div>

      <p v-if="loadError" class="feedback feedback--error" role="alert">{{ loadError }}</p>
      <p v-else-if="store.trashLoading" class="feedback">正在加载回收站…</p>

      <p v-if="actionError" class="feedback feedback--error" role="alert">{{ actionError }}</p>
      <p v-if="actionNotice" class="feedback feedback--ok" role="status">{{ actionNotice }}</p>

      <section v-if="!loadError && !store.trashLoading" class="trash-panel">
        <p v-if="!items.length" class="empty">
          回收站是空的。被删除的简历会出现在这里。
        </p>

        <ul v-else class="trash-list">
          <li v-for="item in items" :key="item.id" class="trash-row">
            <div class="trash-main">
              <p class="trash-title">{{ item.title }}</p>
              <p class="trash-meta">
                <span>{{ item.full_name || '未填写姓名' }}</span>
                <span class="dot">·</span>
                <span>删除于 {{ formatDeletedAt(item.deleted_at) }}</span>
              </p>
            </div>
            <div class="trash-actions">
              <button
                class="row-button"
                type="button"
                :disabled="busyId === item.id"
                @click="handleRestore(item.id, item.title)"
              >
                {{ busyId === item.id ? '处理中…' : '恢复' }}
              </button>
              <button
                class="row-button row-button--danger"
                type="button"
                :disabled="busyId === item.id"
                @click="handlePurge(item.id, item.title)"
              >
                彻底删除
              </button>
            </div>
          </li>
        </ul>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; background: var(--ui-canvas); }
.trash-page { width: min(880px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.trash-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: 12px; letter-spacing: .12em; }
h1 { margin: 0; color: var(--ui-ink); font-size: 22px; font-weight: 650; }
.intro { margin: 8px 0 0; color: var(--ui-ink-muted); font-size: 13px; line-height: 1.6; }
.back-link { flex: 0 0 auto; color: var(--theme-primary); font-size: 13px; text-decoration: none; }
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
.row-button { border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 7px 14px; color: var(--theme-primary-deep); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 12px; line-height: 1.4; white-space: nowrap; }
.row-button:hover:not(:disabled) { background: var(--theme-bg-hover); }
.row-button--danger { border-color: var(--ui-danger-border); color: var(--ui-danger); }
.row-button--danger:hover:not(:disabled) { border-color: var(--ui-danger); background: var(--ui-danger-soft); }
.row-button:disabled { opacity: .6; cursor: progress; }
.row-button:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
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
