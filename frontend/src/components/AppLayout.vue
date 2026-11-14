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
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterView } from 'vue-router'

import AppTopBar from '@/components/AppTopBar.vue'
import ModuleNav from '@/components/ModuleNav.vue'
import ResumeCreateDialog from '@/components/ResumeCreateDialog.vue'
import ResumeDrawer from '@/components/ResumeDrawer.vue'
import ResumeList from '@/components/ResumeList.vue'
import { useViewport } from '@/composables/useViewport'

/**
 * 简历列表住哪：≥1440 常驻左栏，其余宽度折叠成右侧抽屉（清单第 5 条）。
 *
 * 这是全站第一处「按宽度换组件」，所以它必须问 `useViewport()`——守则 L2 只允许
 * `composables/useViewport.ts` 发出宽度 `matchMedia`，别处自己写一份就是第二套断点。
 *
 * **阈值为什么不是 `wide: 1100`**（老板 2026-11-11 裁定的第二次落地，`ROADMAP.md` §6b 第 3 条）：
 * 左栏要吃掉 240px，跟着 1100 登场时纸面从 629px（1024）掉到 485px（1120）——
 * 「屏幕更宽、简历更小」。收窄左栏救不了（要保住 629px 只剩 96px 可用，放不下简历行），
 * 所以抬到纸面已经封顶 A4 原大的那一档：≥1440 时有无左栏纸面都是 794px，登场不缩水。
 * 1100–1439 这段不是「没有列表」，是改用下面那个**已经存在的**抽屉。
 *
 * 开合状态放在这里而不是顶栏：触发按钮在顶栏、抽屉在外框，两者的共同父级只有这一层。
 * 顶栏只发 `toggle-resumes`，它不知道也不关心当前是常驻还是抽屉。
 */
const { isSidebar } = useViewport()
const resumesOpen = ref(false)
const createVisible = ref(false)
/**
 * 左侧简历侧栏的开合（2026-11-14，UI 收尾第 2 条）：**默认收起**。
 *
 * 为什么要默认收起：宽屏常驻左栏会吃掉 240px，纸面会缩水（老板 2026-11-11 裁定的那条
 * 「绝不能屏幕更宽、简历更小」）；而「常驻」又是清单第 5 条的原意——两者只能靠「默认收起、
 * 点一下再展开」调和：想看列表就拉开，不看就让它让出宽度。拉开与收回用的是同一颗按钮
 * （见模板里 `.sidebar-toggle`，上一版它只在收起态渲染，展开后就再也收不回去）。
 *
 * 为什么放在外框而不是顶栏：这个开合只影响「≥1440 的左栏」，窄屏那份列表走右侧抽屉，
 * 与它无关；顶栏不该知道「现在是常驻还是折叠」。
 */
const sidebarOpen = ref(false)

/**
 * 外框高度（顶栏 + 二级导航）实测出来，交给侧栏当 sticky 偏移 —— 2026-11-14 UI 收尾第 1 条。
 *
 * 为什么不许再写死一个数字：上一版这里写的是 `top: 63px`（= 品牌方块 34 那一档的顶栏高度），
 * 而 Logo 放大到 40 之后顶栏实测 69、二级导航又占 41，侧栏的真实起点是 110。
 * 盒子按 `100vh - 63` 算出 837px，比可用的 790px 长出 47px，于是贴在盒子底部的
 * 「新建简历」「回收站」整条掉到屏幕外（1440×900 实测：按钮 top 902 / bottom 937）。
 * 量一次就自洽：Logo 再变大、窄屏顶栏整组换行、导航手动收起，都不需要有人记得改这里。
 */
const chromeEl = ref<HTMLElement | null>(null)
const chromeHeight = ref(0)
/** 导航收起/展开、顶栏换行、窗口缩放都改外框高度，所以必须观察而不是只读一次。 */
let chromeObserver: ResizeObserver | null = null

function measureChrome(): void {
  chromeHeight.value = Math.round(chromeEl.value?.getBoundingClientRect().height ?? 0)
}

onMounted(() => {
  measureChrome()
  chromeObserver = new ResizeObserver(measureChrome)
  if (chromeEl.value) chromeObserver.observe(chromeEl.value)
})

onBeforeUnmount(() => {
  chromeObserver?.disconnect()
  chromeObserver = null
})

function toggleResumes(): void {
  resumesOpen.value = !resumesOpen.value
}

function toggleSidebar(): void {
  sidebarOpen.value = !sidebarOpen.value
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
  <div class="app-shell" :style="{ '--chrome-h': `${chromeHeight}px` }">
    <!--
      顶栏与二级导航合成一个 sticky 的外框（2026-11-14 UI 收尾第 1 条）。
      原来只有顶栏 sticky，二级导航在正常流里——它不是「有人做了自动收起」，而是跟着页面滚走了，
      滚动到中段时整条模块入口就不在屏幕上。合进同一层之后：要么两栏都在，要么两栏一起滚出，
      不存在「导航自己溜走、按钮留在原地」那种半截状态。导航的显隐改由它自己那颗按钮手动控制。
    -->
    <div ref="chromeEl" class="app-chrome">
      <AppTopBar
        :resumes-open="resumesOpen"
        :show-resumes-trigger="!isSidebar"
        @toggle-resumes="toggleResumes"
      />
      <ModuleNav />
    </div>

    <div class="app-body">
      <!--
        ≥1440 的左侧简历栏（2026-11-14 UI 收尾第 1/2 条改版）：
        - 默认**收起**，由左边缘一个常驻的拉出按钮展开；展开态仍是普通流里的 flex 列
          （不是 fixed 悬浮层），所以不会盖住内容；
        - 高度与 sticky 偏移都从 `--chrome-h` 来（见上面 `measureChrome` 那段），
          盒子底边正好落在视口下沿，「新建简历」「回收站」不再被顶出屏幕；
        - 内容区 `.app-content` 用 `flex: 1 1 auto; min-width: 0` 吸收挤压——侧栏收放
          只是让内容变宽变窄，任何页面都不会因为多了一列而产生横向滚动或丢控件。
        它是**内容**（用户自己的简历），不是模块导航，所以不进模块表（L4）。
      -->
      <aside
        v-if="isSidebar"
        id="resume-sidebar"
        class="resume-sidebar"
        :class="{ 'resume-sidebar--closed': !sidebarOpen }"
        aria-label="我的简历"
      >
        <ResumeList @create="openCreate" />
      </aside>

      <div class="app-content">
        <!--
          页面切换不硬切（守则 M1）：淡入淡出 + 6px 微位移，`out-in` 让旧页先退完，
          避免两页同时在文档流里把布局顶一下。时长在这里压到 `--motion-fast`：
          out-in 是「退 + 进」两段，用浮层那档 240ms 会让每次导航等近半秒。
        -->
        <RouterView v-slot="{ Component }">
          <Transition name="ui-fade" mode="out-in">
            <component :is="Component" />
          </Transition>
        </RouterView>
      </div>
    </div>

    <!--
      左边缘的侧栏收放按钮（2026-11-14 UI 收尾第 2 条改版）：只在 ≥1440（有常驻栏）出现，
      而且是**常驻**——收起时贴在视口左缘往外拉，展开时跟着贴到侧栏右缘往里收。
      上一版它只在收起态渲染（`v-if="isSidebar && !sidebarOpen"`），于是拉出来之后
      屏幕上没有任何收回的入口，实测 `clicked: false`——「能收回」这一半根本没做到。
      它不参与 flex 布局，所以不占宽度、不挤内容；展开时压在内容区那 48px 内缩上，
      盖不到任何控件。
    -->
    <button
      v-if="isSidebar"
      class="sidebar-toggle"
      :class="{ 'sidebar-toggle--open': sidebarOpen }"
      type="button"
      :aria-expanded="sidebarOpen"
      aria-controls="resume-sidebar"
      :aria-label="sidebarOpen ? '收起简历列表' : '展开简历列表'"
      @click="toggleSidebar"
    >
      <span class="sidebar-toggle__caret" aria-hidden="true">{{ sidebarOpen ? '‹' : '›' }}</span>
    </button>

    <!--
      抽屉仍然用 `v-if`（2026-11-14 UI 收尾第 5 条试过 `v-show`，放弃了，理由写在这里）：
      `v-show` 是把 `display: none` 挂到组件**根元素**上的，而本组件的根是 `<Teleport>`
      ——Vue 对 Teleport / fragment 根的 `v-show` 并不保证生效（真实内容已经被搬到 body，
      根上那层壳没东西可藏）。要「保持挂载」就得让 `DrawerPanel` 自己接一个 `open` prop
      去控制面板可见性，那是另一个改动面（还要改 `aria-hidden` / 遮罩 / 焦点圈定）。
      **卡顿的真因不在挂载次数上**：抽屉每次开合都会重新挂载 `ResumeList`，而它原来在
      `onMounted` 里无条件 `refreshProfiles()` ——一次多余网络请求 + 骨架闪一下。
      那个已经修了（`profilesLoaded` 缓存，见 `ResumeList.vue`），开合现在是从内存渲染。
    -->
    <Transition name="ui-slide-right">
      <ResumeDrawer
        v-if="!isSidebar && resumesOpen"
        @close="resumesOpen = false"
        @create="openCreate"
      />
    </Transition>

    <Transition name="ui-fade">
      <ResumeCreateDialog v-if="createVisible" @close="createVisible = false" />
    </Transition>
  </div>
</template>

<style scoped>
.app-shell {
  --resume-sidebar-width: 240px;

  min-height: 100vh;
  background: var(--ui-canvas);
}

/*
 * 顶栏 + 二级导航这一组是全站唯一 sticky 的头部。`z-index` 沿用顶栏原来那一级（10），
 * 阶梯值不因为换了个壳子就多出一档（守则 L3 的 10 / 30 / 100）。
 */
.app-chrome {
  position: sticky;
  top: 0;
  z-index: 10;
}

.app-body {
  display: flex;
  align-items: flex-start;
}

/*
 * 常驻左栏。偏移与高度都从 `--chrome-h` 来（由 `measureChrome` 实测写进内联样式），
 * 盒子底边正好落在视口下沿——贴在盒子底部的「新建简历」「回收站」因此在屏幕内。
 * 原来这里是 `top: 63px` + `height: calc(100vh - 63px)`，那个 63 是品牌方块 34px 年代的
 * 顶栏高度：Logo 放大到 40、又加了一条 41px 的二级导航之后，真实起点已经是 110，
 * 盒子比可用高度长出 47px，两个按钮整条掉出视口（1440×900 实测 top 902 / bottom 937）。
 */
.resume-sidebar {
  position: sticky;
  top: var(--chrome-h);
  display: flex;
  flex: 0 0 var(--resume-sidebar-width);
  flex-direction: column;
  width: var(--resume-sidebar-width);
  height: calc(100vh - var(--chrome-h));
  border-right: 1px solid var(--ui-border);
  background: var(--ui-surface);
}

/*
 * 收起态：宽度塌到 0、整栏 `display: none`。用宽度 + display 一起切，而不是只 `display: none`：
 * 只藏 display 会让内容区仍然以为左栏还在（flex 占位），那 240px 就白省了。
 */
.resume-sidebar--closed {
  flex-basis: 0;
  width: 0;
  border-right: 0;
  overflow: hidden;
  display: none;
}

/*
 * 左边缘的收放按钮：贴在视口左缘（收起态）或侧栏右缘（展开态）、垂直居中，不占文档流。
 * `z-index` 取 9（比外框的 10 低一格，滚动时从外框底下穿过而不盖住它）。
 */
.sidebar-toggle {
  position: fixed;
  left: 0;
  top: 50%;
  z-index: 9;
  display: flex;
  align-items: center;
  height: 56px;
  border: 1px solid var(--ui-border);
  border-left: 0;
  border-radius: 0 var(--ui-radius) var(--ui-radius) 0;
  padding: 0 6px;
  color: var(--ui-ink-muted);
  background: var(--ui-surface);
  cursor: pointer;
  transform: translateY(-50%);
}

/* 展开态：按钮跟着挪到侧栏右缘，箭头反向（见模板里那两个字），点它是收回而不是再拉一次。 */
.sidebar-toggle--open {
  left: var(--resume-sidebar-width);
}

.sidebar-toggle:hover {
  color: var(--theme-primary);
  border-color: var(--theme-rule);
}

.sidebar-toggle:focus-visible {
  outline: 2px solid var(--theme-rule);
  outline-offset: 2px;
}

.sidebar-toggle__caret {
  font-size: var(--text-xl);
  line-height: 1;
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

  /* 页面切换的淡入淡出比浮层短一档：out-in 是两段，240ms×2 会让导航像卡住。 */
  --fade-duration: var(--motion-fast);
}

@media print {
  .app-shell {
    background: #fff;
  }

  /* 打印只要纸面：外框（顶栏 + 二级导航）、左栏与收放按钮都是导航，印出来是废纸。 */
  .app-chrome,
  .resume-sidebar,
  .sidebar-toggle {
    display: none;
  }

  .app-body {
    display: block;
  }
}
</style>
