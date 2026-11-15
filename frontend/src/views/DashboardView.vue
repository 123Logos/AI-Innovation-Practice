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
 * 4. **学生「还没有班级」时，这一页替他补上那条入口**（2026-11-15 第 24 轮）。角色过滤之后
 *    学生看不到 `/organization` 页签，而「凭邀请码加入」那一屏本来住在那儿；老板裁定
 *    不把导航加回来，改在这里挂一条横幅 + 一个极简输入框。判据是 `GET /organizations/me`
 *    的 **404**（那是后端对「没有组织」的正面答复），其他失败什么都不画。
 *
 * 刻意**不**做的事：
 *
 * - 不放未实现模块的禁用副本——未实现即不在表里，见上面第 2 条；
 * - 不显示任何未接入的数字，也不用占位符假装有数据；
 * - 不在访客态渲染登录态那四张统计卡：那些数字要登录才读得到，画一批空壳
 *   只能靠假数据填充。
 *
 * **2026-11-15 加了角色分流**（老板指令「基于角色的导航过滤」）：
 *
 * - `student` → 原来那四张统计卡（档案完成度 / 能力标签 / 在招岗位 / 模拟面试）；
 * - `teacher` / `school_admin` / `platform_admin` → 四张卡整块不渲染，改成一组
 *   「教师工具」入口卡，卡片来自 `config/modules.ts::staffOnlyModules()`，
 *   名字与路由都不在本文件写死（L4）；欢迎语换成「X老师，今天从哪一步接着走？」。
 *   教师侧也不发那四个读请求——它们服务的是已经不渲染的卡片。
 * - 模块入口那一栏的小标题随这一轮从「全部模块」改成「可用模块」：登录态那一栏现在是
 *   角色过滤后的结果，写「全部」会多承诺两个入口。访客态仍是全表、仍是「登录后可以用这些」。
 *
 * 一条机制变更要说清：原来那张「学情看板」入口卡的显示判据是权限字符串
 * `dashboard:read`（`auth.hasPermission`），现在统一换成模块表里的 `visibleToRoles`。
 * 今天两者等价——`app/core/permissions.py::ROLE_PERMISSIONS` 里拿到 `dashboard:read`
 * 的恰好就是教师侧三支；换掉是为了让「谁能看到哪个入口」只有一个出处。
 * **边界仍然在后端**：`GET /teacher/dashboard` 的 `dashboard:read`（403）与
 * `action_gate::require_organization`（400）一格没动，前端少画一张卡不构成任何保护。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { assessmentsApi, interviewsApi, isApiError, jobsApi, profilesApi } from '@/api'
import { fetchMyOrganization, redeemInvitation } from '@/api/organizations'
import type { ProfileRead } from '@/api/types'
import OverlayPanel from '@/components/OverlayPanel.vue'
import { APP_MODULES, isStaffRole, modulesVisibleTo, staffOnlyModules } from '@/config/modules'
import { useAuthStore } from '@/stores/auth'

const props = withDefaults(defineProps<{ mode?: 'guest' | 'member' }>(), { mode: 'member' })

const auth = useAuthStore()

/**
 * 除工作台自己以外的模块：本页就是工作台，再摆一张「回工作台」的卡片没有意义。
 *
 * 2026-11-15 起按角色过滤（老板指令「基于角色的导航过滤」），但**访客态例外**：
 * 访客那一屏的任务就是「登录后你可以用这些」，那一刻没有角色可读（`auth.role` 是 `null`），
 * 按 `modulesVisibleTo(null)` 走会得到空数组、介绍页一片空白。所以访客态列全表，
 * 登录态才用那张角色过滤器——两个分支都在这一处，视图里不写第二份。
 */
const entryModules = computed(() => {
  const pool = props.mode === 'guest' ? [...APP_MODULES] : modulesVisibleTo(auth.role)
  return pool.filter((module) => module.key !== 'dashboard')
})

/**
 * 教师侧（`teacher` / `school_admin` / `platform_admin`）在工作台看到的是这一组入口卡，
 * 而不是学生那四张统计卡——判据是「学生看不见的模块」，从模块表反推，
 * 不写模块名（守则 L4），也不写第二份角色判断（`isStaffRole` 是唯一那一处）。
 */
const staffEntryModules = computed(() => staffOnlyModules())

/** 登录态这一页给谁看。学生那四张卡的读请求也只在这一个条件下发。 */
const isStaff = computed(() => isStaffRole(auth.role))

/**
 * 欢迎语。教师侧那句按老板给的样式加「老师」二字；
 * 名字取 `auth.displayName`（= 用户名），今天没有单独的「真实姓名」字段可读。
 */
const greeting = computed(() => {
  const name = auth.displayName
  if (isStaff.value) return name ? `${name}老师，今天从哪一步接着走？` : '老师，今天从哪一步接着走？'
  return name ? `${name}，今天从哪一步接着走？` : '今天从哪一步接着走？'
})

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

/**
 * 学生「加入班级」横幅（2026-11-15 第 24 轮）。
 *
 * 它补的是角色过滤之后断掉的那条路：「凭邀请码加入组织」那一屏本来在 `/organization`，
 * 而入口对学生隐藏了（`config/modules.ts` 的 `visibleToRoles`），老板裁定不把导航加回来，
 * 改在这里补一个入口。
 *
 * 判据只有 404 这一格。`GET /organizations/me` 在「账号没有组织」时返回 404 而不是空对象
 * （`ADR-021` §2.6，`OrganizationView.vue` 文件头同一件事）；其余失败——断网、500——
 * **什么都不画**：那时候我们并不知道这个学生有没有班级，画一条「你还没加入班级」
 * 是替他编一个结论（守则 C1/C3 同一把尺：未验证的状态不上屏）。
 */
const needsOrganization = ref(false)

const redeemOpen = ref(false)
const redeemCode = ref('')
const redeemError = ref('')
const redeeming = ref(false)
const redeemNotice = ref('')

async function loadMembership(): Promise<void> {
  try {
    await fetchMyOrganization()
    needsOrganization.value = false
  } catch (error) {
    needsOrganization.value = isApiError(error) && error.status === 404
  }
}

/**
 * 提交邀请码。成功之后**重读这一页**，而不是 `location.reload()`：
 * 与 `OrganizationView.vue::handleRedeem` 同一支做法（那句「已加入…」要留在屏幕上，
 * 整页刷新会把它连同确认一起抹掉），而这一页的全部真相就是横幅的判据加那四张卡的读，
 * 两个都重跑等于把页面刷新一遍。附带一条实测到的限制：jsdom 的 `Location` 成员是
 * unforgeable（`Object.defineProperty(window.location, 'reload', …)` 直接报
 * `Cannot redefine property`），整页刷新这一步在本仓的单测里根本没法断言——
 * 不可断言的行为不该是交付的行为。失败必须把原因留在屏幕上并且不关面板（守则 C3）。
 */
async function submitRedeem(): Promise<void> {
  if (redeeming.value) return
  const code = redeemCode.value.trim()
  if (!code) {
    redeemError.value = '请填写老师给的邀请码。'
    return
  }
  redeeming.value = true
  redeemError.value = ''
  try {
    const result = await redeemInvitation(code)
    redeemNotice.value = `已加入「${result.organization.name}」`
    await Promise.all([loadMembership(), loadMemberData()])
    redeemOpen.value = false
  } catch (error) {
    redeemError.value = describeError(error, '邀请码没有生效，请核对后重试。')
  } finally {
    redeeming.value = false
  }
}

onMounted(() => {
  // 教师侧这一页不渲染那四张统计卡，所以一次请求都不发——
  // 「隐藏卡片但照样拉四次自己的档案/标签/岗位/面试」是白跑，也会把失败态写进没人看的卡里。
  if (props.mode === 'member' && !isStaff.value) {
    void loadMemberData()
    void loadMembership()
  }
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
        <h1>{{ greeting }}</h1>
      </section>

      <p v-if="loadError" class="page-error" role="alert">{{ loadError }}</p>

      <!--
        兑换成功那句要活过横幅本身：横幅在重读完组织信息之后就该消失了，
        而「已加入哪个班」是用户在这页要看到的答案（守则 C4：可逆动作的完成要说得出结果）。
      -->
      <p v-if="redeemNotice" class="page-notice" role="status">{{ redeemNotice }}</p>

      <!--
        学生且后端回答「还没有组织」（404）时的入口，位置在四张卡之上（老板指令：最显眼处）。
        弹层的三件底层事（点外面 / Esc / 焦点归还）归 `OverlayPanel` 基座（守则 L3），
        这里只写开合与提交；必填校验与失败原因必须上屏（守则 C5 的校验/错误豁免）。
      -->
      <section v-if="!isStaff && needsOrganization" class="join-card">
        <h2 class="join-card__title">加入班级</h2>
        <button
          class="join-card__trigger"
          type="button"
          :aria-expanded="redeemOpen"
          :aria-controls="redeemOpen ? 'join-code-panel' : undefined"
          @click="redeemOpen = !redeemOpen"
        >
          输入邀请码
        </button>
        <OverlayPanel
          v-if="redeemOpen"
          panel-id="join-code-panel"
          panel-class="join-panel"
          label="邀请码"
          role="group"
          @close="redeemOpen = false"
        >
          <form class="join-panel__form" @submit.prevent="submitRedeem">
            <label class="join-panel__label" for="join-code-input">邀请码</label>
            <input
              id="join-code-input"
              v-model="redeemCode"
              class="join-panel__input"
              name="code"
              autocomplete="off"
              :disabled="redeeming"
            />
            <p v-if="redeemError" class="join-panel__error" role="alert">{{ redeemError }}</p>
            <div class="join-panel__actions">
              <button
                class="join-panel__cancel"
                type="button"
                :disabled="redeeming"
                @click="redeemOpen = false"
              >
                取消
              </button>
              <button class="join-panel__submit" type="submit" :disabled="redeeming">
                {{ redeeming ? '提交中…' : '加入' }}
              </button>
            </div>
          </form>
        </OverlayPanel>
      </section>

      <!-- 学生那四张卡：教师侧不渲染（见 script 里 `isStaff` 那条注释）。 -->
      <section v-if="!isStaff" class="stat-grid" aria-label="我的进度">
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

      <!--
        教师侧（teacher / school_admin / platform_admin）的显著入口区：
        模块名与路由都从 `staffOnlyModules()` 来（守则 L4——这里不许出现写死的模块名），
        所以「学情看板」「组织管理」两张卡是那张表反推出来的结果，加一个只对教师侧开放的
        模块进表，这里自动多一张卡。
      -->
      <section v-if="isStaff" class="staff-entries" aria-label="教师工具">
        <RouterLink
          v-for="module in staffEntryModules"
          :key="module.key"
          class="staff-entry"
          :to="{ name: module.routeName }"
        >
          <span class="staff-entry__label">教师工具</span>
          <h2>{{ module.name }}</h2>
          <svg
            class="staff-entry__icon"
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
        </RouterLink>
      </section>
    </template>

    <!-- ------------------------------------------------------- 模块入口（两态共用） -->
    <section class="module-section" aria-label="模块入口">
      <div class="section-heading">
        <h2>{{ mode === 'guest' ? '登录后可以用这些' : '可用模块' }}</h2>
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

/* 成功回执与错误同一档存在感，但不抢红色（守则 C4：完成与失败都要上屏）。 */
.page-notice { margin: 0 0 16px; border: 1px solid var(--theme-fade); border-radius: var(--ui-radius); padding: 10px 14px; color: var(--theme-primary-deep); background: var(--theme-bg-light); font-size: var(--text-base); }

/*
 * 「加入班级」横幅：这一页唯一一条「先把这件事办了再往下」的入口，所以给它主题色
 * 左边竖线 + 一颗靠右的主按钮，不写说明性小字（守则 C5）。
 * `position: relative` 是给弹层当包含块——基座刻意不 Teleport（`OverlayPanel.vue` 文件头
 * 第 1 条「刻意不做」），位置由调用方定，这是调用方那一份。
 */
.join-card { position: relative; display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 16px; border: 1px solid var(--ui-border); border-left: 3px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 16px 20px; background: var(--ui-surface-soft); }
.join-card__title { margin: 0; font-size: var(--text-lg-plus); }
.join-card__trigger { margin-left: auto; border: 0; border-radius: var(--ui-radius); padding: 10px 18px; color: var(--ui-surface); background: var(--theme-rule); cursor: pointer; font: inherit; font-size: var(--text-md); }
.join-card__trigger:hover { background: var(--theme-primary); }
.join-card__trigger:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }

.join-panel { position: absolute; top: calc(100% - 6px); right: 16px; width: min(320px, 100%); border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 14px; background: var(--ui-surface); box-shadow: var(--ui-shadow-menu); }
.join-panel__form { display: flex; flex-direction: column; gap: 8px; }
.join-panel__label { color: var(--ui-ink-muted); font-size: var(--text-sm); }
.join-panel__input { width: 100%; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 9px 12px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: var(--text-md); }
.join-panel__input:focus { outline: 2px solid var(--theme-rule); outline-offset: 1px; }
.join-panel__error { margin: 0; color: var(--ui-danger); font-size: var(--text-base); }
.join-panel__actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 2px; }
.join-panel__cancel, .join-panel__submit { border-radius: var(--ui-radius); padding: 9px 16px; cursor: pointer; font: inherit; font-size: var(--text-md); }
.join-panel__cancel { border: 1px solid var(--ui-border-strong); color: var(--ui-ink); background: var(--ui-surface); }
.join-panel__submit { border: 0; color: var(--ui-surface); background: var(--theme-rule); }
.join-panel__cancel:disabled, .join-panel__submit:disabled { opacity: .6; cursor: progress; }

.stat-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
.stat-card { display: flex; flex-direction: column; gap: 6px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 18px; background: var(--ui-surface); }
.stat-label { color: var(--ui-ink-muted); font-size: var(--text-sm); }
.stat-value { font-size: var(--text-6xl); font-weight: 650; }
.stat-hint { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-sm); line-height: 1.6; }
.card-link { margin-top: 8px; color: var(--theme-rule); font-size: var(--text-base); text-decoration: none; }
.card-link:hover { text-decoration: underline; }

/*
 * 教师侧入口区：两张卡并排（窄屏自动折成一列），左边那条 3px 主题色竖线沿用
 * 原来那张「学情看板」入口卡的样式——它是这一页唯一的「这不是你的东西，去这里」信号。
 */
.staff-entries { display: grid; gap: 12px; margin-top: 16px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
.staff-entry { display: flex; align-items: center; justify-content: space-between; gap: 12px; border: 1px solid var(--ui-border); border-left: 3px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 18px 20px; background: var(--ui-surface-soft); color: inherit; text-decoration: none; }
.staff-entry:hover { border-color: var(--ui-border-strong); border-left-color: var(--theme-primary); background: var(--ui-surface); }
.staff-entry:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.staff-entry__label { display: block; color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .1em; }
.staff-entry h2 { margin: 4px 0 0; font-size: var(--text-lg-plus); }
.staff-entry__icon { flex: 0 0 auto; color: var(--ui-ink-muted); }

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
