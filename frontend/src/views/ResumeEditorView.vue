<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import ResumeEditor from '@/components/ResumeEditor.vue'
import ResumePreview from '@/components/ResumePreview.vue'
import StylePanel from '@/components/StylePanel.vue'
import { isApiError } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useResumeStore } from '@/stores/resume'

const auth = useAuthStore()
const store = useResumeStore()
const loadError = ref('')

/** 草稿写入时间，仅用于提示文案。 */
const draftSavedAt = computed(() => {
  const at = store.pendingDraft?.saved_at
  if (!at) return ''
  return new Date(at).toLocaleString('zh-CN', { hour12: false })
})

onMounted(async () => {
  try {
    await store.loadFromServer()
  } catch (error) {
    loadError.value = isApiError(error)
      ? `读取档案失败：${error.message}`
      : '读取档案失败，请刷新页面重试。'
    // 读取失败时表单是空白骨架，若此时开启自动保存会覆盖已有草稿，所以直接返回。
    return
  }

  const ownerId = auth.user?.id
  if (!ownerId) return
  store.startDraftAutosave(ownerId)
  // 必须在 loadFromServer() 之后：判定「草稿是否值得恢复」的基准是服务端数据。
  store.detectLocalDraft(ownerId)
})

onUnmounted(() => {
  // 先 flush 再解绑：去回收站 / 切路由时，防抖里没写完的编辑也要留下来。
  store.flushDraft()
  store.stopDraftAutosave()
})
</script>

<template>
  <p v-if="loadError" class="load-error">{{ loadError }}</p>
  <p v-else-if="store.loading" class="load-hint">正在加载档案…</p>

  <div v-if="store.pendingDraft" class="draft-banner-shell">
    <section class="draft-banner">
      <p class="draft-banner__text">
        检测到上次未保存的草稿<template v-if="draftSavedAt">（{{ draftSavedAt }}）</template>，
        是否恢复？
      </p>
      <div class="draft-banner__actions">
        <button class="draft-banner__restore" type="button" @click="store.restoreDraft()">
          恢复草稿
        </button>
        <button class="draft-banner__discard" type="button" @click="store.discardDraft()">
          丢弃
        </button>
      </div>
    </section>
  </div>

  <main class="resume-workspace">
    <ResumeEditor class="editor" />
    <ResumePreview />
    <StylePanel class="style-panel" />
    <p class="footer-hint">
      表单与纸面的改动都要点「保存修改」才会保存 · 生成结果基于「已保存」的档案版本
    </p>
  </main>
</template>

<style scoped>
/*
 * 三列的宽度判据走**容器**而不是视口（批次 F3 第三片）：清单第 5 条给外框加了一列常驻简历栏，
 * 内容区从此比视口窄 240px。继续按视口判，1280px 的屏幕会以为自己有 1280px 可用，
 * 于是摆下三列，而实际只有 1025px。阈值仍是令牌表里那两个值（1100 / 760），
 * 只是量的是 `app-content` 这一列的宽度。
 *
 * 纸面那一列的下限从 420px 改成 0：纸面自己用 ResizeObserver 量列宽再 `transform: scale`
 * （`ResumePreview.vue`），硬下限不会让纸更可读，只会把网格撑破。
 * 这不是推测——把旧规则注回去量过（反证探针，数值在本片 PR 里）：左栏在场时，
 * `340px minmax(420px,1fr) 220px` 在 1120 档把文档撑到 1296px（溢出 176px）、
 * 1280 档溢出 16px。改成容器查询 + 下限 0 之后，四档（375/1120/1280/1440）溢出都是 0。
 */
.resume-workspace { display: grid; grid-template-columns: 340px minmax(0, 1fr) 220px; align-items: start; gap: 32px; width: min(1440px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.footer-hint { grid-column: 1 / -1; margin: 0; color: var(--ui-ink-faint); font-size: 12px; text-align: center; }
.load-error, .load-hint { width: min(1440px, 100%); margin: 0 auto; padding: 12px 48px 0; font-size: 13px; }
.load-error { color: var(--ui-danger); }
.load-hint { color: var(--ui-ink-faint); }

/* 本地草稿恢复提示：用页面内联条，不用 window.confirm —— 可样式化、不阻塞、可测。 */
.draft-banner-shell { width: min(1440px, 100%); margin: 0 auto; padding: 12px 48px 0; }
.draft-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px 16px;
  border: 1px solid var(--theme-rule-soft);
  border-left: 4px solid var(--theme-primary);
  border-radius: var(--ui-radius);
  padding: 12px 16px;
  background: var(--theme-bg-light);
}
.draft-banner__text { margin: 0; color: var(--theme-primary-strong); font-size: 13px; line-height: 1.5; }
.draft-banner__actions { display: flex; flex-wrap: wrap; gap: 8px; }
.draft-banner__restore { border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 8px 16px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: 13px; }
.draft-banner__restore:hover { background: var(--theme-primary-hover); }
.draft-banner__discard { border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 8px 16px; color: var(--ui-ink-muted); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 13px; }
.draft-banner__discard:hover { border-color: var(--ui-danger-border); color: var(--ui-danger); }
.draft-banner__restore:focus-visible, .draft-banner__discard:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
@container app-content (max-width: 1100px) {
  .resume-workspace { grid-template-columns: 300px minmax(0, 1fr); gap: 24px; padding: 24px 28px 56px; }
  .style-panel { grid-column: 1 / -1; }
  .load-error, .load-hint, .draft-banner-shell { padding: 12px 28px 0; }
}
@container app-content (max-width: 760px) {
  .resume-workspace { grid-template-columns: 1fr; gap: 24px; padding: 20px 16px 48px; }
  .editor, .style-panel { grid-column: auto; }
  .footer-hint { grid-column: auto; }
  .load-error, .load-hint, .draft-banner-shell { padding: 12px 16px 0; }
  .draft-banner { flex-direction: column; align-items: stretch; }
  .draft-banner__actions { justify-content: flex-end; }
}
@media print {
  .resume-workspace { display: block; padding: 0; }
  .editor, .style-panel, .footer-hint, .load-error, .load-hint, .draft-banner-shell { display: none; }
}
</style>
