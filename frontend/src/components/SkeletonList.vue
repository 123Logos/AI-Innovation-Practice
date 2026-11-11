<script setup lang="ts">
/**
 * 骨架屏（批次 F4 第二片，守则 M2「异步内容先占位后填充」）。
 *
 * **尺寸是量出来的，不是写出来的**：`heights` / `heightsCompact` 由调用方传入该区块
 * 稳定态的真实高度（headless Edge + CDP 在 375 / 1280 两档量的，数值与出处写在各调用点）。
 * M2 明确禁止凭感觉写 `min-height`——凭感觉写的占位块在数据到达那一刻还是会跳一下，
 * 那正是它要消灭的东西。
 *
 * 三件事刻意不做：
 * 1. 不自己判断视口。窄屏那一档走 CSS 媒体查询（守则 L2：宽度驱动的显示差异归 CSS），
 *    所以两组高度都是**样式变量**而不是 JS 分支——内联样式优先级高于样式表，
 *    直接把 `height` 写进内联就没法被媒体查询覆盖了。
 * 2. 不写文案。加载中说什么由调用方给（`label`），这里只把它放进读屏可见、
 *    视觉隐藏的那一行：M3 要求加载中 / 失败 / 空态各自可读，骨架不能把状态吞掉。
 * 3. 不写动画时长。呼吸动画整条来自 `--skeleton-pulse` 令牌，
 *    `prefers-reduced-motion` 下它整条变成 `none`（开关只有 styles.css 那一处）。
 */
const props = withDefaults(
  defineProps<{
    /** 读屏文案：原来那句「正在加载…」，一个字都不改。 */
    label: string
    /** 宽屏（>760）稳定态每一块的高度，单位 px，实测值。 */
    heights: readonly number[]
    /** 窄屏（≤760）同一批块的高度，实测值。长度不足时按宽屏那一档兜底。 */
    heightsCompact: readonly number[]
    /** ≥1100 时的列数（岗位列表是两列卡片，其余都是一列）。 */
    columns?: number
    /** 块间距，取该列表稳定态的真实间距。 */
    gap?: number
  }>(),
  { columns: 1, gap: 0 },
)
</script>

<template>
  <div
    class="skeleton-list"
    role="status"
    aria-busy="true"
    :style="{ '--skeleton-cols': props.columns, '--skeleton-gap': `${props.gap}px` }"
  >
    <span class="ui-sr-only">{{ props.label }}</span>
    <span
      v-for="(height, index) in props.heights"
      :key="index"
      class="ui-skeleton"
      :style="{
        '--row-h': `${height}px`,
        '--row-h-compact': `${props.heightsCompact[index] ?? height}px`,
      }"
    />
  </div>
</template>

<style scoped>
.skeleton-list {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--skeleton-gap);
}
.skeleton-list .ui-skeleton {
  height: var(--row-h);
}
@media (min-width: 1100px) {
  .skeleton-list {
    grid-template-columns: repeat(var(--skeleton-cols), minmax(0, 1fr));
  }
}
@media (max-width: 760px) {
  .skeleton-list .ui-skeleton {
    height: var(--row-h-compact);
  }
}
</style>