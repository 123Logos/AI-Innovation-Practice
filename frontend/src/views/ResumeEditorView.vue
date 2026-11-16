<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import ResumeEditor from '@/components/ResumeEditor.vue'
import ResumePreview from '@/components/ResumePreview.vue'
import StylePanel from '@/components/StylePanel.vue'
import AiKeyHint from '@/components/AiKeyHint.vue'
import { isApiError } from '@/api'
import { TaskFailedError } from '@/composables/usePollTask'
import { useAuthStore } from '@/stores/auth'
import { useDialogStore } from '@/stores/dialog'
import { useResumeStore } from '@/stores/resume'
import SkeletonList from '@/components/SkeletonList.vue'

const auth = useAuthStore()
const store = useResumeStore()
const dialog = useDialogStore()
const loadError = ref('')

/**
 * PDF 导入的两格状态（第 28 轮，`ADR-031`）。
 *
 * `feedback` 只管这一条路径，不接 `StylePanel` 那条（润色的提示住在它自己那一列）。
 * `feedbackCode` 单独留着是因为 `AiKeyHint` 认的是**原因码**而不是文字：
 * 五种 422 里只有「没配 Key」该给「点此配置 AI 密钥」那行链接，
 * 超时与上游挂了给这个链接只会让用户去改一把本来没坏的 Key。
 */
const importFeedback = ref<{ tone: 'ok' | 'error'; text: string } | null>(null)
const importCode = ref<string | null>(null)

/**
 * 任务进度（第 30 轮，`ADR-033`）：进度条与那行文字都读服务端的 `stage`/`progress`。
 *
 * **不在前端自己推**：`progress` 由 `app/core/task_kinds.py::progress_for()` 算，
 * 前端再算一遍就会出现「进度条到 100% 但状态还是 running」这种自相矛盾的画面。
 */
const importStageText = computed(() => {
  switch (store.importTask?.stage) {
    case 'queued':
      return '已排队，马上开始…'
    case 'extracting':
      return '正在提取文字…'
    case 'structuring':
      return '正在整理成档案…'
    default:
      return '正在解析 PDF…'
  }
})
const importProgress = computed(() => store.importTask?.progress ?? 5)
const fileInput = ref<HTMLInputElement | null>(null)

/** 草稿写入时间，仅用于提示文案。 */
const draftSavedAt = computed(() => {
  const at = store.pendingDraft?.saved_at
  if (!at) return ''
  return new Date(at).toLocaleString('zh-CN', { hour12: false })
})

/** 导入成功后那句「读了哪些、丢了哪些」。没有一条需要说的时候返回空串，整条横幅就不画。 */
const importedNotes = computed(() => {
  const meta = store.importedMeta
  if (!meta) return []
  const notes: string[] = []
  if (meta.pages_used < meta.pages_total) {
    notes.push(`这份 PDF 共 ${meta.pages_total} 页，只读了前 ${meta.pages_used} 页。`)
  }
  if (meta.text_truncated) {
    notes.push('文字太多，只处理了前面一部分。')
  }
  const dropped = meta.dropped
  const trimmed = [
    dropped.skills > 0 ? `${dropped.skills} 条技能` : '',
    dropped.experiences > 0 ? `${dropped.experiences} 段经历` : '',
    dropped.educations > 0 ? `${dropped.educations} 段教育经历` : '',
  ].filter(Boolean)
  if (trimmed.length) {
    notes.push(`超出表单条数上限，没有导入：${trimmed.join('、')}。`)
  }
  if (dropped.contact_discarded) {
    notes.push('PDF 里的手机号不是大陆 11 位号码，已留空。')
  }
  if (dropped.text_clamped) {
    notes.push('有描述或职责超出长度上限，已截断。')
  }
  return notes
})

function openFilePicker(): void {
  importFeedback.value = null
  importCode.value = null
  fileInput.value?.click()
}

/**
 * 选完文件：dirty 先拦一次确认，然后交给 store。
 *
 * `input.value = ''` 必须写在读取之后、请求之前：不清的话，用户导入 a.pdf 失败后
 * 再选同一个 a.pdf，`change` 不会触发（浏览器认为值没变），按钮就「点了没反应」。
 */
async function handleFileChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  if (store.dirty) {
    const agreed = await dialog.askConfirm(
      // 标题就是老板第 28 轮点名的那句话（逐字）；正文补的是他这句话里没说但必须看见的
      // 两件事：**不可撤回**，以及导入之后仍然要他自己点保存。
      '导入将覆盖当前未保存的修改，是否继续？',
      '表单里还没保存的改动会被 PDF 里的内容替换掉，且无法撤回。导入后仍需你点「保存修改」才会保存。',
      { confirmLabel: '继续导入', cancelLabel: '先不导入' },
    )
    // 取消就是停在这一步：不发请求、不动表单、不留下任何半截状态。
    if (!agreed) return
  }

  importFeedback.value = null
  importCode.value = null
  try {
    await store.importPdf(file)
  } catch (error) {
    importCode.value = isApiError(error)
      ? error.code
      : error instanceof TaskFailedError
        ? error.code
        : null
    importFeedback.value = { tone: 'error', text: importFailureText(error) }
    return
  }
  importFeedback.value = {
    tone: 'ok',
    text: '已按这份 PDF 填好表单。核对无误后点「保存修改」才会保存。',
  }
}

/**
 * 五种 422 各给一句「下一步做什么」（`ADR-031` 2.5 的码表在前端这一侧的落点）。
 *
 * 后端已经把句子放在受信目录里了，这里只补**导入这一条路径特有的上下文**：
 * 「手动填写」是这条路径唯一的退路，所以每个 AI 侧的失败都要把它说出来；
 * 而扫描件这件事只有用户能判断（我们分不清是扫描件还是加密），所以给的是动作不是原因。
 */
function importFailureText(error: unknown): string {
  // 第 30 轮（`ADR-033`）：失败发生在任务终态里，抛的是 `TaskFailedError`（带码与句子）。
  // 它**不是** `ApiError`——请求成功了，任务失败了；两者的下一步不同，不能合并成一个分支。
  if (error instanceof TaskFailedError) {
    // 任务终态里那句已经是受信目录里的话，而且**已经说清了下一步**
    // （AI_UNAVAILABLE 那句自带「本次请求未被保存，您的资料不受影响」）。
    // 在这里再拼一句只会变成两句同义话，读起来像系统在自我安慰。
    return error.message
  }
  if (!isApiError(error)) return '导入失败，请稍后重试。'
  switch (error.code) {
    case 'PDF_TOO_LARGE':
    case 'PDF_INVALID':
    case 'AI_KEY_MISSING':
    case 'AI_OUTPUT_INVALID':
      // 这四条后端那句中文已经说清了下一步（换文件 / 去设置 / 再试一次或手动填），
      // 原样上屏即可（第 26 轮定的 4xx 中文放行口径）。
      return error.message
    case 'AI_SERVICE_UNAVAILABLE':
    case 'RATE_LIMITED':
      return `${error.message}（本次没有改动表单，您的资料不受影响。）`
    default:
      return error.message
  }
}

/*
 * 已经完整加载过就不再重来（2026-11-14，UI 收尾第 5 条）。
 *
 * 从岗位库 / 测评切回工作台时，store 里还留着刚才那份草稿；再 `loadFromServer()`
 * 等于用服务端版本覆盖它（手改到一半的内容会没），还白花两次请求（列表 + 详情）。
 * `loadedFromServer` 由 store 在成功加载后置真、`reset()` 时置假，所以换账号、
 * 登出回来仍会重新拉。
 */
onMounted(async () => {
  if (store.loadedFromServer) {
    // 草稿自动保存与本地草稿探测仍要接上：卸载时它们被解绑了。
    const ownerId = auth.user?.id
    if (ownerId) {
      store.startDraftAutosave(ownerId)
      store.detectLocalDraft(ownerId)
    }
    return
  }
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

    <div class="import-bar">
      <button
        class="import-button"
        type="button"
        data-testid="import-pdf-button"
        :disabled="store.importing"
        @click="openFilePicker"
      >
        {{ store.importing ? '正在解析 PDF…' : '导入 PDF 简历' }}
      </button>
      <!-- 真正的选择器是这个看不见的 input：按钮只负责打开它（`ADR-031` 2.1 的第一步）。 -->
      <input
        ref="fileInput"
        class="import-file"
        type="file"
        accept="application/pdf"
        data-testid="import-pdf-input"
        @change="handleFileChange"
      />

      <p
        v-if="importFeedback"
        class="import-feedback"
        :class="importFeedback.tone === 'error' ? 'import-feedback--error' : 'import-feedback--ok'"
        :role="importFeedback.tone === 'error' ? 'alert' : 'status'"
        aria-live="polite"
        data-testid="import-feedback"
      >
        {{ importFeedback.text }}
      </p>
      <AiKeyHint :reason="importCode" />

      <!-- 读了哪几页、丢了几条：截断与丢弃必须说出来（`ADR-017` / `ADR-031` 2.3）。 -->
      <ul
        v-if="importedNotes.length"
        class="import-notes"
        role="status"
        aria-live="polite"
        data-testid="import-notes"
      >
        <li v-for="note in importedNotes" :key="note">{{ note }}</li>
      </ul>
    </div>

    <div class="resume-workspace">
      <ResumeEditor class="editor" />
      <ResumePreview />
      <StylePanel class="style-panel" />
    </div>

    <!--
      解析遮罩：外壳用全局 `.ui-overlay`（L3 现阶梯的 100 档），不在这里再写一个 z-index。
      它不是弹窗——没有关闭路径（解析期间关掉等于留下一个改了一半的表单），
      所以只带 `role="status"` 播报进度，不接 Esc、不接点遮罩。
    -->
    <div
      v-if="store.importing"
      class="ui-overlay import-mask"
      role="status"
      aria-live="polite"
      data-testid="import-mask"
    >
      <div class="ui-panel import-mask__card">
        <p class="import-mask__text">{{ importStageText }}</p>
        <!--
          进度条：`role="progressbar"` + 三个 aria 值，屏幕阅读器读得到「到哪一步了」。
          `width` 用内联样式而不是若干档类名：百分比是数据，不是样式。
        -->
        <div
          class="import-mask__bar"
          role="progressbar"
          :aria-valuenow="importProgress"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-label="importStageText"
          data-testid="import-progress"
        >
          <span class="import-mask__fill" :style="{ width: `${importProgress}%` }"></span>
        </div>
        <p class="import-mask__hint">这一步用的是你自己的 AI 用量，通常几秒到半分钟。</p>
      </div>
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
/* 单列堆叠时横幅与下面那一列对齐（横幅内缩 48，所以宽度要补 96 而不是 56）。 */
.load-error, .load-hint { width: min(calc(var(--paper-a4-width) + 96px), 100%); margin: 0 auto; padding: 12px 48px 0; font-size: var(--text-base); }
/* 骨架与它替换掉的那行提示占同一块地方：宽度与内缩一致，数据到达时不横向跳动。 */
.load-skeleton { width: min(calc(var(--paper-a4-width) + 96px), 100%); margin: 0 auto; padding: 12px 48px 0; }
.load-error { color: var(--ui-danger); }
.load-hint { color: var(--ui-ink-faint); }

/*
 * PDF 导入这一行：宽度与内缩跟着上面那条横幅走，两处不同宽会让用户以为
 * 是两个页面的两块东西（同一条列对齐规矩，见 `.draft-banner-shell`）。
 */
.import-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; width: min(calc(var(--paper-a4-width) + 96px), 100%); margin: 0 auto; padding: 12px 48px 0; }
.import-button { border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 8px 16px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: var(--text-base); }
.import-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.import-button:disabled { cursor: default; opacity: 0.6; }
.import-button:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
/* 隐藏的 file input：可点的是上面那个按钮，这一格只负责拿到 File（不进 Tab 序）。 */
.import-file { display: none; }
.import-feedback { margin: 0; font-size: var(--text-base); line-height: 1.5; }
.import-feedback--error { color: var(--ui-danger); }
.import-feedback--ok { color: var(--ui-ink-muted); }
/* 截断与丢弃的账：小字、逐条一行，读起来是「刚才发生了什么」而不是提示废话。 */
.import-notes { display: grid; gap: 2px; margin: 0; color: var(--ui-ink-faint); font-size: var(--text-sm); line-height: 1.5; list-style: none; padding: 0; }
.import-mask__card { text-align: center; }
.import-mask__text { margin: 0; color: var(--ui-ink); font-size: var(--text-lg); font-weight: 650; }
.import-mask__hint { margin: 8px 0 0; color: var(--ui-ink-muted); font-size: var(--text-sm); line-height: 1.5; }
/* 进度条：底轨与填充都用主题色令牌（G4 不许裸 hex）。高度 6px 是「看得见但不抢眼」的那一档。 */
.import-mask__bar { height: 6px; margin: 12px auto 0; border-radius: 999px; background: var(--theme-bg-light); overflow: hidden; }
.import-mask__fill { display: block; height: 100%; border-radius: 999px; background: var(--theme-primary); transition: width var(--motion-fast) ease-out; }

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
  .load-error, .load-hint, .load-skeleton, .draft-banner-shell, .import-bar { width: min(1440px, 100%); }
}
/*
 * 三列：只在纸面已经能按 A4 原大显示、还剩下侧栏的地方时才开（阈值 1434 的算式见文件头）。
 * 左右列按裁定压到 280 / 200，省下的宽度全给纸面。
 */
@container app-content (min-width: 1434px) {
  .resume-workspace { grid-template-columns: 280px minmax(0, 1fr) 200px; gap: 32px; padding: 32px 48px 72px; }
  .style-panel { grid-column: auto; }
  .load-error, .load-hint, .load-skeleton, .draft-banner-shell, .import-bar { padding: 12px 48px 0; }
}
@container app-content (max-width: 760px) {
  .resume-workspace { grid-template-columns: 1fr; gap: 24px; padding: 20px 16px 48px; }
  .editor, .style-panel { grid-column: auto; }
  .load-error, .load-hint, .load-skeleton, .draft-banner-shell, .import-bar { padding: 12px 16px 0; flex-direction: column; align-items: stretch; }
  .draft-banner { flex-direction: column; align-items: stretch; }
  .draft-banner__actions { justify-content: flex-end; }
}
@media print {
  .resume-workspace { display: block; padding: 0; }
  .editor, .style-panel, .load-error, .load-hint, .load-skeleton, .draft-banner-shell, .import-bar { display: none; }
}
</style>
