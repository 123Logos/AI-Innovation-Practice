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
 *
 * 批次 F3 第三片（清单第 5 条）起，外框还负责**简历列表住哪**：宽屏常驻左栏、
 * 其余宽度折叠成右侧抽屉。列表内容只有 `ResumeList.vue` 一份，两个外壳共用；
 * 「新建简历」弹窗也归这里持有，因为它不能随抽屉卸载而消失（理由写在那个组件里）。
 * 于是外框成了三件事的唯一出处：顶栏、简历列表的住法、内容区的宽度参照（容器查询）。
 */
import { ref } from 'vue'
import { RouterView } from 'vue-router'

import AppTopBar from '@/components/AppTopBar.vue'
import ResumeCreateDialog from '@/components/ResumeCreateDialog.vue'
import ResumeDrawer from '@/components/ResumeDrawer.vue'
import ResumeList from '@/components/ResumeList.vue'
import { useViewport } from '@/composables/useViewport'

/**
 * 简历列表住哪：宽屏（≥1100）常驻左栏，其余宽度折叠成右侧抽屉（清单第 5 条）。
 *
 * 这是全站第一处「按宽度换组件」，所以它必须问 `useViewport()`——守则 L2 只允许
 * `composables/useViewport.ts` 发出宽度 `matchMedia`，别处自己写一份就是第二套断点。
 * 中间那段（761–1099）归抽屉：常驻栏要吃掉 240px，而简历工作区在这个宽度本来就已经
 * 退成两列，再塞一列会把纸面挤到不可读。
 *
 * 开合状态放在这里而不是顶栏：触发按钮在顶栏、抽屉在外框，两者的共同父级只有这一层。
 * 顶栏只发 `toggle-resumes`，它不知道也不关心当前是常驻还是抽屉。
 */
const { isWide } = useViewport()
const resumesOpen = ref(false)
const createVisible = ref(false)

function toggleResumes(): void {
  resumesOpen.value = !resumesOpen.value
}

/**
 * 新建弹窗归外框持有，且开它的时候收起抽屉（与原来 `openCreate()` 先关菜单的行为一致）。
 * 弹窗不能长在抽屉里：抽屉收起会卸载列表，弹窗跟着消失，用户字打到一半就没了。
 */
function openCreate(): void {
  resumesOpen.value = false
  createVisible.value = true
}
</script>

<template>
  <div class="app-shell">
    <AppTopBar
      :resumes-open="resumesOpen"
      :show-resumes-trigger="!isWide"
      @toggle-resumes="toggleResumes"
    />

    <div class="app-body">
      <!-- 宽屏：常驻左栏。它是内容（用户自己的简历），不是模块导航，所以不进模块表（L4）。 -->
      <aside v-if="isWide" class="resume-sidebar" aria-label="我的简历">
        <ResumeList @create="openCreate" />
      </aside>

      <div class="app-content">
        <RouterView />
      </div>
    </div>

    <ResumeDrawer
      v-if="!isWide && resumesOpen"
      @close="resumesOpen = false"
      @create="openCreate"
    />

    <ResumeCreateDialog v-if="createVisible" @close="createVisible = false" />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  background: var(--ui-canvas);
}

.app-body {
  display: flex;
  align-items: flex-start;
}

/*
 * 常驻左栏。`top` 那个 63px 是顶栏的高度，由内容算出来而不是令牌：
 * 品牌方块 34 + 上下内缩 14×2 + 下边框 1 = 63（≥1100 档实测 63px，375 档 59px，
 * 但左栏只在 ≥1100 出现，所以只需要宽屏那一档）。顶栏是 sticky 的，左栏若不跟着
 * sticky 就会滚走——「常驻」的意思是不用点一下才出现，也不是滚两屏就没了。
 * 这个数没有令牌可取，所以它被 R2 的两档实测钉住：改顶栏内缩或品牌方块尺寸时要一起改。
 */
.resume-sidebar {
  position: sticky;
  top: 63px;
  display: flex;
  flex: 0 0 240px;
  flex-direction: column;
  width: 240px;
  height: calc(100vh - 63px);
  border-right: 1px solid var(--ui-border);
  background: var(--ui-surface);
}

/* `min-width: 0` 是关键：不给它，flex 子项的最小尺寸是内容宽度，
   简历工作区那张三列网格会把整页撑出横向滚动条。

   `container-type: inline-size` 让内容区成为**容器查询**的参照：加了常驻左栏之后，
   内容区比视口窄 240px，视图里「几列」的判据必须量这一列而不是量视口
   （`ResumeEditorView.vue` 的 1100 / 760 两档因此改成 `@container app-content`）。
   它顺带给后代生成 fixed 定位的包含块，所以视图里不许放 `position: fixed` 的东西——
   今天全仓只有 `DrawerPanel` 用 fixed，而它 Teleport 到 body，不在这棵子树里。 */
.app-content {
  container-type: inline-size;
  container-name: app-content;
  flex: 1 1 auto;
  min-width: 0;
}

@media print {
  .app-shell {
    background: #fff;
  }

  /* 打印只要纸面：左栏是导航，印出来是一列废纸。 */
  .resume-sidebar {
    display: none;
  }

  .app-body {
    display: block;
  }
}
</style>
