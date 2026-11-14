<script setup lang="ts">
/**
 * 模拟面试详情（FR-07 / B11a，ADR-022；B11b 起支持回合制语音作答，ADR-030）。
 *
 * 一屏三块：顶部会话信息 + 中部问答回放（面试官在左、候选人在右）+ 底部作答框。
 * 多行作答框用共享的 `AutoTextarea`（守则 F1：全仓唯一的多行输入实现）。
 *
 * **可见降级（守则 C4）**：后端的 `ai_degrade_reason` 非空代表这一轮是本地题库而非真 AI，
 * 必须在这一轮气泡里原样展示给用户（见 `interviewDegradeHint`），绝不能让用户误以为
 * 「AI 说了这句」。失败一律可见：`role="alert"` 的红字，不静默、不 console.error。
 *
 * **语音那条路（B11b）**：录音 → 直连转写 → **文本**进 draft → 用户确认后按
 * `input_mode="voice"` 发出 → 面试官那段合成朗读。三处故意的设计：
 *
 * 1. **转写结果先进输入框，不直接发送**：识别错一个词就自动发出去，用户连纠正的机会
 *    都没有，而这一轮会永久留在复盘记录里。
 * 2. **音频对象出了 `stopRecordingAndTranscribe()` 就没人引用**：blob 是局部变量，
 *    没有缓存、没有队列、没有 `URL.createObjectURL` 留给后续播放——「转写完毕即删」
 *    在这一层就是作用域事实（`ADR-030` §2.1 不变量 A）。
 * 3. **朗读失败不打扰**：`speak()` 里吞掉异常，只少了声音，文字照常可读
 *    （`ADR-030` §2.1 不变量 D）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { isApiError, interviewsApi } from '@/api'
import type {
  InterviewSessionDetail,
  InterviewTurnRead,
  TurnInputMode,
} from '@/api/interviews'
import { ANSWER_MAX_LEN, SPEAKER_LABELS, interviewDegradeHint } from '@/api/interviews'
import { ASR_MAX_SECONDS, synthesizeSpeech, transcribeAudio } from '@/api/voice'
import AutoTextarea from '@/components/AutoTextarea.vue'
import { useVoiceRecorder } from '@/composables/useVoiceRecorder'
import { playAudioBlob } from '@/utils/audioPlayback'
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

/**
 * 这一次作答是说的还是打的。转写成功才置 `voice`，任何失败路径都退回 `text`——
 * 带着 `voice` 标记却发出去一段用户手打的字，等于在复盘记录里写假话。
 */
const pendingMode = ref<TurnInputMode>('text')
const transcribing = ref(false)
const voiceNotice = ref('')

const {
  supported: voiceSupported,
  recording,
  elapsed,
  message: recorderMessage,
  start: startRecording,
  stop: stopRecording,
  cancel: cancelRecording,
} = useVoiceRecorder()

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

/** 朗读面试官那一段；失败静默（`ADR-030` §2.1 不变量 D）。 */
async function speak(text: string): Promise<void> {
  if (!text.trim()) return
  try {
    await playAudioBlob(await synthesizeSpeech(text))
  } catch {
    // 只少了声音，内容照常可读——不弹红字，也不改这一轮的降级标记。
  }
}

async function sendMessage(): Promise<void> {
  const content = draft.value.trim()
  if (!content || sending.value || isEnded.value) return
  sending.value = true
  sendError.value = ''
  const mode = pendingMode.value
  try {
    const result = await interviewsApi.sendInterviewMessage(sessionId.value, content, mode)
    draft.value = ''
    pendingMode.value = 'text'
    voiceNotice.value = ''
    detail.value?.turns.push(result.candidate_turn, result.interviewer_turn)
    void speak(result.interviewer_turn.content)
  } catch (error) {
    sendError.value = describeError(error, '没能把回答发出去，请稍后重试。')
  } finally {
    sending.value = false
  }
}

/**
 * 停止录音并转写。
 *
 * `blob` 只在这个函数里活着：转写成功后它就不再被引用，等着被回收。
 * 这是「音频不入库、不存盘」在前端的落地形态——不是「删掉」，而是**从来没有第二个持有者**。
 */
async function stopRecordingAndTranscribe(): Promise<void> {
  const blob = await stopRecording()
  if (!blob) {
    pendingMode.value = 'text'
    if (recorderMessage.value) voiceNotice.value = recorderMessage.value
    return
  }
  transcribing.value = true
  voiceNotice.value = ''
  try {
    const result = await transcribeAudio(blob)
    if (!result.text) {
      pendingMode.value = 'text'
      voiceNotice.value = '这一段没有听清，请重说或直接打字。'
      return
    }
    draft.value = result.text
    pendingMode.value = 'voice'
    voiceNotice.value = '已转成文字，确认无误后点「发送回答」。'
  } catch {
    // 识别失败 → 降级为手动输入，并明说（绝不假装识别成功）。
    pendingMode.value = 'text'
    voiceNotice.value = '语音识别失败，请手动输入。'
  } finally {
    transcribing.value = false
  }
}

function discardRecording(): void {
  cancelRecording()
  pendingMode.value = 'text'
  voiceNotice.value = ''
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
            <p class="bubble-speaker">
              {{ SPEAKER_LABELS[turn.speaker] }}
              <span v-if="turn.input_mode === 'voice'" class="bubble-tag">语音作答</span>
            </p>
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

        <!-- 语音作答（B11b / ADR-030 §2.2）：到 120 秒自动停，超体积当场丢弃。 -->
        <div class="voice-bar">
          <template v-if="voiceSupported">
            <template v-if="recording">
              <span class="voice-timer" role="status">
                正在录音 {{ elapsed }} 秒 / 最长 {{ ASR_MAX_SECONDS }} 秒
              </span>
              <button class="primary-button" type="button" @click="stopRecordingAndTranscribe">
                说完了，转成文字
              </button>
              <button class="text-button" type="button" @click="discardRecording">取消</button>
            </template>
            <button
              v-else
              class="ghost-button"
              type="button"
              :disabled="sending || transcribing"
              @click="startRecording"
            >
              {{ transcribing ? '正在转成文字…' : '用语音说' }}
            </button>
          </template>
          <p v-else class="voice-unsupported">这个浏览器不支持录音，请用文字作答。</p>
        </div>
        <p v-if="voiceNotice || recorderMessage" class="voice-notice" role="status">
          {{ voiceNotice || recorderMessage }}
        </p>

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

/* ---- 语音作答（B11b） ---- */
.voice-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.ghost-button { flex: 0 0 auto; height: 30px; border: 1px dashed var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 14px; color: var(--ui-ink-muted); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-sm); }
.ghost-button:hover:not(:disabled) { border-color: var(--theme-rule); color: var(--theme-primary-deep); }
.ghost-button:disabled { opacity: .6; cursor: progress; }
.voice-timer { color: var(--theme-primary-deep); font-size: var(--text-sm); }
.voice-unsupported { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-sm); }
.voice-notice { margin: 0; color: var(--ui-ink-muted); font-size: var(--text-sm); line-height: 1.5; }
.bubble-tag { margin-left: 6px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 6px; color: var(--ui-ink-faint); font-size: var(--text-2xs); }

@media (max-width: 760px) {
  .interview-detail { padding: 20px 16px 48px; }
}
@media print {
  .interview-detail { display: none; }
}
</style>
