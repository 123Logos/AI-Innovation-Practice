<script setup lang="ts">
import { computed, ref } from 'vue'

import { isApiError } from '@/api'
import type { PaperSize, SectionId, ThemeId } from '@/api/types'
import { LAYOUT_TITLE_MAX_LENGTH, THEME_OPTIONS, useResumeStore } from '@/stores/resume'

const store = useResumeStore()

const feedback = ref<{ tone: 'ok' | 'error'; text: string } | null>(null)

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
    const shouldSave = window.confirm('你有未保存的修改，是否先保存再生成？')
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
      text: `已生成并保存到数据库（#${resume.resume_id.slice(0, 8)}）。`,
    }
  } catch (error) {
    feedback.value = {
      tone: 'error',
      text: isApiError(error) ? error.message : '生成失败，请稍后重试。',
    }
  }
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
            ↑
          </button>
          <button
            class="layout-arrow"
            type="button"
            :disabled="index === store.layoutSections.length - 1"
            :aria-label="`下移${store.sectionTitle(section)}`"
            title="下移"
            @click="store.moveSectionDown(section.id)"
          >
            ↓
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
        有未保存的修改：点预览区上方的「保存修改」落库，或直接生成（会先询问是否保存）。
      </p>
    </div>

    <div class="panel-section">
      <button class="generate-button" type="button" :disabled="store.generating" @click="handleGenerate">
        {{ store.generating ? '生成中…' : '生成简历' }}
      </button>
      <p v-if="feedback" :class="['feedback', `feedback--${feedback.tone}`]">{{ feedback.text }}</p>
    </div>

    <div class="panel-section">
      <p class="panel-label">编辑提示</p>
      <p class="hint">
        点击中央纸面上的文字块即可直接编辑，内容会同步到左侧表单；改完点上方「保存修改」落库。
      </p>
      <p class="hint print-tip">
        💡 打印对话框里的纸张要与上方一致（{{ store.paperSpec.label }}），并取消勾选「页眉和页脚」
      </p>
    </div>

    <button class="print-button" type="button" @click="printResume">打印 / 导出 PDF</button>
  </aside>
</template>

<style scoped>
.style-panel { min-width: 0; color: #263330; }
.panel-heading { border-bottom: 1px solid #dbe4e1; padding-bottom: 16px; }
.eyebrow { margin: 0 0 8px; color: var(--theme-rule); font-size: 12px; letter-spacing: .12em; }
h2, p { margin: 0; }
h2 { font-size: 18px; }
.panel-section { display: grid; gap: 10px; border-bottom: 1px solid #dbe4e1; padding: 18px 0; }
.status-row { display: flex; justify-content: space-between; gap: 8px; color: #687875; font-size: 13px; }
.status-row strong { color: #263330; font-family: "SF Mono", Consolas, monospace; font-weight: 500; }
/* 纸张切换：下拉比按钮省地方，也天然表达「二选一」。 */
.paper-select {
  border: 1px solid #d6dfdc;
  border-radius: 0;
  padding: 3px 6px;
  color: #263330;
  background: #fff;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}
.paper-select:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
/* 物理尺寸小字：让「A4 / Letter 到底差多少」一眼可见，不用去查。 */
.status-hint { margin: -6px 0 0; color: #8fa5a1; font-size: 11px; text-align: right; }
/* 8 个色点：外圈用 box-shadow 做选中环，避免 border 改变尺寸导致换选时抖动。 */
.theme-swatches { display: flex; flex-wrap: wrap; gap: 8px; }
.swatch {
  width: 22px;
  height: 22px;
  border: 1px solid #fff;
  border-radius: 50%;
  padding: 0;
  box-shadow: 0 0 0 1px #d6dfdc;
  cursor: pointer;
  transition: box-shadow .12s ease;
}
.swatch:hover { box-shadow: 0 0 0 2px #a9b6b3; }
.swatch--active { box-shadow: 0 0 0 2px #263330; }
.swatch:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
/* 排版（D5.3）：一行 = 标题输入框 + 上下箭头。窄侧栏里也不换行。 */
.layout-row { display: flex; align-items: center; gap: 6px; }
.layout-title {
  flex: 1 1 auto;
  min-width: 0;
  border: 1px solid #d6dfdc;
  border-radius: 0;
  padding: 4px 6px;
  color: #263330;
  background: #fff;
  font: inherit;
  font-size: 13px;
}
.layout-title:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.layout-actions { display: flex; flex: 0 0 auto; gap: 4px; }
.layout-arrow {
  width: 26px;
  height: 26px;
  border: 1px solid #d6dfdc;
  border-radius: 0;
  padding: 0;
  color: var(--theme-primary);
  background: #fff;
  cursor: pointer;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  line-height: 1;
}
.layout-arrow:hover:not(:disabled) { border-color: var(--theme-primary); background: var(--theme-bg-light); }
/* 到边界时禁用：一眼可见「已经到顶 / 到底」，不必点了才知道没反应。 */
.layout-arrow:disabled { color: #c2ccc9; cursor: not-allowed; }
.layout-arrow:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }
.layout-hint { color: #8fa5a1; font-size: 11px; line-height: 1.5; }
/* 样式改动把「已生成简历」顶掉后的提示：琥珀色，与「未保存」同色系但不抢眼。 */
.notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0;
  border-left: 3px solid #c9a227;
  padding: 6px 0 6px 8px;
  color: #7a6414;
  background: #fbf6e4;
  font-size: 11px;
  line-height: 1.5;
}
.notice__close {
  flex: 0 0 auto;
  border: 0;
  padding: 2px 4px;
  color: #7a6414;
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  text-decoration: underline;
}
.notice__close:focus-visible { outline: 2px solid #c9a227; outline-offset: 1px; }
.panel-label { color: var(--theme-primary); font-size: 12px; }
.hint { color: #687875; font-size: 13px; line-height: 1.6; }
/* 浏览器打印页眉/页脚（日期、URL）无法用代码关掉，只能提示用户自己取消勾选。 */
.print-tip { color: #8a7c3f; font-size: 12px; }
.warning { color: #94781a; font-size: 11px; line-height: 1.5; }
.generate-button { width: 100%; border: 0; padding: 11px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: 13px; }
.generate-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.generate-button:disabled { opacity: .6; cursor: progress; }
.feedback { font-size: 12px; line-height: 1.5; }
.feedback--ok { color: #3f7a5f; }
.feedback--error { color: #9a5e58; }
.print-button { width: 100%; margin-top: 20px; border: 1px solid var(--theme-rule); border-radius: 0; padding: 10px; color: var(--theme-primary); background: transparent; cursor: pointer; font: inherit; font-size: 13px; }
.print-button:hover { color: var(--theme-on-primary); background: var(--theme-primary); }
@media (prefers-color-scheme: dark) {
  .style-panel { color: #e8eae9; }
  .panel-heading, .panel-section { border-color: #52635f; }
  .status-row, .hint { color: #b7c3c0; }
  .status-row strong { color: #e8eae9; }
  .paper-select { border-color: #52635f; color: #e8eae9; background: #1f2a28; }
  .status-hint { color: #8fa5a1; }
  .swatch { border-color: #1b2523; box-shadow: 0 0 0 1px #52635f; }
  .swatch:hover { box-shadow: 0 0 0 2px #8fa5a1; }
  .swatch--active { box-shadow: 0 0 0 2px #e8eae9; }
  .layout-title { border-color: #52635f; color: #e8eae9; background: #1f2a28; }
  .layout-arrow { border-color: #52635f; color: #cfd8d4; background: #1f2a28; }
  .layout-arrow:hover:not(:disabled) { color: #e8eae9; background: #2a3733; }
  .layout-arrow:disabled { color: #5d6b67; }
  .layout-hint { color: #8fa5a1; }
  .notice { border-left-color: #c9a227; color: #e2cf8a; background: #2a2417; }
  .notice__close { color: #e2cf8a; }
}
@media print {
  .style-panel { display: none; }
}
</style>
