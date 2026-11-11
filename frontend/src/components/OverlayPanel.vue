<script setup lang="ts">
/**
 * 全站唯一的**浮层基座**（批次 F3 第一片，守则 L3「抽屉 / 侧栏 / Popover 必须是同一个基础件的不同形态」）。
 *
 * 在这之前，「点外面关闭」与「按 Esc 关闭」这两件事写在 `AppTopBar.vue` 的一个
 * `document.addEventListener('pointerdown')` 里，靠两个 `if` 分别管简历切换器与账户面板——
 * 于是每加一个浮层就要多抄一次，而 L3 的判据正是「出现第四份手写监听即红」。
 * 本文件把那一份实现收拢成一处，调用方只保留**产品决定**（谁跟谁互斥、打开时做什么）。
 *
 * 它只干下面这几件事，而且每一件只在这一处实现：
 *
 * 1. 点外面关闭：判定用 `root.contains(target)`，命中面板内部不算关闭，
 *    命中**自己的触发按钮**（`aria-controls` 指向本面板的那个元素）同样不算——
 *    那一下是调用方的开合。只豁免自己那一个面板，所以别的浮层仍把这一下判成「点外面」，
 *    两个抽屉不会同时开着叠在一起（详见 `onPointerDown` 里为什么不用 `@pointerdown.stop`）；
 * 2. `Escape` 关闭：同样一处；
 * 3. 监听的生命周期：挂载即注册、卸载即注销——所以调用方用 `v-if` 收起浮层时，
 *    不会在 document 上留下一份空转的监听。
 * 4. 焦点进出（进面板 / 还触发器）与**模态形态的焦点陷阱**（`trapFocus`，见文件末）。
 *
 * 刻意**不做**的三件事，每一条都是决定不是遗漏：
 *
 * - **不 Teleport**（基座自己不换挂载点）。现有测试与 CSS 都按「弹层就在触发器正下方」写，
 *   改成挂到 `body` 会把 `AppTopBar.spec.ts` 的结构断言整片掀掉，那是另一件事的成本，不该混进这一片。
 *   调用方**可以**自己 Teleport：`UserDrawer.vue` 就把整个抽屉挂到 `body`，因为顶栏的
 *   `backdrop-filter` 会成为 fixed 后代的包含块——抽屉留在顶栏里只有 58px 高（两档实测）。
 *   这一条边界因此是「基座不假设挂载点」，不是「全站不许 Teleport」。
 * - **不定位置、不定尺寸**。形态由 `panelClass` 交回调用方，因此这次迁移**不改一屏像素**。
 * - **不管互斥**。两个浮层贴在顶栏同一处、必须互斥，那是产品行为，留在调用方两行写完更诚实；
 *   把它做进基座会变成「基座替所有调用方决定彼此关系」。
 *
 * 焦点是这一片第二批做的（同一天）：打开时把焦点送进面板第一个可聚焦项，
 * 关闭时还给当初打开它的那个元素——键盘用户按 Esc 关掉简历列表之后，
 * 焦点不该掉回页面顶端，那是「按 Tab 从头走一遍」级别的惩罚。
 *
 * 焦点陷阱是第四片补的，而且**按形态开关**（`trapFocus`）：带遮罩的抽屉是模态的，
 * Tab 必须圈在里面（`DrawerPanel` 打开它）；不带遮罩的 Popover 不是模态的，
 * Tab 该能走出去，圈住它反而把键盘用户关在一个没有遮罩的小盒子里。
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
  /** 关掉之后焦点给谁。默认给「打开它的那个元素」；调用方也可以显式指定。 */
  focusFallback?: string
  /** 模态形态（带遮罩的抽屉）才开：Tab / Shift+Tab 圈在面板里。Popover 不开。 */
  trapFocus?: boolean
}>()

const emit = defineEmits<{ close: [] }>()

const root = ref<HTMLElement | null>(null)

/** 打开面板时页面上拿着焦点的那个元素（通常是顶栏的触发按钮），关闭后把焦点还给它。 */
let opener: HTMLElement | null = null

/** 面板内可聚焦项：菜单项与按钮算，被禁用的不算。 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusFirstItem(): void {
  const el = root.value
  if (!el) return
  const first = el.querySelector<HTMLElement>(FOCUSABLE)
  // 空面板（只有静态文字）也要能收焦点，否则 Esc 之后没地方可还。
  ;(first ?? el).focus({ preventScroll: true })
}

function restoreFocus(): void {
  const target = props.focusFallback
    ? document.querySelector<HTMLElement>(props.focusFallback)
    : opener
  target?.focus({ preventScroll: true })
}

function onPointerDown(event: PointerEvent): void {
  const el = root.value
  if (!el) return
  const target = event.target as Node
  // 只认「点到了本面板之外」；点在面板里（含子节点）不算关闭。
  if (el.contains(target)) return
  // **自己的触发按钮**也不算「外面」：那一下是调用方的开合（再点一次该收起）。
  // 认法是 `aria-controls` 的反向链接——谁声明了「我控制这个面板」，谁就是它的触发器。
  // 为什么不在按钮上写 `@pointerdown.stop`：那样确实能豁免自己，但**别的浮层也看不见这一下**了，
  // 于是「开账户抽屉时简历抽屉还开着」——两个贴右边的满高面板会叠在一起。
  // 用反向链接只豁免自己那一个面板，其余浮层照常把这一下判成「点外面」，互斥因此是天然的。
  // 不用 `querySelector('[aria-controls=…]')` 拼选择器：id 来自调用方，拼进去等于给它注入选择器的口子。
  if (props.panelId) {
    for (const candidate of document.querySelectorAll('[aria-controls]')) {
      if (candidate.getAttribute('aria-controls') === props.panelId && candidate.contains(target)) {
        return
      }
    }
  }
  emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    emit('close')
    return
  }
  if (event.key !== 'Tab' || !props.trapFocus) return
  const el = root.value
  if (!el) return

  const items = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)]
  if (items.length === 0) {
    // 只有文字的面板：Tab 不该把焦点交给背后那一页（遮罩还盖着，点不到）。
    event.preventDefault()
    el.focus({ preventScroll: true })
    return
  }

  const first = items[0] as HTMLElement
  const last = items[items.length - 1] as HTMLElement
  const active = document.activeElement
  const outside = !(active instanceof HTMLElement) || !el.contains(active)

  if (event.shiftKey) {
    if (outside || active === first || active === el) {
      event.preventDefault()
      last.focus({ preventScroll: true })
    }
    return
  }
  if (outside || active === last) {
    event.preventDefault()
    first.focus({ preventScroll: true })
  }
}

onMounted(() => {
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  document.addEventListener('pointerdown', onPointerDown)
  document.addEventListener('keydown', onKeydown)
  focusFirstItem()
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onPointerDown)
  document.removeEventListener('keydown', onKeydown)
  // 焦点还在面板里才需要还：用户可能已经自己点了别处，那时抢焦点是打扰。
  const active = document.activeElement
  if (root.value && active instanceof HTMLElement && (root.value.contains(active) || active === root.value)) {
    restoreFocus()
  }
})
</script>

<template>
  <div
    :id="props.panelId"
    ref="root"
    :class="['overlay-panel', props.panelClass]"
    :role="props.role ?? 'menu'"
    :aria-label="props.label"
    tabindex="-1"
  >
    <slot />
  </div>
</template>

