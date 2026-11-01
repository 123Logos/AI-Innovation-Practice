<script setup lang="ts">
import ResumeEditor from '@/components/ResumeEditor.vue'
import ResumePreview from '@/components/ResumePreview.vue'
import StylePanel from '@/components/StylePanel.vue'
</script>

<template>
  <main class="resume-workspace">
    <ResumeEditor class="editor" />
    <ResumePreview />
    <StylePanel class="style-panel" />
    <p class="footer-hint">本地编辑模式 · 内容暂存于当前页面</p>
  </main>
</template>

<style scoped>
.resume-workspace { display: grid; grid-template-columns: 340px minmax(420px, 1fr) 220px; align-items: start; gap: 32px; width: min(1440px, 100%); min-height: 100vh; margin: 0 auto; padding: 40px 48px 72px; background: #f4f6f5; }
.footer-hint { grid-column: 1 / -1; margin: 0; color: #879692; font-size: 12px; text-align: center; }
@media (max-width: 1100px) {
  .resume-workspace { grid-template-columns: 300px minmax(380px, 1fr); gap: 24px; padding: 28px; }
  .style-panel { grid-column: 1 / -1; }
}
@media (max-width: 760px) {
  .resume-workspace { grid-template-columns: 1fr; gap: 24px; padding: 24px 16px 56px; }
  .editor, .style-panel { grid-column: auto; }
  .footer-hint { grid-column: auto; }
}
@media print {
  .resume-workspace { display: block; min-height: 0; padding: 0; background: #fff; }
  .editor, .style-panel, .footer-hint { display: none; }
}
</style>
