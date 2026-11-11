<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import ResumeEditor from '@/components/ResumeEditor.vue'
import ResumePreview from '@/components/ResumePreview.vue'
import StylePanel from '@/components/StylePanel.vue'
import { isApiError } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useResumeStore } from '@/stores/resume'
import SkeletonList from '@/components/SkeletonList.vue'

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
  <!--
    单根（批次 F6，守则 R1）：外框给 `<RouterView>` 套了 `<Transition>`（批次 F4），
    而 `<Transition>` 只认**单根**——原来这里是四个并列根（错误 / 骨架 / 草稿横幅 / 工作区），
    于是这一页的淡入静默失效，每次进页面还报一条
    「Component inside <Transition> renders non-element root node」。
    收进一个 `<main>` 之后过渡真的生效，页面地标也仍是恰好一个 main。
  -->
  <main class="editor-page">
    <p v-if="loadError" class="load-error">{{ loadError }}</p>
    <!-- 稳定态实测：表单前三段 1280 档 311/84/68px、375 档 311/68/68px，段间距 20px（`.form-section`）。 -->
    <SkeletonList
      v-else-if="store.loading"
      class="load-skeleton"
      label="正在加载档案…"
      :heights="[311, 84, 68]"
      :heights-compact="[311, 68, 68]"
      :gap="20"
    />

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

    <div class="resume-workspace">
      <ResumeEditor class="editor" />
      <ResumePreview />
      <StylePanel class="style-panel" />
      <p class="footer-hint">
        表单与纸面的改动都要点「保存修改」才会保存 · 生成结果基于「已保存」的档案版本
      </p>
    </div>
  </main>
</template>

<style scoped>
/*
 * 列宽判据走**容器**而不是视口（批次 F3 第三片）：清单第 5 条给外框加了一列常驻简历栏，
 * 内容区从此比视口窄 240px。继续按视口判，1280px 的屏幕会以为自己有 1280px 可用。
 *
 * **一条底线管三档**（老板 2026-11-11 裁定，`ROADMAP.md` §6 第 8 条 + §6b 第 3 条）：
 * 「绝不能接受屏幕更宽，简历更小」。这句话不是只针对三列——每一处「多一列」都要过它。
 * 三处都是同一个算术：**多一列就多花「一列宽 + 一个间距」，压到多窄都追不回来**，
 * 所以每一档的阈值都必须是「纸面已经封顶 A4 原大 794px、分完还剩 794」的那个点。
 *
 * 1. **单列堆叠是常态**（容器 < 1174）：整列居中、封顶 `--paper-a4-width`（794px = 210mm）。
 *    原来这里在容器 760 就换并排，于是 760 档纸面 713px、1024 档反而只剩 629px。
 *    并排的阈值 = 794 + 300（表单下限）+ 24（间距）+ 56（内缩）= **1174**。
 *    代价说清楚：841–1188 这段表单在纸面**上方**而不是左边，要滚动才看得到预览；
 *    换来的是这段宽度里纸面是 1:1 原大，而不是缩到 0.79。
 * 2. **两列**（容器 ≥1174）：纸面封顶 794，多出来的宽度给编辑列（`minmax(300px, 1fr)`），
 *    不是留一片空白，也不是把纸越拉越大。
 * 3. **三列**（容器 ≥1434）：阈值 = 794 + 280 + 200 + 间距 64 + 内缩 96 = **1434**（约视口 1690）。
 *    左右列按裁定压到 280 / 200，省下的宽度全给纸面。
 *
 * 实测（CDP，13 档）纸面宽度：375→343、760→713、1024→794、1100→794、1120→794、
 * 1280→794、1366→794、1439→794、1440→794、1536→794、1600→794、1700→800、1920→800，
 * **全程单调不减**，且每一档都不小于改造前。横向溢出 13 档全 0。
 *
 * 纸面那一列的下限是 0：纸面自己用 ResizeObserver 量列宽再 `transform: scale`
 * （`ResumePreview.vue`），硬下限不会让纸更可读，只会把网格撑破（反证探针量过：
 * 旧的 `minmax(420px,1fr)` 在 1120 档溢出 176px、1280 档溢出 16px）。
 */
.resume-workspace { display: grid; grid-template-columns: minmax(0, var(--paper-a4-width)); justify-content: center; align-items: start; gap: 24px; width: min(1440px, 100%); margin: 0 auto; padding: 24px 28px 56px; }
/* 两列时主题面板横跨整行摆在下面；三列时它才回到自己那一列。 */
.style-panel { grid-column: 1 / -1; }
.footer-hint { grid-column: 1 / -1; margin: 0; color: var(--ui-ink-faint); font-size: var(--text-sm); text-align: center; }
/* 单列堆叠时横幅与下面那一列对齐（横幅内缩 48，所以宽度要补 96 而不是 56）。 */
.load-error, .load-hint { width: min(calc(var(--paper-a4-width) + 96px), 100%); margin: 0 auto; padding: 12px 48px 0; font-size: var(--text-base); }
/* 骨架与它替换掉的那行提示占同一块地方：宽度与内缩一致，数据到达时不横向跳动。 */
.load-skeleton { width: min(calc(var(--paper-a4-width) + 96px), 100%); margin: 0 auto; padding: 12px 48px 0; }
.load-error { color: var(--ui-danger); }
.load-hint { color: var(--ui-ink-faint); }

/* 本地草稿恢复提示：用页面内联条，不用 window.confirm —— 可样式化、不阻塞、可测。 */
.draft-banner-shell { width: min(calc(var(--paper-a4-width) + 96px), 100%); margin: 0 auto; padding: 12px 48px 0; }
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
.draft-banner__text { margin: 0; color: var(--theme-primary-strong); font-size: var(--text-base); line-height: 1.5; }
.draft-banner__actions { display: flex; flex-wrap: wrap; gap: 8px; }
.draft-banner__restore { border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 8px 16px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: var(--text-base); }
.draft-banner__restore:hover { background: var(--theme-primary-hover); }
.draft-banner__discard { border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 8px 16px; color: var(--ui-ink-muted); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-base); }
.draft-banner__discard:hover { border-color: var(--ui-danger-border); color: var(--ui-danger); }
.draft-banner__restore:focus-visible, .draft-banner__discard:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
/*
 * 两列：纸面已经能按 A4 原大显示、分给表单 300 + 间距 24 + 内缩 56 之后还剩 794 时才开
 * （阈值 1174 的算式见文件头）。低于它保持单列堆叠——并排会让纸面比单列时更小。
 */
@container app-content (min-width: 1174px) {
  .resume-workspace { grid-template-columns: minmax(300px, 1fr) minmax(0, var(--paper-a4-width)); justify-content: stretch; }
  .load-error, .load-hint, .load-skeleton, .draft-banner-shell { width: min(1440px, 100%); }
}
/*
 * 三列：只在纸面已经能按 A4 原大显示、还剩下侧栏的地方时才开（阈值 1434 的算式见文件头）。
 * 左右列按裁定压到 280 / 200，省下的宽度全给纸面。
 */
@container app-content (min-width: 1434px) {
  .resume-workspace { grid-template-columns: 280px minmax(0, 1fr) 200px; gap: 32px; padding: 32px 48px 72px; }
  .style-panel { grid-column: auto; }
  .load-error, .load-hint, .load-skeleton, .draft-banner-shell { padding: 12px 48px 0; }
}
@container app-content (max-width: 760px) {
  .resume-workspace { grid-template-columns: 1fr; gap: 24px; padding: 20px 16px 48px; }
  .editor, .style-panel { grid-column: auto; }
  .footer-hint { grid-column: auto; }
  .load-error, .load-hint, .load-skeleton, .draft-banner-shell { padding: 12px 16px 0; }
  .draft-banner { flex-direction: column; align-items: stretch; }
  .draft-banner__actions { justify-content: flex-end; }
}
@media print {
  .resume-workspace { display: block; padding: 0; }
  .editor, .style-panel, .footer-hint, .load-error, .load-hint, .load-skeleton, .draft-banner-shell { display: none; }
}
</style>
