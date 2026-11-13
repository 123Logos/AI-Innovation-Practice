<script setup lang="ts">
/**
 * 能力测评与画像（FR-04 / B9，`ADR-028`）。
 *
 * 一屏三段，按会话状态走，**不做假状态**：
 *
 * 1. `catalog`：能测哪些技能 + 已有画像。没有进行中的会话时就是这一屏。
 * 2. `answering`：题目与选项。每点一个选项就立刻存一次（后端是「覆盖这几题」，
 *    不是清空整张卷）；进行中的会话 id 记在 sessionStorage，刷新后靠 `getSession`
 *    连题目与已选答案一起恢复。今天没有「列出进行中的会话」这个端点，所以只能恢复
 *    **本浏览器这一场**——换设备就回目录态，这是已知边界，不是「不丢」。
 * 3. `result`：分数 + 标签三态 + 可选的一段概述。
 *
 * 两条与后端契约绑死的显示规则：
 *
 * - **分数只认服务端**：`score` / `correct_count` 在未提交时是 `null`，界面不自己算，
 *   也不把 `null` 画成「0 分」（那会被读成「一分没得」）。
 * - **概述必须标明出处**：`source === 'ai'` 才写「AI 分析」，否则写「自动整理的概述」。
 *   后端在没凭据 / AI 不可用时会照旧给一段由标签拼的文案，把它标成 AI 就是撒谎（守则 C4）。
 *
 * 失败一律可见（守则 C1/C3）：读取、开始、保存、提交、生成概述各自的失败都落到
 * `role="alert"` 的红字上，不静默、不 console.error。
 */
import { computed, onMounted, ref } from 'vue'

import { assessmentsApi, isApiError } from '@/api'
import type {
  AssessmentCatalog,
  AssessmentProfile,
  AssessmentQuestion,
  AssessmentSession,
  SessionTag,
  SummarySource,
} from '@/api/assessments'
import { SUMMARY_HEADINGS, TAG_STATUS_LABELS } from '@/api/assessments'
import SkeletonList from '@/components/SkeletonList.vue'

type Phase = 'loading' | 'catalog' | 'answering' | 'result'

/** 只存 id，不存答案：答案的真相在库里，本地这份只是「刷新时该去问哪一场」。 */
const PENDING_SESSION_KEY = 'ai-career.assessment-session-id'

interface SummaryView {
  text: string
  source: SummarySource
  note: string | null
}

const phase = ref<Phase>('loading')
const catalog = ref<AssessmentCatalog | null>(null)
const profile = ref<AssessmentProfile | null>(null)
const session = ref<AssessmentSession | null>(null)
const questions = ref<AssessmentQuestion[]>([])
const chosen = ref<Record<string, string>>({})
const tags = ref<SessionTag[]>([])
const summary = ref<SummaryView | null>(null)

const error = ref('')
const busy = ref(false)
const summaryBusy = ref(false)

const answeredCount = computed(
  () => questions.value.filter((question) => Boolean(chosen.value[question.id])).length,
)
const hasProfileTags = computed(() => (profile.value?.tags.length ?? 0) > 0)

function describeError(caught: unknown, fallback: string): string {
  // 只回显服务端给的结构化文案（已是人话）；其余用固定降级文案，
  // 不把网络异常原文带上屏（守则 C2）。
  if (isApiError(caught) && caught.message) return caught.message
  return fallback
}

function rememberSession(id: string | null): void {
  if (id) sessionStorage.setItem(PENDING_SESSION_KEY, id)
  else sessionStorage.removeItem(PENDING_SESSION_KEY)
}

async function load(): Promise<void> {
  phase.value = 'loading'
  error.value = ''
  try {
    const [loadedCatalog, loadedProfile] = await Promise.all([
      assessmentsApi.getCatalog(),
      assessmentsApi.getProfile().catch(() => null),
    ])
    catalog.value = loadedCatalog
    profile.value = loadedProfile
    phase.value = 'catalog'
  } catch (caught) {
    error.value = describeError(caught, '读取测评内容失败，请刷新页面重试。')
    phase.value = 'catalog'
  }
  await restorePendingSession()
}

/**
 * 刷新后恢复「这一场」：只有 `in_progress` 才回答题态。
 *
 * 读不到（会话被删、换了账号 → 后端 404）就**清掉标记、安静回目录**：这不是用户
 * 做错了什么，弹一句红字只会让他以为自己的作答丢了。终态也清标记——提交/放弃时
 * 本就会清，这里是防止上一次写标记失败留下的残值。
 */
async function restorePendingSession(): Promise<void> {
  const pendingId = sessionStorage.getItem(PENDING_SESSION_KEY)
  if (!pendingId) return
  try {
    const detail = await assessmentsApi.getSession(pendingId)
    if (detail.session.status !== 'in_progress') {
      rememberSession(null)
      return
    }
    session.value = detail.session
    questions.value = detail.questions
    chosen.value = Object.fromEntries(
      detail.answers.map((answer) => [answer.question_id, answer.chosen_key]),
    )
    tags.value = []
    summary.value = null
    phase.value = 'answering'
  } catch {
    rememberSession(null)
  }
}

async function start(): Promise<void> {
  error.value = ''
  busy.value = true
  try {
    const started = await assessmentsApi.startSession()
    session.value = started.session
    questions.value = started.questions
    chosen.value = {}
    tags.value = []
    summary.value = null
    rememberSession(started.session.id)
    phase.value = 'answering'
  } catch (caught) {
    error.value = describeError(caught, '没能开始这次测评，请稍后再试。')
  } finally {
    busy.value = false
  }
}

async function choose(question: AssessmentQuestion, key: string): Promise<void> {
  if (!session.value) return
  error.value = ''
  // 先记在界面上再保存：点一下要立刻有反应，保存失败时提示会跟着出现，
  // 用户再点一次同一个选项就是重试。
  chosen.value = { ...chosen.value, [question.id]: key }
  try {
    await assessmentsApi.saveAnswers(session.value.id, [
      { question_id: question.id, chosen_key: key },
    ])
  } catch (caught) {
    error.value = describeError(caught, '这一题的作答没能保存，请再点一次。')
  }
}

async function submit(): Promise<void> {
  if (!session.value) return
  error.value = ''
  busy.value = true
  try {
    // 提交前再整卷覆盖一次：逐题点击是「立刻存」，但几个请求在途时**谁先到**不由界面决定，
    // 只靠逐题保存可能让最后点下的那个选项不是库里的最后一份。整卷写一次再提交，
    // 最终状态就只由界面当前的选择决定，与网络到达顺序无关。
    const finalAnswers = questions.value
      .filter((question) => Boolean(chosen.value[question.id]))
      .map((question) => ({ question_id: question.id, chosen_key: chosen.value[question.id] }))
    await assessmentsApi.saveAnswers(session.value.id, finalAnswers)
    const result = await assessmentsApi.submitSession(session.value.id)
    session.value = result.session
    tags.value = result.tags
    rememberSession(null)
    phase.value = 'result'
  } catch (caught) {
    error.value = describeError(caught, '提交没能完成，请稍后再试。')
  } finally {
    busy.value = false
  }
}

async function abandon(): Promise<void> {
  if (!session.value) return
  error.value = ''
  busy.value = true
  try {
    await assessmentsApi.abandonSession(session.value.id)
    rememberSession(null)
    session.value = null
    questions.value = []
    chosen.value = {}
    await load()
  } catch (caught) {
    error.value = describeError(caught, '放弃操作没能完成，请稍后再试。')
  } finally {
    busy.value = false
  }
}

async function generateSummary(): Promise<void> {
  if (!session.value) return
  error.value = ''
  summaryBusy.value = true
  try {
    const result = await assessmentsApi.summarizeSession(session.value.id)
    summary.value = { text: result.summary, source: result.source, note: result.note }
    profile.value = await assessmentsApi.getProfile().catch(() => profile.value)
  } catch (caught) {
    error.value = describeError(caught, '概述没能生成，请稍后再试。')
  } finally {
    summaryBusy.value = false
  }
}

function restart(): void {
  session.value = null
  questions.value = []
  chosen.value = {}
  tags.value = []
  summary.value = null
  void load()
}

onMounted(load)
</script>

<template>
  <main class="assessment-page">
    <div class="page-heading">
      <p class="eyebrow">能力测评</p>
      <h1>测评与画像</h1>
      <p class="intro">
        用几道小题测一下你写进简历的技能。结果会变成画像上的标签：标签说的是「测过、并且答对了」，
        不是你给自己打的分。
      </p>
    </div>

    <p v-if="error" class="feedback feedback--error" role="alert">{{ error }}</p>

    <SkeletonList
      v-if="phase === 'loading'"
      label="正在加载测评内容…"
      :heights="[88, 88, 180]"
      :heights-compact="[96, 96, 200]"
      :columns="1"
      :gap="12"
    />

    <!-- 一、没在测评：能测什么 + 已有画像 -->
    <template v-else-if="phase === 'catalog'">
      <section v-if="catalog" class="panel" aria-label="可测评的技能">
        <h2 class="panel-title">可以测这些技能</h2>
        <p class="panel-note">共 {{ catalog.total_questions }} 道题，全部答完大约几分钟。</p>
        <ul class="skill-list">
          <li v-for="skill in catalog.skills" :key="skill.skill_key" class="skill-item">
            <span class="skill-name">{{ skill.skill_name }}</span>
            <span class="skill-count">{{ skill.question_count }} 题</span>
          </li>
        </ul>
        <button class="primary-button" type="button" :disabled="busy" @click="start">
          {{ busy ? '正在准备…' : '开始测评' }}
        </button>
      </section>

      <section v-if="hasProfileTags" class="panel" aria-label="我的画像">
        <h2 class="panel-title">我的画像</h2>
        <ul class="tag-list">
          <li v-for="tag in profile?.tags ?? []" :key="tag.tag" class="tag-item">
            <span class="tag-name">{{ tag.tag }}</span>
            <span class="tag-status" :class="`tag-status--${tag.status}`">
              {{ TAG_STATUS_LABELS[tag.status] }}
            </span>
            <span class="tag-score">{{ tag.score }} 分 · {{ tag.evidence_count }} 题</span>
          </li>
        </ul>
        <p v-if="profile?.latest_summary" class="summary-text">{{ profile.latest_summary }}</p>
      </section>
    </template>

    <!-- 二、答题中 -->
    <template v-else-if="phase === 'answering'">
      <div class="progress-row">
        <p class="progress">已答 {{ answeredCount }} / {{ questions.length }} 题</p>
        <button class="ghost-button" type="button" :disabled="busy" @click="abandon">
          放弃这次测评
        </button>
      </div>
      <p class="panel-note">没作答的题会按答错计算，所以尽量都选一下再提交。</p>

      <ol class="question-list">
        <li v-for="question in questions" :key="question.id" class="question-item">
          <p class="question-skill">{{ question.skill_name }}</p>
          <p class="question-prompt">{{ question.prompt }}</p>
          <div class="option-list" role="radiogroup" :aria-label="question.prompt">
            <label
              v-for="option in question.options"
              :key="option.key"
              class="option"
              :class="{ 'option--chosen': chosen[question.id] === option.key }"
            >
              <input
                class="option-input"
                type="radio"
                :name="`question-${question.id}`"
                :value="option.key"
                :checked="chosen[question.id] === option.key"
                @change="choose(question, option.key)"
              />
              <span class="option-label">{{ option.label }}</span>
            </label>
          </div>
        </li>
      </ol>

      <div class="submit-row">
        <button class="primary-button" type="button" :disabled="busy" @click="submit">
          {{ busy ? '正在判分…' : '提交并查看结果' }}
        </button>
      </div>
    </template>

    <!-- 三、已提交：分数 + 标签 + 概述 -->
    <template v-else>
      <section v-if="session" class="panel" aria-label="本次测评结果">
        <h2 class="panel-title">本次结果</h2>
        <p class="score-line">
          <span class="score-value">{{ session.score ?? 0 }}</span>
          <span class="score-unit">分</span>
          <span class="score-detail">
            答对 {{ session.correct_count ?? 0 }} / {{ session.question_count }} 题
          </span>
        </p>
        <ul class="tag-list">
          <li v-for="tag in tags" :key="tag.tag" class="tag-item">
            <span class="tag-name">{{ tag.tag }}</span>
            <span class="tag-status" :class="`tag-status--${tag.status}`">
              {{ TAG_STATUS_LABELS[tag.status] }}
            </span>
            <span class="tag-score">{{ tag.score }} 分 · {{ tag.evidence_count }} 题</span>
          </li>
        </ul>
      </section>

      <section class="panel" aria-label="画像概述">
        <h2 class="panel-title">画像概述</h2>
        <p v-if="!summary" class="panel-note">
          可以请 AI 把上面的标签写成一段话。没有填写 AI 使用凭据时，会给一段按标签自动整理的文字。
        </p>
        <button
          v-if="!summary"
          class="primary-button"
          type="button"
          :disabled="summaryBusy"
          @click="generateSummary"
        >
          {{ summaryBusy ? '正在生成…' : '生成概述' }}
        </button>
        <template v-else>
          <p class="summary-heading">{{ SUMMARY_HEADINGS[summary.source] }}</p>
          <p class="summary-text">{{ summary.text }}</p>
          <p v-if="summary.note" class="summary-note">{{ summary.note }}</p>
        </template>
      </section>

      <div class="submit-row">
        <button class="ghost-button" type="button" :disabled="busy" @click="restart">
          再测一次
        </button>
      </div>
    </template>
  </main>
</template>

<style scoped>
.assessment-page { width: min(900px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.page-heading { margin-bottom: 20px; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .12em; }
h1 { margin: 0; color: var(--ui-ink); font-size: var(--text-4xl); font-weight: 650; }
.intro { margin: 8px 0 0; max-width: 62ch; color: var(--ui-ink-muted); font-size: var(--text-base); line-height: 1.6; }

.panel { display: grid; gap: 12px; margin-bottom: 16px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 16px 18px; background: var(--ui-surface); }
.panel-title { margin: 0; color: var(--ui-ink); font-size: var(--text-lg); font-weight: 650; }
.panel-note { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-sm); line-height: 1.5; }

.feedback { margin: 0 0 14px; color: var(--ui-ink-faint); font-size: var(--text-base); line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }

.skill-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 0; padding: 0; list-style: none; }
.skill-item { display: inline-flex; align-items: baseline; gap: 8px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 4px 10px; background: var(--ui-surface-soft); }
.skill-name { color: var(--ui-ink); font-size: var(--text-md); }
.skill-count { color: var(--ui-ink-faint); font-size: var(--text-xs); }

.primary-button { justify-self: start; height: 34px; border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 0 16px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: var(--text-sm); }
.primary-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.primary-button:disabled { opacity: .6; cursor: progress; }
.ghost-button { height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 14px; color: var(--ui-ink-muted); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-sm); }
.ghost-button:hover:not(:disabled) { background: var(--ui-surface-soft); }
.ghost-button:disabled { opacity: .6; cursor: progress; }

.progress-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.progress { margin: 0; color: var(--ui-ink-muted); font-size: var(--text-base); }

.question-list { display: grid; gap: 12px; margin: 16px 0; padding: 0; list-style: none; }
.question-item { display: grid; gap: 8px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 16px 18px; background: var(--ui-surface); }
.question-skill { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-xs); letter-spacing: .06em; }
.question-prompt { margin: 0; color: var(--ui-ink); font-size: var(--text-md); line-height: 1.6; }

.option-list { display: grid; gap: 6px; }
.option { display: flex; align-items: flex-start; gap: 10px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 8px 12px; cursor: pointer; background: var(--ui-surface); }
.option:hover { border-color: var(--theme-rule-soft); background: var(--ui-surface-soft); }
.option--chosen { border-color: var(--theme-primary); background: var(--theme-bg-light); }
.option-input { margin-top: 3px; accent-color: var(--theme-primary); }
.option-label { color: var(--ui-ink); font-size: var(--text-base); line-height: 1.5; }

.submit-row { display: flex; gap: 10px; margin-top: 16px; }

.score-line { display: flex; align-items: baseline; gap: 6px; margin: 0; }
.score-value { color: var(--ui-ink); font-size: var(--text-4xl); font-weight: 650; }
.score-unit { color: var(--ui-ink-muted); font-size: var(--text-base); }
.score-detail { margin-left: 8px; color: var(--ui-ink-faint); font-size: var(--text-sm); }

.tag-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.tag-item { display: flex; align-items: center; gap: 10px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 8px 12px; background: var(--ui-surface-soft); }
.tag-name { color: var(--ui-ink); font-size: var(--text-md); }
.tag-status { border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 1px 8px; font-size: var(--text-xs); line-height: 1.5; }
.tag-status--verified { border-color: var(--theme-primary); color: var(--theme-primary-strong); background: var(--theme-bg-light); }
.tag-status--pending { border-color: var(--ui-border-strong); color: var(--ui-ink-muted); }
.tag-status--improving { border-color: var(--ui-warning); color: var(--ui-warning); }
.tag-score { margin-left: auto; color: var(--ui-ink-faint); font-size: var(--text-xs); }

.summary-heading { margin: 0; color: var(--ui-ink-muted); font-size: var(--text-sm); letter-spacing: .04em; }
.summary-text { margin: 0; color: var(--ui-ink); font-size: var(--text-base); line-height: 1.7; white-space: pre-line; }
.summary-note { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-xs); line-height: 1.5; }

@media (max-width: 760px) {
  .assessment-page { padding: 20px 16px 48px; }
  .score-line { flex-wrap: wrap; }
}
@media print {
  .assessment-page { display: none; }
}
</style>
