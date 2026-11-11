<script setup lang="ts">
/**
 * 岗位详情页（议题 3.B）。
 *
 * 三条硬约束：
 * 1. **过期标记只看后端 `is_expired`**：凡 `deadline < 今天` 一律显示「已过期」，
 *    不区分查看者身份、也不绑定列表的 `include_expired` 参数（2.G：判据在 service 层
 *    用应用时钟算，前端再实现一遍必然漂移）。
 * 2. **`origin` 徽标只有两态**，文案固定为「可回溯原文」/「未标注出处」，
 *    **不出现「AI 提取」字样**（v7：这一列只证明「这句话在 JD 原文里出现过」，
 *    不证明是谁写的——手动粘一段原文进 `evidence` 也会被派生成同一个值）。
 * 3. **`evidence_text` 为 NULL 时整块不渲染**：不空着一条展开区、也不写「无依据」占位。
 *    `confidence` 同理：`null` 就不显示那一格，而不是显示「0」或「—」——
 *    把「模型没给数」画成一个看起来像数值的占位符，是给用户一份不存在的依据。
 * 4. **确认弹窗一律用页面内的 `.ui-overlay` / `.ui-panel`**，不用 `window.confirm`：
 *    原生对话框阻塞 CDP，自动化验收走不完（理由与事故记录写在 `askDelete()` 上方）。
 *
 * 「你已具备」角标的数据来自 `GET /jobs/{job_id}/requirement-match`（ADR-013 §10 第 3c 步）：
 * 一次只读 GET，判等在后端，与匹配共用同一版写法判据；不写表、不打分、不排序、不花额度。
 *
 * 它与下面的「匹配度」卡（第 8 步）是同一版判据的两个视图：
 * 角标只回答「这条要求我命中了吗」，匹配卡额外回答「这段经历撑不撑得起它」。
 * 两边不可能不一致，因为**前端不参与判等**（以前这里是一套本地归一化比对，
 * 于是同一屏会出现角标与清单各说一套——那是 §1.3 那屏矛盾的第二半，2026-11-08 消除）。
 * **只有点按钮才发匹配请求**：那一步每次都可能向 DeepSeek 真实调用、花用户自己的 Key，
 * 所以不许在 `onMounted` 里自动算（`load()` / `loadCoverage()` 是两个只读 GET，与它不同）。
 *
 * 只有创建者能看到「发布 / 下架 / 删除」——后端对非创建者一律 404（1.F），
 * 摆一个必然失败的按钮比不摆更糟。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { isApiError } from '@/api'
import {
  EMPLOYMENT_TYPE_LABELS,
  JOB_ORIGIN_BADGES,
  JOB_SKILL_TYPE_LABELS,
  JOB_STATUS_LABELS,
  matchDegradeLines,
  matchFailureText,
  useJobStore,
} from '@/stores/job'
import { formatDate, formatDateTime } from '@/utils/datetime'
import SkeletonList from '@/components/SkeletonList.vue'

const store = useJobStore()
const route = useRoute()
const router = useRouter()

const loadError = ref('')
const actionError = ref('')
const actionNotice = ref('')
/** 正在执行发布/下架/删除时锁住这一组按钮。 */
const actionBusy = ref<'publish' | 'unpublish' | 'delete' | null>(null)

/** JD 超过这么长就先折叠；600 字大约是手机上 12–14 行，够看清「这是不是我要的岗位」。 */
const COLLAPSE_THRESHOLD = 600
const expanded = ref(false)

const job = computed(() => store.detail)
const isLongDescription = computed(() => (job.value?.description.length ?? 0) > COLLAPSE_THRESHOLD)
const descriptionText = computed(() => {
  const text = job.value?.description ?? ''
  if (!isLongDescription.value || expanded.value) return text
  return `${text.slice(0, COLLAPSE_THRESHOLD)}…`
})

/** 只有创建者才有的操作区；比对用的是 JWT 里的用户 id 与 `created_by_user_id`。 */
const isCreator = computed(() => store.isCreatorOf(job.value))

function employmentLabel(value: string | null): string {
  if (!value) return ''
  return EMPLOYMENT_TYPE_LABELS[value as keyof typeof EMPLOYMENT_TYPE_LABELS] ?? value
}

/** 技能分类标签：未分类（NULL）返回空串，调用方据此不渲染徽标。 */
function skillTypeLabel(value: string | null): string {
  if (!value) return ''
  return JOB_SKILL_TYPE_LABELS[value as keyof typeof JOB_SKILL_TYPE_LABELS] ?? value
}

/**
 * 出处徽标：两态固定文案（「可回溯原文」/「未标注出处」），**不含任何「AI」字样**。
 * 未知值（后端将来加第三种）一律按「未标注出处」显示 —— 说不出出处就是未标注，
 * 反过来把它说成「可回溯」才是真错误。
 */
function originBadge(origin: string): string {
  return JOB_ORIGIN_BADGES[origin as keyof typeof JOB_ORIGIN_BADGES] ?? JOB_ORIGIN_BADGES.unverified
}

/**
 * `confidence` 的展示：`0.9000` → `0.90`；`null` → 空串（调用方整块不渲染）。
 *
 * 后端列宽是 `DECIMAL(5,4)`、JSON 里是字符串，这里只裁到两位小数：
 * 第四位没有决策价值，而 ADR-011「后果」章已写明 `confidence` 是不是模型随手填的
 * 本轮**无法验证**——把一个不确定的自报数显示成 `0.9123` 那种精度，
 * 反而会让用户以为它有含义。原始串仍然在库里，这里只动显示。
 */
function confidenceLabel(value: string | null): string {
  if (!value) return ''
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return value
  return parsed.toFixed(2)
}

/**
 * 薪资区间展示。后端是 `DECIMAL(12,2)` 的**字符串**（`"8000.00"`），
 * 这里只去掉多余的 `.00`，不做数值运算——避免把「库里存了什么」看成被改过。
 */
function trimAmount(value: string): string {
  return value.replace(/\.00$/, '')
}

const salaryLabel = computed(() => {
  const current = job.value
  if (!current) return ''
  const min = current.salary_min ? trimAmount(current.salary_min) : ''
  const max = current.salary_max ? trimAmount(current.salary_max) : ''
  if (min && max) return `${min} – ${max} 元`
  if (min) return `${min} 元起`
  if (max) return `最高 ${max} 元`
  return ''
})

function describeError(error: unknown, fallback: string): string {
  if (isApiError(error) && error.message) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}

async function load(): Promise<void> {
  const jobId = String(route.params.id ?? '')
  if (!jobId) return
  loadError.value = ''
  // 两件事互不依赖：详情读不到「你已具备」只是少标几个，不该拖慢主内容。
  try {
    await store.loadDetail(jobId)
  } catch (error) {
    loadError.value = isApiError(error) && error.status === 404
      ? '这个岗位不存在、是别人的草稿，或已经在回收站里。'
      : describeError(error, '读取岗位详情失败，请稍后重试。')
  }
}

async function loadCoverage(): Promise<void> {
  const jobId = String(route.params.id ?? '')
  if (!jobId) return
  try {
    await store.loadRequirementMatch(jobId)
  } catch {
    // 角标是增益信息：拉不到就一条都不标。
    // 但它**不报错也不重试**是有边界的——不能顺带把详情也拖红，那两件事互不依赖。
    store.clearRequirementMatch()
  }
}

async function handlePublish(): Promise<void> {
  const current = job.value
  if (!current || actionBusy.value) return
  actionBusy.value = 'publish'
  actionError.value = ''
  actionNotice.value = ''
  try {
    await store.publish(current.id)
    actionNotice.value = '已发布，任何登录用户都能在列表里看到它。'
  } catch (error) {
    actionError.value = describeError(error, '发布失败，请稍后重试。')
  } finally {
    actionBusy.value = null
  }
}

async function handleUnpublish(): Promise<void> {
  const current = job.value
  if (!current || actionBusy.value) return
  actionBusy.value = 'unpublish'
  actionError.value = ''
  actionNotice.value = ''
  try {
    await store.unpublish(current.id)
    actionNotice.value = '已下架回草稿，现在只有你自己看得到。'
  } catch (error) {
    actionError.value = describeError(error, '下架失败，请稍后重试。')
  } finally {
    actionBusy.value = null
  }
}

async function handleDelete(): Promise<void> {
  const current = job.value
  if (!current || actionBusy.value) return
  actionBusy.value = 'delete'
  actionError.value = ''
  try {
    await store.remove(current.id)
    deleteConfirmOpen.value = false
    await router.push({ name: 'job-board' })
  } catch (error) {
    actionError.value = describeError(error, '删除失败，请稍后重试。')
  } finally {
    actionBusy.value = null
  }
}

/* -------------------------------- 人岗匹配 -------------------------------- */

/**
 * 匹配失败的红字（429 / 404 / 401 / 网络 / 500）。
 *
 * **AI 失败不在这里**：后端把它翻译成 `200 + meta.degrade_reason`，
 * 由 `matchDegradeLines()` 分行显示在结果区里。把它做成红字会同时犯两个错——
 * 用户以为这次什么都没算出来（其实分数是真的），而且看不出自己该去补 Key 还是该等。
 */
const matchError = ref('')

const match = computed(() => store.matchResult)
const matchHintLines = computed(() => (match.value ? matchDegradeLines(match.value) : []))

/** 分数只给一个整数百分比：`0.612` 在屏幕上没有额外含义，而 61% 有。 */
const matchScoreLabel = computed(() => {
  const score = match.value?.score
  // `null` 显示成「—」而不是 0%（F1:A，老板 2026-11-08 裁定）：
  // 「0%」是一句关于能力的判断，而真相是「这次没有可比对的内容」，两者修法完全不同。
  return score === null || score === undefined ? '—' : `${Math.round(score * 100)}%`
})

/**
 * 角标的悬停说明：命中用的到底是用户自己写的哪个写法。
 *
 * 岗位要求 `Python`、简历写的是 `Python3` 时，「你已具备」背后是一次**版本号判等**
 * （`skill_forms_match` 的第 ③/④ 条）。把对面那个写法说出来，用户才知道我们没有瞎认；
 * 而它只放在 `title` 里：这是解释性的增益信息，不是用户修得动的失败原因，
 * 不值得在 375px 的一行里再挤出一段文字。
 */
function badgeTitle(name: string): string {
  const form = store.matchedFormOf(name)
  return form && form !== name ? `按你简历里写的「${form}」认的` : '这条技能在你的简历里'
}

/** 证据出自经历的哪一栏；标签与简历编辑器里同一栏的名字逐字一致。 */
function evidenceFieldLabel(field: string): string {
  if (field === 'title') return '项目名称'
  if (field === 'responsibility') return '个人职责'
  return '项目描述'
}

async function handleMatch(): Promise<void> {
  const current = job.value
  if (!current || store.matching) return
  matchError.value = ''
  try {
    await store.runMatch(current.id)
  } catch (error) {
    matchError.value = matchFailureText(error)
  }
}

/* ------------------------------ 删除确认弹窗 ------------------------------ */

/**
 * 删除确认用页面内的 `.ui-overlay` / `.ui-panel` 弹层，**不用 `window.confirm`**。
 *
 * Why：原生 `window.confirm` 是**同步阻塞**的——它把 JS 主线程钉在那里等鼠标，
 * 于是浏览器自动化通道（CDP）在这一步既拿不到返回值、也发不出后续指令，整条验收
 * 流程停在弹窗上不能继续。本轮之前就真发生过：驱动点「删除」后对话框挂死，
 * 自动化删除流程走不通，只能退回手写 curl 验证。这一条不是风格偏好，是验收能力。
 *
 * 换成自建弹层后，「确认」是一次普通 click 事件：可读、可点、可截图、可断言。
 * 键盘可达性照 `AppTopBar.vue` 的先例补齐：Esc 关、点遮罩关、焦点进得到按钮。
 */
const deleteConfirmOpen = ref(false)

function askDelete(): void {
  if (!job.value || actionBusy.value) return
  actionError.value = ''
  deleteConfirmOpen.value = true
}

function closeDeleteConfirm(): void {
  // 删除在途时不关：否则用户看见的是「弹窗消失了但按钮还转圈」，
  // 分不清是删完了还是被自己关掉了。
  if (actionBusy.value === 'delete') return
  deleteConfirmOpen.value = false
}

function onDialogKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && deleteConfirmOpen.value) closeDeleteConfirm()
}

onMounted(() => {
  document.addEventListener('keydown', onDialogKeydown)
  void load()
  void loadCoverage()
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onDialogKeydown)
})
</script>

<template>
  <main class="detail-page">
    <div class="page-heading">
      <div class="heading-main">
        <p class="eyebrow">岗位详情</p>
      </div>
      <div class="heading-actions">
        <RouterLink class="text-link" :to="{ name: 'job-board' }">返回列表</RouterLink>
        <!-- 编辑入口只对创建者显示：后端对非创建者的 PUT 一律 404（1.F #4），
             摆一个必然失败的链接比不摆更糟。`/jobs/:id/edit` 是 ADR-011 议题 3.B v9 补的那一条。 -->
        <RouterLink
          v-if="isCreator"
          class="text-link"
          data-testid="edit-link"
          :to="{ name: 'job-edit', params: { id: String(route.params.id ?? '') } }"
        >
          编辑
        </RouterLink>
        <RouterLink class="text-link" :to="{ name: 'job-trash' }">我的删除</RouterLink>
      </div>
    </div>

    <p v-if="loadError" class="feedback feedback--error" role="alert">{{ loadError }}</p>
    <!-- 稳定态实测：前三块 1280 档 229/167/331px、375 档 377/200/508px，块间距 16px（`.detail-block`）。 -->
    <SkeletonList
      v-else-if="store.detailLoading"
      label="正在加载岗位…"
      :heights="[229, 167, 331]"
      :heights-compact="[377, 200, 508]"
      :gap="16"
    />
    <p v-else-if="!job" class="feedback">没有可显示的内容。</p>

    <template v-else>
      <section class="detail-card">
        <h1 class="detail-title">{{ job.title }}</h1>
        <p class="detail-company">{{ job.company_name }}</p>

        <div class="badge-row">
          <span class="badge">{{ JOB_STATUS_LABELS[job.status] }}</span>
          <span v-if="employmentLabel(job.employment_type)" class="badge">
            {{ employmentLabel(job.employment_type) }}
          </span>
          <span v-if="job.is_expired" class="badge badge--expired">已过期</span>
        </div>

        <dl class="meta-grid">
          <div class="meta-item">
            <dt>工作地点</dt>
            <dd>{{ job.location || '未填写' }}</dd>
          </div>
          <div class="meta-item">
            <dt>截止时间</dt>
            <dd>
              {{ job.deadline ? formatDate(job.deadline) || job.deadline : '长期有效' }}
              <span v-if="job.is_expired" class="meta-flag">已过期</span>
            </dd>
          </div>
          <div class="meta-item">
            <dt>薪资区间</dt>
            <dd>{{ salaryLabel || '未填写' }}</dd>
          </div>
          <div class="meta-item">
            <dt>经验要求</dt>
            <dd>{{ job.experience_required || '未填写' }}</dd>
          </div>
          <div class="meta-item">
            <dt>录入时间</dt>
            <dd>{{ formatDateTime(job.created_at) || job.created_at }}</dd>
          </div>
        </dl>

        <div v-if="isCreator" class="owner-actions">
          <button
            v-if="job.status === 'draft'"
            class="row-button row-button--primary"
            type="button"
            :disabled="actionBusy !== null || store.submitting"
            @click="handlePublish"
          >
            {{ actionBusy === 'publish' ? '发布中…' : '发布到广场' }}
          </button>
          <button
            v-else
            class="row-button"
            type="button"
            :disabled="actionBusy !== null || store.submitting"
            @click="handleUnpublish"
          >
            {{ actionBusy === 'unpublish' ? '下架中…' : '下架回草稿' }}
          </button>
          <button
            class="row-button row-button--danger"
            type="button"
            data-testid="delete-button"
            :disabled="actionBusy !== null || store.submitting"
            @click="askDelete"
          >
            {{ actionBusy === 'delete' ? '删除中…' : '删除' }}
          </button>
        </div>

        <p v-if="actionError" class="feedback feedback--error" role="alert">{{ actionError }}</p>
        <p v-if="actionNotice" class="feedback feedback--ok" role="status">{{ actionNotice }}</p>
      </section>

      <!--
        匹配卡排在 JD **前面**（第一条内容块）。理由不是排版偏好，是量出来的：
        放在技能表后面时，实测这条种子岗位的按钮在 1280px 下距视口顶 1776px、375px 下 2218px，
        也就是要翻完整份 JD 加整张技能表才看得见——一个必须点击才会发真实请求的功能被埋在那儿，
        等于没有。改到最前面之后同一岗位、同一账号复测：按钮距顶 497px（1280×900）与
        694px（375×780），两个视口都还在首屏内。
      -->
      <section class="detail-block match-block" data-testid="match-block">
        <h2 class="block-title">我和这个岗位的匹配度</h2>
        <p class="block-hint">
          匹配度是参考，不是录用概率：它只按你的简历与这个岗位的技能要求算重合，
          不看经验年限、薪资与面试表现。
          这一步会用到你自己的 AI 用量，所以只在你点下面这个按钮时才发生。
        </p>
        <button
          class="row-button row-button--primary"
          type="button"
          data-testid="match-run"
          :aria-busy="store.matching"
          :disabled="store.matching"
          @click="handleMatch"
        >
          {{ store.matching ? '正在匹配…' : match ? '再算一次' : '算一次匹配' }}
        </button>
        <p v-if="matchError" class="feedback feedback--error" role="alert">{{ matchError }}</p>

        <div v-if="match" class="match-result">
          <p class="match-score">
            <span class="match-score__value" data-testid="match-score">{{ matchScoreLabel }}</span>
            <span class="match-score__caption">匹配度</span>
          </p>
          <!-- 用的是哪一份档案由响应回显（ADR-013 §13 第 7 条）：请求体是空的，
               不写这一行，编辑器里开着 B 档案的人会把 A 档案的分数读成自己的。 -->
          <p class="match-source">
            <template v-if="match.profile_id">按《{{ match.profile_title || '未命名简历' }}》打分</template>
            <template v-else>
              你还没有保存的简历，这次是按<b>空简历</b>打的分——先去「我的简历」填一份，分数才有意义。
            </template>
          </p>

          <ul v-if="matchHintLines.length" class="match-degrade" role="status">
            <li v-for="line in matchHintLines" :key="line">{{ line }}</li>
          </ul>

          <!--
            `score === null` 不配独立文案（2026-11-08 老板裁定 + 同日真浏览器实测）：
            `score is null` 与 `no_required_skills` 是**同一件事**——核心 term 不在就整个不给分
            （`app/services/match_scoring.py` 的短路，枚举 81 组合由
            `tests/test_match_scoring.py::test_score_is_null_exactly_when_the_job_has_no_required_skills`
            钉住），所以下面那行 `.empty` 就是这一屏唯一的说明。
            这里曾经有三行同义反复（新文案 + `.empty` + 档案为空时的 `.match-source`），
            计数断言 `occurrences()` 是它的回归锁。
            权重、分母、`weight_sum_used` 都不出现在这里——那是响应里给对账用的，不是给用户读的。
          -->
          <p v-if="match.meta.no_required_skills" class="empty">
            这个岗位还没有填写技能要求，所以没有可比对的条目。
          </p>
          <div v-else class="match-tiers">
            <section v-if="match.covered.length" class="match-tier match-tier--covered">
              <h3 class="tier-title">已具备 {{ match.covered.length }} 项</h3>
              <ul class="tier-list">
                <li v-for="item in match.covered" :key="item.skill" class="tier-item">
                  <span class="tier-skill">{{ item.skill }}</span>
                  <!-- 片段是经历原文的连续子串（服务端切的），这里原样显示、不改写不截断：
                       用户能一眼认出那是自己写过的话，这才叫可核对。 -->
                  <ul class="tier-evidence">
                    <li
                      v-for="entry in item.evidence"
                      :key="`${entry.experience_id}:${entry.field}`"
                      class="evidence-line"
                    >
                      <span class="evidence-from">{{ evidenceFieldLabel(entry.field) }}</span>
                      <span class="evidence-text">{{ entry.snippet }}</span>
                    </li>
                  </ul>
                </li>
              </ul>
            </section>

            <section v-if="match.partial.length" class="match-tier match-tier--partial">
              <h3 class="tier-title">部分具备 {{ match.partial.length }} 项</h3>
              <ul class="tier-list">
                <li v-for="item in match.partial" :key="item.skill" class="tier-item">
                  <span class="tier-skill">{{ item.skill }}</span>
                  <span class="tier-reason">{{ item.reason }}</span>
                </li>
              </ul>
            </section>

            <section v-if="match.gaps.length" class="match-tier match-tier--gap">
              <h3 class="tier-title">还缺 {{ match.gaps.length }} 项</h3>
              <ul class="tier-list">
                <li v-for="gap in match.gaps" :key="gap.skill" class="tier-item">
                  <span class="tier-skill">{{ gap.skill }}</span>
                  <p class="tier-action">{{ gap.action }}</p>
                  <p class="tier-verify">{{ gap.verify }}</p>
                </li>
              </ul>
            </section>
          </div>
        </div>
      </section>

      <section class="detail-block">
        <h2 class="block-title">岗位描述（JD 原文）</h2>
        <p class="description">{{ descriptionText }}</p>
        <button
          v-if="isLongDescription"
          class="expand-button"
          type="button"
          :aria-expanded="expanded"
          @click="expanded = !expanded"
        >
          {{ expanded ? '收起全文' : '展开全文' }}
        </button>
      </section>

      <section class="detail-block">
        <h2 class="block-title">岗位要求的技能</h2>
        <p class="block-hint">
          「可回溯原文」表示这条技能能在这份 JD 原文里逐字找到；
          「未标注出处」表示当前没有可回溯的依据。
        </p>

        <p v-if="!job.skills.length" class="empty">这个岗位还没有填写技能要求。</p>

        <ul v-else class="skill-list">
          <li v-for="skill in job.skills" :key="skill.id" class="skill-item">
            <div class="skill-head">
              <span class="skill-name">{{ skill.name }}</span>
              <span v-if="skillTypeLabel(skill.type)" class="badge badge--soft">
                {{ skillTypeLabel(skill.type) }}
              </span>
              <span class="badge badge--origin">{{ originBadge(skill.origin) }}</span>
              <span
                v-if="store.isRequirementHit(skill.name)"
                class="badge badge--mine"
                :title="badgeTitle(skill.name)"
                >你已具备</span
              >
            </div>
            <!--
              `evidence_text` 为 NULL 时整块不渲染：不留下空的展开区、也不写「无依据」占位。
              这里**保持 B2 的直出显示**（议题 3.B「evidence 展开可见」），不改成交互展开：
              详情页是核对场所，依据默认看得见比少一次点击更重要；录入页才需要收起。
              `confidence` 是本轮补上的一格——C1 之后它有真值了，而这一页原先根本不显示它。
            -->
            <p v-if="skill.evidence_text" class="skill-evidence">
              原文依据：{{ skill.evidence_text }}
              <!-- `confidence` 为 null 时这一格整个不出现：留「—」会被读成一个真实数值。 -->
              <span v-if="confidenceLabel(skill.confidence)" class="skill-confidence">
                · AI 把握 {{ confidenceLabel(skill.confidence) }}
              </span>
            </p>
          </li>
        </ul>
      </section>
    </template>
  </main>

  <!--
    删除确认弹层：`Teleport` 到 body（与 `AppTopBar.vue` 的两个自建弹窗同一做法），
    遮罩用全局 `.ui-overlay`（flex + `margin:auto`）——它刻意不用
    `grid + place-items:center`：内容比视口高时居中会把面板顶出滚动范围、按钮够不着。
  -->
  <Teleport to="body">
    <div v-if="deleteConfirmOpen" class="ui-overlay" data-testid="delete-dialog" @click.self="closeDeleteConfirm">
      <section
        class="ui-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="job-delete-heading"
      >
        <h2 id="job-delete-heading" class="dialog-title">删除这条岗位？</h2>
        <p class="dialog-body">
          删除后这条岗位进「我的删除」，技能条目一行都不会丢，随时可以恢复。
          <b>{{ job?.title }}</b>（{{ job?.company_name }}）
        </p>
        <div class="dialog-actions">
          <button
            class="row-button row-button--ghost"
            type="button"
            data-testid="delete-cancel"
            :disabled="actionBusy === 'delete'"
            @click="closeDeleteConfirm"
          >
            取消
          </button>
          <button
            class="row-button row-button--danger"
            type="button"
            data-testid="delete-confirm"
            :disabled="actionBusy !== null || store.submitting"
            @click="handleDelete"
          >
            {{ actionBusy === 'delete' ? '删除中…' : '确认删除' }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.detail-page { width: min(880px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.page-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.heading-main { min-width: 0; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: 12px; letter-spacing: .12em; }
.heading-actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; gap: 8px 14px; }
.text-link { color: var(--theme-primary); font-size: 13px; text-decoration: none; white-space: nowrap; }
.text-link:hover { text-decoration: underline; }

.feedback { margin: 0 0 12px; color: var(--ui-ink-faint); font-size: 13px; line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }
.feedback--ok { color: var(--theme-primary-deep); }

.detail-card { display: grid; gap: 12px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 20px 22px; background: var(--ui-surface); }
.detail-title { margin: 0; color: var(--ui-ink); font-size: 20px; font-weight: 650; }
.detail-company { margin: 0; color: var(--ui-ink-muted); font-size: 13px; }
.badge-row { display: flex; flex-wrap: wrap; gap: 6px; }
.badge { border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 2px 7px; color: var(--ui-ink-muted); background: var(--ui-surface-soft); font-size: 11px; line-height: 1.5; white-space: nowrap; }
.badge--soft { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); background: var(--theme-bg-light); }
.badge--expired { border-color: var(--ui-danger-border); color: var(--ui-danger); background: var(--ui-danger-soft); }
/* 出处徽标刻意用中性色：它不是成就，只是一个「能不能回溯」的事实。 */
.badge--origin { border-style: dashed; color: var(--ui-ink-faint); }
.badge--mine { border-color: var(--theme-primary); color: var(--theme-primary-strong); background: var(--theme-bg-light); font-weight: 600; }

.meta-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px 18px; margin: 0; }
.meta-item { min-width: 0; }
.meta-item dt { color: var(--ui-ink-faint); font-size: 11px; letter-spacing: .04em; }
.meta-item dd { margin: 4px 0 0; color: var(--ui-ink); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.meta-flag { margin-left: 6px; color: var(--ui-danger); font-size: 11px; }

.owner-actions { display: flex; flex-wrap: wrap; gap: 10px; border-top: 1px solid var(--ui-surface-sunken); padding-top: 14px; }

.detail-block { margin-top: 16px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 18px 22px; background: var(--ui-surface); }
.block-title { margin: 0 0 10px; color: var(--ui-ink); font-size: 14px; font-weight: 650; }
.block-hint { margin: 0 0 12px; color: var(--ui-ink-faint); font-size: 11px; line-height: 1.5; }
/* JD 是用户粘进来的原文：保留换行，但不允许它撑破容器（长 URL / 长英文单词）。 */
.description { margin: 0; color: var(--ui-ink); font-size: 13px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.expand-button { margin-top: 10px; border: 0; padding: 0; color: var(--theme-primary); background: none; cursor: pointer; font: inherit; font-size: 12px; text-decoration: underline; }
.expand-button:hover { color: var(--theme-primary-strong); }

.skill-list { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.skill-item { display: grid; gap: 6px; border-bottom: 1px solid var(--ui-surface-sunken); padding-bottom: 10px; }
.skill-item:last-child { border-bottom: 0; padding-bottom: 0; }
.skill-head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.skill-name { color: var(--ui-ink); font-size: 13px; font-weight: 600; }
.skill-evidence { margin: 0; border-left: 2px solid var(--theme-rule-soft); padding-left: 8px; color: var(--ui-ink-faint); font-size: 12px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.empty { margin: 0; color: var(--ui-ink-faint); font-size: 13px; }

/* 匹配卡：一个分数 + 三档清单。刻意不显示权重与计数（ADR-013 §7.5 的 2026-11-07 裁定）。 */
.match-block { display: grid; gap: 12px; }
/* 按钮按内容宽而不是撑满这一栏：它是一次会计费的调用，不该在视觉上比「发布 / 删除」更强势。 */
.match-block > .row-button { justify-self: start; }
.match-result { display: grid; gap: 14px; border-top: 1px solid var(--ui-surface-sunken); padding-top: 14px; }
.match-score { display: flex; align-items: baseline; gap: 8px; margin: 0; }
.match-score__value { color: var(--theme-primary-strong); font-size: 30px; font-weight: 700; line-height: 1.1; }
.match-score__caption { color: var(--ui-ink-faint); font-size: 12px; letter-spacing: .04em; }
.match-source { margin: 0; color: var(--ui-ink-muted); font-size: 12px; line-height: 1.6; }
.match-source b { color: var(--ui-ink); font-weight: 600; }
/* 退化提示用中性虚线框：它不是错误（分数是真的），但也不能长得像正文，
   否则用户会把它当成一次普通的说明读过去。 */
.match-degrade { display: grid; gap: 4px; margin: 0; padding: 10px 12px; border: 1px dashed var(--ui-border-strong); border-radius: var(--ui-radius); background: var(--ui-surface-soft); list-style: none; color: var(--ui-ink-muted); font-size: 12px; line-height: 1.6; }
.match-tiers { display: grid; gap: 16px; }
.match-tier { display: grid; gap: 8px; }
.tier-title { margin: 0; color: var(--ui-ink); font-size: 13px; font-weight: 650; }
.tier-list { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.tier-item { display: grid; gap: 4px; border-left: 2px solid var(--ui-surface-sunken); padding-left: 10px; }
.match-tier--covered .tier-item { border-left-color: var(--theme-primary); }
.match-tier--partial .tier-item { border-left-color: var(--theme-rule-soft); }
.tier-skill { color: var(--ui-ink); font-size: 13px; font-weight: 600; }
.tier-reason { color: var(--ui-ink-faint); font-size: 12px; line-height: 1.6; }
.tier-action, .tier-verify { margin: 0; color: var(--ui-ink-faint); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.tier-verify { color: var(--ui-ink-muted); }
/* 证据片段是用户自己写过的原文：保留换行、允许断词，但不允许撑破容器。 */
.tier-evidence { display: grid; gap: 4px; margin: 2px 0 0; padding: 0; list-style: none; }
/* 每条片段两行：上面那行是「出自哪一栏」，下面才是原文。
   不分行会把标签和原文连成一句（「项目描述用 Python 重写了…」），读起来像原文里有这个词。 */
.evidence-line { display: grid; gap: 1px; }
.evidence-from { color: var(--ui-ink-faint); font-size: 11px; letter-spacing: .04em; }
.evidence-text { color: var(--ui-ink-muted); font-size: 12px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }

/* 删除确认弹层：外壳是全局 `.ui-overlay` / `.ui-panel`，这里只补这一页特有的三件。 */
.dialog-title { margin: 0; color: var(--ui-ink); font-size: 17px; font-weight: 650; }
.dialog-body { margin: 8px 0 0; color: var(--ui-ink-muted); font-size: 13px; line-height: 1.6; }
.dialog-body b { color: var(--ui-ink); font-weight: 600; }
.dialog-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 10px; margin-top: 20px; }

.row-button { height: 32px; border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 0 14px; color: var(--theme-primary-deep); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 12px; white-space: nowrap; }
.row-button:hover:not(:disabled) { background: var(--theme-bg-hover); }
.row-button:disabled { opacity: .5; cursor: progress; }
.row-button--primary { border-color: var(--theme-primary); color: var(--theme-on-primary); background: var(--theme-primary); }
.row-button--primary:hover:not(:disabled) { border-color: var(--theme-primary-hover); background: var(--theme-primary-hover); }
.row-button--danger { border-color: var(--ui-danger-border); color: var(--ui-danger); }
.row-button--danger:hover:not(:disabled) { border-color: var(--ui-danger); background: var(--ui-danger-soft); }

@media (max-width: 760px) {
  .detail-page { padding: 20px 16px 48px; }
  .page-heading { flex-direction: column; }
  .heading-actions { justify-content: flex-start; }
}
@media print {
  .detail-page { display: none; }
}
</style>
