<script setup lang="ts">
/**
 * 模块导航条（2026-11-14，UI 收尾第 4 条）。
 *
 * 它替代的是 Logo 右侧那个「工作区切换器」弹层（`WorkspaceSwitcher.vue` 已删除）：
 * 模块切换从「点一下、等弹层、再点一下」变成**一眼可见的一条横栏**。
 * 换掉的理由不是好看——弹层里藏着一个必须点开才知道有哪些模块的入口，
 * 而模块数量已经到 7 个（简历工作台 / 岗位库 / 组织管理 / 模拟面试 / 能力测评 /
 * 学情看板 / 工作台），「点开再点」这两次操作在窄屏上尤其亏。
 *
 * 三条口径：
 *
 * 1. **数据只来自 `APP_MODULES`**（守则 L4）。这里不写死任何模块名，也不维护第二份顺序：
 *    加一个模块 = 改那张表，本文件一个字都不用动。
 * 2. **高亮是「归属」而不是「名称相等」**。详情页不属于它自己的名字：
 *    `/jobs/{id}` 归属「岗位库」、`/interviews/{id}` 归属「模拟面试」、
 *    `/jobs/new` 与 `/jobs/trash` 也归岗位库、`/trash`（简历回收站）归简历工作台。
 *    别名表 `ROUTE_OWNER` 是唯一需要额外维护的东西，且它是**显式**的——
 *    按路径前缀猜会在路由改名时静默失灵（页面照渲染，只是没有任何页签高亮）。
 * 3. **不做 sticky**。顶栏本身是 sticky，它的高度不是令牌（≥1100 档 63px、375 档 59px，
 *    窄屏还会整组换行），把本栏也 sticky 就要把那两个数再抄一遍、换行时还会互相穿插。
 *    放在正常流里，最差也只是滚走，永远不会遮住内容。
 */
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { APP_MODULES, type AppModuleKey } from '@/config/modules'

/** 「这一页归哪个模块」：只列**不在模块表里**、但应高亮某个页签的路由名。 */
const ROUTE_OWNER: Readonly<Record<string, AppModuleKey>> = {
  'job-detail': 'job-board',
  'job-create': 'job-board',
  'job-edit': 'job-board',
  'job-trash': 'job-board',
  'interview-detail': 'interview',
  // 简历回收站是「简历工作台」的下钻页（入口在列表底部），不属于任何独立模块。
  'recycle-bin': 'resume-editor',
}

const route = useRoute()

const activeKey = computed<AppModuleKey | null>(() => {
  const name = typeof route.name === 'string' ? route.name : ''
  if (!name) return null
  const direct = APP_MODULES.find((module) => module.routeName === name)
  return direct ? direct.key : (ROUTE_OWNER[name] ?? null)
})
</script>

<template>
  <nav class="module-nav" aria-label="模块导航">
    <ul class="module-nav__track">
      <li v-for="item in APP_MODULES" :key="item.key">
        <RouterLink
          class="module-tab"
          :class="{ 'module-tab--active': activeKey === item.key }"
          :to="{ name: item.routeName }"
          :aria-current="activeKey === item.key ? 'page' : undefined"
        >
          <svg
            class="module-tab__icon"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path v-for="(path, index) in item.iconPaths" :key="index" :d="path" />
          </svg>
          <span class="module-tab__label">{{ item.name }}</span>
        </RouterLink>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
/*
 * 整条横幅：左右内缩与顶栏同一档（48px；窄屏 16px），底部分隔线把它与内容区分开。
 * 不用阴影——顶栏那层 `backdrop-filter` 与阴影叠在一起会脏。
 */
.module-nav {
  border-bottom: 1px solid var(--ui-border);
  background: var(--ui-surface);
}

.module-nav__track {
  display: flex;
  gap: 4px;
  margin: 0;
  padding: 0 48px;
  overflow-x: auto;
  list-style: none;
  scrollbar-width: none;
}

.module-nav__track::-webkit-scrollbar {
  display: none;
}

.module-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  border-bottom: 2px solid transparent;
  padding: 0 12px;
  color: var(--ui-ink-muted);
  font-size: var(--text-md);
  text-decoration: none;
  white-space: nowrap;
}

.module-tab:hover {
  color: var(--theme-primary);
}

/*
 * 高亮只靠「底部 2px 边 + 文字变深」，不加底色：七个页签铺开时，
 * 一块块色块会让这一栏看起来像七个按钮，而它是同级的页面切换。
 */
.module-tab--active {
  border-bottom-color: var(--theme-primary);
  color: var(--theme-primary-deep);
  font-weight: 600;
}

.module-tab__icon {
  flex: 0 0 auto;
}

@media (max-width: 760px) {
  .module-nav__track {
    padding: 0 16px;
  }

  .module-tab {
    height: 36px;
    padding: 0 10px;
    font-size: var(--text-base);
  }

  /* 窄屏只留文字：七个「图标 + 文字」在 375px 上要横滚两屏，图标帮不上辨认。 */
  .module-tab__icon {
    display: none;
  }
}
</style>
