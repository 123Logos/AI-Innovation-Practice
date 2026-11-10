<script setup lang="ts">
import { computed, ref } from 'vue'

import { isApiError } from '@/api'
import type { PaperSize, PolishMode, SectionId, ThemeId } from '@/api/types'
import { LAYOUT_TITLE_MAX_LENGTH, THEME_OPTIONS, useResumeStore } from '@/stores/resume'
import { useDialogStore } from '@/stores/dialog'

import PolishReviewDialog from './PolishReviewDialog.vue'
import PolishSettingsDialog from './PolishSettingsDialog.vue'

const store = useResumeStore()
const dialog = useDialogStore()

const feedback = ref<{ tone: 'ok' | 'error'; text: string } | null>(null)

/** 润色档位弹窗与对照弹窗的开关（两个弹窗是两段独立的用户决定）。 */
const settingsOpen = ref(false)
const reviewOpen = ref(false)

/** 纸张尺寸展示：`215.9` 这类小数保留一位，`210` 不补 `.0`。 */
function formatMm(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

const paperSizeText = computed(
  () => `${formatMm(store.paperSpec.widthMm)} × ${formatMm(store.paperSpec.heightMm)} mm`,
)

/**
 * 切换纸张（D5.1）。
 *
 * 走 store 的 `updatePaperSize()` 而不是直接调接口：它和改姓名一样属于
 * 「未保存的修改」，要跟着「保存修改」一起落库，也才会触发本地草稿。
 */
function handlePaperChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  const size: PaperSize = value === 'Letter' ? 'Letter' : 'A4'
  store.updatePaperSize(size)
}

/**
 * 切换配色（D5.2）。
 *
 * 只把 id 交给 store：色值、脏标记、以及「已生成简历」视图的自动退出
 * （含提示文案）都在 store 里统一处理，面板只负责展示。
 * 这里**不接受任何自定义色值**——8 套预设是离线验过对比度的，
 * 放开 HEX 输入就等于把对比度问题甩给运行时（ADR-007 §5.2）。
 */
function handleThemeChange(theme: ThemeId): void {
  feedback.value = null
  store.updateTheme(theme)
}

/**
 * 改模块标题（D5.3）。
 *
 * 用 `:value` + `@input` 而不是 `v-model`：标题存在 Pinia 里，必须经过
 * `updateSectionTitle()` 才能置 `dirty`（否则「改了标题但按钮还写着已保存」）。
 * 输入过程不 trim、不截断成默认值 —— 理由见 store 里的注释。
 */
function handleSectionTitle(id: SectionId, event: Event): void {
  feedback.value = null
  store.updateSectionTitle(id, (event.target as HTMLInputElement).value)
}

/**
 * 档案状态跟「是否有未保存修改」联动：
 * 保存入口合并成一个后，这里若还按 `hasSavedProfile` 单独显示，
 * 用户会看到「档案 已保存」但按钮写着「保存修改」，两处互相打架。
 */
const profileStatus = computed(() => {
  if (store.dirty) return '待保存'
  return store.hasSavedProfile ? '已保存' : '未保存'
})
const resumeStatus = computed(() =>
  store.generatedResumeId ? `已生成 #${store.generatedResumeId.slice(0, 8)}` : '未生成',
)

/**
 * 生成简历：**纯模板**，读库里已保存的数据（ADR-010）。
 *
 * 与润色的分工在这里就说清了：这里只问「未保存的修改要不要先存」，
 * 不再问「AI 挂了要不要改用模板」——它本来就是模板，没有降级这一说。
 */
async function handleGenerate(): Promise<void> {
  feedback.value = null

  // 保存已经不再拦必填，所以「能不能生成」的判断全部落在这里（严格校验）。
  const issue = store.validateForGenerate()
  if (issue) {
    feedback.value = { tone: 'error', text: issue }
    return
  }

  // 未保存的修改不能悄悄丢掉：先确认，再保存，最后生成。
  if (store.dirty) {
    const shouldSave = await dialog.askConfirm(
      '生成前先保存修改？',
      '生成读的是已经保存的那一版。不保存就生成，你刚才改的内容不会出现在新简历里。',
      { confirmLabel: '保存并生成', cancelLabel: '先不生成' },
    )
    if (!shouldSave) {
      feedback.value = {
        tone: 'error',
        text: '已取消生成：请先保存修改，或再次点击「生成简历」并按提示确认保存。',
      }
      return
    }
    try {
      await store.save()
    } catch (error) {
      feedback.value = {
        tone: 'error',
        text: isApiError(error) ? `保存失败：${error.message}` : '保存失败，请稍后重试。',
      }
      return
    }
  }

  try {
    const resume = await store.generate('我的简历')
    feedback.value = {
      tone: 'ok',
      text: `已按模板生成并保存好了（记录号 #${resume.resume_id.slice(0, 8)}）。`,
    }
  } catch (error) {
    feedback.value = {
      tone: 'error',
      text: isApiError(error) ? error.message : '生成失败，请稍后重试。',
    }
  }
}

/* -------------------------------- AI 润色 -------------------------------- */

/**
 * 点「AI 智能润色」：先过润色自己的门槛，再开档位弹窗。
 *
 * 这里**不要求先保存**：润色吃的就是草稿态（服务端不落库），
 * 逼用户存一次才能看文案，等于把「润色 → 改 → 再润色」的循环切成两次点击。
 */
function openPolishSettings(): void {
  feedback.value = null
  const issue = store.validateForPolish()
  if (issue) {
    feedback.value = { tone: 'error', text: issue }
    return
  }
  settingsOpen.value = true
}

/** 档位弹窗确认 → 发请求 → 成功就打开对照弹窗；失败只给一句人话，不动表单。 */
async function handlePolishConfirm(options: { mode: PolishMode; maxWords: number }): Promise<void> {
  settingsOpen.value = false
  feedback.value = { tone: 'ok', text: 'AI 正在改写文案，请稍候…' }

  // 结果由 store 存进 `polishResult`（对照弹窗直接读它），这里不需要接返回值。
  try {
    await store.polish(options)
  } catch (error) {
    feedback.value = { tone: 'error', text: polishFailureText(error) }
    return
  }

  feedback.value = null
  reviewOpen.value = true
}

/**
 * 润色失败的分支文案。
 *
 * 每个码给的**下一步**不一样，这是 ADR-010 删掉模板兜底之后剩下的唯一 guidance：
 * 没 Key 去设置、Key 无效去修 Key、上游挂了只能重试（生成简历那条路不受影响）。
 */
function polishFailureText(error: unknown): string {
  if (!isApiError(error)) return '润色失败，请稍后重试。'
  switch (error.code) {
    case 'AI_KEY_MISSING':
      // ui-copy:allow C2 唯一豁免：用户必须亲手去 DeepSeek 拿这把凭据，界面里的原词可以出现
      return '请先在右上角「AI 设置」里填好你的 DeepSeek API Key。'
    case 'AI_KEY_INVALID':
      // ui-copy:allow 同上：这句话指向他自己粘 Key 的那个输入框
      return '你的 DeepSeek API Key 无效或已欠费，请打开「AI 设置」检查后重试。'
    case 'AI_SERVICE_UNAVAILABLE':
      return `${error.message}（生成简历不受影响，可以先直接生成。）`
    case 'RATE_LIMITED':
      return '润色太频繁了，请等一分钟再试——这一步用的是你自己的 AI 用量。'
    case 'VALIDATION_ERROR':
      return '润色请求没有被接受：技能与项目经历要至少各 1 条，且不能太长。'
    case 'NETWORK_ERROR':
      return '等 AI 返回超时，或者连不上服务。先重试一次；如果一直不行，是你这边之外的问题，与填的内容无关。'
    default:
      return error.message
  }
}

/** 接受：把候选文案同步进表单并置 dirty，但**不落库**——保存仍由用户点。 */
function handleAcceptPolish(): void {
  const result = store.polishResult
  reviewOpen.value = false
  if (!result) return
  store.applyPolish(result)
  feedback.value = {
    tone: 'ok',
    text: '已同步到左侧表单。核对无误后点「保存修改」才会保存，也可以先再生成一次简历。',
  }
}

/** 放弃（含直接关窗）：清掉候选文案，表单一个字段都不动。 */
function handleDiscardPolish(): void {
  reviewOpen.value = false
  if (!store.polishResult) return
  store.discardPolish()
  feedback.value = { tone: 'error', text: '已放弃这次润色结果，表单保持原样。' }
}

function printResume(): void {
  window.print()
}
</script>

<template>
  <aside class="style-panel">
    <div class="panel-heading">
      <p class="eyebrow">辅助面板</p>
      <h2>版式检查</h2>
    </div>

    <div class="panel-section">
      <div class="status-row">
        <span>纸张</span>
        <select
          class="paper-select"
          :value="store.draft.paper_size"
          aria-label="纸张尺寸"
          @change="handlePaperChange"
        >
          <option value="A4">A4</option>
          <option value="Letter">Letter</option>
        </select>
      </div>
      <p class="status-hint">{{ paperSizeText }}</p>
      <div class="status-row"><span>页数</span><strong>{{ store.pageCount }} 页</strong></div>
      <div class="status-row"><span>配色</span><strong>{{ store.themeSpec.name }}</strong></div>
      <!-- 8 套预设：圆点用 `accent`（真正染在 h2 上的那个色）做预览。 -->
      <div class="theme-swatches" role="radiogroup" aria-label="配色主题">
        <button
          v-for="option in THEME_OPTIONS"
          :key="option.id"
          class="swatch"
          :class="{ 'swatch--active': store.draft.theme === option.id }"
          type="button"
          role="radio"
          :aria-checked="store.draft.theme === option.id"
          :aria-label="option.name"
          :title="option.name"
          :style="{ background: option.accent }"
          @click="handleThemeChange(option.id)"
        />
      </div>
      <p v-if="store.styleNotice" class="notice">
        {{ store.styleNotice }}
        <button class="notice__close" type="button" @click="store.dismissStyleNotice()">
          知道了
        </button>
      </p>
    </div>

    <!--
      排版（D5.3）：只做「顺序 + 标题」，不做显隐（ADR-007 §6.8(6)）。
      交互刻意用上下箭头而不是拖拽：纸面上有 13 处 contenteditable，
      mousedown+mousemove 会被浏览器解释成「选文字」，拖拽与编辑必然抢手势
      （ADR-007 §8.1）；而且这里明确不引入任何拖拽库。
    -->
    <div class="panel-section">
      <p class="panel-label">排版与标题</p>
      <div v-for="(section, index) in store.layoutSections" :key="section.id" class="layout-row">
        <input
          class="layout-title"
          type="text"
          :value="section.title"
          :maxlength="LAYOUT_TITLE_MAX_LENGTH"
          :aria-label="`${store.sectionTitle(section)}的标题`"
          spellcheck="false"
          @input="handleSectionTitle(section.id, $event)"
        />
        <div class="layout-actions">
          <button
            class="layout-arrow"
            type="button"
            :disabled="index === 0"
            :aria-label="`上移${store.sectionTitle(section)}`"
            title="上移"
            @click="store.moveSectionUp(section.id)"
          >
            上移
          </button>
          <button
            class="layout-arrow"
            type="button"
            :disabled="index === store.layoutSections.length - 1"
            :aria-label="`下移${store.sectionTitle(section)}`"
            title="下移"
            @click="store.moveSectionDown(section.id)"
          >
            下移
          </button>
        </div>
      </div>
      <p class="layout-hint">
        顺序即纸面顺序；标题最多 {{ LAYOUT_TITLE_MAX_LENGTH }} 字（超过会折行挤乱版式）。
      </p>
    </div>

    <div class="panel-section">
      <p class="panel-label">数据状态</p>
      <div class="status-row"><span>档案</span><strong>{{ profileStatus }}</strong></div>
      <div class="status-row"><span>简历</span><strong>{{ resumeStatus }}</strong></div>
      <div class="status-row"><span>最近保存</span><strong>{{ store.lastSavedAt ?? '—' }}</strong></div>
      <p v-if="store.dirty" class="warning">
        有未保存的修改：点预览区上方的「保存修改」才会保存，或直接生成（会先询问是否保存）。
      </p>
    </div>

    <!--
      两个动作刻意分成两个按钮（ADR-010）：
      「生成简历」= 库里数据套模板，免费、秒出、必然成功；
      「AI 智能润色」= 花用户自己的 DeepSeek 额度改写文案，且必须经用户确认才进表单。
      合在一个按钮里，用户就无法只走其中一条。
    -->
    <div class="panel-section">
      <button
        class="generate-button"
        type="button"
        :disabled="store.generating"
        @click="handleGenerate"
      >
        {{ store.generating ? '正在生成，请稍候…' : '生成简历' }}
      </button>
      <button
        class="polish-button"
        type="button"
        :disabled="store.polishing"
        @click="openPolishSettings"
      >
        {{ store.polishing ? 'AI 润色中，请稍候…' : 'AI 润色' }}
      </button>
      <p v-if="feedback" :class="['feedback', `feedback--${feedback.tone}`]">{{ feedback.text }}</p>
    </div>

    <div class="panel-section">
      <p class="panel-label">编辑提示</p>
      <p class="hint">
        点击中央纸面上的文字块即可直接编辑，内容会同步到左侧表单；改完点上方「保存修改」才会保存。
      </p>
      <p class="hint">
        「AI 润色」只改左侧表单里的描述与职责，不会自动保存，也不会自动替换——
        对照弹窗里点「接受」才填进来，之后仍需点「保存修改」。
      </p>
      <p class="hint print-tip">
        打印对话框里的纸张要与上方一致（{{ store.paperSpec.label }}），并取消勾选「页眉和页脚」
      </p>
    </div>

    <button class="print-button" type="button" @click="printResume">打印 / 导出 PDF</button>

    <!-- 润色的两段用户决定：先选档位，再对照确认。 -->
    <PolishSettingsDialog v-model="settingsOpen" @confirm="handlePolishConfirm" />
    <PolishReviewDialog
      v-model="reviewOpen"
      :result="store.polishResult"
      @accept="handleAcceptPolish"
      @discard="handleDiscardPolish"
    />
  </aside>
</template>

<style scoped>
/* 色值一律走 `--ui-*` / `--theme-*` 令牌：这个文件原先有 18 个硬编码 hex，
 * 暗色靠一整块 `@media (prefers-color-scheme: dark)` 逐条补，补漏的那几条
 * （`.feedback--ok` 的 #3f7a5f、`.feedback--error` 的 #9a5e58、`.print-tip` 的 #8a7c3f）
 * 在深墨绿底上只有 2.6:1，读不清。令牌化后暗色由令牌统一负责，这里不再写第二遍。 */
.style-panel { min-width: 0; color: var(--ui-ink); }
.panel-heading { border-bottom: 1px solid var(--ui-border); padding-bottom: 16px; }
.eyebrow { margin: 0 0 8px; color: var(--theme-rule); font-size: 12px; letter-spacing: .12em; }
h2, p { margin: 0; }
h2 { font-size: 18px; }
.panel-section { display: grid; gap: 10px; border-bottom: 1px solid var(--ui-border); padding: 18px 0; }
.status-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--ui-ink-muted); font-size: 13px; }
.status-row strong { color: var(--ui-ink); font-family: "SF Mono", Consolas, monospace; font-weight: 500; }
/* 纸张切换：下拉比按钮省地方，也天然表达「二选一」。 */
.paper-select {
  flex: 0 0 auto;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 3px 6px;
  color: var(--ui-ink);
  background: var(--ui-surface);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}
.paper-select:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
/* 物理尺寸小字：让「A4 / Letter 到底差多少」一眼可见，不用去查。 */
.status-hint { margin: -6px 0 0; color: var(--ui-ink-faint); font-size: 11px; text-align: right; }
/* 8 个色点：外圈用 box-shadow 做选中环，避免 border 改变尺寸导致换选时抖动。 */
.theme-swatches { display: flex; flex-wrap: wrap; gap: 8px; }
.swatch {
  width: 22px;
  height: 22px;
  border: 1px solid var(--ui-surface);
  border-radius: 50%;
  padding: 0;
  box-shadow: 0 0 0 1px var(--ui-border-strong);
  cursor: pointer;
  transition: box-shadow .12s ease;
}
.swatch:hover { box-shadow: 0 0 0 2px var(--ui-ink-faint); }
.swatch--active { box-shadow: 0 0 0 2px var(--ui-ink); }
.swatch:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
/* 排版（D5.3）：一行 = 标题输入框 + 上下箭头。窄侧栏里也不换行。 */
.layout-row { display: flex; align-items: center; gap: 6px; }
.layout-title {
  flex: 1 1 auto;
  min-width: 0;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 4px 6px;
  color: var(--ui-ink);
  background: var(--ui-surface);
  font: inherit;
  font-size: 13px;
}
.layout-title:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.layout-actions { display: flex; flex: 0 0 auto; gap: 4px; }
.layout-arrow {
  /* 文案清扫（批次 F1a）把箭头符号换成「上移/下移」，宽度必须让给文字，否则两个字会被 26px 挤掉。 */
  width: auto;
  min-width: 26px;
  padding: 0 6px;
  height: 26px;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 0;
  color: var(--theme-primary);
  background: var(--ui-surface);
  cursor: pointer;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  line-height: 1;
}
.layout-arrow:hover:not(:disabled) { border-color: var(--theme-primary); background: var(--theme-bg-light); }
/* 到边界时禁用：一眼可见「已经到顶 / 到底」，不必点了才知道没反应。 */
.layout-arrow:disabled { color: var(--el-text-color-disabled); cursor: not-allowed; }
.layout-arrow:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }
.layout-hint { color: var(--ui-ink-faint); font-size: 11px; line-height: 1.5; }
/* 样式改动把「已生成简历」顶掉后的提示：琥珀色，与「未保存」同色系但不抢眼。 */
.notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0;
  border-left: 3px solid var(--ui-warning-border);
  padding: 6px 0 6px 8px;
  color: var(--ui-warning);
  background: var(--ui-warning-soft);
  font-size: 11px;
  line-height: 1.5;
}
.notice__close {
  flex: 0 0 auto;
  border: 0;
  border-radius: var(--ui-radius);
  padding: 2px 4px;
  color: var(--ui-warning);
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  text-decoration: underline;
}
.notice__close:focus-visible { outline: 2px solid var(--ui-warning-border); outline-offset: 1px; }
.panel-label { color: var(--theme-primary); font-size: 12px; }
.hint { color: var(--ui-ink-muted); font-size: 13px; line-height: 1.6; }
/* 浏览器打印页眉/页脚（日期、URL）无法用代码关掉，只能提示用户自己取消勾选。 */
.print-tip { color: var(--ui-warning); font-size: 12px; line-height: 1.5; }
.warning { color: var(--ui-warning); font-size: 11px; line-height: 1.5; }
.generate-button { width: 100%; border: 0; border-radius: var(--ui-radius); padding: 11px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: 13px; line-height: 1.4; }
.generate-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.generate-button:disabled { opacity: .6; cursor: progress; }
/* 润色是「可选的第二步」，所以描边而不是实心：与「生成简历」同宽但明显轻一级。 */
.polish-button { width: 100%; border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 10px; color: var(--theme-primary); background: transparent; cursor: pointer; font: inherit; font-size: 13px; line-height: 1.4; }
.polish-button:hover:not(:disabled) { color: var(--theme-on-primary); background: var(--theme-primary); }
.polish-button:disabled { opacity: .6; cursor: progress; }
.feedback { font-size: 12px; line-height: 1.5; }
.feedback--ok { color: var(--ui-success); }
.feedback--error { color: var(--ui-danger); }
.print-button { width: 100%; margin-top: 20px; border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 10px; color: var(--theme-primary); background: transparent; cursor: pointer; font: inherit; font-size: 13px; line-height: 1.4; }
.print-button:hover { color: var(--theme-on-primary); background: var(--theme-primary); }
</style>
