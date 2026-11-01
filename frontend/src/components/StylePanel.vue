<script setup lang="ts">
function printResume() {
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
.panel-section { display: grid; gap: 12px; border-bottom: 1px solid #dbe4e1; padding: 18px 0; }
.status-row { display: flex; justify-content: space-between; color: #687875; font-size: 13px; }
.status-row strong { color: #263330; font-family: "SF Mono", Consolas, monospace; font-weight: 500; }
.panel-label { color: #527873; font-size: 12px; }
.hint { color: #687875; font-size: 13px; line-height: 1.6; }
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
