<script setup lang="ts">
/**
 * 全站唯一的**浮层基座**（批次 F3 第一片，守则 L3「抽屉 / 侧栏 / Popover 必须是同一个基础件的不同形态」）。
 *
 * 在这之前，「点外面关闭」与「按 Esc 关闭」这两件事写在 `AppTopBar.vue` 的一个
 * `document.addEventListener('pointerdown')` 里，靠两个 `if` 分别管简历切换器与「更多」面板——
 * 于是每加一个浮层就要多抄一次，而 L3 的判据正是「出现第四份手写监听即红」。
 * 本文件把那一份实现收拢成一处，调用方只保留**产品决定**（谁跟谁互斥、打开时做什么）。
 *
 * 它只干三件事，而且这三件事只在这一处实现：
 *
 * 1. 点外面关闭：判定用 `root.contains(event.target)`，命中面板内部不算关闭；
 * 2. `Escape` 关闭：同样一处；
 * 3. 监听的生命周期：挂载即注册、卸载即注销——所以调用方用 `v-if` 收起浮层时，
 *    不会在 document 上留下一份空转的监听。
 *
 * 刻意**不做**的四件事，每一条都是决定不是遗漏：
 *
 * - **不 Teleport**。现有测试与 CSS 都按「弹层就在触发器正下方」写，改成挂到 `body`
 *   会把 `AppTopBar.spec.ts` 的结构断言整片掀掉，那是另一件事的成本，不该混进这一片。
 *   真要做抽屉（清单 4 的右侧用户抽屉）时再连同挂载点一起定。
 * - **不定位置、不定尺寸**。形态由 `panelClass` 交回调用方，因此这次迁移**不改一屏像素**。
 * - **不管互斥**。两个弹层贴在顶栏同一处、必须互斥，那是产品行为，留在调用方两行写完更诚实；
 *   把它做进基座会变成「基座替所有调用方决定彼此关系」。
 * - **不接管焦点归还**。目前调用方的触发按钮是原生 `<button>`，收起后焦点落回原处由浏览器负责；
 *   键盘体验要单独测，不在这条片里顺手宣称。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps<{
  /** 给读屏软件的面板名，映射到 `aria-label`。 */
  label: string
  /** 调用方自己的形态类（沿用原 CSS 选择器，所以视觉上零变化）。 */
  panelClass?: string
  /** 需要被 `aria-controls` 指到时由调用方传入。 */
  panelId?: string
  /** 面板语义：简历切换器是 `menu`，一组动作按钮是 `group`。默认 `menu`。 */
  role?: 'menu' | 'group'
}>()

const emit = defineEmits<{ close: [] }>()

const root = ref<HTMLElement | null>(null)

function onPointerDown(event: PointerEvent): void {
  const el = root.value
  if (!el) return
  // 只认「点到了本面板之外」；点在面板里（含子节点）不算关闭。
  if (!el.contains(event.target as Node)) emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') emit('close')
}

onMounted(() => {
  document.addEventListener('pointerdown', onPointerDown)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onPointerDown)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div
    :id="props.panelId"
    ref="root"
    :class="['overlay-panel', props.panelClass]"
    :role="props.role ?? 'menu'"
    :aria-label="props.label"
  >
    <slot />
  </div>
</template>
