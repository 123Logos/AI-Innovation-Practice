<script setup lang="ts">
/**
 * 工作区切换器（老板前端清单第 6 条，批次 F3 第四片）：Logo 右侧一个触发按钮，
 * 打开是一张**横向卡片** Popover，一张卡片 = 一个工作区。
 *
 * 三条边界写在这里，因为它们都是决定不是遗漏：
 *
 * 1. **卡片来自模块表**（守则 L4）。这里不写死任何模块名，也不另抄一份数组：
 *    `APP_MODULES` 是唯一来源，顶栏那条文字入口读的是同一张表的 `inTopBar` 过滤。
 *    于是「岗位库」在顶栏与卡片里各出现一次是**有意的两处入口**（ADR-011 议题 3.A
 *    要一条不藏起来的文字入口，清单第 6 条要一张切换卡片），不是两份真相。
 * 2. **「组织管理」连禁用态都不放**（守则 L5，老板 2026-11-09 把这一格交给我定）。
 *    它在批次 B5 落地当天才进模块表，那一天这里自动多一张卡片，本文件一个字都不用改。
 *    模拟面试同理（B11a）。`layoutGuards.spec.ts` 用 `router.hasRoute()` 钉着这一格。
 * 3. **不圈焦点**。这是不带遮罩的 Popover，不是模态对话框：Tab 该能走出去
 *    （基座的 `trapFocus` 默认关，只有 `DrawerPanel` 开它）。点外面 / Esc / 焦点归还
 *    仍然一行都不在这里——全在 `OverlayPanel` 基座里（守则 L3）。
 *
 * 弹层的位置参照的是**顶栏那个盒子**，不是本组件：`.app-topbar` 是 sticky（而且带
 * `backdrop-filter`，按规范两者都会成为绝对定位后代的包含块），所以这里与外层包裹
 * 都保持 `position: static`，让 `top: calc(100% + 8px)` 量到的是整条顶栏的高度。
 * 这样做的好处是窄屏不必把弹层往左挪：左右都取顶栏的内缩值，375px 下也不会顶出视口。
 */
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import OverlayPanel from '@/components/OverlayPanel.vue'
import { APP_MODULES, type AppModule } from '@/config/modules'

const route = useRoute()
const router = useRouter()

const open = ref(false)

/**
 * 当前在哪个工作区：拿模块表里每一项的**路由路径**做最长前缀匹配。
 * 路径由 `router.resolve()` 从路由表取，不在这里第二次声明「岗位库在 /jobs」。
 * `/` 是最短的前缀，所以 `/trash`（简历回收站）这类页面自然归简历工作台，
 * 而 `/jobs/...` 归岗位库——与用户对「我在哪一块」的直觉一致。
 */
const current = computed<AppModule | null>(() => {
  let best: AppModule | null = null
  let bestLength = -1
  for (const module of APP_MODULES) {
    const base = router.resolve({ name: module.routeName }).path
    const hit = base === '/' ? route.path.startsWith('/') : route.path === base || route.path.startsWith(`${base}/`)
    if (hit && base.length > bestLength) {
      best = module
      bestLength = base.length
    }
  }
  return best
})

const currentName = computed(() => current.value?.name ?? '工作区')

function toggle(): void {
  open.value = !open.value
}

function close(): void {
  open.value = false
}
</script>

<template>
  <div class="workspace-switcher">
    <!--
      两个标签恒在 DOM 里，显示谁由 CSS 决定（宽屏当前工作区名、窄屏「切换」二字）——
      与账户按钮同一套做法，宽度驱动的差异不进 JS（守则 L2）。
      `aria-controls` 只在展开时给：收起态弹层不在 DOM 里，指一个不存在的 id 是假声明。
    -->
    <button
      class="workspace-trigger"
      type="button"
      :aria-expanded="open"
      :aria-controls="open ? 'workspace-panel' : undefined"
      :aria-label="`切换工作区，当前：${currentName}`"
      :title="`切换工作区，当前：${currentName}`"
      @click="toggle"
    >
      <span class="workspace-trigger__name">{{ currentName }}</span>
      <span class="workspace-trigger__word">切换</span>
      <svg
        class="workspace-trigger__caret"
        :class="{ 'workspace-trigger__caret--open': open }"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
    </button>

    <Transition name="ui-fade-down">
      <OverlayPanel
        v-if="open"
        panel-id="workspace-panel"
        panel-class="workspace-popover"
        role="group"
        label="切换工作区"
        @close="close"
      >
        <div class="workspace-cards">
          <RouterLink
            v-for="module in APP_MODULES"
            :key="module.key"
            class="workspace-card"
            :to="{ name: module.routeName }"
            :aria-current="current?.key === module.key ? 'page' : undefined"
            @click="close"
          >
            <svg
              class="workspace-card__icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path v-for="(d, index) in module.iconPaths" :key="index" :d="d" />
            </svg>
            <span class="workspace-card__name">{{ module.name }}</span>
          </RouterLink>
        </div>
      </OverlayPanel>
    </Transition>
  </div>
</template>

<style scoped>
/*
 * 本组件与包裹层都不定位（见文件头）：弹层的包含块是顶栏。
 * 这里只负责触发按钮自己的尺寸，与顶栏其余控件同一档 32px 高。
 */
.workspace-trigger { display: inline-flex; flex: 0 1 auto; min-width: 0; align-items: center; gap: 6px; height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 10px; color: var(--ui-ink); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 12px; white-space: nowrap; }
.workspace-trigger:hover { border-color: var(--theme-rule); color: var(--theme-primary); }
.workspace-trigger:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.workspace-trigger__name { overflow: hidden; max-width: 14ch; font-size: 13px; font-weight: 600; text-overflow: ellipsis; }
.workspace-trigger__word { display: none; color: var(--ui-ink-muted); font-size: 13px; }
.workspace-trigger__caret { flex: 0 0 auto; width: 12px; height: 12px; color: var(--ui-ink-faint); transition: transform var(--motion-base) var(--motion-ease); }
.workspace-trigger__caret--open { transform: rotate(180deg); }

/*
 * 左右都取顶栏的内缩值：宽屏时 `max-width` 先咬住（340px，两张卡片各 150px 上下），
 * 窄屏时可用宽度先咬住（375 - 32 = 343），两种情况都不会顶出视口。
 */
.workspace-popover {
  position: absolute;
  top: calc(100% + 8px);
  right: var(--topbar-inset);
  left: var(--topbar-inset);
  max-width: 340px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  padding: 12px;
  background: var(--ui-surface);
  box-shadow: var(--ui-shadow-menu);
}
.workspace-cards { display: flex; gap: 10px; }
.workspace-card {
  display: grid;
  flex: 1 1 0;
  min-width: 0;
  justify-items: center;
  gap: 8px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  padding: 14px 8px;
  color: var(--ui-ink-muted);
  text-decoration: none;
}
.workspace-card:hover { border-color: var(--theme-rule); color: var(--theme-primary); }
.workspace-card:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.workspace-card[aria-current='page'] { border-color: var(--ui-border-strong); color: var(--theme-primary-deep); background: var(--theme-bg-light); }
.workspace-card__icon { width: 24px; height: 24px; }
.workspace-card__name { overflow: hidden; max-width: 100%; font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }

@media (max-width: 760px) {
  /* 手机上顶栏只有 343px：当前工作区名让位给「切换」二字，与账户按钮同一套取舍。
     内缩同时收到 8px——这一格是顶栏单行预算里挤出来的（实测两行 101px → 一行）。 */
  .workspace-trigger { padding: 0 8px; }
  .workspace-trigger__name { display: none; }
  .workspace-trigger__word { display: inline; }
}
@media print {
  .workspace-switcher { display: none; }
}
</style>