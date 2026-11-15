<script setup lang="ts">
/**
 * 「AI 凭据没配好」那句话下面的一行小链接（2026-11-15 第 25 轮，老板指令第 5 条）。
 *
 * 它只做一件事：把「去设置里填 Key」从**一句提醒**变成**一次点击**。以前那句话写的是
 * 「打开右上角『AI 设置』」，用户得自己关掉弹窗、找抽屉、再点一次；现在点这里直接从
 * 任何页面弹出设置弹窗（`stores/ui.ts`），AI 页签默认就是打开的那一页。
 *
 * 判据收在这一个组件里：四个调用点（润色 / 提取 / 匹配 / 面试）传进来的都是后端或
 * store 给的**原因码**，认得的只有这两种——`key_missing` 与 `AI_KEY_MISSING`（HTTP 错误码）、
 * `key_invalid` 与 `AI_KEY_INVALID`。其余原因（超时、上游、输出不可解析）不给这个链接：
 * 那些不是用户去设置里能修好的，给了只会让他改一把本来没坏的 Key。
 */
import { computed } from 'vue'

import { useUiStore } from '@/stores/ui'

const props = defineProps<{
  /** 失败 / 降级原因码；`null` 或不认识 → 不渲染（这是正向判据：不该出现的场景不画）。 */
  reason?: string | null
}>()

const KEY_REASONS = new Set(['key_missing', 'key_invalid', 'AI_KEY_MISSING', 'AI_KEY_INVALID'])

const ui = useUiStore()
const visible = computed(() => (props.reason ? KEY_REASONS.has(props.reason) : false))
</script>

<template>
  <button
    v-if="visible"
    class="ai-key-hint"
    type="button"
    data-testid="ai-key-hint"
    @click="ui.openSettings()"
  >
    <!-- ui-copy:allow 老板 2026-11-15 第 25 轮原话就是「👉 点此配置 AI 密钥」：符号是句子的一部分，不是图标 -->
    👉 点此配置 AI 密钥
  </button>
</template>

<style scoped>
/*
 * 小字链接：它是「上面那句话的下一步」，不是新的主按钮——所以用链接的外观、
 * 与提示文字同一档字号，不给边框和底色。
 */
.ai-key-hint {
  display: inline-block;
  margin-top: 4px;
  border: 0;
  padding: 0;
  color: var(--theme-primary);
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: var(--text-sm);
  text-align: left;
  text-decoration: underline;
}

.ai-key-hint:hover {
  color: var(--theme-primary-deep);
}

.ai-key-hint:focus-visible {
  outline: 2px solid var(--theme-rule);
  outline-offset: 2px;
}
</style>
