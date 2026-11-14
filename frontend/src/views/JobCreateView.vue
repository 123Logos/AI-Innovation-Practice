<script setup lang="ts">
/**
 * 岗位录入 / 编辑页（议题 3.B + 3.B v9 补的 `/jobs/:id/edit`）。**整页而不是弹窗**：
 * JD 最长 8000 字，而 Phase 3.2 定的弹窗在 ≤768px 是 `calc(100% - 24px)`，长表单塞进去
 * 只会得到一条很高的内部滚动条，比整页更难填。
 *
 * **一个组件两种模式，由路由 `props` 传 `mode`**，不新写一份编辑表单：
 * 两份 300 行的表单一定会漂移——最可能的那种漂移是「录入页改了技能投影、编辑页没跟上」，
 * 表现成用户在编辑页保存时把 evidence 悄悄清空、徽标掉回「未标注出处」。
 * 两种模式的差别只有四处，全部就地按 `isEdit` 分流：初值来源、保存打哪个端点、
 * 保存成功后的回跳、「取消」的去处。
 *
 * 链路（ADR-011 2.A / ADR-010 §5 的草稿态确认制搬到岗位侧）：
 * 粘贴 JD → 点「✨ AI 提取技能」→ 候选**只进表单**（草稿在 `stores/job.ts`）→
 * 逐条删改 → 点「保存岗位」才发 `POST/PUT /jobs`。
 * **提取成功之后绝不自动提交**：这条线一旦越过去，AI 就只凭一次点击改了库。
 * 本组件里唯一发起写请求的函数是 `submit()`，而它只挂在 `@submit.prevent` 上。
 *
 * 表单与请求体的分工：本文件只持有原始输入（空串 = 没填），
 * 「空串转 null」「技能投影」「提交前的 422 自检」「提取结果的合并与计数文案」全在
 * `stores/job.ts`。因此这里没有一处直接调 API，也就没有第二份 fetch 逻辑。
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { isApiError } from '@/api'
import {
  DESCRIPTION_LIMIT,
  EMPLOYMENT_TYPE_OPTIONS,
  JOB_SKILL_TYPE_OPTIONS,
  SKILL_LIMIT,
  TEXT_LIMIT,
  createEmptyJobForm,
  createEmptySkillRow,
  extractCountLines,
  extractFailureText,
  extractMergeLines,
  jobToFormInput,
  useJobStore,
} from '@/stores/job'
import AutoTextarea from '@/components/AutoTextarea.vue'

/** `create` = 录入新岗位；`edit` = 改一条已有的。两条路由共用本组件。 */
const props = withDefaults(defineProps<{ mode?: 'create' | 'edit'; jobId?: string }>(), {
  mode: 'create',
  jobId: '',
})

const store = useJobStore()
const router = useRouter()

const isEdit = computed(() => props.mode === 'edit')

const form = reactive(createEmptyJobForm())
const formError = ref('')
/** 编辑态回填失败：读不到就不给保存，否则用户会把自己带进一条必败的 PUT。 */
const loadError = ref('')
const loadingDetail = ref(false)

/**
 * 提取反馈分两格而不是一个 feedback：失败要用 danger 色并留在屏幕上，
 * 「候选已进表单、还没保存」是进度陈述。混用一个 ref 会让成功文案盖掉上一条失败原因
 * （或反过来），而「Key 没配」这种问题必须一直看得见。
 */
const extractError = ref('')
const extractNotice = ref('')

/** 展开显示原文依据的行下标：默认收起，evidence 不挤占主视觉（议题 3.B）。 */
const openEvidence = ref<Set<number>>(new Set())

function toggleEvidence(index: number): void {
  const next = new Set(openEvidence.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  openEvidence.value = next
}

/** 技能行数到达后端上限就不再给「添加技能」，免得用户填完才发现被 422。 */
const canAddSkill = computed(() => form.skills.length < SKILL_LIMIT)

/** 已填了名字的行数：计数器展示用，空行不算「一条技能」。 */
const filledSkillCount = computed(
  () => form.skills.filter((row) => row.name.trim() !== '').length,
)

const descriptionLength = computed(() => form.description.length)

/** 提取按钮的启用条件：JD 里得有内容，而且不在任何在途请求里（保存中也算）。 */
const canExtract = computed(
  () => form.description.trim().length > 0 && !store.extracting && !store.submitting,
)

/** 核验计数：三条各自独立成句、0 的不出现（口径与理由见 `extractCountLines`）。 */
const countLines = computed(() => extractCountLines(store.extractDraft?.meta ?? null))
const mergeLines = computed(() => extractMergeLines(store.extractPlan))

/** 有得放弃 = 这次提取的结果还挂在草稿里（计数还在屏幕上）。 */
const canDiscard = computed(() => store.extractDraft !== null)

function addSkillRow(): void {
  if (!canAddSkill.value) return
  form.skills.push(createEmptySkillRow())
}

function removeSkillRow(index: number): void {
  // 至少留一行空行：区段整个消失会让「还能加技能」这件事变得看不见。
  if (form.skills.length <= 1) return
  form.skills.splice(index, 1)
  // 展开态按下标记，删行会让「依据」挂到隔壁行上；直接清空最简单，也不会说错话。
  openEvidence.value = new Set()
}

function describeError(error: unknown, fallback: string): string {
  if (isApiError(error) && error.message) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}

/** 快照成普通对象再交给 store：不把 reactive 代理传出去，也便于测试逐字段断言。 */
function snapshotForm() {
  return { ...form, skills: form.skills.map((row) => ({ ...row })) }
}

/**
 * 「✨ AI 提取技能」：只发提取请求、只改表单里的技能行，**不发任何写请求**。
 *
 * 失败**必须显式出现在屏幕上并按 code 给下一步**（`extractFailureText`）：
 * 把「Key 没配」显示成「这次没提取到技能」，用户会去改 JD 而不是去配 Key——
 * 静默降级在 ADR-010 里被禁止过一次，在岗位侧由 ADR-011 2.A 再禁止一次。
 */
async function runExtract(): Promise<void> {
  if (!canExtract.value) return
  extractError.value = ''
  extractNotice.value = ''

  let plan
  try {
    plan = await store.extractSkills(
      { description: form.description.trim(), title: form.title.trim() || null },
      form.skills.map((row) => ({ ...row })),
    )
  } catch (error) {
    extractError.value = extractFailureText(error)
    return
  }

  // `null` = 上一次请求还在途（store 里那道闸），本次点击什么都没做。
  if (!plan) return
  form.skills = plan.rows
  openEvidence.value = new Set()

  extractNotice.value = plan.added.length
    ? `已把 ${plan.added.length} 条候选技能放进下面的技能区。逐条核看、删掉不对的，点「保存岗位」才会保存。`
    : '这次没有新技能进入表单（原因见上面的计数说明）。可以调整 JD 原文后重试。'
}

/**
 * 「放弃本次提取」：撤掉刚加进来的候选（已被用户改过的行保留），**一个请求都不发**。
 * 与 ADR-010 §5 的「放弃：什么都不改」同一含义——不改的是数据库。
 */
function discardExtract(): void {
  const undo = store.discardExtraction(form.skills.map((row) => ({ ...row })))
  form.skills = undo.rows
  openEvidence.value = new Set()
  extractError.value = ''
  if (!undo.removed) {
    extractNotice.value = '本次提取的候选都已被你改动，只清掉了计数说明，表单保持不变。岗位没有被保存。'
    return
  }
  extractNotice.value = `已撤回 ${undo.removed} 条候选${
    undo.kept ? `，另有 ${undo.kept} 条已被你改动、保留在表单里` : ''
  }。岗位没有被保存。`
}

/** 唯一的写请求出口：`POST /jobs`（录入）或 `PUT /jobs/{id}`（编辑）。 */
async function submit(): Promise<void> {
  if (store.submitting || store.extracting) return
  if (isEdit.value && loadError.value) return
  formError.value = ''
  try {
    const saved = isEdit.value
      ? await store.updateJob(props.jobId, snapshotForm())
      : await store.createJob(snapshotForm())
    // 存完直接进详情页：用户最想知道的是「我刚填的那些真的进去了吗」。
    await router.push({ name: 'job-detail', params: { id: saved.id } })
  } catch (error) {
    formError.value = describeError(
      error,
      isEdit.value ? '保存修改失败，请检查后重试。' : '保存岗位失败，请检查后重试。',
    )
  }
}

async function cancel(): Promise<void> {
  await router.push(
    isEdit.value ? { name: 'job-detail', params: { id: props.jobId } } : { name: 'job-board' },
  )
}

/** 编辑态：先 GET 回填，再允许编辑与保存；读不到就禁掉保存按钮（见 `submit` 与模板）。 */
async function loadForEdit(): Promise<void> {
  if (!isEdit.value || !props.jobId) return
  loadingDetail.value = true
  loadError.value = ''
  try {
    const job = await store.loadDetail(props.jobId)
    if (!job) {
      loadError.value = '读不到这个岗位，无法编辑。'
      return
    }
    Object.assign(form, jobToFormInput(job))
    extractNotice.value =
      '这是已保存的岗位。点「保存修改」才会生效；离开本页不会自动保存，岗位状态也不受本次编辑影响。'
  } catch (error) {
    loadError.value =
      isApiError(error) && error.status === 404
        ? '这个岗位不存在、是别人的草稿，或已经在回收站里。'
        : describeError(error, '读取岗位失败，请稍后重试。')
  } finally {
    loadingDetail.value = false
  }
}

/**
 * 改 JD 就丢掉上一批的计数说明：那七个数字描述的是**上一次喂给模型的那份 JD**，
 * 挂在新 JD 下面是假话，而「核验计数可见」是本端点唯一的产品承诺。
 *
 * 表单里已经落下的技能行**不撤**——那是用户已经看过并认可的候选，撤掉等于丢他的劳动；
 * 想撤请点「放弃本次提取」。服务端会拿**本次提交的** `description` 重算每条 `origin`
 * （1.D），所以改了 JD 之后依据对不上，落库自然标成「未标注出处」，不会留下假徽标。
 */
watch(
  () => form.description,
  () => {
    if (!store.extractDraft) return
    store.clearExtract()
    extractNotice.value = ''
  },
)

/** 离开本页（含保存成功后的跳转）就把提取草稿清掉：草稿的生命周期等于这一次录入。 */
onBeforeUnmount(() => {
  store.clearExtract()
})

void loadForEdit()
</script>

<template>
  <main class="create-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">岗位库</p>
        <h1>{{ isEdit ? '编辑岗位' : '录入岗位' }}</h1>
        <p class="intro">
          <template v-if="isEdit">
            改动一条已保存的岗位。保存是显式动作：点下面的「保存修改」才生效，
            离开本页不会自动保存，岗位状态（草稿 / 已发布）也不受本次保存影响。
          </template>
          <template v-else>
            把招聘信息原文粘进来，可以用「AI 提取技能」从 JD 里取候选，再逐条删改。
            保存后是「草稿」，只有你自己看得到；确认无误后可在详情页发布到广场。
          </template>
        </p>
      </div>
      <RouterLink
        class="text-link"
        :to="isEdit ? { name: 'job-detail', params: { id: props.jobId } } : { name: 'job-board' }"
      >
        {{ isEdit ? '返回详情' : '返回列表' }}
      </RouterLink>
    </div>

    <p v-if="loadingDetail" class="feedback">正在读取岗位…</p>
    <p v-else-if="loadError" class="feedback feedback--error" role="alert">{{ loadError }}</p>

    <form class="job-form" @submit.prevent="submit">
      <section class="form-block">
        <h2 class="block-title">岗位基本信息</h2>
        <div class="field-grid">
          <label class="field">
            <span>企业名称 <em class="required">必填</em></span>
            <input
              v-model="form.company_name"
              type="text"
              :maxlength="TEXT_LIMIT.company"
              placeholder="如：示例科技"
            />
          </label>

          <label class="field">
            <span>岗位名称 <em class="required">必填</em></span>
            <input
              v-model="form.title"
              type="text"
              :maxlength="TEXT_LIMIT.title"
              placeholder="如：后端开发工程师"
            />
          </label>

          <label class="field">
            <span>工作地点</span>
            <input
              v-model="form.location"
              type="text"
              :maxlength="TEXT_LIMIT.location"
              placeholder="如：上海（选填）"
            />
          </label>

          <label class="field">
            <span>工作方式</span>
            <select v-model="form.employment_type">
              <option value="">未指定</option>
              <option v-for="[value, label] in EMPLOYMENT_TYPE_OPTIONS" :key="value" :value="value">
                {{ label }}
              </option>
            </select>
          </label>

          <label class="field">
            <span>经验要求</span>
            <input
              v-model="form.experience_required"
              type="text"
              :maxlength="TEXT_LIMIT.experience"
              placeholder="如：3 年以上后端经验（选填）"
            />
          </label>

          <label class="field">
            <span>截止时间</span>
            <input v-model="form.deadline" type="date" />
          </label>

          <label class="field">
            <span>薪资下限（元）</span>
            <input v-model="form.salary_min" type="text" inputmode="decimal" placeholder="如：3000" />
          </label>

          <label class="field">
            <span>薪资上限（元）</span>
            <input v-model="form.salary_max" type="text" inputmode="decimal" placeholder="如：6000" />
          </label>
        </div>
      </section>

      <section class="form-block">
        <h2 class="block-title">岗位描述（JD 原文）</h2>
        <label class="field">
          <span>原文 <em class="required">必填</em></span>
          <AutoTextarea
            v-model="form.description"
            :min-height="300"
            :maxlength="DESCRIPTION_LIMIT"
            placeholder="把招聘页的职责与要求原样粘进来"
            data-testid="jd-input"
          />
          <small class="field-hint">{{ descriptionLength }} / {{ DESCRIPTION_LIMIT }} 字</small>
        </label>

        <!-- 提取入口紧跟 JD：它读的就是这段原文；放在技能区里会让人以为候选来自已填好的那几行。 -->
        <div class="extract-row">
          <button
            class="row-button row-button--extract"
            type="button"
            data-testid="extract-button"
            :disabled="!canExtract"
            :aria-busy="store.extracting"
            @click="runExtract"
          >
            {{ store.extracting ? '提取中…' : 'AI 提取技能' }}
          </button>
          <button
            v-if="canDiscard"
            class="row-button row-button--ghost"
            type="button"
            data-testid="extract-discard"
            @click="discardExtract"
          >
            放弃本次提取
          </button>
          <span class="extract-hint">
            {{
              store.extracting
                ? 'AI 正在读这份 JD，最长等 45 秒。这一步只是给建议，不会保存，也不会改动已经存下来的岗位。'
                /* ui-copy:allow C2 唯一豁免：这一步花的是他自己的凭据与用量 */
                : '用的是你自己的 DeepSeek API Key；结果只填进下面的表单，你点「保存岗位」才会存下来。'
            }}
          </span>
        </div>

        <p
          v-if="extractError"
          class="feedback feedback--error"
          role="alert"
          data-testid="extract-error"
        >
          {{ extractError }}
        </p>
        <p v-else-if="extractNotice" class="feedback" role="status" data-testid="extract-notice">
          {{ extractNotice }}
        </p>
      </section>

      <section class="form-block">
        <div class="block-head">
          <h2 class="block-title">岗位要求的技能</h2>
          <span class="counter">{{ filledSkillCount }} / {{ SKILL_LIMIT }}</span>
        </div>

        <!-- 核验计数三条各占一行、各自成句：并成一个数会把「什么都不用做」混进「该怀疑规则」。 -->
        <ul
          v-if="countLines.length || mergeLines.length"
          class="extract-counts"
          data-testid="extract-counts"
        >
          <li v-for="line in countLines" :key="line" class="extract-count-line" data-testid="count-line">
            {{ line }}
          </li>
          <li v-for="line in mergeLines" :key="line" class="extract-count-line extract-count-line--merge">
            {{ line }}
          </li>
        </ul>

        <ul class="skill-list" data-testid="skill-list">
          <li v-for="(row, index) in form.skills" :key="index" class="skill-item">
            <div class="skill-row">
              <input
                v-model="row.name"
                class="skill-name"
                type="text"
                :maxlength="TEXT_LIMIT.skillName"
                placeholder="如：Python / 沟通能力强"
                :title="row.evidence_text ? `原文依据：${row.evidence_text}` : undefined"
              />
              <select v-model="row.type" class="skill-type">
                <option v-for="[value, label] in JOB_SKILL_TYPE_OPTIONS" :key="value" :value="value">
                  {{ label }}
                </option>
              </select>
              <!-- evidence 收在展开里：主视觉留给技能名与分类，要核对的人多点一下就能看到。 -->
              <button
                v-if="row.evidence_text"
                class="row-button row-button--tiny"
                type="button"
                data-testid="evidence-toggle"
                :aria-expanded="openEvidence.has(index)"
                @click="toggleEvidence(index)"
              >
                原文依据
              </button>
              <button
                class="row-button row-button--ghost"
                type="button"
                data-testid="skill-remove"
                :disabled="form.skills.length <= 1"
                @click="removeSkillRow(index)"
              >
                删除
              </button>
            </div>
            <p v-if="row.evidence_text && openEvidence.has(index)" class="skill-evidence">
              原文依据：{{ row.evidence_text }}
              <span v-if="row.confidence !== undefined">· AI 把握 {{ row.confidence }}</span>
              <span class="skill-origin">
                保存后这条能不能标成「可回溯原文」，取决于系统能否在本页原文里逐字找到它
              </span>
            </p>
          </li>
        </ul>

        <button class="row-button add-skill" type="button" :disabled="!canAddSkill" @click="addSkillRow">
          添加技能
        </button>
      </section>

      <p v-if="formError" class="feedback feedback--error" role="alert">{{ formError }}</p>

      <div class="form-actions">
        <button
          class="row-button row-button--ghost"
          type="button"
          data-testid="cancel-button"
          :disabled="store.submitting"
          @click="cancel"
        >
          {{ isEdit ? '不保存返回' : '取消' }}
        </button>
        <button
          class="row-button row-button--primary"
          type="submit"
          data-testid="save-job"
          :disabled="store.submitting || store.extracting || Boolean(loadError) || loadingDetail"
        >
          {{ store.submitting ? '保存中…' : isEdit ? '保存修改' : '保存岗位' }}
        </button>
      </div>
    </form>
  </main>
</template>

<style scoped>
.create-page { width: min(880px, 100%); margin: 0 auto; padding: 32px 48px 72px; }
.page-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.eyebrow { margin: 0 0 10px; color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .12em; }
h1 { margin: 0; color: var(--ui-ink); font-size: var(--text-4xl); font-weight: 650; }
.intro { margin: 8px 0 0; max-width: 62ch; color: var(--ui-ink-muted); font-size: var(--text-base); line-height: 1.6; }
.text-link { flex: 0 0 auto; color: var(--theme-primary); font-size: var(--text-base); text-decoration: none; white-space: nowrap; }
.text-link:hover { text-decoration: underline; }

.job-form { display: grid; gap: 18px; }
.form-block { border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 18px 20px; background: var(--ui-surface); }
.block-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.block-title { margin: 0 0 14px; color: var(--ui-ink); font-size: var(--text-md); font-weight: 650; }
.counter { color: var(--ui-ink-faint); font-size: var(--text-sm); }

/* 两列字段在宽屏上并排，窄屏自动回落单列：不写死断点像素，靠 `auto-fit` + 最小列宽。 */
.field-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px 18px; }
.field { display: grid; gap: 6px; }
.field > span { color: var(--ui-ink-muted); font-size: var(--text-sm); }
.required { margin-left: 4px; color: var(--ui-danger); font-size: var(--text-xs); font-style: normal; }
.field input,
.field select,
.field textarea { width: 100%; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 8px 10px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: var(--text-base); outline: 0; }
.field input,
.field select { height: 32px; padding: 0 10px; }
.field textarea { padding: 10px; line-height: 1.6; }
.field input:focus,
.field select:focus,
.field textarea:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
.field-hint { margin: 8px 0 0; color: var(--ui-ink-faint); font-size: var(--text-xs); line-height: 1.5; }

.extract-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 14px; }
.extract-hint { flex: 1 1 200px; color: var(--ui-ink-faint); font-size: var(--text-xs); line-height: 1.5; }

/* 核验计数：虚线框 + 每行一条，视觉上就是三条独立结论，不给「并成一个数」留余地。 */
.extract-counts { display: grid; gap: 4px; margin: 0 0 12px; padding: 10px 12px; border: 1px dashed var(--ui-border-strong); border-radius: var(--ui-radius); list-style: none; }
.extract-count-line { color: var(--ui-ink-muted); font-size: var(--text-sm); line-height: 1.6; }
/* 「跳过重名」是前端这一侧的合并（少这一步就变成保存时一条看不懂的 422），语气比核验计数轻一档。 */
.extract-count-line--merge { color: var(--ui-ink-faint); }

.skill-list { display: grid; gap: 8px; margin: 0 0 12px; padding: 0; list-style: none; }
.skill-item { display: grid; gap: 4px; }
.skill-row { display: flex; flex-wrap: wrap; gap: 8px; }
.skill-name { flex: 1 1 220px; min-width: 0; height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 10px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: var(--text-base); outline: 0; }
.skill-type { flex: 0 0 auto; min-width: 108px; height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 8px; color: var(--ui-ink-muted); background: var(--ui-surface); font: inherit; font-size: var(--text-sm); outline: 0; }
.skill-name:focus, .skill-type:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
.skill-evidence { margin: 0; border-left: 2px solid var(--theme-rule-soft); padding-left: 8px; color: var(--ui-ink-faint); font-size: var(--text-xs); line-height: 1.6; overflow-wrap: anywhere; }
.skill-origin { display: block; margin-top: 2px; color: var(--ui-ink-faint); font-size: var(--text-xs); }
.add-skill { width: 100%; }

.feedback { margin: 12px 0 0; color: var(--ui-ink-faint); font-size: var(--text-base); line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }
.form-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 10px; }

.row-button { height: 32px; border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 0 14px; color: var(--theme-primary-deep); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-sm); white-space: nowrap; }
.row-button:hover:not(:disabled) { background: var(--theme-bg-hover); }
.row-button:disabled { opacity: .5; cursor: not-allowed; }
.row-button--ghost { border-color: var(--ui-border-strong); color: var(--ui-ink-muted); }
.row-button--primary { border-color: var(--theme-primary); color: var(--theme-on-primary); background: var(--theme-primary); }
.row-button--primary:hover:not(:disabled) { border-color: var(--theme-primary-hover); background: var(--theme-primary-hover); }
/* AI 动作是增益而不是提交，底色用主题浅底；高度 32px、圆角 2px 仍走全局规格，不搞第二套按钮。 */
.row-button--extract { border-color: var(--theme-rule-soft); color: var(--theme-primary-strong); background: var(--theme-bg-light); font-weight: 600; }
.row-button--extract:hover:not(:disabled) { border-color: var(--theme-rule); background: var(--theme-bg-hover); }
.row-button--tiny { height: 32px; padding: 0 10px; font-size: var(--text-xs); color: var(--ui-ink-faint); border-color: var(--ui-border-strong); }

@media (max-width: 760px) {
  .create-page { padding: 20px 16px 48px; }
  .page-heading { flex-direction: column; }
  .skill-type { flex: 1 1 100%; }
  .extract-hint { flex: 1 1 100%; }
}
@media print {
  .create-page { display: none; }
}
</style>
