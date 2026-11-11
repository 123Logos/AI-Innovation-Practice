<script setup lang="ts">
/**
 * 新建简历弹窗（批次 F3 第三片从 `AppTopBar.vue` 搬出来，内容一字未改）。
 *
 * 为什么它不跟着简历列表住进抽屉里：弹窗是 `.ui-overlay` 那一档（z-index 100），
 * 而抽屉收起时列表组件会被卸载。弹窗若长在列表里，用户点进输入框的那一下会被
 * 浮层基座判成「点外面」→ 抽屉卸载 → 弹窗跟着消失，字打到一半没了。
 * 所以列表只发 `create` 意图，弹窗由外框（`AppLayout`）持有，不随抽屉开合而生死。
 *
 * 形态照 `ConfirmDialog.vue` 的先例：全局 `.ui-overlay` + `.ui-panel`，
 * Esc 挂在遮罩元素上（`@keydown`）而不是 document——模态弹窗不需要全局监听，
 * 挂载时把焦点送进标题输入框，按键自然冒泡到遮罩。
 */
import { nextTick, onMounted, ref } from 'vue'

import { isApiError } from '@/api'
import { useResumeStore } from '@/stores/resume'

const emit = defineEmits<{ close: [] }>()

const resume = useResumeStore()

type CreateMode = 'blank' | 'copy'

const titleInput = ref<HTMLInputElement | null>(null)
const createTitle = ref('')
const createMode = ref<CreateMode>('blank')
const creating = ref(false)
const createError = ref('')

/**
 * 标题的「自动递增」默认值：我的简历 → 我的简历 2 → 我的简历 3。
 *
 * 与后端 `_get_next_default_title()` 同规则，这里先算好是为了**让用户看得见**；
 * 用户把标题清空时仍交给后端命名。另外后端会拦重名，所以即便前端算错也不会写脏。
 */
function nextDefaultTitle(): string {
  const titles = new Set(resume.profiles.map((profile) => profile.title))
  if (!titles.has('我的简历')) return '我的简历'
  let index = 2
  while (titles.has(`我的简历 ${index}`)) index += 1
  return `我的简历 ${index}`
}

function describeError(error: unknown, fallback: string): string {
  if (isApiError(error)) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}

onMounted(async () => {
  createTitle.value = nextDefaultTitle()
  // 有当前简历时默认「复制当前」——大多数新简历是现有简历的一个变体。
  createMode.value = resume.currentProfileId ? 'copy' : 'blank'
  await nextTick()
  titleInput.value?.focus()
})

function close(): void {
  if (creating.value) return
  emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') close()
}

async function submitCreate(): Promise<void> {
  if (creating.value) return
  createError.value = ''
  creating.value = true
  try {
    await resume.createProfile({
      title: createTitle.value,
      copyCurrent: createMode.value === 'copy',
    })
    emit('close')
  } catch (error) {
    createError.value = describeError(error, '新建简历失败，请稍后重试。')
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="ui-overlay" @click.self="close" @keydown="onKeydown">
      <section
        class="ui-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-profile-heading"
      >
        <h2 id="create-profile-heading" class="modal-title">新建简历</h2>
        <p class="modal-intro">
          每份简历独立保存，可用于投不同岗位；生成简历时使用当前选中的这一份。
        </p>

        <label class="modal-field">
          <span>简历标题</span>
          <input
            ref="titleInput"
            v-model="createTitle"
            maxlength="200"
            placeholder="留空则自动命名为「我的简历 N」"
            @keydown.enter.prevent="submitCreate"
          />
        </label>

        <fieldset class="modal-modes">
          <legend>内容来源</legend>
          <label class="modal-radio">
            <input v-model="createMode" type="radio" value="blank" />
            <span>空白新建</span>
          </label>
          <label class="modal-radio" :class="{ 'modal-radio--disabled': !resume.currentProfileId }">
            <input
              v-model="createMode"
              type="radio"
              value="copy"
              :disabled="!resume.currentProfileId"
            />
            <span>复制当前简历</span>
          </label>
        </fieldset>

        <p v-if="createError" class="modal-error" role="alert">{{ createError }}</p>

        <div class="modal-actions">
          <button class="modal-button" type="button" :disabled="creating" @click="close">
            取消
          </button>
          <button
            class="modal-button modal-button--primary"
            type="button"
            :disabled="creating"
            @click="submitCreate"
          >
            {{ creating ? '创建中…' : '创建' }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>
