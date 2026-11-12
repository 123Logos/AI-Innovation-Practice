<script setup lang="ts">
/**
 * 模拟面试列表（FR-07 / B11a，ADR-022）。
 *
 * 一张卡片 = 一场面试（方向 / 状态 / 开始时间），点卡片进独立详情页。
 * 顶部一段「开始模拟面试」：可填目标岗位（选填），点开始直接进详情——
 * 开场问题由后端在创建时一并生成，详情页首屏就是面试官的第一句话。
 *
 * 数据走 `api/interviews.ts`，不引 store（这一屏没有跨页要保留的筛选/页码状态）。
 * 失败一律可见（守则 C1/C3）：列表读取失败 → 红字 `role="alert"`；创建失败 → 同样红字提示，
 * 不静默、不 console.error。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { isApiError, interviewsApi } from '@/api'
import type { InterviewSessionRead, InterviewSessionStatus } from '@/api/interviews'
import { TARGET_ROLE_MAX_LEN } from '@/api/interviews'
import { formatDate } from '@/utils/datetime'
import SkeletonList from '@/components/SkeletonList.vue'

const router = useRouter()

const sessions = ref<InterviewSessionRead[]>([])
const total = ref(0)
const loading = ref(false)
const loadError = ref('')

const targetRoleDraft = ref('')
const creating = ref(false)
const createError = ref('')

const STATUS_LABELS: Record<InterviewSessionStatus, string> = {
  active: '进行中',
  ended: '已结束',
}

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
    const result = await interviewsApi.listInterviews(1, 20)
    sessions.value = result.sessions
    total.value = result.total
  } catch (error) {
    loadError.value = describeError(error, '读取面试列表失败，请刷新页面重试。')
  } finally {
    loading.value = false
  }
}

async function startInterview(): Promise<void> {
  createError.value = ''
  creating.value = true
  try {
    const result = await interviewsApi.createInterview({
      target_role: targetRoleDraft.value.trim() || null,
    })
    await router.push({ name: 'interview-detail', params: { id: result.session.id } })
  } catch (error) {
    createError.value = describeError(error, '没能开始这场面试，请稍后重试。')
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<template>
  <main class="interview-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">模拟面试</p>
        <h1>面试练习</h1>
        <p class="intro">
          和面试官一对一地练。先做个自我介绍，再根据你的回答继续追问，结束后可以回看整段对话。
        </p>
      </div>
    </div>

    <section class="start-panel" aria-label="开始一场面试">
      <form class="start-form" @submit.prevent="startInterview">
        <label class="field-label" for="target-role">目标岗位（选填）</label>
        <div class="start-row">
          <input
            id="target-role"
            v-model="targetRoleDraft"
            class="role-input"
            type="text"
            :maxlength="TARGET_ROLE_MAX_LEN"
            placeholder="如：前端开发工程师"
          />
          <button class="primary-button" type="submit" :disabled="creating">
            {{ creating ? '正在开始…' : '开始模拟面试' }}
          </button>
        </div>
      </form>
      <p v-if="createError" class="feedback feedback--error" role="alert">{{ createError }}</p>
    </section>

    <p v-if="loadError" class="feedback feedback--error" role="alert">{{ loadError }}</p>
    <SkeletonList
      v-else-if="loading"
      label="正在加载面试记录…"
      :heights="[72, 72, 72, 72]"
      :heights-compact="[88, 88, 88, 88]"
      :columns="1"
      :gap="12"
    />
    <p v-else-if="!sessions.length" class="feedback">
      这里还没有面试记录，开始一场试试。
    </p>

    <ul v-else class="session-list">
      <li v-for="session in sessions" :key="session.id">
        <RouterLink class="session-card" :to="{ name: 'interview-detail', params: { id: session.id } }">
          <div class="card-head">
            <p class="card-role">{{ session.target_role || '通用模拟面试' }}</p>
            <span
              class="tag"
              :class="session.status === 'ended' ? 'tag--ended' : 'tag--active'"
            >
              {{ STATUS_LABELS[session.status] }}
            </span>
          </div>
          <p class="card-time">开始时间：{{ formatDate(session.created_at) || session.created_at }}</p>
        </RouterLink>
      </li>
    </ul>

    <p v-if="total > sessions.length && !loading" class="feedback feedback--more">
      还有 {{ total - sessions.length }} 场更早的记录。
    </p>
  </main>
</template>

<style scoped>
.interview-page { width: min(900px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.page-heading { margin-bottom: 20px; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .12em; }
h1 { margin: 0; color: var(--ui-ink); font-size: var(--text-4xl); font-weight: 650; }
.intro { margin: 8px 0 0; max-width: 60ch; color: var(--ui-ink-muted); font-size: var(--text-base); line-height: 1.6; }

.start-panel { display: grid; gap: 12px; margin-bottom: 20px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 16px 18px; background: var(--ui-surface); }
.field-label { color: var(--ui-ink-faint); font-size: var(--text-xs); letter-spacing: .04em; }
.start-row { display: flex; flex-wrap: wrap; gap: 10px; }
.role-input { flex: 1 1 220px; min-width: 0; height: 34px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 12px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: var(--text-md); outline: 0; }
.role-input:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
.primary-button { flex: 0 0 auto; height: 34px; border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 0 16px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: var(--text-sm); white-space: nowrap; }
.primary-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.primary-button:disabled { opacity: .6; cursor: progress; }

.feedback { margin: 0 0 14px; color: var(--ui-ink-faint); font-size: var(--text-base); line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }
.feedback--more { color: var(--ui-ink-faint); font-size: var(--text-sm); }

.session-list { display: grid; gap: 12px; margin: 0; padding: 0; list-style: none; }
.session-card { display: grid; gap: 8px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 16px 18px; color: inherit; background: var(--ui-surface); text-decoration: none; }
.session-card:hover { border-color: var(--theme-rule-soft); background: var(--ui-surface-soft); }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.card-role { margin: 0; color: var(--ui-ink); font-size: var(--text-lg); font-weight: 650; }
.card-time { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-sm); }
.tag { border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 1px 8px; font-size: var(--text-xs); line-height: 1.5; white-space: nowrap; }
.tag--active { border-color: var(--theme-primary); color: var(--theme-primary-strong); background: var(--theme-bg-light); }
.tag--ended { border-color: var(--ui-border-strong); color: var(--ui-ink-muted); background: var(--ui-surface-soft); }

@media (max-width: 760px) {
  .interview-page { padding: 20px 16px 48px; }
}
@media print {
  .interview-page { display: none; }
}
</style>
