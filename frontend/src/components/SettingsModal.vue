<script setup lang="ts">
/**
 * 设置弹窗（2026-11-15 第 25 轮，老板指令第 4 条）。
 *
 * 「AI 设置」从顶栏里那个独立弹窗**搬进这里**，作为一个页签；同时预留「其他设置」
 * 的位置（现在只有一句占位，不放禁用控件——守则 L5：没有实现的功能不放入口）。
 * 抽屉里那一行「AI 设置」快捷入口随之删除：账户的设置在「设置」里，不在抽屉里晒两遍。
 *
 * 口径照 `ConfirmDialog.vue`：`.ui-overlay`（z-index 100，压在抽屉 30 之上）+
 * `.ui-panel` + `role="dialog"` + `aria-modal`，Esc 与点遮罩都关闭。
 * 开合状态在 `stores/ui.ts`：任何页面都能调 `openSettings()`（老板指令第 5 条）。
 *
 * Key 的三条老规矩一格没改（ADR-009 / `client.ts`）：只进 `sessionStorage`、
 * 打开弹窗**不回填**已存的值、保存后立刻清空输入框。
 */
import { computed, ref, watch } from 'vue'

import { clearDeepSeekKey, hasDeepSeekKey, setDeepSeekKey } from '@/api'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

type SettingsTab = 'ai' | 'other'

const activeTab = ref<SettingsTab>('ai')

const TABS: ReadonlyArray<{ key: SettingsTab; label: string }> = [
  { key: 'ai', label: 'AI 密钥' },
  { key: 'other', label: '其他设置' },
]

/**
 * 输入框草稿：打开弹窗时**不回填**已存的 Key。
 * Key 只在 sessionStorage 里活着，没必要再把它显示到屏幕上（肩窥 / 屏幕录制 / 截图）。
 */
const aiKeyDraft = ref('')
const aiKeySaved = ref(false)
const error = ref('')
const saved = ref(false)

// 每次打开都从干净状态开始：默认回到 AI 页签（从「点此配置 AI 密钥」进来的用户就在这一页）。
watch(
  () => ui.settingsOpen,
  (open) => {
    if (!open) return
    activeTab.value = 'ai'
    aiKeyDraft.value = ''
    aiKeySaved.value = hasDeepSeekKey()
    error.value = ''
    saved.value = false
  },
)

const statusText = computed(() =>
  aiKeySaved.value ? '这个标签页里已经填好了' : '这个标签页里还没填',
)

function submitAiKey(): void {
  const value = aiKeyDraft.value.trim()
  if (!value) {
    // ui-copy:allow C2 唯一豁免：这句话指向用户自己去 DeepSeek 复制的那串凭据，改名他会找不到
    error.value = '这里还空着——把你的 DeepSeek API Key 粘进来才行。'
    return
  }
  setDeepSeekKey(value)
  aiKeySaved.value = true
  // 保存后立刻清空输入框：屏幕上看不到完整 Key，也避免下次打开时留着一份明文。
  aiKeyDraft.value = ''
  error.value = ''
  saved.value = true
}

function forgetAiKey(): void {
  clearDeepSeekKey()
  aiKeySaved.value = false
  aiKeyDraft.value = ''
  error.value = ''
  saved.value = false
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') ui.closeSettings()
}
</script>

<template>
  <Teleport to="body">
    <Transition name="ui-fade">
      <div
        v-if="ui.settingsOpen"
        class="ui-overlay"
        data-testid="settings-modal"
        @click.self="ui.closeSettings()"
        @keydown="onKeydown"
      >
        <section
          class="ui-panel settings-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby="settings-heading"
        >
          <h2 id="settings-heading" class="modal-title">设置</h2>

          <div class="settings-tabs" role="tablist" aria-label="设置分类">
            <button
              v-for="tab in TABS"
              :key="tab.key"
              class="settings-tab"
              :class="{ 'settings-tab--active': activeTab === tab.key }"
              type="button"
              role="tab"
              :aria-selected="activeTab === tab.key"
              @click="activeTab = tab.key"
            >
              {{ tab.label }}
            </button>
          </div>

          <div v-if="activeTab === 'ai'" class="settings-body" role="tabpanel">
            <p class="modal-intro">
              <!-- ui-copy:allow 老板 2026-11-09 定的 C2 唯一豁免：用户必须亲手去 DeepSeek 拿这把凭据，
                   界面里的原词（DeepSeek API Key）可以出现；删掉它他反而认不出要粘什么。 -->
              「AI 润色」用的是你自己申请的 DeepSeek API Key；「生成简历」是纯模板，不需要这把 Key。
              润色失败只会提示你重试，不会改动表单。
              Key 只留在这个标签页里：关掉标签页就没了，不会存进你的账号，也不会被记录或转发。
            </p>

            <p class="ai-settings-status">{{ statusText }}</p>

            <label class="modal-field">
              <!-- ui-copy:allow 输入框标签：与他 DeepSeek 控制台里的叫法一致，是 C2 的唯一豁免 -->
              <span>DeepSeek API Key</span>
              <input
                v-model="aiKeyDraft"
                type="password"
                autocomplete="off"
                spellcheck="false"
                placeholder="sk-……"
                @keydown.enter.prevent="submitAiKey"
              />
            </label>

            <p v-if="error" class="modal-error" role="alert">{{ error }}</p>
            <p v-else-if="saved" class="modal-notice" role="status">已保存在这个标签页里。</p>

            <div class="modal-actions">
              <button class="modal-button" type="button" @click="forgetAiKey">清除 Key</button>
              <button class="modal-button modal-button--primary" type="button" @click="submitAiKey">
                保存 Key
              </button>
            </div>
          </div>

          <!-- 预留位置：不放禁用控件（守则 L5），只说清这里以后会有别的设置。 -->
          <div v-else class="settings-body" role="tabpanel">
            <p class="modal-intro">其他设置会陆续放进来。</p>
          </div>

          <div class="modal-actions modal-actions--footer">
            <button class="modal-button" type="button" data-testid="settings-close" @click="ui.closeSettings()">
              关闭
            </button>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* 这两个类来自 `styles.css` 的全局弹窗层（`.ui-overlay` 100 档 / `.ui-panel`），
   这里只补「比确认框宽一点」这一格：设置有两栏控件与一段说明。 */
.settings-panel {
  width: min(520px, 100%);
}

.settings-tabs {
  display: flex;
  gap: 4px;
  margin: 14px 0 0;
  border-bottom: 1px solid var(--ui-border);
}

.settings-tab {
  border: 0;
  border-bottom: 2px solid transparent;
  padding: 8px 12px;
  color: var(--ui-ink-muted);
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: var(--text-base);
}

.settings-tab:hover {
  color: var(--theme-primary);
}

.settings-tab--active {
  border-bottom-color: var(--theme-primary);
  color: var(--theme-primary-deep);
  font-weight: 600;
}

.settings-tab:focus-visible {
  outline: 2px solid var(--theme-rule);
  outline-offset: 2px;
}

.settings-body {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.ai-settings-status {
  margin: 0;
  color: var(--ui-ink-muted);
  font-size: var(--text-base);
}

.modal-notice {
  margin: 0;
  color: var(--theme-primary-deep);
  font-size: var(--text-base);
}

.modal-actions--footer {
  margin-top: 16px;
}
</style>
