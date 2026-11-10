<script setup lang="ts">
/**
 * 唯一的外框（守则 L1：一个布局，一处定义）。
 *
 * 批次 F2 之前，`app-shell` 这一层外框加 `<AppTopBar />` 在 6 个视图里各抄了一份
 * （`ResumeEditorView`、`JobBoardView`、`JobCreateView`、`JobDetailView`、
 * `JobTrashView`、`RecycleBinView`）。后果不是难看，是**每条导航改动都要改 7 遍**：
 * 抽屉、侧栏、工作区切换器任何一项落地都得逐个视图摆一次，而 `views/DashboardView.vue`
 * 那种没挂路由的副本还会漏掉（守则 §11 登记的规划资产，批次 F7 才对齐）。
 *
 * 现在外框只在这里出现：路由把需要顶栏的页面收成 `AppLayout` 的 children
 * （见 `router/index.ts`），视图自己只渲染内容区。
 * 判据由 `src/__tests__/layoutGuards.spec.ts` 钉住：`src/views/` 下再出现第二处手工摆
 * `<AppTopBar />` 即红。
 *
 * 两条搬样式时的取舍，写在这里免得被当成漏搬：
 *
 * 1. `.app-shell { min-height: 100vh }` 从 6 份收成 1 份，语义没变：外框仍然是
 *    「至少一屏高 + 画布底色」。纸面区（`ResumePreview`）与三列工作区的高度依赖
 *    留在 `ResumeEditorView.vue` 自己的 `.resume-workspace` 上——那是页面内部的结构，
 *    不是外框，所以不搬。
 * 2. `@media print { .app-shell { background: #fff } }` 原本只有简历编辑器写了
 *    （暗色模式的深底不能跟着进打印）。它是**外框**的属性，现在收进本文件并对所有
 *    页面生效：其余页面在打印时本来就把内容区整块 `display: none`，改的只是一层
 *    铺底颜色，不会把内容印出来或藏起来。
 */
import { RouterView } from 'vue-router'

import AppTopBar from '@/components/AppTopBar.vue'
</script>

<template>
  <div class="app-shell">
    <AppTopBar />
    <RouterView />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  background: var(--ui-canvas);
}

@media print {
  .app-shell {
    background: #fff;
  }
}
</style>
