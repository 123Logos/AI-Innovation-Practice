<script setup lang="ts">
import { computed, ref } from 'vue'

import { isApiError } from '@/api'
import { useResumeStore } from '@/stores/resume'

const store = useResumeStore()

const feedback = ref<{ tone: 'ok' | 'error'; text: string } | null>(null)

const profileStatus = computed(() => (store.hasSavedProfile ? '已保存' : '未保存'))
const resumeStatus = computed(() =>
  store.generatedResumeId ? `已生成 #${store.generatedResumeId.slice(0, 8)}` : '未生成',
)

async function handleGenerate(): Promise<void> {
  feedback.value = null
  if (!store.hasSavedProfile) {
    feedback.value = {
      tone: 'error',
      text: '请先在左侧完善并保存档案，再生成简历。',
    }
    return
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
      <div class="status-row"><span>纸张</span><strong>A4</strong></div>
      <div class="status-row"><span>页数</span><strong>1 页</strong></div>
      <div class="status-row"><span>配色</span><strong>青绿</strong></div>
    </div>

    <div class="panel-section">
      <p class="panel-label">数据状态</p>
      <div class="status-row"><span>档案</span><strong>{{ profileStatus }}</strong></div>
      <div class="status-row"><span>简历</span><strong>{{ resumeStatus }}</strong></div>
      <p v-if="store.dirty" class="warning">左侧有未保存的修改，生成时使用的是已保存版本。</p>
    </div>

    <div class="panel-section">
      <button class="generate-button" type="button" :disabled="store.generating" @click="handleGenerate">
        {{ store.generating ? '生成中…' : '生成简历' }}
      </button>
      <p v-if="feedback" :class="['feedback', `feedback--${feedback.tone}`]">{{ feedback.text }}</p>
    </div>

    <div class="panel-section">
      <p class="panel-label">编辑提示</p>
      <p class="hint">点击中央纸面上的文字块即可直接编辑，内容会同步到左侧表单。</p>
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
.panel-label { color: #527873; font-size: 12px; }
.hint { color: #687875; font-size: 13px; line-height: 1.6; }
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
}
@media print {
  .style-panel { display: none; }
}
</style>
