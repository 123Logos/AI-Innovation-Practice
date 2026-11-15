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
 * 3. **显隐只由人控制，不由滚动控制**（2026-11-14 UI 收尾第 1 条改的）。本栏和顶栏一起
 *    被 `AppLayout` 的那层 `.app-chrome` sticky 住，滚到哪都在；要腾地方就点右侧那颗
 *    「收起导航」，收起后这一条只剩一行细栏：当前模块 + 展开入口。外框高度是
 *    `AppLayout` 实测出来的（`measureChrome`），所以这一条变高变矮都不需要别处跟着改数字。
 * 4. **页签按角色过滤**（2026-11-15，老板指令「基于角色的导航过滤」）。过滤器是
 *    `config/modules.ts::modulesVisibleTo`，本文件不写第二条 `filter`，也不写任何角色判断——
 *    「谁能看到什么入口」那张表在模块表里，这里只负责读它。
 *    这条只减入口、不改边界：`/teacher/*` 与 `/organization/*` 的权限仍在后端判
 *    （403 / 400），学生直接敲地址进来看到的是后端的答复，不是这个导航栏。
 *    收起态那句兜底文案（当前页不属于任何模块时显示）跟着改成「可用模块」——
 *    展开之后看到的是角色过滤后的那一组，写「全部模块」会多承诺两个入口。
 * 5. **窄屏换形态，不换名单**（2026-11-15 第 24 轮）。375px 上七个页签要横向滚 242px
 *    （本轮 CDP 实测），滚过去的那几个既看不见也点不到，而右边那颗收起按钮随时可能被顶出屏幕。
 *    窄屏因此改成「当前模块 + 下拉」：菜单项遍历的还是同一个 `visibleModules`，
 *    浮层的点外面 / Esc / 焦点归还还是 `OverlayPanel` 基座（守则 L3）。
 *    **形态由 CSS 决定，不用 `matchMedia`**（守则 L2）：两棵都在 DOM 里，谁显示看断点。
 */
import { computed, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import OverlayPanel from '@/components/OverlayPanel.vue'
import { APP_MODULES, modulesVisibleTo, type AppModuleKey } from '@/config/modules'
import { useAuthStore } from '@/stores/auth'

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
const auth = useAuthStore()

/** 当前角色能看到的页签。顺序仍是模块表的顺序，不在这里重排（L4：一处定义顺序）。 */
const visibleModules = computed(() => modulesVisibleTo(auth.role))

const activeKey = computed<AppModuleKey | null>(() => {
  const name = typeof route.name === 'string' ? route.name : ''
  if (!name) return null
  const direct = APP_MODULES.find((module) => module.routeName === name)
  return direct ? direct.key : (ROUTE_OWNER[name] ?? null)
})

/** 收起态也要能说出「你在哪个模块」，所以它读的是同一个 `activeKey`，不是第二份表。 */
const activeModule = computed(() => APP_MODULES.find((module) => module.key === activeKey.value) ?? null)

/** 手动收起（默认展开）：状态只在本地，刷新后回到展开——它是一次性的腾地方，不是偏好设置。 */
const collapsed = ref(false)

/**
 * 窄屏那棵下拉的开合。菜单没有自己的数据：条目就是 `visibleModules`，
 * 选中一条即关闭并跳转（老板指令「选完后自动收起并跳转」）。
 */
const menuOpen = ref(false)

/** 触发按钮上那句当前模块名；当前页不属于任何模块时说「可用模块」。 */
const menuName = computed(() => activeModule.value?.name ?? '可用模块')

/**
 * 读屏那句单独算：「切换模块，当前：可用模块」是句自相矛盾的话——
 * 没有当前模块的时候就不承诺「当前」。
 */
const menuAriaLabel = computed(() =>
  activeModule.value ? `切换模块，当前：${activeModule.value.name}` : '切换模块',
)
</script>

<template>
  <nav class="module-nav" aria-label="模块导航">
    <!--
      收起态用 `v-show` 而不是 `v-if`：这一棵是普通元素（不是 Teleport 根），
      `display: none` 同时把它移出 Tab 序与读屏朗读，展开时又不用重新拉一遍模块表。
    -->
    <ul v-show="!collapsed" id="module-nav-track" class="module-nav__track">
      <li v-for="item in visibleModules" :key="item.key">
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

    <p v-show="collapsed" class="module-nav__current">
      <RouterLink v-if="activeModule" :to="{ name: activeModule.routeName }">
        {{ activeModule.name }}
      </RouterLink>
      <span v-else>可用模块</span>
    </p>

    <!--
      窄屏形态（第 24 轮）：一行「当前模块 ▾」+ 垂直菜单。它替的是横向滚动那一排，
      不是第二个入口——宽屏这棵被 CSS 藏掉（`display: none` 同时移出 Tab 序与读屏），
      两者读的是同一个 `visibleModules`。基座不 Teleport（它刻意不做，见 `OverlayPanel.vue`），
      所以包含块是这一格 `position: relative`。
    -->
    <div class="module-nav__mobile">
      <button
        class="module-nav__menu-trigger"
        type="button"
        :aria-expanded="menuOpen"
        :aria-controls="menuOpen ? 'module-nav-menu' : undefined"
        :aria-label="menuAriaLabel"
        @click="menuOpen = !menuOpen"
      >
        <span class="module-nav__menu-name">{{ menuName }}</span>
        <svg
          class="module-nav__menu-caret"
          :class="{ 'module-nav__menu-caret--open': menuOpen }"
          width="14"
          height="14"
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
          v-if="menuOpen"
          panel-id="module-nav-menu"
          panel-class="module-nav__menu"
          role="group"
          label="切换模块"
          @close="menuOpen = false"
        >
          <ul class="module-nav__menu-list">
            <li v-for="item in visibleModules" :key="item.key">
              <RouterLink
                class="module-nav__menu-item"
                :to="{ name: item.routeName }"
                :aria-current="activeKey === item.key ? 'page' : undefined"
                @click="menuOpen = false"
              >
                {{ item.name }}
              </RouterLink>
            </li>
          </ul>
        </OverlayPanel>
      </Transition>
    </div>

    <button
      class="module-nav__toggle"
      type="button"
      :aria-expanded="!collapsed"
      aria-controls="module-nav-track"
      @click="collapsed = !collapsed"
    >
      {{ collapsed ? '展开导航' : '收起导航' }}
    </button>
  </nav>
</template>

<style scoped>
/*
 * 整条横幅：左右内缩与顶栏同一档（48px；窄屏 16px），底部分隔线把它与内容区分开。
 * 不用阴影——顶栏那层 `backdrop-filter` 与阴影叠在一起会脏。
 *
 * 内缩写在这一层而不是页签轨道上，是因为轨道右边多了一颗收起按钮：
 * 各自加一次内缩会在窄屏与收起态下错位，而轨道自己要做横向滚动，`padding` 反而会
 * 让滚过去的页签停在半格上。
 */
.module-nav {
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid var(--ui-border);
  background: var(--ui-surface);
  padding: 0 48px;
}

.module-nav__track {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  gap: 4px;
  margin: 0;
  padding: 0;
  overflow-x: auto;
  list-style: none;
  scrollbar-width: none;
}

.module-nav__track::-webkit-scrollbar {
  display: none;
}

/*
 * 收起态这一行只说「你在哪个模块」，名字仍从 `activeModule` 来（同一张表、同一条归属），
 * 不是第二份导航。
 */
.module-nav__current {
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: var(--theme-primary-deep);
  font-size: var(--text-md);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.module-nav__current a {
  color: inherit;
  text-decoration: none;
}

.module-nav__toggle {
  flex: 0 0 auto;
  height: 24px;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 0 8px;
  color: var(--ui-ink-muted);
  background: var(--ui-surface);
  cursor: pointer;
  font: inherit;
  font-size: var(--text-xs);
  white-space: nowrap;
}

.module-nav__toggle:hover {
  border-color: var(--theme-rule);
  color: var(--theme-primary);
}

.module-nav__toggle:focus-visible {
  outline: 2px solid var(--theme-rule);
  outline-offset: 2px;
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

/*
 * 窄屏那棵默认藏掉，包括它里面可能正开着的弹层——宽屏不需要第二种形态，
 * 而 `display: none` 会把整棵（含弹层）一起移出 Tab 序与读屏朗读。
 */
.module-nav__mobile {
  display: none;
}

@media (max-width: 760px) {
  .module-nav {
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

  /*
   * 横向滚动那一排在窄屏整条退役（老板指令「绝对不能把右侧按钮挤出屏幕或让用户看不见」）：
   * 轨道与收起按钮都不渲染出来，这一行只剩「当前模块 ▾」一颗——没有东西可挤。
   */
  .module-nav__track,
  .module-nav__toggle {
    display: none;
  }

  .module-nav__mobile {
    position: relative;
    display: flex;
    flex: 1 1 auto;
    min-width: 0;
  }
}

.module-nav__menu-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 6px 10px;
  color: var(--ui-ink);
  background: var(--ui-surface);
  cursor: pointer;
  font: inherit;
  font-size: var(--text-base);
}

.module-nav__menu-trigger:hover {
  border-color: var(--theme-rule);
  color: var(--theme-primary);
}

.module-nav__menu-trigger:focus-visible {
  outline: 2px solid var(--theme-rule);
  outline-offset: 2px;
}

.module-nav__menu-name {
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.module-nav__menu-caret {
  flex: 0 0 auto;
  color: var(--ui-ink-faint);
  transition: transform var(--motion-base) var(--motion-ease);
}

.module-nav__menu-caret--open {
  transform: rotate(180deg);
}

/*
 * 菜单宽度取这一行的整宽（左边贴导航内缩）：七个条目竖排，横向不需要滚动，
 * 也不再有任何东西被挤出屏幕。z-index 不在这里新增——它住在 `.app-chrome`（10）
 * 这个层叠上下文里，天然盖住内容区，而抽屉（30）与模态（100）仍在它之上（守则 L3 阶梯）。
 */
.module-nav__menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  width: min(280px, 100vw - 32px);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 6px;
  background: var(--ui-surface);
  box-shadow: var(--ui-shadow-menu);
}

.module-nav__menu-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.module-nav__menu-item {
  display: block;
  border-radius: var(--ui-radius);
  padding: 10px 12px;
  color: var(--ui-ink-muted);
  font-size: var(--text-md);
  text-decoration: none;
}

.module-nav__menu-item:hover {
  color: var(--theme-primary);
  background: var(--ui-surface-soft);
}

.module-nav__menu-item:focus-visible {
  outline: 2px solid var(--theme-rule);
  outline-offset: -2px;
}

.module-nav__menu-item[aria-current='page'] {
  color: var(--theme-primary-deep);
  background: var(--theme-bg-light);
  font-weight: 600;
}
</style>
