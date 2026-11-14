<script setup lang="ts">
/**
 * 工作台首屏 · 双态（批次 F7）。
 *
 * 老板 2026-11-09 两轮原话定了这一页的两个硬要求：**「纳入路由，不要删」** +
 * **「dashboard 也需要有已登录的状态」**。改造前它是一张没人路由到的孤岛：
 * 一份硬编码的模块数组（守则 L4 违例）、5 个只会弹「模块正在搭建中」的按钮（守则 L5 违例）、
 * 以及三张写死 `0%` / `0` / `未设置` 的统计卡——数字没接任何接口，却长得像真的。
 *
 * 这一版按三条口径重写：
 *
 * 1. **两个状态由路由决定，不由组件自己猜**。同一份组件挂两条路由：
 *    `/dashboard`（登录态，挂在 `AppLayout` 下）与 `/welcome`（访客态，独立一页）。
 *    组件只读 `mode` 这个 prop，不读 `auth.isAuthenticated`——「我是不是登录了」在
 *    路由层已经有唯一答案（`meta.requiresAuth` + 守卫），在这里再判一次就是第二套真相，
 *    而两套真相打架的那天（令牌过期但页面还挂着）会出现「登录态页面渲染访客内容」。
 *
 * 2. **模块入口只从 `APP_MODULES` 读**（守则 L4）。这里不写任何一个模块名：
 *    新增/下线一个模块只改那张表，本文件一个字都不用动。未实现的模块本来就不在表里
 *    （守则 L5）——所以「连禁用态都不放」这条自动成立，不需要在这里再加一层过滤。
 *
 * 3. **登录态的每个数字都来自一个真端点，读不到就说读不到**。四张卡分别对应
 *    `GET /profiles/me`、`GET /assessments/profile`、`GET /jobs`、`GET /interviews`；
 *    每张卡有独立的 `loading / ready / failed`，失败时显示「—」+「暂时读不到」而不是 `0`。
 *    这不是啰嗦：把失败画成 `0` 与把「没填」画成 `0%` 是同一类谎，用户会据此
 *    以为「我已经有 0 个技能」而不是「这次没读上」。
 *
 * 刻意**不**做的事：
 *
 * - 不放未实现模块的禁用副本——未实现即不在表里，见上面第 2 条；
 * - 不显示任何未接入的数字，也不用占位符假装有数据；
 * - 不在访客态渲染登录态那四张统计卡：那些数字要登录才读得到，画一批空壳
 *   只能靠假数据填充。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { assessmentsApi, interviewsApi, isApiError, jobsApi, profilesApi } from '@/api'
import type { ProfileRead } from '@/api/types'
import { APP_MODULES } from '@/config/modules'
import { useAuthStore } from '@/stores/auth'

const props = withDefaults(defineProps<{ mode?: 'guest' | 'member' }>(), { mode: 'member' })

const auth = useAuthStore()

/**
 * 教师端入口的可见条件。
 *
 * 与 `app/core/permissions.py::DASHBOARD_READ` 同一个字符串：`/auth/me` 把权限表原样
 * 发给前端，两边的判据因此同源。这里只决定「要不要显示这张卡」，真正的边界在后端
 * （无权限 403、无组织 400），前端少画一张卡不构成任何保护。
 */
const TEACHER_DASHBOARD_PERMISSION = 'dashboard:read'

/** 除工作台自己以外的模块：本页就是工作台，再摆一张「回工作台」的卡片没有意义。 */
const entryModules = computed(() => APP_MODULES.filter((module) => module.key !== 'dashboard'))

const showTeacherEntry = computed(
  () => props.mode === 'member' && auth.hasPermission(TEACHER_DASHBOARD_PERMISSION),
)

type LoadState = 'loading' | 'ready' | 'failed'

const profile = ref<ProfileRead | null>(null)
const profileState = ref<LoadState>('loading')

const verifiedTags = ref(0)
const pendingTags = ref(0)
const tagState = ref<LoadState>('loading')

const publishedJobs = ref(0)
const jobState = ref<LoadState>('loading')

const interviewTotal = ref(0)
const interviewState = ref<LoadState>('loading')

/**
 * 档案完成度 = 5 个「有没有填」的检查点，各占 20%。
 *
 * 为什么是这 5 个而不是按字段数量加权：这一格要回答的是「还差哪一块」，
 * 用户看完能立刻去补；按字段数算出来的 63% 说不出该补什么。
 * 判据全部来自 `GET /profiles/me` 的真实字段，没有任何估计值。
 */
const PROFILE_CHECKS = [
  { label: '填好姓名与联系方式', test: (p: ProfileRead) => Boolean(p.full_name && p.contact) },
  { label: '设一个目标岗位', test: (p: ProfileRead) => Boolean(p.target_role) },
  { label: '至少一段技能', test: (p: ProfileRead) => p.skills.length > 0 },
  { label: '至少一段经历', test: (p: ProfileRead) => p.experiences.length > 0 },
  { label: '至少一条教育经历', test: (p: ProfileRead) => p.educations.length > 0 },
] as const

const missingChecks = computed(() => {
  const current = profile.value
  if (!current) return PROFILE_CHECKS.map((check) => check.label)
  return PROFILE_CHECKS.filter((check) => !check.test(current)).map((check) => check.label)
})

const profileCompletion = computed(() => {
  const done = PROFILE_CHECKS.length - missingChecks.value.length
  return Math.round((done / PROFILE_CHECKS.length) * 100)
})

/** 失败一律可见（守则 C1/C3）：只回显服务端已给的句子，其余用固定降级文案。 */
function describeError(error: unknown, fallback: string): string {
  if (isApiError(error) && error.message) return error.message
  return fallback
}

const loadError = ref('')

async function loadProfile(): Promise<void> {
  profileState.value = 'loading'
  try {
    profile.value = await profilesApi.getMyProfile()
    profileState.value = 'ready'
  } catch (error) {
    profileState.value = 'failed'
    loadError.value = describeError(error, '档案读不到，请刷新页面重试。')
  }
}

async function loadTags(): Promise<void> {
  tagState.value = 'loading'
  try {
    const result = await assessmentsApi.getProfile()
    verifiedTags.value = result.tags.filter((tag) => tag.status === 'verified').length
    pendingTags.value = result.tags.filter((tag) => tag.status !== 'verified').length
    tagState.value = 'ready'
  } catch (error) {
    tagState.value = 'failed'
    loadError.value = describeError(error, '能力标签读不到，请刷新页面重试。')
  }
}

async function loadJobs(): Promise<void> {
  jobState.value = 'loading'
  try {
    // 只要总数，所以取一页最小粒度：广场默认 `status=published`，与岗位库入口看到的一致。
    const result = await jobsApi.listJobs({ page: 1, pageSize: 1 })
    publishedJobs.value = result.total
    jobState.value = 'ready'
  } catch (error) {
    jobState.value = 'failed'
    loadError.value = describeError(error, '岗位数据读不到，请刷新页面重试。')
  }
}

async function loadInterviews(): Promise<void> {
  interviewState.value = 'loading'
  try {
    const result = await interviewsApi.listInterviews(1, 1)
    interviewTotal.value = result.total
    interviewState.value = 'ready'
  } catch (error) {
    interviewState.value = 'failed'
    loadError.value = describeError(error, '面试记录读不到，请刷新页面重试。')
  }
}

/**
 * 四张卡并发读、各自失败各自标——`Promise.all` 会让一张卡失败拖掉其余三张，
 * 而它们本来就是互不依赖的四个读。
 */
async function loadMemberData(): Promise<void> {
  loadError.value = ''
  await Promise.all([loadProfile(), loadTags(), loadJobs(), loadInterviews()])
}

onMounted(() => {
  if (props.mode === 'member') void loadMemberData()
})

/** `—` 表示「这次没读到」，与「读到确实是 0」必须长得不一样。 */
function statText(state: LoadState, value: string): string {
  if (state === 'loading') return '…'
  if (state === 'failed') return '—'
  return value
}
</script>

<template>
  <main class="dashboard-page">
    <!-- ------------------------------------------------------------------ 访客态 -->
    <template v-if="mode === 'guest'">
      <section class="hero">
        <p class="eyebrow">AI 职业发展助手</p>
        <h1>先把做过的事写清楚，再谈去哪</h1>
        <p class="intro">
          把课程项目、实习、比赛整理成一份能被阅读的档案，平台据此帮你核对岗位要求、找出差距，
          并在模拟面试里练一遍怎么把它讲出来。
        </p>
        <div class="hero-actions">
          <RouterLink class="primary-button" :to="{ name: 'register' }">创建账号</RouterLink>
          <RouterLink class="ghost-button" :to="{ name: 'login' }">我已有账号，去登录</RouterLink>
        </div>
      </section>

      <section class="steps" aria-label="使用流程">
        <article class="step-card">
          <span class="step-index">01</span>
          <h2>整理档案</h2>
          <p>技能、经历、教育，一格一格填。填得越具体，后面能对上的岗位越多。</p>
        </article>
        <article class="step-card">
          <span class="step-index">02</span>
          <h2>对照岗位</h2>
          <p>打开岗位详情，逐条核对「要求写了什么、我这边有什么」，差距会直接列出来。</p>
        </article>
        <article class="step-card">
          <span class="step-index">03</span>
          <h2>练一遍再投</h2>
          <p>模拟面试一次一回合地追问，结束能回看整段对话，知道哪句没答到点上。</p>
        </article>
      </section>
    </template>

    <!-- ------------------------------------------------------------------ 登录态 -->
    <template v-else>
      <section class="hero hero--member">
        <p class="eyebrow">工作台</p>
        <h1>{{ auth.displayName ? `${auth.displayName}，今天从哪一步接着走？` : '今天从哪一步接着走？' }}</h1>
      </section>

      <p v-if="loadError" class="page-error" role="alert">{{ loadError }}</p>

      <section class="stat-grid" aria-label="我的进度">
        <article class="stat-card">
          <span class="stat-label">档案完成度</span>
          <strong class="stat-value">{{
            profileState === 'ready' ? `${profileCompletion}%` : statText(profileState, '')
          }}</strong>
          <p v-if="profileState === 'ready'" class="stat-hint">
            {{ missingChecks.length ? `还差：${missingChecks[0]}` : '五项都已填好' }}
          </p>
          <p v-else-if="profileState === 'failed'" class="stat-hint">暂时读不到</p>
          <RouterLink class="card-link" :to="{ name: 'resume-editor' }">去完善档案</RouterLink>
        </article>

        <article class="stat-card">
          <span class="stat-label">能力标签</span>
          <strong class="stat-value">{{ statText(tagState, `${verifiedTags}`) }}</strong>
          <p v-if="tagState === 'ready' && pendingTags" class="stat-hint">另有 {{ pendingTags }} 条待提升</p>
          <p v-else-if="tagState === 'failed'" class="stat-hint">暂时读不到</p>
          <RouterLink class="card-link" :to="{ name: 'assessment' }">去做能力测评</RouterLink>
        </article>

        <article class="stat-card">
          <span class="stat-label">在招岗位</span>
          <strong class="stat-value">{{ statText(jobState, `${publishedJobs}`) }}</strong>
          <p v-if="jobState === 'failed'" class="stat-hint">暂时读不到</p>
          <RouterLink class="card-link" :to="{ name: 'job-board' }">去看岗位</RouterLink>
        </article>

        <article class="stat-card">
          <span class="stat-label">模拟面试</span>
          <strong class="stat-value">{{ statText(interviewState, `${interviewTotal}`) }}</strong>
          <p v-if="interviewState === 'failed'" class="stat-hint">暂时读不到</p>
          <RouterLink class="card-link" :to="{ name: 'interview-list' }">去练一场</RouterLink>
        </article>
      </section>

      <RouterLink v-if="showTeacherEntry" class="teacher-entry" :to="{ name: 'teacher-dashboard' }">
        <span class="teacher-entry__label">教师工具</span>
        <h2>学情看板</h2>
      </RouterLink>
    </template>

    <!-- ------------------------------------------------------- 模块入口（两态共用） -->
    <section class="module-section" aria-label="全部模块">
      <div class="section-heading">
        <h2>{{ mode === 'guest' ? '登录后可以用这些' : '全部模块' }}</h2>
      </div>
      <ul class="module-grid">
        <li v-for="module in entryModules" :key="module.key">
          <RouterLink class="module-card" :to="{ name: module.routeName }">
            <svg
              class="module-icon"
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.7"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path v-for="(path, index) in module.iconPaths" :key="index" :d="path" />
            </svg>
            <span>{{ module.name }}</span>
          </RouterLink>
        </li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.dashboard-page { padding: 28px 24px 48px; }
.hero { max-width: 760px; }
.hero--member { margin-bottom: 20px; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .12em; }
h1 { margin: 0 0 12px; font-size: var(--text-6xl); line-height: 1.25; }
h2 { margin: 0 0 8px; font-size: var(--text-xl); }
.intro { margin: 0; color: var(--ui-ink-muted); font-size: var(--text-md); line-height: 1.75; }
.hero-actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 24px; }
.primary-button, .ghost-button { display: inline-flex; align-items: center; border-radius: var(--ui-radius); padding: 10px 18px; font-size: var(--text-md); text-decoration: none; }
.primary-button { color: var(--ui-surface); background: var(--theme-rule); }
.ghost-button { border: 1px solid var(--ui-border-strong); color: var(--ui-ink); }

.steps { display: grid; gap: 14px; margin-top: 36px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
.step-card { border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 20px; background: var(--ui-surface); }
.step-index { color: var(--ui-ink-faint); font-size: var(--text-sm); letter-spacing: .08em; }
.step-card h2 { margin-top: 8px; font-size: var(--text-lg-plus); }
.step-card p { margin: 0; color: var(--ui-ink-muted); font-size: var(--text-base); line-height: 1.7; }

.page-error { margin: 0 0 16px; border: 1px solid var(--ui-danger-border); border-radius: var(--ui-radius); padding: 10px 14px; color: var(--ui-danger); background: var(--ui-danger-soft); font-size: var(--text-base); }

.stat-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
.stat-card { display: flex; flex-direction: column; gap: 6px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 18px; background: var(--ui-surface); }
.stat-label { color: var(--ui-ink-muted); font-size: var(--text-sm); }
.stat-value { font-size: var(--text-6xl); font-weight: 650; }
.stat-hint { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-sm); line-height: 1.6; }
.card-link { margin-top: 8px; color: var(--theme-rule); font-size: var(--text-base); text-decoration: none; }
.card-link:hover { text-decoration: underline; }

.teacher-entry { display: flex; flex-direction: column; gap: 4px; margin-top: 16px; border: 1px solid var(--ui-border); border-left: 3px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 18px 20px; background: var(--ui-surface-soft); color: inherit; text-decoration: none; }
.teacher-entry__label { color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .1em; }
.teacher-entry h2 { margin: 0; font-size: var(--text-lg-plus); }

.module-section { margin-top: 36px; }
.section-heading { display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.section-heading h2 { margin: 0; font-size: var(--text-xl); }
.module-grid { display: grid; gap: 12px; margin: 0; padding: 0; list-style: none; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
.module-card { display: flex; align-items: center; gap: 10px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 14px 16px; color: var(--ui-ink); background: var(--ui-surface); font-size: var(--text-md); text-decoration: none; }
.module-card:hover { border-color: var(--ui-border-strong); background: var(--ui-surface-soft); }
.module-icon { color: var(--ui-ink-muted); }

@media (max-width: 760px) {
  .dashboard-page { padding: 20px 16px 40px; }
  h1 { font-size: var(--text-4xl); }
}
</style>
