<script setup lang="ts">
/**
 * 右侧用户抽屉（批次 F3 第二片，老板前端清单第 2 条「退出登录 / AI 设置收进右侧用户抽屉」）。
 *
 * 它替掉的是顶栏那个「更多」弹层——里面本来就只有这两项，改名成抽屉不是加一层：
 * 账户级动作（我要退出、我要换凭据）原本和「岗位库」「当前简历」混在同一排，
 * 用户想找退出时先要认出「更多」两个字。现在这一排的入口只留功能导航，账户的事在右边。
 *
 * 三个刻意：
 *
 * 1. **浮层的三件底层事（点外面 / Esc / 焦点归还）仍然由 `OverlayPanel` 一处实现**（L3）。
 *    本组件只写抽屉该有的形态：贴右边、满高、遮罩、窄屏不越过 86vw。
 *    换句话说，清单 4 是基座的第二个消费者，不是第三份手写监听。
 * 2. **Teleport 到 `body`**——这一条与基座相反，而且是被实测逼出来的：`.app-topbar` 上有
 *    `backdrop-filter`，而 `filter` / `backdrop-filter` 按规范给**所有后代**生成 fixed 定位的
 *    包含块。抽屉留在顶栏里时，`inset: 0` 量到的是顶栏那个 58px 高的盒子（375 与 1280 两档
 *    都一样，数值在本片 PR 描述里），抽屉和遮罩都只盖住顶栏那一条。挂到 `body` 后包含块
 *    回到视口。基座自己仍然不 Teleport，「位置与挂载点归调用方」这条没变。
 *    `z-index: 30`（L3 现阶梯里菜单那一档）在根层仍压在顶栏这个 `z-index: 10` 的
 *    stacking context 之上，AI 设置弹窗走 `.ui-overlay` 的 100 档，照样盖住抽屉。
 * 3. **不自带业务决定**：AI 设置怎么开、退出怎么调接口，全在调用方（`AppTopBar.vue`）
 *    原来的 handler 里，本组件只发两个意图事件。所以这一片没有第二套退出逻辑，也没有
 *    「抽屉里再确认一次」那种分叉。
 */
import OverlayPanel from '@/components/OverlayPanel.vue'

const props = defineProps<{
  /** 当前登录名，只作身份确认用，不可在这一屏改。 */
  username: string
  /** 正在退出：按钮禁用并改文案，防止连点发两次。 */
  signingOut?: boolean
}>()

const emit = defineEmits<{
  close: []
  openAiSettings: []
  logout: []
}>()
</script>

<template>
  <Teleport to="body">
    <div class="drawer-root">
    <!--
      遮罩不写 `@click`：它在面板**之外**，基座的 pointerdown 判定天然把它算成「点外面」，
      于是关闭只有一条路径。这里再挂一个 click 就是同一件事写两处——L3 判据针对的正是这种重复。
    -->
    <div class="drawer-mask" />

    <OverlayPanel
      panel-id="user-drawer"
      panel-class="user-drawer"
      role="group"
      label="账户"
      @close="emit('close')"
    >
      <div class="drawer-heading">
        <p class="drawer-eyebrow">已登录</p>
        <p class="drawer-name">{{ username }}</p>
      </div>

      <button class="drawer-item" type="button" @click="emit('openAiSettings')">AI 设置</button>

      <button
        class="drawer-item drawer-item--danger"
        type="button"
        :disabled="props.signingOut"
        @click="emit('logout')"
      >
        {{ props.signingOut ? '退出中…' : '退出登录' }}
      </button>
    </OverlayPanel>
    </div>
  </Teleport>
</template>

<style scoped>
/* 抽屉的层级只写一个数：`.drawer-root` 自己就是那个 fixed 盒子，遮罩与面板在它里面按
   DOM 顺序叠，不各自再造一档。30 是 L3 现阶梯里菜单那一档；AI 设置弹窗走 `.ui-overlay`
   的 100 档，所以它压在抽屉之上——退出确认也一样，那是本来就该在上面。 */
.drawer-root {
  position: fixed;
  inset: 0;
  z-index: 30;
}

.drawer-mask {
  position: absolute;
  inset: 0;
  background: rgb(15 20 25 / 42%);
}

.user-drawer {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(320px, 86vw);
  padding: 20px 20px 28px;
  border-left: 1px solid var(--ui-border);
  background: var(--ui-surface);
  box-shadow: var(--ui-shadow-menu);
}

.drawer-heading {
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ui-border);
}

.drawer-eyebrow {
  margin: 0 0 4px;
  color: var(--ui-ink-faint);
  font-size: 11px;
  letter-spacing: 0.12em;
}

.drawer-name {
  margin: 0;
  color: var(--ui-ink);
  font-size: 15px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.drawer-item {
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  color: var(--ui-ink);
  background: var(--ui-canvas);
  font: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}

.drawer-item:hover:not(:disabled) {
  border-color: var(--theme-rule);
}

.drawer-item:focus-visible {
  outline: 2px solid var(--theme-rule);
  outline-offset: 2px;
}

.drawer-item:disabled {
  color: var(--ui-ink-faint);
  cursor: default;
}

/* 退出是「不可逆但要紧」那一类：用现成的危险色三件套，不新调色板。 */
.drawer-item--danger {
  margin-top: 8px;
  border-color: var(--ui-danger-border);
  color: var(--ui-danger);
  background: var(--ui-danger-soft);
}
</style>
