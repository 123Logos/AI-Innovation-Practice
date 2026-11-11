<script setup lang="ts">
/**
 * 抽屉形态的外壳（批次 F3 第三片，守则 L3「抽屉 / 侧栏 / Popover 必须是同一个基础件的不同形态」）。
 *
 * 它不是第二个浮层基座：点外面 / Esc / 焦点归还仍然只在 `OverlayPanel.vue` 一处实现，
 * 本文件只负责「抽屉」这一种形态的三件外壳事——
 *
 * 1. **Teleport 到 `body`**。这不是风格选择：顶栏有 `backdrop-filter`，按规范它给所有后代
 *    生成 fixed 定位的包含块，抽屉留在顶栏里时 `inset: 0` 参照的是顶栏那个 58px 高的盒子
 *    （375 / 1280 两档实测量到过，jsdom 一条用例都测不出来）。挂到 body 才拿得到视口。
 * 2. **遮罩**。它是面板的**兄弟**而不是子节点，所以基座的「点外面」判定天然把点遮罩算成关闭——
 *    遮罩上因此不写第二个 `@click`，关闭只有一条路径。
 * 3. **形态本身**：贴右、满高、`min(320px, 86vw)`、`z-index` 取 L3 现阶梯的 30 档。
 *    两个抽屉（账户、简历）共用这一份尺寸与层级，不各写一个数。
 *
 * 它也是「模态」这句话的落点：`trap-focus` 只在这里开。有遮罩 = 背后那一页点不到，
 * 所以 Tab 必须圈在抽屉里；不带遮罩的 Popover 不圈（那是基座的默认）。
 *
 * 内容内缩与排布归调用方（`UserDrawer.vue` / `ResumeDrawer.vue`）：账户那两行要 20px 内缩，
 * 简历列表的排序条与行各自带内缩，外壳替它们决定只会打架。
 */
import OverlayPanel from '@/components/OverlayPanel.vue'

const props = defineProps<{
  /** 给读屏软件的面板名。 */
  label: string
  /** 需要被触发按钮的 `aria-controls` 指到时传入。 */
  panelId?: string
  /** 面板语义：一组动作是 `group`，可方向键走的列表才用 `menu`。默认 `group`。 */
  role?: 'menu' | 'group'
}>()

const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <Teleport to="body">
    <div class="drawer-root">
      <div class="drawer-mask" />

      <OverlayPanel
        :panel-id="props.panelId"
        panel-class="drawer-panel ui-scroll"
        :role="props.role ?? 'group'"
        :label="props.label"
        trap-focus
        @close="emit('close')"
      >
        <slot />
      </OverlayPanel>
    </div>
  </Teleport>
</template>

<style scoped>
/* 层级只写一个数：`.drawer-root` 自己就是那个 fixed 盒子，遮罩与面板在它里面按 DOM 顺序叠。
   30 是 L3 现阶梯里菜单那一档；自建弹窗走 `.ui-overlay` 的 100 档，所以弹窗压在抽屉之上。 */
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

.drawer-panel {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  width: min(320px, 86vw);
  border-left: 1px solid var(--ui-border);
  background: var(--ui-surface);
  box-shadow: var(--ui-shadow-menu);
  overflow-y: auto;
}
</style>
