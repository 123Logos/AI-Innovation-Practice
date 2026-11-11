<script setup lang="ts">
/**
 * 润色结果对照确认弹窗（ADR-010 §5）。
 *
 * 这一层是整个 AI 链路上唯一把候选文案交给用户看的地方，规则只有两条：
 *
 * 1. **左栏是发出去那一刻的原文快照**（`result.originals`），不是实时读 `draft`。
 *    这样「左边显示的文字」永远等于模型实际改写的那份，比对才有意义；
 * 2. **只有「接受并同步到表单」会改写数据**，而且改的是 Pinia 草稿，
 *    不落库——用户之后还得自己点「保存修改」。关弹窗（含点遮罩、按 Esc）等同于放弃。
 *
 * 所有文本都走 Vue 的文本插值，绝不 `v-html`：AI 产出的文案里如果出现 `<img onerror>`，
 * 在这里也只是一串可见的字符。
 */
import { computed } from 'vue'

import { POLISH_MODE_LABELS } from '@/api/types'
import type { PolishedExperience } from '@/api/types'
import type { PolishResult } from '@/stores/resume'

const props = defineProps<{ modelValue: boolean; result: PolishResult | null }>()

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
  accept: []
  discard: []
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const modeLabel = computed(() =>
  props.result ? POLISH_MODE_LABELS[props.result.mode] : ''
)

/** 逐段对照：左=原文快照，右=候选文案；`changed` 用于标出「这段其实没被改」。 */
const segments = computed(() => {
  const result = props.result
  if (!result) return []
  return result.originals.map((original, index) => {
    const polished = result.copy.experiences.find((item) => item.index === index)
    return {
      index,
      title: original.title || `第 ${index + 1} 段经历`,
      before: original,
      after: polished ?? original,
      changed:
        !!polished &&
        (polished.description !== original.description ||
          polished.responsibility !== original.responsibility),
      limitNote: limitNoteOf(polished),
    }
  })
})

/**
 * 两道边各自的说法（ADR-018 §6 那张表，措辞守 C3：给动作，不只说「超出」）。
 *
 * - 超**目标**：必须有数，用户据此自己减字；
 * - 超**契约**：只要求「出现 + 标注」，**不报少了多少字**（老板 2026-11-09 第 6 轮明确否掉过）。
 * 两道都吞掉不显示等于违反 ADR——「可见」的定义是在用户正看着的这块区域里可见。
 */
function limitNoteOf(polished: PolishedExperience | undefined): string {
  if (!polished) return ''
  const parts: string[] = []
  if (polished.truncated) {
    parts.push('这段内容超长，末尾已被截断，接受后请自己补完整。')
  }
  if (polished.target_words !== null && polished.over_limit_by > 0) {
    parts.push(
      `比目标（${polished.target_words} 字）多 ${polished.over_limit_by} 字，接受后删掉 ${polished.over_limit_by} 字再保存。`,
    )
  }
  return parts.join('')
}

/** 技能顺序：只有真的变了才显示这一栏，否则等于提醒用户「这里有个你可能没动的东西」。 */
const skillsChanged = computed(() => {
  const result = props.result
  if (!result) return false
  return result.copy.skills.join('\u0000') !== result.originalSkills.join('\u0000')
})

function accept(): void {
  emit('accept')
}

/** 「放弃」与直接关弹窗走同一条路：都不动表单。 */
function discard(): void {
  visible.value = false
  emit('discard')
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="`对照确认润色结果（${modeLabel}）`"
    class="ui-dialog ui-dialog--wide"
    @closed="emit('discard')"
  >
    <p class="review-note">
      左边是<b>你原来的文字</b>，右边是 AI 的建议。点「接受并同步到表单」只会填进左侧表单，
      <b>仍需你自己点「保存修改」才会保存</b>；关掉这个窗口就等于放弃。
    </p>

    <div v-if="skillsChanged && result" class="review-skills">
      <p class="review-skills__label">技能顺序（AI 建议的排序）</p>
      <div class="review-grid">
        <p class="review-cell">
          <span class="review-cell__tag">原</span>
          {{ result.originalSkills.join(' · ') }}
        </p>
        <p class="review-cell review-cell--after">
          <span class="review-cell__tag">荐</span>
          {{ result.copy.skills.join(' · ') }}
        </p>
      </div>
    </div>

    <section v-for="segment in segments" :key="segment.index" class="review-block">
      <h3 class="review-block__title">
        {{ segment.title }}
        <span v-if="!segment.changed" class="review-block__unchanged">未改动</span>
      </h3>
      <div class="review-grid">
        <div class="review-column">
          <p class="review-column__label">原文</p>
          <p class="review-column__field">项目描述</p>
          <p class="review-column__text">{{ segment.before.description || '（空）' }}</p>
          <p class="review-column__field">个人职责</p>
          <p class="review-column__text">{{ segment.before.responsibility || '（空）' }}</p>
        </div>
        <div class="review-column review-column--after">
          <p class="review-column__label">润色后</p>
          <p class="review-column__field">项目描述</p>
          <p class="review-column__text">{{ segment.after.description || '（空）' }}</p>
          <p class="review-column__field">个人职责</p>
          <p class="review-column__text">{{ segment.after.responsibility || '（空）' }}</p>
        </div>
      </div>
      <p v-if="segment.limitNote" class="review-block__limit">{{ segment.limitNote }}</p>
    </section>

    <template #footer>
      <div class="review-footer">
        <el-button @click="discard">放弃</el-button>
        <el-button type="primary" @click="accept">接受并同步到表单</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
/*
 * 节奏：块间 16px，栏内 12px，字段标签与正文 4px。
 * 原来 8 / 12 / 14px 混用，两栏看上去「一边松一边紧」。
 *
 * 次要文字一律用 `--ui-ink-muted` 而不是 `--ui-ink-faint`：faint 在白底上实测只有
 * 3.08:1，11–12px 的说明文字达不到 WCAG AA 的 4.5:1；层级改由字号与字重承担。
 */
.review-note {
  margin: 0 0 16px;
  color: var(--ui-ink-muted);
  font-size: var(--text-sm);
  line-height: 1.6;
}
.review-block {
  border-top: 1px solid var(--ui-border);
  padding: 16px 0;
}
/* 超限 / 截断提示：琥珀色，与「未保存」同色系——它要的是用户动手，不是报错。 */
.review-block__limit {
  margin: 10px 0 0;
  border-left: 3px solid var(--ui-warning-border);
  padding-left: 8px;
  color: var(--ui-ink-muted);
  font-size: var(--text-sm);
  line-height: 1.6;
}
.review-block__title {
  margin: 0 0 12px;
  color: var(--ui-ink);
  font-size: var(--text-md);
  font-weight: 650;
  line-height: 1.4;
}
.review-block__unchanged {
  margin-left: 6px;
  color: var(--ui-ink-muted);
  font-size: var(--text-xs);
  font-weight: 400;
}
.review-skills {
  margin: 0 0 16px;
  border: 1px dashed var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 12px;
}
.review-skills__label {
  margin: 0 0 8px;
  color: var(--theme-primary);
  font-size: var(--text-sm);
}
/*
 * 两栏对照：`repeat(2, minmax(0,1fr))` 保证左右**等宽**，
 * `minmax(0,…)` 是关键——默认 `auto` 会被长单词/长 URL 撑开，右栏比左栏宽。
 * 768px 以下（含平板竖屏）改上下堆叠：左右并排时每栏只剩 150px，一行排不下 6 个汉字。
 */
.review-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
/* 两栏用同一套盒子（描边 + 内缩），只把左竖线做成身份标识：灰=原文，主色=建议。 */
.review-column {
  min-width: 0;
  border: 1px solid var(--ui-border);
  border-left: 3px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 12px;
  background: var(--ui-surface);
}
.review-column--after {
  border-left-color: var(--theme-primary);
  background: var(--ui-surface-soft);
}
.review-column__label {
  margin: 0 0 8px;
  color: var(--ui-ink);
  font-size: var(--text-base);
  font-weight: 600;
}
.review-column__field {
  margin: 12px 0 4px;
  color: var(--ui-ink-muted);
  font-size: var(--text-xs);
}
.review-column__text {
  margin: 0;
  color: var(--ui-ink);
  font-size: var(--text-base);
  line-height: 1.6;
  /* 用户原文可能很长：保留换行、允许断词，不撑破弹窗。 */
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.review-cell {
  margin: 0;
  color: var(--ui-ink);
  font-size: var(--text-sm);
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.review-cell__tag {
  margin-right: 4px;
  color: var(--ui-ink-muted);
  font-size: var(--text-xs);
}
/* 按钮恒定右对齐；窄屏放不下时整组换行，仍然靠右，不「散成两行左对齐」。 */
.review-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}
.review-footer :deep(.el-button + .el-button) {
  margin-left: 0;
}
@media (max-width: 768px) {
  .review-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
