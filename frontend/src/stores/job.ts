/**
 * 岗位库 store（Phase 4 · B2，ADR-011 议题 3）。
 *
 * 岗位这条链路的**唯一**数据来源：四个视图都只读这里，不各自抄一份 fetch。
 * 端点封装在 `api/jobs.ts`，本文件负责三件端点层不该管的事：
 *
 * 1. **筛选与分页状态**（`keyword` / `status` / `includeExpired` + `page`）。
 *    放在 store 而不是组件里：从详情页返回列表页时要能接着看到原来那一屏，
 *    组件卸载重挂就丢掉筛选条件是最常见的「我刚才筛的呢」。
 * 2. **写侧载荷的投影**（`toWritePayload()`）。后端 `JobWriteRequest` 只接受技能的
 *    `name` 与 `type`；`origin` / `evidence_text` / `confidence` 与顶层的 `status`
 *    会被 Pydantic `extra="ignore"` **整键丢掉且不报错**（ADR-011 1.F 的 v6 补条）。
 *    「不报错」意味着前端若图省事把读模型原样发回去，用户会以为存进去了。
 *    所以投影只在这里做一次，`stores/__tests__/job.spec.ts` 钉一条反向断言。
 * 3. **「你已具备」的只读比对**（`loadMySkillNames()` + `matchesMySkill()`）。
 *    按 2.E：拿当前登录用户**默认简历**的技能名与 `job_skills.name` 做归一化等值比较
 *    （口径与后端同：`trim` → NFKC → 小写，见 `normalizeSkillName()`），命中就标一下。
 *    不写任何表、不产生匹配分数、不排序——人岗匹配仍在 AGENTS.md §3.2 的冻结清单里。
 *
 * 过期判定不在这里：后端 `JobSummary.is_expired` / `JobRead.is_expired` 已用**应用时钟**
 * 算好（2.G 明确禁止前端各写一份 `deadline < 今天`，两处实现必然漂移），前端只读那个布尔。
 *
 * 登出的清理：`reset()` 由 `AppTopBar.vue` 的 `handleLogout()` 在 `auth.logout()` **之前**
 * 调用，与 `resume.reset()` 并列——「先清业务数据再清登录态」这条不变量涵盖岗位数据。
 * 每个 `load*()` 在发请求前清空旧数据只是**并发/换账号时的补帧缓解**，不替代这条不变量。
 */

import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'

import { jobsApi, profilesApi } from '@/api'
import type { jobsApi as JobsContract } from '@/api'
import { useAuthStore } from '@/stores/auth'

type JobSummary = JobsContract.JobSummary
type JobRead = JobsContract.JobRead
type JobStatus = JobsContract.JobStatus
type JobSkillType = JobsContract.JobSkillType
type EmploymentType = JobsContract.EmploymentType

/* -------------------------------- 展示文案 -------------------------------- */

/**
 * 工作方式的中文标签，与后端 `ck_jobs_employment_type` 的五个值一一对应。
 *
 * 文案放在 store 而不是各视图里：列表卡片、详情页、录入页下拉框都要同一套名字，
 * 三处各写一份就会漂移（同一个枚举在屏幕上出现两个名字，用户会以为是两个东西）。
 */
export const EMPLOYMENT_TYPE_LABELS: Record<EmploymentType, string> = {
  internship: '实习',
  full_time: '全职',
  part_time: '兼职',
  contract: '合同制',
  other: '其它',
}

/** 录入页下拉框的渲染顺序（即产品顺序，与后端枚举声明顺序一致）。 */
export const EMPLOYMENT_TYPE_OPTIONS = Object.entries(EMPLOYMENT_TYPE_LABELS) as [
  EmploymentType,
  string,
][]

/**
 * 技能分类标签（`ck_job_skills_type`）。
 *
 * `domain` 译作「领域知识」：`schema-draft.sql` 里这三档的区分点是「通用能力 /
 * 岗位软素质 / 行业特定」，直译成「领域」在屏幕上看不出差别。
 */
export const JOB_SKILL_TYPE_LABELS: Record<JobSkillType, string> = {
  hard: '硬技能',
  soft: '软技能',
  domain: '领域知识',
}

export const JOB_SKILL_TYPE_OPTIONS: [JobSkillType | '', string][] = [
  ['', '未分类'],
  ['hard', JOB_SKILL_TYPE_LABELS.hard],
  ['soft', JOB_SKILL_TYPE_LABELS.soft],
  ['domain', JOB_SKILL_TYPE_LABELS.domain],
]

/**
 * `origin` 徽标的两态固定文案（议题 3.B，v7 裁定）。
 *
 * **不得出现「AI 提取」字样**：这一列只证明「这句话在 JD 原文里出现过」，
 * 不证明「这句话是谁写的」；本轮所有条目都是手填 → 恒为 `unverified`。
 * 未知值（将来新增）回落到「未标注出处」，宁少说不多说。
 */
export const JOB_ORIGIN_BADGES: Record<JobsContract.JobSkillOrigin, string> = {
  jd_verifiable: '可回溯原文',
  unverified: '未标注出处',
}

/** 岗位状态的中文标签（列表筛选用；徽标只在详情页出现）。 */
export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  published: '已发布',
  draft: '草稿',
}

/* -------------------------------- 表单约束 -------------------------------- */

/** JD 输入上限，与后端 `MAX_DESCRIPTION_LENGTH` 同源（议题 3.B：录入页用整页正是要容纳它）。 */
export const DESCRIPTION_LIMIT = jobsApi.MAX_JOB_DESCRIPTION_LENGTH

/** 技能条数上限，与后端 `MAX_SKILLS_PER_JOB` 同源。 */
export const SKILL_LIMIT = jobsApi.MAX_JOB_SKILLS

/** 各文本字段长度上限，直接取端点层从后端抄来的那一组数字。 */
export const TEXT_LIMIT = jobsApi.JOB_TEXT_LIMIT

/** 首屏一次拉 50 条（议题 3.C）。 */
export const LIST_PAGE_SIZE = jobsApi.JOB_LIST_PAGE_SIZE

/**
 * 录入表单的原始状态：**空串表示「没填」**。
 *
 * `<input>` / `<select>` 不接受 `null`，所以视图里存空串，
 * 由 `toWritePayload()` 在提交那一刻把空串转成 `null`（交给后端存 NULL）。
 * 与 `stores/resume.ts` 的 `ExperienceDraft.end_date` 是同一套约定。
 */
export interface JobFormInput {
  company_name: string
  title: string
  location: string
  employment_type: EmploymentType | ''
  description: string
  salary_min: string
  salary_max: string
  experience_required: string
  /** `YYYY-MM-DD`，空串 = 未填（视为长期有效）。 */
  deadline: string
  skills: JobSkillFormRow[]
}

/** 表单里的一行技能：只有 `name` 与 `type` 两格，**没有** AI 语义字段的输入位。 */
export interface JobSkillFormRow {
  name: string
  type: JobSkillType | ''
}

/** 一行都还没填时的空行；录入页至少留一行，区段才不会消失。 */
export function createEmptySkillRow(): JobSkillFormRow {
  return { name: '', type: '' }
}

/** 打开录入页时的空白表单。 */
export function createEmptyJobForm(): JobFormInput {
  return {
    company_name: '',
    title: '',
    location: '',
    employment_type: '',
    description: '',
    salary_min: '',
    salary_max: '',
    experience_required: '',
    deadline: '',
    skills: [createEmptySkillRow()],
  }
}

/* -------------------------------- 纯函数 ---------------------------------- */

/**
 * 技能名的归一化键：`trim` → NFKC（全角→半角、兼容字符归一）→ 小写。
 *
 * **口径对齐后端**：判定「同一个技能」用的是
 * `unicodedata.normalize("NFKC", name).casefold()`（`app/schemas/job.py` 的
 * `JobSkillInput.dedup_key`，ADR-011 §2.D.1 定义得更完整），列的排序规则
 * `utf8mb4_0900_ai_ci` 同样是全半角、大小写都不敏感。B2 只做 `trim` + 小写，
 * 后果在两个用途上都看得见：
 * - 「你已具备」（2.E 只读比对）：用户档案里写成 `Ｐython` 就标不上；
 * - 录入页重名自检（`validateJobForm`）：前端放过、后端 422，用户看到的是
 *   「我明明没重复却被拒了」。
 * 两端同口径才能既不漏标也不漏拦。
 *
 * 已知差异（本轮接受，不写特例）：JS 的 `toLowerCase()` 与 Python 的 `casefold()`
 * 在极少数语言上不等价（`ß` → JS `ß` / Python `ss`；`İ` → JS `i̇` / Python `i̇` 亦不同）。
 * 本项目技能名是中英混合（Python / 沟通能力强 / C++），不涉及这些字符；
 * 若将来出现土耳其语等企业名再单开 ADR。零新增依赖（不引 `unorm`/`Intl` 之类）。
 */
export function normalizeSkillName(value: string): string {
  return value.trim().normalize('NFKC').toLowerCase()
}

/** 已填了名字的技能行（空行既不提交也不参与重名判断）。 */
function meaningfulSkills(rows: JobSkillFormRow[]): JobSkillFormRow[] {
  return rows.filter((row) => row.name.trim() !== '')
}

/** 宽松的数值判断：只拦「填了但不是数」的输入，格式化交给后端 Decimal。 */
function isNumeric(value: string): boolean {
  return /^\d+(\.\d{1,2})?$/.test(value.trim())
}

/**
 * 把表单状态投影成后端接受的请求体。
 *
 * 两条硬规则：
 * - 技能只取 `name` / `type` 两个键（`origin` / `evidence_text` / `confidence` 一律不发）；
 * - 顶层不写 `status`：发布是 `PUT /jobs/{id}/publish` 的独立动作，不是创建的副作用。
 */
export function toWritePayload(form: JobFormInput): JobsContract.JobWriteRequest {
  return {
    company_name: form.company_name.trim(),
    title: form.title.trim(),
    location: form.location.trim() || null,
    employment_type: form.employment_type || null,
    description: form.description.trim(),
    salary_min: form.salary_min.trim() || null,
    salary_max: form.salary_max.trim() || null,
    experience_required: form.experience_required.trim() || null,
    deadline: form.deadline || null,
    skills: meaningfulSkills(form.skills).map((row) => ({
      name: row.name.trim(),
      // 空串转 null：后端 `type: SkillType | None`，传 `''` 会 422。
      type: row.type || null,
    })),
  }
}

/**
 * 提交前的契约自检：只拦**后端一定会 422** 的输入，返回 null 表示可以提交。
 *
 * 与 `stores/resume.ts::validateForSave()` 同一个理由：与其发出去再翻译一堆 `loc` 路径，
 * 不如在点「保存岗位」时就给一句人话。`deadline` 允许是过去的日期（后端刻意允许，
 * 「补录一个已结束的岗位」是合法需求），所以这里不校验它。
 */
export function validateJobForm(form: JobFormInput): string | null {
  const company = form.company_name.trim()
  if (!company) return '请填写企业名称。'
  if (company.length > TEXT_LIMIT.company) return `企业名称最长 ${TEXT_LIMIT.company} 个字符。`

  const title = form.title.trim()
  if (!title) return '请填写岗位名称。'
  if (title.length > TEXT_LIMIT.title) return `岗位名称最长 ${TEXT_LIMIT.title} 个字符。`

  const description = form.description.trim()
  if (!description) return '请粘贴岗位描述（JD 原文）。'
  if (description.length > DESCRIPTION_LIMIT) {
    return `岗位描述最长 ${DESCRIPTION_LIMIT} 个字符，当前 ${description.length} 个。`
  }

  if (form.location.trim().length > TEXT_LIMIT.location) {
    return `工作地点最长 ${TEXT_LIMIT.location} 个字符。`
  }
  if (form.experience_required.trim().length > TEXT_LIMIT.experience) {
    return `经验要求最长 ${TEXT_LIMIT.experience} 个字符。`
  }

  const salaryMin = form.salary_min.trim()
  const salaryMax = form.salary_max.trim()
  if (salaryMin && !isNumeric(salaryMin)) return '薪资下限只能填数字，最多两位小数。'
  if (salaryMax && !isNumeric(salaryMax)) return '薪资上限只能填数字，最多两位小数。'
  // 与后端 `ck_jobs_salary_range` 同一条规则：两端都填了才比较。
  if (salaryMin && salaryMax && Number(salaryMin) > Number(salaryMax)) {
    return '薪资下限不能大于薪资上限。'
  }

  const skills = meaningfulSkills(form.skills)
  if (skills.length > SKILL_LIMIT) return `技能最多 ${SKILL_LIMIT} 条。`
  const names = skills.map((row) => row.name.trim())
  if (names.some((name) => name.length > TEXT_LIMIT.skillName)) {
    return `技能名称最长 ${TEXT_LIMIT.skillName} 个字符。`
  }
  const keys = names.map(normalizeSkillName)
  if (new Set(keys).size !== keys.length) return '技能名称不能重复，请先合并同名技能。'

  return null
}

/* --------------------------------- store ---------------------------------- */

export const useJobStore = defineStore('job', () => {
  /* 列表 */
  const jobs = ref<JobSummary[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(LIST_PAGE_SIZE)
  const loading = ref(false)

  /**
   * 筛选条件。
   *
   * `status` 的默认值是 `published`（后端 `DEFAULT_LIST_STATUS`）：
   * 首屏看到的是「广场」，自己的草稿要显式切过去才出现（1.F #1）。
   */
  const filter = reactive({
    keyword: '',
    status: 'published' as JobStatus,
    includeExpired: false,
  })

  /* 详情 */
  const detail = ref<JobRead | null>(null)
  const detailLoading = ref(false)

  /* 回收站 */
  const trash = ref<JobSummary[]>([])
  const trashLoading = ref(false)

  /** 「我」的技能名（默认简历的技能），只用于详情页的只读比对。 */
  const mySkillNames = ref<string[]>([])
  const mySkillNamesLoading = ref(false)

  /** 新建 / 发布 / 下架 / 删除等写操作进行中，视图据此禁用按钮。 */
  const submitting = ref(false)

  const hasPrevPage = computed(() => page.value > 1)
  const hasNextPage = computed(() => page.value * pageSize.value < total.value)
  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

  /** 归一化后的「我」的技能名集合：比对只做一次 O(n) 构建，不在每次渲染里重建。 */
  const mySkillKeys = computed(() => new Set(mySkillNames.value.map(normalizeSkillName)))

  /** 当前登录用户是不是这条岗位的创建者（后端只有创建者能改/删/发布）。 */
  function isCreatorOf(job: JobSummary | JobRead | null): boolean {
    if (!job) return false
    const userId = useAuthStore().user?.id
    return Boolean(userId) && userId === job.created_by_user_id
  }

  /** 详情页用：这条技能名是否命中「我」的技能。 */
  function matchesMySkill(skillName: string): boolean {
    return mySkillKeys.value.has(normalizeSkillName(skillName))
  }

  /* ------------------------------- 列表与筛选 ------------------------------ */

  /**
   * 拉列表。
   *
   * 发请求前先把旧数据清空：换账号 / 换筛选条件时，
   * 「先渲染上一个人的岗位再刷成新结果」比多一帧空白更容易被当成 bug。
   */
  async function loadJobs(targetPage = 1): Promise<void> {
    loading.value = true
    jobs.value = []
    try {
      const result = await jobsApi.listJobs({
        status: filter.status,
        keyword: filter.keyword,
        includeExpired: filter.includeExpired,
        page: targetPage,
        pageSize: pageSize.value,
      })
      jobs.value = result.jobs
      total.value = result.total
      page.value = result.page
    } finally {
      loading.value = false
    }
  }

  /** 条件变了就回到第 1 页：停在第 3 页改关键词，用户看到的必然是「空的第 3 页」。 */
  function applyFilter(): Promise<void> {
    return loadJobs(1)
  }

  function setStatusFilter(status: JobStatus): Promise<void> {
    filter.status = status
    return applyFilter()
  }

  function setKeyword(keyword: string): Promise<void> {
    filter.keyword = keyword
    return applyFilter()
  }

  function toggleIncludeExpired(): Promise<void> {
    filter.includeExpired = !filter.includeExpired
    return applyFilter()
  }

  function goPage(targetPage: number): Promise<void> {
    if (targetPage < 1 || targetPage > totalPages.value) return Promise.resolve()
    return loadJobs(targetPage)
  }

  /* --------------------------------- 详情 --------------------------------- */

  async function loadDetail(jobId: string): Promise<JobRead | null> {
    detailLoading.value = true
    detail.value = null
    try {
      detail.value = await jobsApi.getJob(jobId)
      return detail.value
    } finally {
      detailLoading.value = false
    }
  }

  /* --------------------------------- 写入 --------------------------------- */

  /**
   * 录入一条岗位：先跑契约自检，不合格**不发请求**。
   *
   * 成功后立刻刷新列表（新岗位一定在 `draft` 那一屏，不在默认的广场屏），
   * 返回详情给调用方决定跳到哪——路由跳转留在视图里，store 不依赖 router。
   */
  async function createJob(form: JobFormInput): Promise<JobRead> {
    const problem = validateJobForm(form)
    if (problem) throw new Error(problem)

    submitting.value = true
    try {
      const created = await jobsApi.createJob(toWritePayload(form))
      // 列表刷新失败不影响「岗位已创建」这个事实，但必须让调用方看得见异常，
      // 所以只吞「刷新」这一半。
      try {
        await loadJobs(1)
      } catch {
        // 忽略：下一次进列表会重新拉。
      }
      return created
    } finally {
      submitting.value = false
    }
  }

  /** 发布（仅创建者）：同步详情页，列表由调用方按需刷新。 */
  async function publish(jobId: string): Promise<JobRead> {
    submitting.value = true
    try {
      const updated = await jobsApi.publishJob(jobId)
      if (detail.value?.id === jobId) detail.value = updated
      return updated
    } finally {
      submitting.value = false
    }
  }

  /** 下架回草稿（仅创建者）。 */
  async function unpublish(jobId: string): Promise<JobRead> {
    submitting.value = true
    try {
      const updated = await jobsApi.unpublishJob(jobId)
      if (detail.value?.id === jobId) detail.value = updated
      return updated
    } finally {
      submitting.value = false
    }
  }

  /**
   * 软删除（仅创建者）：删完刷新列表，删的是当前详情页时清掉详情。
   *
   * 刻意**不刷新详情**：后端 `get_readable` 排除了软删行，删完再去 GET 必然 404。
   */
  async function remove(jobId: string): Promise<void> {
    submitting.value = true
    try {
      await jobsApi.deleteJob(jobId)
    } finally {
      submitting.value = false
      if (detail.value?.id === jobId) detail.value = null
    }
    await loadJobs(1)
  }

  /* -------------------------------- 回收站 -------------------------------- */

  async function loadTrash(): Promise<void> {
    trashLoading.value = true
    trash.value = []
    try {
      trash.value = await jobsApi.listJobTrash()
    } finally {
      trashLoading.value = false
    }
  }

  /** 恢复：回收站与列表都要刷新——恢复出来的岗位重新回到广场/草稿那一屏。 */
  async function restore(jobId: string): Promise<JobRead> {
    const restored = await jobsApi.restoreJob(jobId)
    await loadTrash()
    await loadJobs(page.value)
    return restored
  }

  /* ---------------------------- 「你已具备」比对 ---------------------------- */

  /**
   * 拉当前登录用户的技能名（2.E 的只读比对）。
   *
   * 走 `GET /profiles/me`（默认简历）而不是复用编辑器的 Pinia 草稿：
   * 用户完全可能直接从 `/jobs/:id` 进来，那一刻编辑器的 `draft` 还是空白骨架，
   * 拿它比对会**静默**一条都标不上。这里多发一次只读 GET，换「结果与来源都确定」。
   * 还没有任何档案时后端返回 `{ profile: null }`，翻成空数组而不是报错。
   */
  async function loadMySkillNames(): Promise<string[]> {
    mySkillNamesLoading.value = true
    try {
      const profile = await profilesApi.getMyProfile()
      mySkillNames.value = (profile?.skills ?? []).map((skill) => skill.name)
      return mySkillNames.value
    } finally {
      mySkillNamesLoading.value = false
    }
  }

  /** 登出时清空，避免下一个账号短暂看到上一个人的岗位。 */
  function reset(): void {
    jobs.value = []
    total.value = 0
    page.value = 1
    detail.value = null
    trash.value = []
    mySkillNames.value = []
    filter.keyword = ''
    filter.status = 'published'
    filter.includeExpired = false
  }

  return {
    jobs,
    total,
    page,
    pageSize,
    loading,
    filter,
    hasPrevPage,
    hasNextPage,
    totalPages,
    loadJobs,
    applyFilter,
    setStatusFilter,
    setKeyword,
    toggleIncludeExpired,
    goPage,
    detail,
    detailLoading,
    loadDetail,
    trash,
    trashLoading,
    loadTrash,
    restore,
    submitting,
    createJob,
    publish,
    unpublish,
    remove,
    mySkillNames,
    mySkillNamesLoading,
    loadMySkillNames,
    matchesMySkill,
    isCreatorOf,
    reset,
  }
})
