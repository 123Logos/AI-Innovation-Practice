<script setup lang="ts">
/**
 * 全站唯一的确认 / 提示弹窗（批次 F1b，守则 C6「不用原生弹层」+ L3「同一件事只有一种做法」）。
 *
 * 形态照 `JobDetailView.vue` 的先例抄：遮罩用全局 `.ui-overlay`（flex + `margin:auto`，
 * 内容比视口高时退化成顶部对齐，按钮仍然够不着不了——理由写在那段注释里），
 * 面板用 `.ui-panel`，`role="dialog"` + `aria-modal` + `aria-labelledby`，
 * Esc 与点遮罩都算「取消」，打开时焦点直接进主按钮。
 *
 * 组件本身**不持有任何业务决定**：它只读 `stores/dialog.ts` 里那一格请求，
 * 把一次点击翻译成 `settle(true/false)`。所以六处调用点的逻辑一行都不必改写形状。
 */
import { nextTick, ref, watch } from 'vue'

import { useDialogStore } from '@/stores/dialog'

const dialog = useDialogStore()

const primaryButton = ref<HTMLButtonElement | null>(null)

// 焦点进得到主按钮：键盘用户按回车就等价于点「确认」，按 Esc 等价于取消。
watch(
  () => dialog.pending,
  async (request) => {
    if (!request) return
    await nextTick()
    primaryButton.value?.focus()
  },
)

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') dialog.settle(false)
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="dialog.pending"
      class="ui-overlay"
      data-testid="app-dialog"
      @click.self="dialog.settle(false)"
      @keydown="onKeydown"
    >
      <section
        class="ui-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="app-dialog-title"
      >
        <h2 id="app-dialog-title" class="dialog-title">{{ dialog.pending.title }}</h2>
        <p class="dialog-body">{{ dialog.pending.body }}</p>
        <p v-if="dialog.pending.subject" class="dialog-subject">
          {{ dialog.pending.subject }}
        </p>
        <div class="dialog-actions">
          <button
            v-if="dialog.pending.kind === 'confirm'"
            class="dialog-button dialog-button--ghost"
            type="button"
            data-testid="app-dialog-cancel"
            @click="dialog.settle(false)"
          >
            {{ dialog.pending.cancelLabel }}
          </button>
          <button
            ref="primaryButton"
            class="dialog-button"
            :class="
              dialog.pending.tone === 'danger' ? 'dialog-button--danger' : 'dialog-button--primary'
            "
            type="button"
            data-testid="app-dialog-confirm"
            @click="dialog.settle(true)"
          >
            {{ dialog.pending.confirmLabel }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
/*
 * 这里不复用 `JobDetailView` 的 scoped 按钮：scoped 样式不出组件，复制一份是
 * 唯一不牵动那张页面的做法（批次 F2 的「弹层基础件」会把这一格和它一起收掉）。
 */
.dialog-title {
  margin: 0;
  color: var(--ui-ink);
  font-size: 17px;
  font-weight: 650;
}
.dialog-body {
  margin: 8px 0 0;
  color: var(--ui-ink-muted);
  font-size: 13px;
  line-height: 1.6;
}
.dialog-subject {
  margin: 10px 0 0;
  padding: 8px 10px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  color: var(--ui-ink);
  background: var(--ui-canvas);
  font-size: 13px;
  line-height: 1.5;
}
.dialog-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
}
.dialog-button {
  min-width: 88px;
  padding: 8px 14px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  color: var(--ui-ink);
  background: var(--ui-surface);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.dialog-button--primary {
  border-color: var(--theme-primary);
  color: #fff;
  background: var(--theme-primary);
}
.dialog-button--danger {
  border-color: var(--ui-danger-border);
  color: var(--ui-danger);
  background: var(--ui-danger-soft);
}
.dialog-button--ghost {
  border-color: transparent;
  color: var(--ui-ink-muted);
  background: transparent;
}
.dialog-button:hover:not(:disabled) {
  filter: brightness(0.97);
}
</style>
