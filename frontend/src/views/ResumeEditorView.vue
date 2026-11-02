<script setup lang="ts">
import { onMounted, ref } from 'vue'

import AppTopBar from '@/components/AppTopBar.vue'
import ResumeEditor from '@/components/ResumeEditor.vue'
import ResumePreview from '@/components/ResumePreview.vue'
import StylePanel from '@/components/StylePanel.vue'
import { isApiError } from '@/api'
import { useResumeStore } from '@/stores/resume'

const store = useResumeStore()
const loadError = ref('')

onMounted(async () => {
  try {
    await store.loadFromServer()
  } catch (error) {
    loadError.value = isApiError(error)
      ? `读取档案失败：${error.message}`
      : '读取档案失败，请刷新页面重试。'
  }
})
</script>

<template>
  <div class="app-shell">
    <AppTopBar />

    <p v-if="loadError" class="load-error">{{ loadError }}</p>
    <p v-else-if="store.loading" class="load-hint">正在加载档案…</p>

    <main class="resume-workspace">
      <ResumeEditor class="editor" />
      <ResumePreview />
      <StylePanel class="style-panel" />
      <p class="footer-hint">
        档案与简历已保存到服务器 · 生成结果基于「已保存」的档案版本
      </p>
    </main>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; background: #f4f6f5; }
.resume-workspace { display: grid; grid-template-columns: 340px minmax(420px, 1fr) 220px; align-items: start; gap: 32px; width: min(1440px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.footer-hint { grid-column: 1 / -1; margin: 0; color: #879692; font-size: 12px; text-align: center; }
.load-error, .load-hint { width: min(1440px, 100%); margin: 0 auto; padding: 12px 48px 0; font-size: 13px; }
.load-error { color: #9a5e58; }
.load-hint { color: #879692; }
@media (max-width: 1100px) {
  .resume-workspace { grid-template-columns: 300px minmax(380px, 1fr); gap: 24px; padding: 24px 28px 56px; }
  .style-panel { grid-column: 1 / -1; }
  .load-error, .load-hint { padding: 12px 28px 0; }
}
@media (max-width: 760px) {
  .resume-workspace { grid-template-columns: 1fr; gap: 24px; padding: 20px 16px 48px; }
  .editor, .style-panel { grid-column: auto; }
  .footer-hint { grid-column: auto; }
  .load-error, .load-hint { padding: 12px 16px 0; }
}
@media print {
  .app-shell { background: #fff; }
  .resume-workspace { display: block; padding: 0; }
  .editor, .style-panel, .footer-hint, .load-error, .load-hint { display: none; }
}
</style>
