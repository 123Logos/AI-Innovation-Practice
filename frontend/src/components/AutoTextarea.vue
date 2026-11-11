<script setup lang="ts">
/**
 * 多行输入的**唯一**实现（守则 F1，批次 F5）。
 *
 * 它替掉的三处各写各的：`ResumeEditor.vue` 的项目描述 / 个人职责（无 `rows`，
 * 靠 `min-height:58px` + 手动拖高）、`JobCreateView.vue` 的 JD（`rows="14"` +
 * `min-height:300px`）。三处的共同毛病是**盒子高度与内容无关**：粘一段长文进去，
 * 文字被关在一个固定高度的小窗口里滚，用户看不到自己刚粘进来的东西。
 *
 * 三条实现上的取舍：
 *
 * 1. **量的是 `scrollHeight`，不是 `field-sizing: content`。** 后者一行 CSS 就够，
 *    但它是 Chromium 123+ 才有的属性，Safari / Firefox 至今不认；认不认这件事
 *    在页面上表现为「有些人的输入框会撑开、有些人的不会」，比多写十几行 JS 糟得多。
 *
 * 2. **不设高度上限。** 守则 F1 要的就是「不出现内部滚动条」，加个 `max-height`
 *    等于把滚动条留给长文——那正是它要消灭的东西。JD 上限 8000 字
 *    （`api/jobs.ts` 的 `MAX_JOB_DESCRIPTION_LENGTH`），极端情况下这一页会很长，
 *    由**页面**滚动接管；页面滚动条不会像框内滚动那样把滚轮吃掉。
 *
 * 3. **只观察宽度变化。** 高度是本组件自己写的，若对每次尺寸变化都回量一次，
 *    就是「改高度 → 触发观察 → 再改高度」的自激环。`lastWidth` 那道比较把它掐断：
 *    宽度没变就什么都不做。换行位置只由宽度决定，所以这也正是需要重量的唯一时机
 *    （窗口缩放、三列 ↔ 两列切换、侧栏开合）。
 *
 * 与纸面的分工（守则 F2）：纸面那 13 个 `contenteditable` 块的高度由
 * `ResumePreview.vue` 的 `scrollHeight` / `ResizeObserver` 通道量，本组件不碰纸面 DOM，
 * 也不读 `--paper-*`。表单长高了只是这一页变长，A4 分页不受影响。
 *
 * 视觉（边框、内边距、字号、行高）留在各调用方的 `.field` 里：两个表单的字段密度
 * 本来就不同，统一的是**行为**不是外观。本组件只声明与高度算法直接相关的三条。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    /** 内容很少时的下限高度（px）。只托底，不封顶。 */
    minHeight?: number
  }>(),
  { minHeight: 0 },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const area = ref<HTMLTextAreaElement | null>(null)

function syncHeight(): void {
  const node = area.value
  if (!node) return
  // 先塌回 auto：不塌的话 scrollHeight 量到的是「现在有多高」，删字就再也缩不回去。
  node.style.height = 'auto'
  const style = window.getComputedStyle(node)
  // box-sizing 是 border-box，而 scrollHeight 不含边框，所以高度要把两条边框加回来。
  const borders =
    (Number.parseFloat(style.borderTopWidth) || 0) + (Number.parseFloat(style.borderBottomWidth) || 0)
  node.style.height = `${Math.max(node.scrollHeight + borders, props.minHeight)}px`
}

function onInput(event: Event): void {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
  syncHeight()
}

let observer: ResizeObserver | null = null
/** -1 是「还没量过」：观察器挂上后必然会送一次当前尺寸，那一次照常走。 */
let lastWidth = -1

onMounted(() => {
  const node = area.value
  if (!node) return
  syncHeight()
  observer = new ResizeObserver((entries) => {
    const width = entries[0]?.contentRect.width ?? 0
    if (width === lastWidth) return
    lastWidth = width
    syncHeight()
  })
  observer.observe(node)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
})

/* 外部改值也要重量：接受润色、恢复本地草稿、编辑态 GET 回填都不经过 `onInput`。 */
watch(() => [props.modelValue, props.minHeight], syncHeight, { flush: 'post' })
</script>

<template>
  <textarea
    ref="area"
    class="auto-textarea"
    :value="props.modelValue"
    @input="onInput"
  />
</template>

<style scoped>
.auto-textarea {
  display: block;
  width: 100%;
  /* 高度算法按 border-box 加边框，这里写死是为了不随全局 `*` 那条规则漂移。 */
  box-sizing: border-box;
  /* 自适应之后不该再有框内滚动条；手动拖高也会和自动撑开互相覆盖，一并去掉。 */
  overflow-y: hidden;
  resize: none;
}
</style>