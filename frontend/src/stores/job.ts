/**
 * 岗位库 store（Phase 4 · B2，ADR-011 议题 3）。
 *
 * 岗位这条链路的**唯一**数据来源：四个视图都只读这里，不各自抄一份 fetch。
 * 端点封装在 `api/jobs.ts`，本文件负责三件端点层不该管的事：
 *
 * 1. **筛选与分页状态**（`keyword` / `status` / `includeExpired` + `page`）。
 *    放在 store 而不是组件里：从详情页返回列表页时要能接着看到原来那一屏，
 *    组件卸载重挂就丢掉筛选条件是最常见的「我刚才筛的呢」。
 * 2. **写侧载荷的投影**（`toWritePayload()`）。后端 `JobWriteRequest` 的 `skills[]` 只接受
 *    `name` / `type` / `evidence_text` / `confidence` 四格；`origin` 与三列提取溯源戳
 *    （`extract_model` / `extract_prompt_version` / `extracted_at`）以及顶层 `status`
 *    会被 Pydantic `extra="ignore"` **整键丢掉且不报错**（ADR-011 1.D / 1.F v9 第 ①② 条）。
 *    「不报错」意味着前端若图省事把读模型原样发回去，用户会以为存进去了，而 `origin`
 *    其实由服务端按本次提交重算。所以投影只在这里做一次（逐键挑，不是逐键删），
 *    `stores/__tests__/job.spec.ts` 钉一条反向断言。
 * 3. **AI 提取结果的草稿态**（`extractSkills()` + `mergeExtractedInto()`）。
 *    提取是纯计算（2.A），结果只存在这个 store 里，表单行由视图承接，
 *    **库里要到用户点「保存岗位」才被动**；`reset()` 连这份草稿一起清掉。
 * 4. **「你已具备」的只读比对**（`loadMySkillNames()` + `matchesMySkill()`）。
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

import { jobsApi, profilesApi, isApiError } from '@/api'
import type { jobsApi as JobsContract } from '@/api'
import { useAuthStore } from '@/stores/auth'

type JobSummary = JobsContract.JobSummary
type JobRead = JobsContract.JobRead
type JobStatus = JobsContract.JobStatus
type JobSkillType = JobsContract.JobSkillType
type EmploymentType = JobsContract.EmploymentType
type ExtractSkillsResult = JobsContract.ExtractSkillsResult

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

/** 表单里的一行技能。
 *
 * `name` / `type` 是用户直接编辑的两格；`evidence_text` / `confidence` 是
 * **跟着 AI 结果一起带进来的附注**（ADR-011 v9 放开写回路径）：表单不给它们输入位、
 * 但提交时要把它们原样发回去，否则详情页的「可回溯原文」徽标与 §2.D.1 的核验无处落地。
 * 它们只可能由提取写入，手动新增的行永远是 `undefined`。
 *
 * **没有 `origin`**：那是服务端的派生结论，表单里出现它只会让人以为可以手填出处。
 */
export interface JobSkillFormRow {
  name: string
  type: JobSkillType | ''
  /** JD 原文片段（≤60 字），来自提取结果；手动行为 `undefined`。 */
  evidence_text?: string
  /** 模型自报的把握；提取响应里是数字，编辑态回填时是后端读回的字符串，两种都原样带。 */
  confidence?: string | number
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
 * 三条硬规则：
 * - 技能**逐键挑**而不是原样转发：只发 `name` / `type` / `evidence_text` / `confidence`，
 *   `origin` 永远不发（服务端按 §2.D.1 现场派生，客户端声称的出处没有分量）；
 * - 没有值的两格**不写进键集**：后端 `evidence_text` / `confidence` 的默认值就是 `None`，
 *   手动行发 `null` 与不发是同一件事，但不发能让「这条依据是 AI 给的还是没给」在
 *   测试里一眼可辨（也避免把空串发过去——空串会先被后端的 `normalize_evidence_text` 折成 NULL）。
 * - 顶层不写 `status`：发布是 `PUT /jobs/{id}/publish` 的独立动作，不是创建或编辑的副作用。
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
    skills: meaningfulSkills(form.skills).map((row) => {
      const evidence = row.evidence_text?.trim()
      const skill: JobsContract.JobSkillInput = {
        name: row.name.trim(),
        // 空串转 null：后端 `type: SkillType | None`，传 `''` 会 422。
        type: row.type || null,
      }
      if (evidence) skill.evidence_text = evidence
      if (row.confidence !== undefined && row.confidence !== null && row.confidence !== '') {
        skill.confidence = row.confidence
      }
      return skill
    }),
  }
}

/**
 * 把详情读模型摊平成表单初值（`/jobs/:id/edit` 的回填）。
 *
 * 与 `toWritePayload()` 是同一枚硬币的两面，所以放在同一个文件里：
 * - **`origin` 不落进表单行**：它是服务端的派生结论，不是用户能填的东西；
 *   带进表单只会在下一次 PUT 时变成一个「看起来是我选的出处」的假事实。
 * - `id` / `status` / 三列溯源戳 / `created_*` 同理丢掉：请求体里没有这些键。
 * - `evidence_text` / `confidence` 反过来**要带回来**：编辑态把技能删到只剩名字再保存，
 *   等于把上一次提取的核验锚点清空，徽标会从「可回溯原文」掉成「未标注出处」，
 *   而用户在屏幕上什么提示都没拿到。
 * - `salary_*` 读回来是字符串（`"8000.00"`），原样填进 `<input>`，不做数值转换。
 * - 技能为空时补一行空行：与 `createEmptyJobForm()` 同一约定，区段不能整个消失。
 */
export function jobToFormInput(job: JobRead): JobFormInput {
  return {
    company_name: job.company_name,
    title: job.title,
    location: job.location ?? '',
    employment_type: (job.employment_type as EmploymentType) ?? '',
    description: job.description,
    salary_min: job.salary_min ?? '',
    salary_max: job.salary_max ?? '',
    experience_required: job.experience_required ?? '',
    // `deadline` 是 `YYYY-MM-DD`，与 `<input type="date">` 的取值格式一致，不需要换算。
    deadline: job.deadline ?? '',
    skills: job.skills.length
      ? job.skills.map((skill) => {
          const row: JobSkillFormRow = {
            name: skill.name,
            type: (skill.type as JobSkillType) ?? '',
          }
          if (skill.evidence_text) row.evidence_text = skill.evidence_text
          if (skill.confidence !== null) row.confidence = skill.confidence
          return row
        })
      : [createEmptySkillRow()],
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

/* ------------------------------ AI 提取（草稿态） --------------------------- */

/**
 * 一次「把提取结果合进表单」的产出。
 *
 * 分成四个数而不是一个，是因为用户要做 decisions 各不相同：
 * 重名跳过 → 什么都不用做（库里本来也只允许一条）；超上限没加入 → 得先删掉几行才能继续加。
 * 把两者并成「跳过了若干条」会让人以为表单坏了。
 */
export interface ExtractMergePlan {
  /** 合并后的完整技能行数组，视图直接整体替换 `form.skills`。 */
  rows: JobSkillFormRow[]
  /**
   * 本次真正加进表单的那几行（同一批对象引用）。
   * 「放弃本次提取」靠它判断哪些行可以安全撤掉——只撤内容还没被改过的。
   */
  added: JobSkillFormRow[]
  /** 与已有技能行（或本批更早的候选）重名而被跳过的条数。 */
  skippedDuplicate: number
  /** 因为表单已经到了 15 条上限而没能加入的条数（后端 `max_length` 会 422）。 */
  skippedLimit: number
  /** 候选里名字为空、无法成为一行的条数（后端 `name` 有 `min_length=1`）。 */
  skippedBlank: number
}

/**
 * 把提取结果合进技能行数组。**纯函数，不发请求、不落库。**
 *
 * 判重键必须用 `normalizeSkillName()`：它与后端 `skill_name_key()`
 * （NFKC + casefold，`app/schemas/job.py`）同口径，而 `JobWriteRequest.validate_unique_skill_names`
 * 用的就是那个键。这里换成别的口径（比如裸字符串比较）的后果不是难看，而是
 * **把一个注定 422 的 payload 交给用户**——他做完全部删改、点保存，才看到一条
 * 「技能名称重复」，而屏幕上两条看起来长得不一样（`Python` / `python`）。
 * 这个函数是 ADR-011 §2.D.2 那条教训在前端的落点。
 *
 * 两条刻意的取舍：
 * - 重名的候选**跳过而不是覆盖**：已有行可能是用户手写的、还带着他自己选的 `type`，
 *   用 AI 的版本盖掉等于改了他写的东西；
 * - 到达上限就停手并计数：静默丢候选是本项目红线（禁止静默降级），
 *   多塞进表单则会把「保存」变成一次必败的 422。
 */
export function mergeExtractedSkills(
  rows: JobSkillFormRow[],
  skills: JobsContract.ExtractedSkill[],
  limit: number = SKILL_LIMIT,
): ExtractMergePlan {
  const merged = rows.map((row) => ({ ...row }))
  // 已有行先进键集：本批候选之间也要判重，所以每加一条就把它自己的键也放进去。
  const taken = new Set(merged.map((row) => normalizeSkillName(row.name)).filter((key) => key !== ''))

  const added: JobSkillFormRow[] = []
  let skippedDuplicate = 0
  let skippedLimit = 0
  let skippedBlank = 0

  for (const skill of skills) {
    const name = skill.name.trim()
    const key = normalizeSkillName(name)
    if (!key) {
      skippedBlank += 1
      continue
    }
    if (taken.has(key)) {
      skippedDuplicate += 1
      continue
    }
    if (merged.length >= limit) {
      skippedLimit += 1
      continue
    }
    const row: JobSkillFormRow = { name, type: skill.type ?? '' }
    // `evidence` → `evidence_text`：字段名在两侧就是不同的（2.C 与修订清单 #2），
    // 这一行改名是契约的一部分，不是笔误。
    const evidence = skill.evidence?.trim()
    if (evidence) row.evidence_text = evidence
    if (skill.confidence !== null && skill.confidence !== undefined) {
      row.confidence = skill.confidence
    }
    merged.push(row)
    added.push(row)
    taken.add(key)
  }

  return { rows: merged, added, skippedDuplicate, skippedLimit, skippedBlank }
}

/** 两行是否内容完全一致（用于判断「这一行还没被用户动过」）。 */
function isSameSkillRow(row: JobSkillFormRow, added: JobSkillFormRow): boolean {
  return (
    row.name === added.name &&
    row.type === added.type &&
    (row.evidence_text ?? '') === (added.evidence_text ?? '') &&
    (row.confidence ?? '') === (added.confidence ?? '')
  )
}

/**
 * 撤掉上一次提取加进表单的行。
 *
 * **只撤内容还一字未动的那些**：用户可能已经把某条候选改了名或换了分类，
 * 那种情况下把它删掉就是丢掉他的劳动。改过的行留着，并在返回值里报数，
 * 让屏幕上那句「几条已被你改动、保留在表单里」成为可见的事实而不是猜测。
 */
export function undoExtractMerge(
  rows: JobSkillFormRow[],
  added: JobSkillFormRow[],
): { rows: JobSkillFormRow[]; removed: number; kept: number } {
  const removable = added.filter((candidate) =>
    rows.some((row) => isSameSkillRow(row, candidate)),
  )
  const keptRows = rows.filter(
    (row) => !removable.some((candidate) => isSameSkillRow(row, candidate)),
  )
  return {
    rows: keptRows.length ? keptRows : [createEmptySkillRow()],
    removed: removable.length,
    kept: added.length - removable.length,
  }
}

/**
 * 核验计数的文案（2.D「核验计数必须可见」——本端点最重要的一条产品承诺）。
 *
 * 三条各自独立成句、且只在该项 > 0（`truncated` 为真）时才出现，两条硬规矩：
 *
 * 1. `dropped_count` 与 `deduped_count` **绝不并成一个数**：前者意味着
 *    「该怀疑核验规则，或者换一份 JD 再试」，后者意味着「什么都不用做」。
 *    并起来会把那句「什么都不用做」混进那句「该怀疑规则」。
 * 2. **不把三个数相加去凑 `received_count`**：算术上
 *    `received_count = dropped + deduped + 返回条数 + 被 15 条封顶切掉的条数`，
 *    最后那格后端**没有对应字段**（已定不加第 8 个计数），所以 `truncated` 为真时
 *    前三项之和必然小于 `received_count`。前端一旦显示「共 X 条 = …」就会当场对不上账。
 */
export function extractCountLines(meta: JobsContract.ExtractSkillsMeta | null): string[] {
  if (!meta) return []
  const lines: string[] = []
  if (meta.dropped_count > 0) {
    lines.push(
      `AI 给出 ${meta.received_count} 条，其中 ${meta.dropped_count} 条无法在 JD 原文中找到依据，已剔除`,
    )
  }
  if (meta.deduped_count > 0) {
    lines.push(`另有 ${meta.deduped_count} 条重名已合并`)
  }
  if (meta.truncated) {
    lines.push(jobsApi.EXTRACT_TRUNCATED_HINT)
  }
  return lines
}

/** 合并结果的文案：只在该项 > 0 时出现。 */
export function extractMergeLines(plan: ExtractMergePlan | null): string[] {
  if (!plan) return []
  const lines: string[] = []
  if (plan.skippedDuplicate > 0) {
    lines.push(`已跳过 ${plan.skippedDuplicate} 条与现有技能重名`)
  }
  if (plan.skippedLimit > 0) {
    lines.push(`表单技能已达 ${SKILL_LIMIT} 条上限，本次未加入 ${plan.skippedLimit} 条`)
  }
  if (plan.skippedBlank > 0) {
    lines.push(`${plan.skippedBlank} 条候选没有技能名，无法作为一行，已跳过`)
  }
  return lines
}

/**
 * 提取失败的分支文案：同一个码给的**下一步**不一样，所以按 code 分支。
 *
 * 三条约束：
 * - **禁止静默降级**（AGENTS.md §7、ADR-010 §5）：任何失败都必须显式出现在屏幕上，
 *   而且表单与库里都保持原样——本端点纯计算，失败只损失这次候选。
 * - 第 7 情形（输出被服务端 token 上限切断）与普通 502 都是 `AI_SERVICE_UNAVAILABLE`，
 *   分流判据是 `ApiError.finishReason`（取自错误信封**外层** `meta.finish_reason`），
 *   **不是文案**：服务端措辞属于可变量，拿它做分支等于把功能挂在翻译上。
 * - `AI_KEY_INVALID` 是 401，但它是「你的 DeepSeek Key 有问题」，
 *   与登录态无关；`client.ts` 已经把它挡在令牌刷新之外，这里只负责说清去哪儿修。
 */
export function extractFailureText(error: unknown): string {
  if (!isApiError(error)) return '提取失败，请稍后重试。岗位不会被改动。'
  switch (error.code) {
    case 'AI_KEY_MISSING':
      return '请先在右上角「AI 设置」里填好你的 DeepSeek API Key，再点一次「AI 提取技能」。'
    case 'AI_KEY_INVALID':
      return '你的 DeepSeek API Key 无效或已欠费，请打开「AI 设置」检查后再试。（与登录状态无关，不需要重新登录。）'
    case 'AI_SERVICE_UNAVAILABLE':
      if (error.finishReason === 'length') {
        return '这条 JD 太长或技能太多，模型的输出被服务端上限切断了。请把 JD 里真正想要的技能写清楚（或只粘核心段落）后再试一次。'
      }
      return `${error.message}（稍后重试即可；本次结果没有被保存，岗位也没有被改动。）`
    case 'RATE_LIMITED':
      return '提取太频繁了，请等一分钟再试——这一步花的是你自己的额度。'
    case 'VALIDATION_ERROR':
      return '提取请求没有被接受：JD 原文不能为空，且最长 8000 字。'
    case 'NETWORK_ERROR':
      return '等 AI 返回超时或连不上服务，请确认后端在跑，然后重试。'
    default:
      return `${error.message}（岗位没有被改动。）`
  }
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

  /**
   * AI 提取的**草稿态**（ADR-011 2.A：端点纯计算、不落库，所以「落库」这件事
   * 只能由用户点「保存岗位」触发一次 `POST/PUT /jobs`）。
   *
   * 三个状态各有各的去处，不要合并：
   * - `extractDraft`：原始响应（含 `data.meta` 的七个计数），计数文案只读它；
   * - `extractPlan`：合并进表单的那一次的产出与加入的行，「放弃本次提取」只读它；
   * - `extracting`：在途标记，视图用它禁用按钮并显示 Loading。
   *
   * 这份草稿**只在内存里**：不写 localStorage、不写 sessionStorage，
   * `reset()` 连它一起清。JD 正文与 evidence 片段属于用户粘进来的原文，
   * 落盘只会多一处「换个人用这台电脑就能看到」的泄漏面。
   */
  const extractDraft = ref<ExtractSkillsResult | null>(null)
  const extractPlan = ref<ExtractMergePlan | null>(null)
  const extracting = ref(false)

  /** 上一次提取加进表单的行数（=0 表示这批候选一条都没落地，视图该说一句为什么）。 */
  const extractedAddedCount = computed(() => extractPlan.value?.added.length ?? 0)

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

  /* -------------------------------- AI 提取 -------------------------------- */

  /**
   * 「✨ AI 提取技能」：发提取请求 → 结果进草稿 → 顺手把候选合进技能行，**不发任何写请求**。
   *
   * 返回合并计划（视图用它整体替换 `form.skills` 并渲染计数）；失败原样抛给调用方，
   * 由视图用 `extractFailureText()` 翻译成下一步动作——本函数**不吞异常也不降级**。
   *
   * 在途时重复点击直接返回 `null`：这是烧用户额度的请求（限流 10/minute），
   * 让按钮连点变成两次上游调用是最省事的坏设计。视图也禁了按钮，两道闸都要有。
   */
  async function extractSkills(
    input: JobsContract.ExtractSkillsRequest,
    rows: JobSkillFormRow[],
  ): Promise<ExtractMergePlan | null> {
    if (extracting.value) return null
    extracting.value = true
    // 新一批开始前先丢掉上一批：屏幕上挂着两批候选，「放弃」就不知道该撤哪一批了。
    extractDraft.value = null
    extractPlan.value = null
    try {
      const result = await jobsApi.extractJobSkills({
        description: input.description,
        title: input.title?.trim() || null,
      })
      extractDraft.value = result
      // 判重口径只有 `normalizeSkillName()` 这一个（理由见 `mergeExtractedSkills`）。
      const plan = mergeExtractedSkills(rows, result.skills)
      extractPlan.value = plan
      return plan
    } finally {
      extracting.value = false
    }
  }

  /**
   * 「放弃本次提取」：把上一批加进来、且**还没被用户改过**的行撤掉，并清空草稿。
   *
   * 与 ADR-010 §5 的「放弃：什么都不改」同源——这里不改的是**库里**：
   * 本函数一个请求都不发，岗位仍然是没保存的状态。改过的行留着（见 `undoExtractMerge`）。
   */
  function discardExtraction(rows: JobSkillFormRow[]): {
    rows: JobSkillFormRow[]
    removed: number
    kept: number
  } {
    const undo = undoExtractMerge(rows, extractPlan.value?.added ?? [])
    extractDraft.value = null
    extractPlan.value = null
    return undo
  }

  /** 只清草稿（换 JD 时用）：表单里已有的行一概不动。 */
  function clearExtract(): void {
    extractDraft.value = null
    extractPlan.value = null
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

  /**
   * 编辑岗位（`/jobs/:id/edit` 的保存动作）：与 `createJob` 同一套提交前自检、同一个投影。
   *
   * 后端 `PUT /jobs/{id}` 是**整体覆盖**：字段覆盖 + 技能子表先删后插，
   * 数组里没出现的技能会被删掉，所以这里必须发完整列表（`toWritePayload` 已是全量）。
   * `status` 不在请求体里（发布是独立动作），三列提取溯源戳由服务端按本次提交整批重算。
   *
   * 与 `createJob` 不同的一点：**不刷新列表**。列表此刻不在屏幕上（用户从详情页进来），
   * 而 `loadJobs()` 会按当前筛选条件清空重拉，反而可能把他回来的那一屏换掉。
   * 详情页的同步靠 `detail.value` 就地替换。
   */
  async function updateJob(jobId: string, form: JobFormInput): Promise<JobRead> {
    const problem = validateJobForm(form)
    if (problem) throw new Error(problem)

    submitting.value = true
    try {
      const updated = await jobsApi.updateJob(jobId, toWritePayload(form))
      if (detail.value?.id === jobId) detail.value = updated
      return updated
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
    }
    // 清空详情与重拉列表只属于**成功路径**。写在 `finally` 里会在删除失败时也把
    // `detail` 拔掉，而详情页的错误横幅在「有岗位」那个分支里，于是服务端那句
    // 「岗位不存在，或已经不是你的岗位」根本没有落点：用户看到的是一条
    // 并没有被删除、却凭空变成「没有可显示的内容」的岗位——比报个错更糟。
    if (detail.value?.id === jobId) detail.value = null
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

  /**
   * 登出 / 离开编辑链路时清空，避免下一个账号短暂看到上一个人的岗位。
   *
   * `clearExtract()` 在这一串里：AI 提取的草稿带着 JD 原文片段（evidence），
   * 它是「上一个人的屏幕」的一部分，漏掉就等于把候选技能连带留给了下一个账号。
   * 视图里的表单数组不在 store 里，由 `JobCreateView` 自己 `reset` 时重建。
   */
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
    clearExtract()
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
    updateJob,
    publish,
    unpublish,
    remove,
    extractDraft,
    extractPlan,
    extracting,
    extractedAddedCount,
    extractSkills,
    discardExtraction,
    clearExtract,
    mySkillNames,
    mySkillNamesLoading,
    loadMySkillNames,
    matchesMySkill,
    isCreatorOf,
    reset,
  }
})
