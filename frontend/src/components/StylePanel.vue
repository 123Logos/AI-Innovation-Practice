<script setup lang="ts">
import { computed, ref } from 'vue'

import { isApiError } from '@/api'
import type { PaperSize } from '@/api/types'
import { useResumeStore } from '@/stores/resume'

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
      <div class="status-row"><span>配色</span><strong>青绿</strong></div>
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
.eyebrow { margin: 0 0 8px; color: #6b8f8a; font-size: 12px; letter-spacing: .12em; }
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
.paper-select:focus-visible { outline: 2px solid #6b8f8a; outline-offset: 2px; }
/* 物理尺寸小字：让「A4 / Letter 到底差多少」一眼可见，不用去查。 */
.status-hint { margin: -6px 0 0; color: #8fa5a1; font-size: 11px; text-align: right; }
.panel-label { color: #527873; font-size: 12px; }
.hint { color: #687875; font-size: 13px; line-height: 1.6; }
/* 浏览器打印页眉/页脚（日期、URL）无法用代码关掉，只能提示用户自己取消勾选。 */
.print-tip { color: #8a7c3f; font-size: 12px; }
.warning { color: #94781a; font-size: 11px; line-height: 1.5; }
.generate-button { width: 100%; border: 0; padding: 11px; color: #fff; background: #527873; cursor: pointer; font: inherit; font-size: 13px; }
.generate-button:hover:not(:disabled) { background: #3f635e; }
.generate-button:disabled { opacity: .6; cursor: progress; }
.feedback { font-size: 12px; line-height: 1.5; }
.feedback--ok { color: #3f7a5f; }
.feedback--error { color: #9a5e58; }
.print-button { width: 100%; margin-top: 20px; border: 1px solid #6b8f8a; border-radius: 0; padding: 10px; color: #527873; background: transparent; cursor: pointer; font: inherit; font-size: 13px; }
.print-button:hover { color: #fff; background: #6b8f8a; }
@media (prefers-color-scheme: dark) {
  .style-panel { color: #e8eae9; }
  .panel-heading, .panel-section { border-color: #52635f; }
  .status-row, .hint { color: #b7c3c0; }
  .status-row strong { color: #e8eae9; }
  .paper-select { border-color: #52635f; color: #e8eae9; background: #1f2a28; }
  .status-hint { color: #8fa5a1; }
}
@media print {
  .style-panel { display: none; }
}
</style>
