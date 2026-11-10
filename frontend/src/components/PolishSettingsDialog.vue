<script setup lang="ts">
/**
 * AI 润色的档位选择对话框（ADR-010 §3）。
 *
 * 只做「选档位 + 收字数」这一件事：请求由 `stores/resume.ts` 的 `polish()` 发，
 * 结果确认在 `PolishReviewDialog.vue`。这里不落任何数据，也不碰 store。
 *
 * 字数上限只在 `expand` 档出现——其余档位后端会强制把 `max_words` 置空，
 * 如果界面上还让人填，就是给了一个「填了没用」的控件（比没有更糟）。
 */
import { computed, ref, watch } from 'vue'

import { DEFAULT_POLISH_MAX_WORDS, POLISH_MAX_WORDS_RANGE, POLISH_MODE_LABELS } from '@/api/types'
import type { PolishMode } from '@/api/types'

const props = defineProps<{ modelValue: boolean }>()

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
  confirm: [options: { mode: PolishMode; maxWords: number }]
}>()

/**
 * 档位文案：名字取自 `POLISH_MODE_LABELS`（与对照弹窗同一份），
 * `hint` 直接抄后端 Prompt 指令的口径，避免「界面承诺与模型行为不一致」。
 */
const MODE_OPTIONS: { value: PolishMode; label: string; hint: string }[] = [
  { value: 'detail', label: POLISH_MODE_LABELS.detail, hint: '只修语病、错别字与标点，长度基本不变' },
  { value: 'medium', label: POLISH_MODE_LABELS.medium, hint: '换用更准确的动词与表述，删掉口头禅' },
  { value: 'heavy', label: POLISH_MODE_LABELS.heavy, hint: '按 STAR 重写，突出做了什么与带来什么' },
  { value: 'expand', label: POLISH_MODE_LABELS.expand, hint: '丰富细节，严格不超过下面的字数' },
]

const mode = ref<PolishMode>('medium')
const maxWords = ref<number>(DEFAULT_POLISH_MAX_WORDS)

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

/**
 * 关闭时把字数钳回合法区间。
 *
 * `el-input-number` 自己会拦越界输入，但清空输入框会得到 `null`（我们让它是可空的），
 * 直接带着 `null` 去 `expand` 会让后端补默认值，而界面上显示的是空——
 * 下次打开就必须是「看得见的数字」。
 */
watch(visible, (open) => {
  if (open) return
  const value = Number(maxWords.value)
  if (!Number.isFinite(value) || value <= 0) {
    maxWords.value = DEFAULT_POLISH_MAX_WORDS
    return
  }
  maxWords.value = Math.min(POLISH_MAX_WORDS_RANGE.max, Math.max(POLISH_MAX_WORDS_RANGE.min, Math.round(value)))
})

function submit(): void {
  const value = Math.min(
    POLISH_MAX_WORDS_RANGE.max,
    Math.max(POLISH_MAX_WORDS_RANGE.min, Math.round(Number(maxWords.value) || DEFAULT_POLISH_MAX_WORDS)),
  )
  emit('confirm', { mode: mode.value, maxWords: value })
}
</script>

<template>
  <el-dialog v-model="visible" title="AI 智能润色" class="ui-dialog">
    <p class="polish-intro">
      润色只改写<b>项目经历的描述与职责</b>，标题、时间、教育经历、联系方式都不参与，
      也不会凭空多出你没写过的技术或数字。
    </p>

    <el-radio-group v-model="mode" class="polish-modes">
      <el-radio v-for="option in MODE_OPTIONS" :key="option.value" :value="option.value" class="polish-mode">
        <span class="polish-mode__label">{{ option.label }}</span>
        <span class="polish-mode__hint">{{ option.hint }}</span>
      </el-radio>
    </el-radio-group>

    <div v-if="mode === 'expand'" class="polish-words">
      <label class="polish-words__label" for="polish-max-words">每段字数上限</label>
      <el-input-number
        id="polish-max-words"
        v-model="maxWords"
        class="polish-words__input"
        :min="POLISH_MAX_WORDS_RANGE.min"
        :max="POLISH_MAX_WORDS_RANGE.max"
        :step="20"
        controls-position="right"
      />
      <p class="polish-words__hint">
        {{ POLISH_MAX_WORDS_RANGE.min }}–{{ POLISH_MAX_WORDS_RANGE.max }}，指每段「描述 + 职责」的合计长度
      </p>
    </div>

    <template #footer>
      <div class="polish-footer">
        <!-- ui-copy:allow C2 唯一豁免：这里说的就是用户自己去 DeepSeek 申请的那把凭据 -->
        <span class="polish-footer__note">用的是你自己的 DeepSeek API Key，按你的用量计费</span>
        <el-button type="primary" @click="submit">开始润色</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
/*
 * 竖向节奏统一成三档：块与块 16px、卡片与卡片 8px、标签与说明 2px。
 * 原来这里混着 12 / 14 / 6px，四档单选的间距看上去忽松忽紧。
 */
.polish-intro {
  margin: 0 0 16px;
  color: var(--ui-ink-muted);
  font-size: 13px;
  line-height: 1.6;
}
/* 四档竖排：一行一条说明才读得完，横排在窄弹窗里会挤成一团。 */
.polish-modes {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
/*
 * 每档做成一张可点的卡片：选中态只有 radio 圆点时，375px 上「选中了哪一档」
 * 要顺着圆点找一圈；整条描边 + 底色后一眼可辨。
 * 圆点默认与整块（标题 + 说明）居中对齐，两行文字下会偏下，所以 `align-items:flex-start`
 * 再给圆点补 3px：正好落在 14px 标题行的光学中心（行高 20.3px，圆点 14px）。
 */
.polish-mode {
  display: flex;
  align-items: flex-start;
  width: 100%;
  height: auto;
  min-height: 54px;
  margin-right: 0;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  padding: 10px 12px;
  background: var(--ui-surface);
  white-space: normal;
  transition: border-color .12s ease, background-color .12s ease;
}
.polish-mode:hover {
  border-color: var(--theme-rule-soft);
  background: var(--ui-surface-soft);
}
.polish-mode.is-checked {
  border-color: var(--theme-primary);
  /*
   * 选中底色用 `--theme-bg-light`，不用 `--theme-bg-hover`：
   * 暗色下 `bg-hover` = 主色朝深底掺 90%，与弹窗表面色实测只差 2/255（rgb(40,51,48)
   * vs rgb(38,51,48)），等于没有；`bg-light` 掺 18%，暗色下实测 rgb(51,62,59)，
   * 和描边一起才构成「这一档被选中了」。写在 `:hover` 之后，悬停时不会被覆盖。
   */
  background: var(--theme-bg-light);
}
.polish-mode :deep(.el-radio__input) {
  margin-top: 3px;
  flex: 0 0 auto;
}
.polish-mode :deep(.el-radio__label) {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-left: 8px;
  line-height: 1.45;
  white-space: normal;
}
.polish-mode__label {
  color: var(--ui-ink);
  font-size: 14px;
  font-weight: 600;
}
.polish-mode__hint {
  color: var(--ui-ink-muted);
  font-size: 12px;
  font-weight: 400;
}
/*
 * 字数行：标签在左、数字框在右、说明独占一行。
 * 原来标签是裸文本 + flex 挤在一行，中文标签和数字框基线不齐，
 * 说明又被 `flex:1 1 100%` 顶到下一行，看上去像另一个字段。
 */
.polish-words {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 130px;
  align-items: center;
  gap: 6px 12px;
  margin-top: 16px;
}
.polish-words__label {
  color: var(--ui-ink);
  font-size: 13px;
}
.polish-words__input {
  width: 100%;
}
.polish-words__hint {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--ui-ink-muted);
  font-size: 11px;
  line-height: 1.5;
}
/* 底部：说明与按钮两端对齐，窄屏放不下时整条换行，按钮靠右而不是被文字挤扁。 */
.polish-footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
}
.polish-footer__note {
  flex: 1 1 160px;
  color: var(--ui-ink-muted);
  font-size: 11px;
  line-height: 1.5;
}
.polish-footer :deep(.el-button) {
  flex: 0 0 auto;
}
</style>
