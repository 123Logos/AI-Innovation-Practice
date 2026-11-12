<script setup lang="ts">
/**
 * 模拟面试详情（FR-07 / B11a，ADR-022）。
 *
 * 一屏三块：顶部会话信息 + 中部问答回放（面试官在左、候选人在右）+ 底部作答框。
 * 多行作答框用共享的 `AutoTextarea`（守则 F1：全仓唯一的多行输入实现）。
 *
 * **可见降级（守则 C4）**：后端的 `ai_degrade_reason` 非空代表这一轮是本地题库而非真 AI，
 * 必须在这一轮气泡里原样展示给用户（见 `interviewDegradeHint`），绝不能让用户误以为
 * 「AI 说了这句」。失败一律可见：`role="alert"` 的红字，不静默、不 console.error。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { isApiError, interviewsApi } from '@/api'
import type { InterviewSessionDetail, InterviewTurnRead } from '@/api/interviews'
import { ANSWER_MAX_LEN, SPEAKER_LABELS, interviewDegradeHint } from '@/api/interviews'
import AutoTextarea from '@/components/AutoTextarea.vue'
import { formatDate } from '@/utils/datetime'
import SkeletonList from '@/components/SkeletonList.vue'

const route = useRoute()
const sessionId = computed<string>(() => String(route.params.id ?? ''))

const detail = ref<InterviewSessionDetail | null>(null)
const loading = ref(false)
const loadError = ref('')

const draft = ref('')
const sending = ref(false)
const sendError = ref('')

const isEnded = computed(() => detail.value?.session.status === 'ended')

function describeError(error: unknown, fallback: string): string {
  // 只回显服务端给的结构化文案（已是人话）；其余一律用固定降级文案，
  // 不把技术错误的原文（如网络异常）带上屏——那属于 G1 闸禁止的暴露面与黑话。
  if (isApiError(error) && error.message) return error.message
  return fallback
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    detail.value = await interviewsApi.getInterview(sessionId.value)
  } catch (error) {
    loadError.value = describeError(error, '读取这场面试失败，请返回列表重试。')
  } finally {
    loading.value = false
  }
}

async function sendMessage(): Promise<void> {
  const content = draft.value.trim()
  if (!content || sending.value || isEnded.value) return
  sending.value = true
  sendError.value = ''
  try {
    const result = await interviewsApi.sendInterviewMessage(sessionId.value, content)
    draft.value = ''
    detail.value?.turns.push(result.candidate_turn, result.interviewer_turn)
  } catch (error) {
    sendError.value = describeError(error, '没能把回答发出去，请稍后重试。')
  } finally {
    sending.value = false
  }
}

async function endInterview(): Promise<void> {
  if (!detail.value || isEnded.value) return
  try {
    const session = await interviewsApi.endInterview(sessionId.value)
    detail.value.session = session
  } catch (error) {
    sendError.value = describeError(error, '没能结束这场面试，请稍后重试。')
  }
}

/** 一个轮次的降级提示（非空才展示）。 */
function turnHint(turn: InterviewTurnRead): string | null {
  // 开场轮（sequence=1）走的是「不带凭据的刻意内置题库」，与「用户没填凭据」不是
  // 同一件事，话术必须分开；非开场轮仍按后端给的降级键逐字翻译（守则 C4）。
  return interviewDegradeHint(turn.ai_degrade_reason, turn.sequence === 1)
}

onMounted(load)
</script>

<template>
  <main class="interview-detail">
    <RouterLink class="back-link" :to="{ name: 'interview-list' }">返回面试列表</RouterLink>

    <p v-if="loadError" class="feedback feedback--error" role="alert">{{ loadError }}</p>
    <template v-else-if="detail">
      <header class="detail-head">
        <div>
          <p class="eyebrow">模拟面试</p>
          <h1>{{ detail.session.target_role || '通用模拟面试' }}</h1>
          <p class="meta">
            开始时间：{{ formatDate(detail.session.created_at) || detail.session.created_at }}
            <span class="dot">·</span>
            {{ isEnded ? '已结束' : '进行中' }}
          </p>
        </div>
        <button
          v-if="!isEnded"
          class="text-button"
          type="button"
          :disabled="sending"
          @click="endInterview"
        >
          结束这场面试
        </button>
      </header>

      <ol class="chat" aria-label="面试对话">
        <li
          v-for="turn in detail.turns"
          :key="turn.id"
          class="chat-row"
          :class="turn.speaker === 'candidate' ? 'chat-row--mine' : 'chat-row--theirs'"
        >
          <div class="bubble">
            <p class="bubble-speaker">{{ SPEAKER_LABELS[turn.speaker] }}</p>
            <p class="bubble-text">{{ turn.content }}</p>
            <p v-if="turnHint(turn)" class="bubble-hint" role="alert">{{ turnHint(turn) }}</p>
          </div>
        </li>
      </ol>

      <p v-if="isEnded" class="feedback feedback--ended">
        这场面试已结束，可以回看上面的整段对话。
      </p>
      <form v-else class="answer-form" @submit.prevent="sendMessage">
        <label class="field-label" for="answer">你的回答</label>
        <AutoTextarea
          id="answer"
          v-model="draft"
          class="answer-field"
          :min-height="58"
          :maxlength="ANSWER_MAX_LEN"
          placeholder="写下你的回答，面试官会根据它继续追问。"
        />
        <div class="answer-actions">
          <p v-if="sendError" class="feedback feedback--error" role="alert">{{ sendError }}</p>
          <button class="primary-button" type="submit" :disabled="sending || !draft.trim()">
            {{ sending ? '面试官思考中…' : '发送回答' }}
          </button>
        </div>
      </form>
    </template>

    <div v-else>
      <SkeletonList
        label="正在加载这场面试…"
        :heights="[60, 60, 60, 60, 60]"
        :heights-compact="[72, 72, 72, 72, 72]"
        :columns="1"
        :gap="12"
      />
    </div>
  </main>
</template>

<style scoped>
.interview-detail { width: min(820px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.back-link { display: inline-block; margin-bottom: 16px; color: var(--theme-primary); font-size: var(--text-base); text-decoration: none; }
.back-link:hover { text-decoration: underline; }

.eyebrow { margin: 0 0 8px; color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .12em; }
h1 { margin: 0; color: var(--ui-ink); font-size: var(--text-4xl); font-weight: 650; }
.meta { margin: 8px 0 0; color: var(--ui-ink-muted); font-size: var(--text-sm); }
.dot { margin: 0 6px; color: var(--ui-ink-faint); }

.detail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.text-button { flex: 0 0 auto; height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 14px; color: var(--ui-ink-muted); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-sm); white-space: nowrap; }
.text-button:hover:not(:disabled) { border-color: var(--theme-rule); color: var(--theme-primary-deep); }

.chat { display: grid; gap: 14px; margin: 0 0 20px; padding: 0; list-style: none; }
.chat-row { display: flex; }
.chat-row--mine { justify-content: flex-end; }
.chat-row--theirs { justify-content: flex-start; }
.bubble { max-width: 78%; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 12px 14px; background: var(--ui-surface); }
.chat-row--mine .bubble { border-color: var(--theme-primary); background: var(--theme-bg-light); }
.bubble-speaker { margin: 0 0 4px; color: var(--ui-ink-faint); font-size: var(--text-xs); }
.bubble-text { margin: 0; color: var(--ui-ink); font-size: var(--text-md); line-height: 1.65; white-space: pre-wrap; word-break: break-word; }
.bubble-hint { margin: 8px 0 0; padding-top: 8px; border-top: 1px dashed var(--ui-border-strong); color: var(--ui-warning); font-size: var(--text-sm); line-height: 1.5; }
.chat-row--mine .bubble-hint { color: var(--ui-warning); }

.feedback { margin: 0 0 14px; color: var(--ui-ink-faint); font-size: var(--text-base); line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }
.feedback--ended { color: var(--ui-ink-muted); font-size: var(--text-sm); }

.answer-form { display: grid; gap: 8px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 14px 16px; background: var(--ui-surface); }
.field-label { color: var(--ui-ink-faint); font-size: var(--text-xs); letter-spacing: .04em; }
.answer-field { border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 10px 12px; color: var(--ui-ink); background: var(--ui-surface); font-size: var(--text-md); line-height: 1.6; }
.answer-field:focus-within { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
.answer-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 12px; }
.primary-button { flex: 0 0 auto; height: 34px; border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 0 18px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: var(--text-sm); white-space: nowrap; }
.primary-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.primary-button:disabled { opacity: .6; cursor: progress; }

@media (max-width: 760px) {
  .interview-detail { padding: 20px 16px 48px; }
}
@media print {
  .interview-detail { display: none; }
}
</style>
