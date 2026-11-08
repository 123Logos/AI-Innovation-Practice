/**
 * `useJobStore` 与岗位路由表的单测（Phase 4 · B2 + C2）。
 *
 * 分四半，各自钉住一条「坏了不会报错、只会让用户看见错数据」的链路：
 *
 * 1. **状态流转**：列表加载 → 创建 → 软删 → 回收站 → 恢复。
 *    这五步各自都会写 store 里的一份列表，任何一步漏刷新，用户就会对着一条
 *    「已经删掉却还在列表里」的岗位反复点。做法同 `api/__tests__/jobs.spec.ts`：
 *    换掉 axios 的 adapter，让真实的 store → api → 拦截器 链路跑一遍。
 * 2. **AI 提取的草稿态**：合并、判重跳过、计数文案、失败分流，以及**提取之后没有发出任何
 *    写请求**（那条 spy 断言是本轮的红线：AI 不能只凭一次点击改库）。
 * 3. **路由表自证**：五条 jobs 路由存在、`name` 是 kebab-case、`requiresAuth` 都在，
 *    并且 `/jobs/trash`、`/jobs/new` 不会被 `/jobs/:id` 吞掉、`/jobs/:id/edit` 落到编辑态。
 *    放在这里而不是单开一个 spec 文件：这条断言属于「岗位切片能不能走通」，与 store 同一条验收线。
 * 4. **技能名归一化口径（B3）**：`normalizeSkillName()` 与后端 `dedup_key` 同口径
 *    （`trim` → NFKC → 小写），并钉住它的两个实际用途——「你已具备」与录入页重名自检。
 *    这条属于「前端放过、后端 422」那一类只有比对两端才能发现的缺陷，所以跟状态流转放一起。
 *
 * 写侧投影的断言（请求体里不许出现 `origin` / `status`、必须出现确认过的
 * `evidence_text` / `confidence`）用的是「把读模型原样当表单行塞进来」这种真实误用形态——
 * 后端是 `extra="ignore"`，传错了**不报错**，只有这里能发现。
 */

import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, http } from '@/api/client'
import type { jobsApi as JobsContract } from '@/api'
import {
  createEmptyJobForm,
  extractCountLines,
  extractFailureText,
  extractMergeLines,
  jobToFormInput,
  matchDegradeLines,
  matchFailureText,
  mergeExtractedSkills,
  normalizeSkillName,
  toWritePayload,
  useJobStore,
} from '@/stores/job'
import type { JobFormInput, JobSkillFormRow } from '@/stores/job'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'

interface Captured {
  method: string
  url: string
  query: Record<string, string>
  body: unknown
}

let captured: Captured[] = []
let savedAdapter: typeof http.defaults.adapter
const OWN_USER_ID = 'user-under-test'

/**
 * `GET /jobs/{id}/requirement-match` 的可注入响应（ADR-013 §10 第 3c 步）。
 * `null` = 用 `defaultRequirementEnvelope()`；每条用例跑完在 `beforeEach` 里复位。
 *
 * 默认那一份把 `Python` 判成 `covered` 且 `matched_form` 是带首尾空格的 `' Python '`：
 * 这一格是**后端**回的东西，前端原样消费。留着它是为了钉住「前端不再判等」——
 * 只要本地归一化还参与判定，把信封改成 `missing` 时角标就不会跟着变，用例当场红。
 */
let requirementEnvelope: unknown = null

function requirementRow(
  name: string,
  status: JobsContract.RequirementStatus,
  matchedForm: string | null = null,
): JobsContract.RequirementCoverageRow {
  return { name, status, matched_form: matchedForm }
}

function defaultRequirementEnvelope(
  coverage: JobsContract.RequirementCoverageRow[] = [
    requirementRow('Python', 'covered', ' Python '),
    requirementRow('FastAPI', 'covered', 'FastAPI'),
    requirementRow('MySQL', 'partial', 'MySQL'),
    requirementRow('Docker', 'missing', null),
  ],
): unknown {
  return {
    data: {
      job_id: 'job-1',
      profile_id: 'profile-1',
      profile_title: '默认简历',
      coverage,
      meta: { matcher_version: 'v1' },
    },
    meta: { request_id: 'req-test' },
  }
}

function skillRow(id: string, name: string, overrides: Record<string, unknown> = {}) {
  return { id, name, type: 'hard', origin: 'unverified', confidence: null, evidence_text: null, ...overrides }
}

function summary(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    company_name: '示例科技',
    title: `岗位 ${id}`,
    location: '上海',
    employment_type: 'internship',
    salary_min: null,
    salary_max: null,
    deadline: null,
    status: 'published',
    created_by_user_id: OWN_USER_ID,
    created_at: '2026-11-05T10:00:00+08:00',
    updated_at: '2026-11-05T10:00:00+08:00',
    deleted_at: null,
    skills: [skillRow('skill-1', 'Python')],
    is_expired: false,
    ...overrides,
  }
}

function detail(id: string, overrides: Record<string, unknown> = {}) {
  return {
    ...summary(id),
    description: '负责后端接口开发，熟练掌握 Python 与 FastAPI。',
    experience_required: '3 年以上',
    extract_model: null,
    extract_prompt_version: null,
    extracted_at: null,
    ...overrides,
  }
}

function envelope(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config } as AxiosResponse
}

/* ------------------------- 提取端点的可注入响应 ------------------------- */

/**
 * 提取响应由用例临时指定：`data.meta` 的七个计数需要逐条摆出来，
 * 而失败分流要造 400/401/502 三种真实错误形状。`null` = 用默认那份。
 * 每条用例跑完在 `beforeEach` 里复位，防一份假信封漏进下一个断言。
 */
let extractEnvelope: unknown = null
let extractErrorFactory: ((config: InternalAxiosRequestConfig) => AxiosResponse) | null = null

function extractMetaFixture(
  overrides: Partial<JobsContract.ExtractSkillsMeta> = {},
): JobsContract.ExtractSkillsMeta {
  return {
    model: 'deepseek-flash',
    prompt_version: 'v1',
    received_count: 20,
    dropped_count: 14,
    deduped_count: 1,
    truncated: true,
    finish_reason: 'stop',
    ...overrides,
  }
}

function extractSkill(
  name: string,
  overrides: Partial<JobsContract.ExtractedSkill> = {},
): JobsContract.ExtractedSkill {
  return { name, type: 'hard', evidence: `熟练掌握 ${name}`, confidence: 0.9, ...overrides }
}

/** 默认那份：三条候选 + 一份「AI 给了 20 条、剔了 14 条、合并 1 条、被截断」的计数。 */
function defaultExtractEnvelope(skills: JobsContract.ExtractedSkill[] = [extractSkill('Python'), extractSkill('FastAPI'), extractSkill('Ｄocker')]): unknown {
  return {
    data: { skills, meta: extractMetaFixture({ received_count: 20, dropped_count: 14, deduped_count: 1, truncated: true }) },
    meta: { request_id: 'req-test' },
  }
}

/**
 * 匹配端点的可注入响应（ADR-013 §10 第 8 步）。
 * `null` = 用下面那份默认的 `ai_scored` 结果；每条用例跑完在 `beforeEach` 里复位。
 */
let matchEnvelope: unknown = null
let matchErrorFactory: ((config: InternalAxiosRequestConfig) => AxiosResponse) | null = null

/**
 * `matchDegradeLines()` 的测试对象：一份完整的 `MatchResult`（不是只有 meta）。
 *
 * 签名从 `MatchMeta` 改成 `MatchResult` 不是洁癖：「命中了但经历里找不到出处」与
 * 「一条都没命中」在 meta 里长得**一模一样**（`keyword_fallback` + `degrade_reason=null`），
 * 分得开它们的只有 `terms[evidence].note`。少了这一格，文案会对什么都没命中的用户说
 * 「你命中的技能找不到出处」——那是假话（F1:A 的那组数据把它撞出来的）。
 */
function degradeSubject(
  metaOverrides: Partial<JobsContract.MatchMeta> = {},
  evidenceNote: string | null = null,
): JobsContract.MatchResult {
  const result = (defaultMatchEnvelope() as { data: JobsContract.MatchResult }).data
  return {
    ...result,
    terms: result.terms.map((item) =>
      item.name === 'evidence' ? { ...item, note: evidenceNote } : item,
    ),
    meta: matchMetaFixture(metaOverrides),
  }
}

function matchMetaFixture(
  overrides: Partial<JobsContract.MatchMeta> = {},
): JobsContract.MatchMeta {
  return {
    model: 'deepseek-flash',
    prompt_version: 'v1',
    evidence_mode: 'ai_scored',
    degrade_reason: null,
    candidate_pairs: 2,
    verified_pairs: 2,
    dropped_unreferenced: 0,
    dropped_unknown_skill: 0,
    dropped_unusable_strength: 0,
    dropped_missing_output: 0,
    clamped_strength: 0,
    fragments_truncated: 0,
    truncated: false,
    finish_reason: 'stop',
    weight_sum_used: 0.65,
    no_required_skills: false,
    matcher_version: 'v1',
    note: null,
    ...overrides,
  }
}

/**
 * 一份形状完整的匹配结果：三档各一条、五项分项、meta 十个计数。
 *
 * 这里刻意把**全部**格子写出来而不是只写用到的那几格：`MatchMeta` 是
 * 后端 `app/schemas/match.py` 的镜像，少写一格会让 typecheck 在契约变化时漏红。
 */
function defaultMatchEnvelope(overrides: Partial<JobsContract.MatchResult> = {}): unknown {
  return {
    data: {
      profile_id: 'profile-1',
      profile_title: '默认简历',
      job_id: 'job-1',
      score: 0.669,
      weights_version: 'v1',
      terms: [
        { name: 'coverage', weight: 0.45, value: 0.667, computed: true, note: null },
        { name: 'evidence', weight: 0.2, value: 0.667, computed: true, note: null },
        { name: 'relevance', weight: 0.15, value: null, computed: false, note: 'not_measurable_v1' },
        { name: 'preference', weight: 0.1, value: null, computed: false, note: 'no_target_role' },
        { name: 'remaining_time', weight: 0.1, value: null, computed: false, note: 'no_deadline' },
      ],
      covered: [
        {
          skill: 'Python',
          evidence: [
            {
              experience_id: 'exp-1',
              field: 'description',
              snippet: '用 Python 重写了订单服务',
              strength: 0.667,
            },
          ],
        },
      ],
      partial: [{ skill: 'FastAPI', reason: '只在 1 处经历里出现，证据偏薄' }],
      gaps: [
        {
          skill: 'MySQL',
          action: '补一条真的用 MySQL 做过的项目经历：做了什么 → 怎么做的 → 结果如何。',
          verify: '按你写的过程细节追问，能答出为什么这样选，就算验证通过。',
        },
      ],
      meta: matchMetaFixture(),
      ...overrides,
    },
    meta: { request_id: 'req-test' },
  }
}

/**
 * 假的服务端：`server` 里只有「删除过的进回收站」这一条最小状态，
 * 目的是让五条动作在 store 里留下的痕迹可被断言，而不是重做一遍后端。
 */
function fakeAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const method = (config.method ?? 'get').toLowerCase()
  const url = config.url ?? ''
  const rawParams = (config.params ?? {}) as Record<string, unknown>
  const query = Object.fromEntries(
    Object.entries(rawParams).map(([key, value]) => [key, String(value)]),
  )
  const body = typeof config.data === 'string' && config.data ? (JSON.parse(config.data) as unknown) : config.data
  captured.push({ method, url, query, body })

  if (method === 'get' && /^\/jobs\/[^/]+\/requirement-match$/.test(url)) {
    // 第 3c 条：一次只读 GET 拿全量逐条状态。异常形状由用例改 adapter，这里只给成功信封。
    return Promise.resolve(envelope(config, (requirementEnvelope ?? defaultRequirementEnvelope()) as never))
  }

  if (method === 'get' && url === '/jobs/trash') {
    return Promise.resolve(
      envelope(config, {
        data: { jobs: [summary('job-deleted', { deleted_at: '2026-11-05T12:00:00+08:00', status: 'draft' })] },
        meta: { request_id: 'req-test' },
      }),
    )
  }

  if (method === 'get' && url === '/jobs') {
    const isDraft = query.status === 'draft'
    return Promise.resolve(
      envelope(config, {
        data: {
          jobs: isDraft
            ? []
            : [summary('job-1'), summary('job-2', { title: '算法实习', is_expired: true })],
        },
        // `total` 刻意比当页条数大：它是「符合筛选的总数」，前端靠它算页数与「有没有下一页」。
        meta: { request_id: 'req-test', page: Number(query.page ?? 1), page_size: Number(query.page_size ?? 20), total: isDraft ? 0 : 120 },
      }),
    )
  }

  if (method === 'post' && /^\/jobs\/[^/]+\/match$/.test(url)) {
    if (matchErrorFactory) {
      return Promise.reject(
        new AxiosError('匹配失败', 'ERR_BAD_RESPONSE', config, {}, matchErrorFactory(config)),
      )
    }
    return Promise.resolve(envelope(config, matchEnvelope ?? defaultMatchEnvelope()))
  }

  if (method === 'post' && url === '/jobs/extract-skills') {
    // 提取端点：默认回一份「三条候选 + 七个计数」的信封；用例可以改成任何 AI 失败形状。
    if (extractErrorFactory) {
      return Promise.reject(
        new AxiosError('提取失败', 'ERR_BAD_RESPONSE', config, {}, extractErrorFactory(config)),
      )
    }
    return Promise.resolve(envelope(config, extractEnvelope ?? defaultExtractEnvelope()))
  }

  if (method === 'post' && url === '/jobs') {
    // 一律落 draft：`status` 由服务端写死，与请求体无关（1.F #3）。
    return Promise.resolve(envelope(config, { data: { job: detail('job-new', { status: 'draft' }) }, meta: { request_id: 'req-test' } }, 201))
  }

  if (method === 'put' && url === '/jobs/job-1/publish') {
    return Promise.resolve(envelope(config, { data: { job: detail('job-1', { status: 'published' }) }, meta: { request_id: 'req-test' } }))
  }

  if (method === 'put' && url === '/jobs/job-1/unpublish') {
    return Promise.resolve(envelope(config, { data: { job: detail('job-1', { status: 'draft' }) }, meta: { request_id: 'req-test' } }))
  }

  if (method === 'put' && /^\/jobs\/[^/]+$/.test(url)) {
    // PUT 是「整体覆盖」：回的是本次提交的内容（这里只把 title 带回去，
    // 好让「保存后详情页同步成新值」那条断言不是靠假数据蒙对的）。
    const sent = (body ?? {}) as Record<string, unknown>
    return Promise.resolve(
      envelope(config, {
        data: { job: detail(url.split('/')[2] ?? 'job-1', { title: String(sent.title ?? '') }) },
        meta: { request_id: 'req-test' },
      }),
    )
  }

  if (method === 'put' && url.endsWith('/restore')) {
    return Promise.resolve(envelope(config, { data: { job: detail('job-deleted') }, meta: { request_id: 'req-test' } }))
  }

  if (method === 'delete') {
    return Promise.resolve(envelope(config, { data: { job_id: 'job-1', deleted: true }, meta: { request_id: 'req-test' } }))
  }

  return Promise.resolve(envelope(config, { data: { job: detail('job-1') }, meta: { request_id: 'req-test' } }))
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  captured = []
  requirementEnvelope = null
  extractEnvelope = null
  extractErrorFactory = null
  matchEnvelope = null
  matchErrorFactory = null
  savedAdapter = http.defaults.adapter
  http.defaults.adapter = fakeAdapter
  useAuthStore().user = { id: OWN_USER_ID, username: 'tester', email: null, created_at: '2026-11-05T10:00:00+08:00' }
})

afterEach(() => {
  http.defaults.adapter = savedAdapter
})

function requestsFor(method: string, url?: string): Captured[] {
  return captured.filter((item) => item.method === method && (url === undefined || item.url === url))
}

function lastRequest(): Captured {
  const request = captured[captured.length - 1]
  if (!request) throw new Error('没有任何请求发出')
  return request
}

/**
 * 把某条路由记录里的 props（对象或工厂函数）算成**实际传给组件的那份**。
 *
 * 为什么不直接断言 `router.resolve()` 的结果：`resolve()` 返回的是路由位置信息，
 * props 的求值发生在组件匹配那一步，测试里拿不到。这里对着路由表自己算一次，
 * 断言的就是 `router/index.ts` 里写的那两个工厂的输出。
 */
function propsPassedTo(routeName: string, params: Record<string, string> = {}): Record<string, unknown> {
  const record = router.getRoutes().find((route) => route.name === routeName)
  const option = record?.props?.default
  if (typeof option === 'function') {
    // 先折成一个明确的工厂类型再调：`record.props.default` 的声明是
    // `Function | ((to) => …)` 这个联合，`Parameters<>` 摊到裸 `Function` 上会直接报 TS2344。
    const factory = option as (to: unknown) => Record<string, unknown>
    return factory({ params })
  }
  return (option ?? {}) as Record<string, unknown>
}

/** 一份填好的表单：技能行里**故意带上**读模型才有的三个键，模拟「原样回传」的误用。 */
function filledForm(): JobFormInput {
  const rows = [
    { name: 'Python', type: 'hard', origin: 'jd_verifiable', evidence_text: '熟练掌握 Python', confidence: '0.9' },
    { name: '   ', type: 'soft' },
    { name: '沟通能力强', type: '' },
  ] as unknown as JobSkillFormRow[]
  return {
    ...createEmptyJobForm(),
    company_name: ' 示例科技 ',
    title: '后端实习',
    description: '熟练掌握 Python。',
    location: '上海',
    employment_type: 'internship',
    deadline: '2099-12-31',
    salary_min: '3000',
    salary_max: '6000',
    skills: rows,
  }
}

describe('useJobStore 的状态流转', () => {
  it('列表加载 → 创建 → 软删 → 回收站 → 恢复，每一步的 store 与请求都对得上', async () => {
    const store = useJobStore()

    // 1) 列表加载：默认广场，两条都在；`total` 是符合筛选的总数（120），不是当页条数。
    await store.loadJobs()
    expect(store.jobs.map((job) => job.id)).toEqual(['job-1', 'job-2'])
    expect(store.total).toBe(120)
    expect(store.totalPages).toBe(3)
    expect(store.hasNextPage).toBe(true)
    expect(requestsFor('get', '/jobs')).toHaveLength(1)
    // store 的 camelCase 条件必须翻译成后端的 snake_case。
    expect(lastRequest().query).toMatchObject({ status: 'published', page: '1', page_size: '50' })

    // 2) 创建：POST 成功后立刻回第 1 页刷新列表（新岗位在草稿那一屏，不在广场）。
    const created = await store.createJob(filledForm())
    expect(created.id).toBe('job-new')
    expect(requestsFor('post', '/jobs')).toHaveLength(1)
    expect(requestsFor('get', '/jobs')).toHaveLength(2)

    // 3) 软删：DELETE 后列表刷新；删的是当前详情时详情被清空（后端对软删行一律 404）。
    await store.loadDetail('job-1')
    expect(store.detail?.id).toBe('job-1')
    await store.remove('job-1')
    expect(requestsFor('delete', '/jobs/job-1')).toHaveLength(1)
    expect(store.detail).toBeNull()

    // 4) 回收站：GET /jobs/trash，不是 GET /jobs?status=…（静态段，路径本身即语义）。
    await store.loadTrash()
    expect(store.trash.map((job) => job.id)).toEqual(['job-deleted'])
    expect(requestsFor('get', '/jobs/trash')).toHaveLength(1)

    // 5) 恢复：PUT /jobs/{id}/restore，随后回收站与列表都刷新。
    const before = captured.length
    await store.restore('job-deleted')
    const restoreCalls = captured.slice(before).filter((item) => item.method === 'put')
    expect(restoreCalls.map((item) => item.url)).toEqual(['/jobs/job-deleted/restore'])
    expect(requestsFor('get', '/jobs/trash').length).toBe(2)
    // 广场列表在这条链路里被刷新了 4 次：初次加载、创建后、删除后、恢复后。
    expect(requestsFor('get', '/jobs').length).toBe(4)
  })

  it('筛选状态改变会回到第 1 页，并把 keyword / include_expired 拼进查询串', async () => {
    const store = useJobStore()
    await store.loadJobs(3)

    await store.setStatusFilter('draft')
    expect(store.filter.status).toBe('draft')
    expect(lastRequest().query).toMatchObject({ status: 'draft', page: '1' })

    await store.setKeyword('  字节  ')
    expect(lastRequest().query).toMatchObject({ status: 'draft', keyword: '字节', page: '1' })

    await store.toggleIncludeExpired()
    expect(store.filter.includeExpired).toBe(true)
    expect(lastRequest().query).toMatchObject({ include_expired: 'true', page: '1' })
  })

  it('翻页只改 page，不丢筛选条件', async () => {
    const store = useJobStore()
    await store.loadJobs()
    await store.setKeyword('后端')
    await store.toggleIncludeExpired()

    await store.goPage(2)
    expect(lastRequest().query).toMatchObject({ keyword: '后端', include_expired: 'true', page: '2' })

    // 越界页不发请求：`page` 的下限后端是 `ge=1`（0 会 422），上限之外是空列表没意义。
    const before = captured.length
    await store.goPage(0)
    await store.goPage(store.totalPages + 1)
    expect(captured.length).toBe(before)
  })

  it('loadJobs 在请求发出前清空旧列表：换账号时不会渲染上一个人的岗位', async () => {
    const store = useJobStore()
    await store.loadJobs()
    expect(store.jobs).toHaveLength(2)

    // 广场 → 草稿：这一次服务端返回空表。若不清空，中间那一帧会挂着前一次的行。
    await store.setStatusFilter('draft')
    expect(store.jobs).toEqual([])
  })
})

describe('写侧请求体的反向断言', () => {
  /**
   * **C2 更新**（ADR-011 1.F v9 放开写回路径，监督者裁定）：
   * B2 那一条断言的是「`evidence_text` / `confidence` 一律不发」，理由是当时没有 AI 路径、
   * 接受填值只会落下无法核验的数据。C1 把提取链路接通之后，用户是在草稿态逐条确认过
   * AI 结果才点保存的，这两格**必须存得进去**——否则 1.D 的 `origin` 派生与 §2.D.1 的核验
   * 无处落地，徽标永远是「未标注出处」。
   *
   * 所以这条用例没有删掉断言，而是**跟着契约搬了一位**：
   * - `origin` / `status` / `created_by_user_id` 仍然禁止（服务端派生，客户端说了不算）；
   * - `evidence_text` / `confidence` 改为**必须原样带上**，并断言 `origin` 即使挂在行上
   *   也进不了请求体（这才是 1.F 那条「不采信客户端」的真实防线）。
   */
  it('创建请求体带上确认过的 evidence / confidence，而 origin 即使在行上也被丢掉', async () => {
    const store = useJobStore()
    await store.createJob(filledForm())

    // 取**那一次 POST**的请求体，不是最后一条请求（创建成功后 store 还会刷新列表）。
    const post = requestsFor('post', '/jobs')[0]
    expect(post, '没有发出 POST /jobs').toBeDefined()
    const body = post?.body as Record<string, unknown> & { skills: Record<string, unknown>[] }

    for (const key of ['status', 'created_by_user_id', 'origin', 'extract_model', 'extracted_at']) {
      expect(body, `请求体不该带 ${key}`).not.toHaveProperty(key)
    }
    expect(body.skills).toHaveLength(2)
    for (const skill of body.skills) {
      expect(skill, '技能条目不该带 origin').not.toHaveProperty('origin')
    }

    // 第一条：核验锚点与自报把握原样发出去（`filledForm` 里那行确实带着它们）。
    expect(body.skills[0]).toEqual({
      name: 'Python',
      type: 'hard',
      evidence_text: '熟练掌握 Python',
      confidence: '0.9',
    })
    // 第二条：手填、没有依据 → 只有两个键，空串 `type` 转成 `null`（后端 `''` 会 422）。
    expect(body.skills[1]).toEqual({ name: '沟通能力强', type: null })

    // 空串 → null（后端存 NULL），并且顺手裁掉首尾空格。
    expect(body).toMatchObject({ company_name: '示例科技', location: '上海', experience_required: null })
  })

  it('薪资填反了、技能重名、JD 超长都在提交前被拦下，不发请求', async () => {
    const store = useJobStore()

    const before = captured.length
    await expect(
      store.createJob({ ...filledForm(), salary_min: '9000', salary_max: '3000' }),
    ).rejects.toThrow('薪资下限不能大于薪资上限。')

    await expect(
      store.createJob({
        ...filledForm(),
        skills: [{ name: 'Python', type: 'hard' }, { name: ' python ', type: 'soft' }],
      }),
    ).rejects.toThrow('技能名称不能重复，请先合并同名技能。')

    await expect(
      store.createJob({ ...filledForm(), description: 'x'.repeat(8001) }),
    ).rejects.toThrow(/岗位描述最长 8000 个字符/)

    await expect(store.createJob({ ...filledForm(), company_name: '  ' })).rejects.toThrow('请填写企业名称。')

    expect(captured.length).toBe(before)
  })
})

describe('详情页的只读信息', () => {
  it('「你已具备」的状态来自 requirement-match 端点，前端只消费不判断', async () => {
    const store = useJobStore()
    await Promise.all([store.loadDetail('job-1'), store.loadRequirementMatch('job-1')])

    expect(store.skillStatus('Python')).toBe('covered')
    expect(store.skillStatus('MySQL')).toBe('partial')
    expect(store.skillStatus('Docker')).toBe('missing')
    expect(store.matchedFormOf('Python')).toBe(' Python ')
    // 后端没回过的名字 = 这一行不存在，状态必须是 null 而不是「猜一个不具备」。
    expect(store.skillStatus('沟通能力强')).toBeNull()

    // 一次详情页一次请求：不许退化成「每条技能问一遍」。
    expect(requestsFor('get', '/jobs/job-1/requirement-match')).toHaveLength(1)
    // 全程只有 GET：这条链路按定义不写任何表（ADR-011 1.E / 2.E）。
    expect(requestsFor('post')).toEqual([])
    expect(requestsFor('put')).toEqual([])
    expect(requestsFor('delete')).toEqual([])
  })

  it('换岗位时清空上一条的逐条状态：B 岗位不许长出 A 岗位的角标', async () => {
    const store = useJobStore()
    await store.loadRequirementMatch('job-1')
    expect(store.requirementCoverage).toHaveLength(4)

    await store.loadDetail('job-2')
    expect(store.requirementCoverage).toEqual([])
    expect(store.skillStatus('Python')).toBeNull()
  })

  it('拉不到就异常外抛，由视图 catch 后一条都不标（不许静默留旧值）', async () => {
    const store = useJobStore()
    await store.loadRequirementMatch('job-1')
    expect(store.requirementCoverage).toHaveLength(4)

    http.defaults.adapter = () => Promise.reject(new AxiosError('连不上。', 'ERR_NETWORK'))
    await expect(store.loadRequirementMatch('job-2')).rejects.toBeInstanceOf(Error)
    // store 这边不清（清不清是调用方的决定），但 `clearRequirementMatch()` 必须是可用的那条路。
    expect(store.requirementCoverage).toHaveLength(4)
    store.clearRequirementMatch()
    expect(store.requirementCoverage).toEqual([])
  })

  it('isCreatorOf 用 JWT 里的用户 id 比对 created_by_user_id，非创建者拿 false', () => {
    const store = useJobStore()
    const asSummary = (row: Record<string, unknown>) => row as unknown as JobsContract.JobSummary

    expect(store.isCreatorOf(asSummary({ ...summary('job-1'), created_by_user_id: 'someone-else' }))).toBe(false)
    expect(store.isCreatorOf(asSummary(summary('job-1')))).toBe(true)
    expect(store.isCreatorOf(null)).toBe(false)
  })

  it('发布 / 下架同步详情页；删除清空详情页', async () => {
    const store = useJobStore()
    await store.loadDetail('job-1')

    await store.publish('job-1')
    expect(store.detail?.status).toBe('published')

    await store.unpublish('job-1')
    expect(requestsFor('put', '/jobs/job-1/unpublish')).toHaveLength(1)

    await store.remove('job-1')
    expect(store.detail).toBeNull()
  })
})

describe('技能名归一化口径（与后端 dedup_key 同：trim → NFKC → 小写）', () => {
  it('normalizeSkillName 把全角 / 大小写 / 首尾空格折成同一个键', () => {
    // 这就是验收时那条失败：`Ｐython ` 此前只 trim + 小写 → `ｐython`，与 `python` 不相等。
    expect(normalizeSkillName('Ｐython ')).toBe(normalizeSkillName('python'))
    expect(normalizeSkillName(' ＰＹＴＨＯＮ')).toBe(normalizeSkillName('python'))
    expect(normalizeSkillName('Python')).toBe(normalizeSkillName('  python  '))
    // 反向：归一化不是「删字符」也不是前缀匹配，`C++` 与 `C` 仍是两个技能（后端同口径）。
    expect(normalizeSkillName('C++')).not.toBe(normalizeSkillName('C'))
    expect(normalizeSkillName('python')).not.toBe(normalizeSkillName('python3'))
    // 汉字没有兼容映射，NFKC 是恒等的：不会因为归一化把中文技能名改样。
    expect(normalizeSkillName(' 沟通能力强 ')).toBe('沟通能力强')
  })

  it('用途一已下线：判定归后端，本地归一化再也标不出一个「已具备」', async () => {
    const store = useJobStore()

    // 后端说 missing，界面就必须说不出「已具备」——哪怕简历里明明白白写着 Python。
    // 这一条存在的意义是：本地那套 trim+NFKC+小写比对曾经自己判过一次，
    // 于是同一屏会出现角标与匹配卡各说一套（ADR-013 §1.3 的第二半）。
    requirementEnvelope = defaultRequirementEnvelope([
      requirementRow('Python', 'missing', null),
      requirementRow('Ｐython', 'missing', null),
    ])
    await store.loadRequirementMatch('job-1')
    expect(store.skillStatus('Python')).toBe('missing')
    expect(store.skillStatus('Ｐython')).toBe('missing')

    // 反向：后端按版本号判成 covered、对面写法是 `Python3`，前端也不许把它改回 missing。
    requirementEnvelope = defaultRequirementEnvelope([requirementRow('Python', 'covered', 'Python3')])
    await store.loadRequirementMatch('job-1')
    expect(store.skillStatus('Python')).toBe('covered')
    expect(store.matchedFormOf('Python')).toBe('Python3')
    // 本地那套键**够不着**这一格：拿它自己判就会判成不相等——这正是判定必须下线的理由。
    expect(normalizeSkillName('Python')).not.toBe(normalizeSkillName('Python3'))
  })

  it('用途二 录入页重名自检：全角同名在提交前拦下，不再发请求给后端 422', async () => {
    const store = useJobStore()
    const before = captured.length

    await expect(
      store.createJob({
        ...filledForm(),
        skills: [
          { name: 'Python', type: 'hard' },
          { name: 'Ｐython', type: 'soft' },
        ],
      }),
    ).rejects.toThrow('技能名称不能重复，请先合并同名技能。')

    // 后端 `JobWriteRequest.validate_unique_skill_names` 用的是同一条键；
    // 前端若只 trim + 小写就会放过这一对，用户在屏幕上看到一条看不懂的 422。
    expect(captured.length).toBe(before)
  })
})

describe('AI 提取：结果只进草稿，绝不自动落库', () => {
  /**
   * 本组用例的核心红线（ADR-011 2.A / ADR-010 §5）：提取是**纯计算**端点，
   * 候选必须停在草稿态直到用户亲手点「保存岗位」。
   * 谁哪天「顺手」把提取成功后紧接着调一次 createJob，屏幕上看起来更省事，
   * 但 AI 就只凭一次点击改了库——这条 spy 断言会当场变红。
   */
  it('提取成功之后：只有那一次 POST /jobs/extract-skills，一个写请求都没发', async () => {
    const store = useJobStore()
    /**
     * spy 挂在 store 的两个保存动作上：`toHaveBeenCalledTimes(0)` 是本轮的红线本身——
     * 「提取成功后顺手保存」在代码里读得出来，但只有计数能在有人改完之后继续拦住它。
     */
    const createSpy = vi.spyOn(store, 'createJob')
    const updateSpy = vi.spyOn(store, 'updateJob')

    const plan = await store.extractSkills(
      { description: '负责后端接口开发，熟练掌握 Python 与 FastAPI。', title: '后端实习' },
      [{ name: '沟通能力强', type: 'soft' }],
    )

    expect(requestsFor('post', '/jobs/extract-skills')).toHaveLength(1)
    // 这三行是本轮最重要的断言：提取 ≠ 保存。
    expect(requestsFor('post', '/jobs')).toHaveLength(0)
    expect(requestsFor('put')).toEqual([])
    expect(requestsFor('delete')).toEqual([])
    expect(createSpy).toHaveBeenCalledTimes(0)
    expect(updateSpy).toHaveBeenCalledTimes(0)
    // 也没有偷偷去读库/读列表（提取端点既不读库也不写库，2.A）。
    expect(requestsFor('get', '/jobs')).toHaveLength(0)

    // 结果进的是 store 的草稿，不是数据库。
    expect(store.extractDraft?.skills.map((skill) => skill.name)).toEqual(['Python', 'FastAPI', 'Ｄocker'])
    expect(plan?.added).toHaveLength(3)
    // 提取请求的载荷只有两格（后端 `ExtractSkillsRequest`），且 `title` 空串转 null。
    const body = requestsFor('post', '/jobs/extract-skills')[0]?.body as Record<string, unknown>
    expect(Object.keys(body).sort()).toEqual(['description', 'title'])
  })

  it('草稿态里的候选不会自己消失，但 `reset()` 连它一起清掉', async () => {
    const store = useJobStore()
    await store.extractSkills({ description: '熟练掌握 Python' }, [])
    expect(store.extractDraft).not.toBeNull()
    expect(store.extractPlan).not.toBeNull()

    // 登出走这条：草稿带着 JD 原文片段，留给下一个账号就是泄漏。
    store.reset()
    expect(store.extractDraft).toBeNull()
    expect(store.extractPlan).toBeNull()
    expect(store.extracting).toBe(false)
  })

  it('在途时重复点击不再发第二次请求（这是烧用户额度的端点，限流 10/minute）', async () => {
    const store = useJobStore()
    const pending = store.extractSkills({ description: '熟练掌握 Python' }, [])
    // 第一次还没落地（adapter 的微任务队列），第二次点击应当被 store 那道闸挡住。
    const second = await store.extractSkills({ description: '熟练掌握 Python' }, [])
    expect(second).toBeNull()
    expect(requestsFor('post', '/jobs/extract-skills')).toHaveLength(0)
    await pending
    expect(requestsFor('post', '/jobs/extract-skills')).toHaveLength(1)
  })

  it('换 JD 时 `clearExtract()` 只清计数说明，不动已经落进表单的行', async () => {
    const store = useJobStore()
    const plan = await store.extractSkills({ description: '熟练掌握 Python' }, [])
    expect(plan?.added).toHaveLength(3)

    store.clearExtract()
    expect(store.extractDraft).toBeNull()
    // 反向确认：清草稿不是清表单——视图里的行是用户已经看过并认可的。
    expect(plan?.rows).toHaveLength(3)
  })
})

describe('AI 提取：合并进表单时的判重与上限', () => {
  /**
   * `mergeExtractedSkills` 用 `normalizeSkillName()` 判重，与后端 `skill_name_key`
   * 同口径（NFKC + casefold）。这一条防的不是难看，而是**必败的 payload**：
   * `JobWriteRequest.validate_unique_skill_names` 对同名技能报 422，
   * 而报错出现在用户做完全部删改、点保存的那一刻。
   */
  it('与已有行重名的候选被跳过并计数（大小写 / 全半角 / 首尾空格都算同名）', () => {
    const rows: JobSkillFormRow[] = [
      { name: 'Python', type: 'hard' },
      { name: ' Web 开发 ', type: 'soft' },
    ]
    const skills: JobsContract.ExtractedSkill[] = [
      { name: 'python', type: 'hard', evidence: '熟悉 python', confidence: 0.8 },
      { name: 'PYTHON', type: null, evidence: 'PYTHON 基础', confidence: null },
      { name: 'FastAPI', type: 'hard', evidence: '熟悉 FastAPI', confidence: 0.9 },
      // 名字里的**空格差一个就是一条**：这与后端 `skill_name_key`（只有 NFKC + casefold、
      // 不去空白）和 `UNIQUE (job_id, name)` 的实测结论一致（ADR-011 §2.D.2 表第 4 行）。
      { name: 'Web开发', type: 'soft', evidence: '负责Web开发', confidence: 0.7 },
      { name: '   ', type: 'hard', evidence: '没有名字', confidence: 0.5 },
    ]

    const plan = mergeExtractedSkills(rows, skills)

    // 5 条候选：2 条与已有重名、1 条空白名、真正加入 2 条。
    expect(plan.skippedDuplicate).toBe(2)
    expect(plan.skippedBlank).toBe(1)
    expect(plan.skippedLimit).toBe(0)
    expect(plan.rows.map((row) => row.name)).toEqual(['Python', ' Web 开发 ', 'FastAPI', 'Web开发'])
    // **跳过而不是覆盖**：已有行是用户手写的，AI 的版本没有资格盖掉他的 `type`。
    expect(plan.rows[0]).toEqual({ name: 'Python', type: 'hard' })
    // 新加入的行把 `evidence` 改名为 `evidence_text`（两个名字都是契约，见 2.C / 修订清单 #2）。
    expect(plan.rows[2]).toEqual({ name: 'FastAPI', type: 'hard', evidence_text: '熟悉 FastAPI', confidence: 0.9 })
    // `confidence: null` 不该变成行上的键：没给把握就是没给，不是 0。
    expect(plan.rows[2]).not.toHaveProperty('origin')
    expect(Object.keys(plan.rows[2] as unknown as Record<string, unknown>).sort()).toEqual([
      'confidence',
      'evidence_text',
      'name',
      'type',
    ])
  })

  it('本批候选内部的重名也只加入第一条（保留模型自己的排序）', () => {
    const plan = mergeExtractedSkills(
      [],
      [
        { name: 'PyTorch', type: 'hard', evidence: '熟悉 PyTorch', confidence: 0.9 },
        { name: 'pytorch', type: 'soft', evidence: 'pytorch 训练', confidence: 0.5 },
      ],
    )
    expect(plan.rows.map((row) => row.name)).toEqual(['PyTorch'])
    expect(plan.skippedDuplicate).toBe(1)
  })

  it('`C#` 与 `C` 是两条技能（§2.D.2 口径错误的回归护栏）', () => {
    // 四步归一化（去标点）会把 `C#` 折成 `C`（`#` 是 `Po`），静默吞掉一条真实岗位要求。
    // 后端为这件事专门写了 `skill_name_key`（只有 NFKC + casefold），前端必须跟同一个口径。
    const plan = mergeExtractedSkills(
      [{ name: 'C', type: 'hard' }],
      [
        { name: 'C#', type: 'hard', evidence: '熟悉 C# 开发', confidence: 0.9 },
        { name: 'user_id', type: 'hard', evidence: '熟悉 user_id 设计', confidence: 0.9 },
      ],
    )
    expect(plan.rows.map((row) => row.name)).toEqual(['C', 'C#', 'user_id'])
    expect(plan.skippedDuplicate).toBe(0)
    // 反向确认口径没有松成「前缀匹配」：`C` 与 `C#` 不同键，`C++` 与 `C` 也不同。
    expect(normalizeSkillName('C#')).not.toBe(normalizeSkillName('C'))
  })

  it('表单已到 15 条上限时停手并报了数（不静默丢，也不塞出一次 422）', () => {
    const rows: JobSkillFormRow[] = Array.from({ length: 15 }, (_, index) => ({
      name: `已有技能 ${index}`,
      type: '' as const,
    }))
    const plan = mergeExtractedSkills(rows, [
      { name: '新来的 1', type: 'hard', evidence: 'e1', confidence: 0.9 },
      { name: '新来的 2', type: 'hard', evidence: 'e2', confidence: 0.9 },
    ])
    expect(plan.rows).toHaveLength(15)
    expect(plan.added).toHaveLength(0)
    expect(plan.skippedLimit).toBe(2)
    expect(extractMergeLines(plan)).toContain(`表单技能已达 15 条上限，本次未加入 2 条`)
  })

  it('重名跳过的条数在屏幕上是一句独立的话', () => {
    const plan = mergeExtractedSkills(
      [{ name: 'Python', type: 'hard' }],
      [
        { name: 'python', type: 'hard', evidence: '熟悉 python', confidence: 0.9 },
        { name: 'Docker', type: 'hard', evidence: '熟悉 Docker', confidence: 0.9 },
      ],
    )
    expect(extractMergeLines(plan)).toEqual(['已跳过 1 条与现有技能重名'])
    // 0 的时候整句消失：屏幕上不该出现「已跳过 0 条」这种噪音。
    expect(extractMergeLines(mergeExtractedSkills([], []))).toEqual([])
  })

  it('放弃提取：撤掉一字未动的候选，保留被用户改过的那条，并且一个请求都不发', async () => {
    const store = useJobStore()
    const plan = await store.extractSkills(
      { description: '负责后端接口开发，熟练掌握 Python 与 FastAPI。' },
      [{ name: 'Python', type: 'hard' }],
    )
    // 默认信封给三条候选（Python / FastAPI / Ｄocker），其中 Python 与已有行重名被跳过。
    expect(plan?.added.map((row) => row.name)).toEqual(['FastAPI', 'Ｄocker'])

    const rows = plan!.rows.map((row) => ({ ...row }))
    // 用户改了最后一条（Ｄocker）的分类：这一行已经有他自己的工作，不能被他没点过的按钮撤走。
    const last = rows.length - 1
    rows[last] = { ...rows[last]!, type: 'domain' }

    const before = captured.length
    const undo = store.discardExtraction(rows)

    expect(undo.removed).toBe(1)
    expect(undo.kept).toBe(1)
    expect(undo.rows.map((row) => row.name)).toEqual(['Python', 'Ｄocker'])
    // 「放弃」什么都不改：一个请求都没发（数据库里本来就没有这次提取的痕迹）。
    expect(captured.length).toBe(before)
    expect(store.extractDraft).toBeNull()
    expect(store.extractPlan).toBeNull()
  })
})

describe('岗位路由表（议题 3.B 的自证）', () => {
  /**
   * **C2 加了第五条**：`/jobs/:id/edit`（ADR-011 议题 3.B v9，B2 裁定归并进 C 轮的那一项）。
   * 上一版这里写死四条 + 一句「四条 jobs 路由」，加路由必然让它变红——
   * 这正是这条用例该有的行为：路由表变了一定要写代码的人看一眼，而不是没人记得它在。
   */
  const JOB_ROUTES = ['job-board', 'job-create', 'job-edit', 'job-trash', 'job-detail']

  it('五条 jobs 路由都存在、name 是 kebab-case、全部 requiresAuth', () => {
    const records = router.getRoutes().filter((route) => route.path.startsWith('/jobs'))
    const names = records.map((route) => String(route.name ?? '')).sort()

    expect(names).toEqual([...JOB_ROUTES].sort())
    // kebab-case：只允许小写字母与连字符，不能出现 camelCase 或下划线。
    for (const name of names) {
      expect(name).toMatch(/^[a-z][a-z]*(-[a-z]+)*$/)
    }
    for (const route of records) {
      expect(route.meta.requiresAuth, `${String(route.name)} 必须要求登录`).toBe(true)
    }
  })

  it('路径与 name 一一对应，且静态段不被 /jobs/:id 吞掉', () => {
    expect(router.resolve('/jobs').name).toBe('job-board')
    expect(router.resolve('/jobs/new').name).toBe('job-create')
    // 这条是本用例的存在理由：`trash` / `new` 若被当成 :id，详情页会拿 'trash' 去 GET /jobs/trash。
    expect(router.resolve('/jobs/trash').name).toBe('job-trash')
    expect(router.resolve('/jobs/trash').params).toEqual({})
    expect(router.resolve('/jobs/job-42').name).toBe('job-detail')
    expect(router.resolve('/jobs/job-42').params).toEqual({ id: 'job-42' })
    // 编辑态必须落进 `/jobs/:id/edit`，而不是被详情页吃掉（那会渲染成「id = job-42/edit」的 404）。
    expect(router.resolve('/jobs/job-42/edit').name).toBe('job-edit')
    expect(router.resolve('/jobs/job-42/edit').params).toEqual({ id: 'job-42' })
  })

  /**
   * 两条路由共用一个组件、靠 props 分流。这里读的是**路由表里那份 props 定义本身**
   * （`router.resolve()` 不回显算好的 props），因为组件行为取决于它：
   * `mode` 缺失时 `JobCreateView` 默认按 create 走、保存会打 `POST /jobs`——
   * 也就是说「编辑路由忘了传 mode」的后果不是报错，而是**把一条已有岗位另存成新岗位**。
   */
  it('job-create 给 mode=create，job-edit 给 mode=edit + jobId（同一组件的双模式入口）', () => {
    expect(propsPassedTo('job-create')).toEqual({ mode: 'create' })
    expect(propsPassedTo('job-edit', { id: 'job-42' })).toEqual({ mode: 'edit', jobId: 'job-42' })

    // 反向：录入路由不该带 jobId（带上了就等于「新建页以为自己在编辑」，会打 PUT）。
    expect(propsPassedTo('job-create', { id: 'x' })).not.toHaveProperty('jobId')
  })

  it('录入页与编辑页挂的是同一个组件，而不是复制了一份表单', () => {
    const byName = (name: string) => router.getRoutes().find((route) => route.name === name)
    const create = byName('job-create')?.components?.default
    const edit = byName('job-edit')?.components?.default

    // 引用相等：这两条路由必须指向同一个组件对象。漂移的代价见 JobCreateView 文件头。
    expect(create).toBeDefined()
    expect(edit).toBe(create)
  })
})

describe('核验计数的三句文案（2.D「计数必须可见」）', () => {
  it('三个数各成一句、按顺序出现，且 dropped 与 deduped 绝不并成一个数', () => {
    const lines = extractCountLines(
      extractMetaFixture({ received_count: 20, dropped_count: 14, deduped_count: 3, truncated: true }),
    )

    expect(lines).toEqual([
      'AI 给出 20 条，其中 14 条无法在 JD 原文中找到依据，已剔除',
      '另有 3 条重名已合并',
      '模型给出的条目超过 15 条，只保留了前 15 条',
    ])
    // 三句彼此独立：合并掉的数量不许混进那句「无法回溯原文」里。
    expect(lines[0]).not.toContain('重名')
    expect(lines[1]).not.toContain('无法在 JD 原文中找到依据')
    // 反向护栏：把两个数相加显示成一个（早期设想过的「共剔除 17 条」）一定通不过这三行。
    expect(lines.join('')).not.toContain('17')
  })

  it('每项为 0（`truncated` 为假）时那一句整条消失，三项全 0 时一句都不说', () => {
    expect(extractCountLines(extractMetaFixture({ dropped_count: 0, deduped_count: 1, truncated: false })))
      .toEqual(['另有 1 条重名已合并'])
    expect(extractCountLines(extractMetaFixture({ dropped_count: 3, deduped_count: 0, truncated: false })))
      .toEqual(['AI 给出 20 条，其中 3 条无法在 JD 原文中找到依据，已剔除'])
    expect(extractCountLines(extractMetaFixture({ dropped_count: 0, deduped_count: 0, truncated: false })))
      .toEqual([])
    // 草稿还没回来（null）时也不能崩，屏幕上什么都不显示。
    expect(extractCountLines(null)).toEqual([])
  })

  it('不把三个计数相加去凑 `received_count`（被封顶切掉的那格后端没有字段）', () => {
    // 这份数字刻意做成「前三项之和 < received_count」：20 ≠ 14 + 1 + 返回 3 条。
    const meta = extractMetaFixture({ received_count: 20, dropped_count: 14, deduped_count: 1, truncated: true })
    const rendered = extractCountLines(meta).join(' ')

    // 文案里出现的数只有 20 / 14 / 1 / 15 这几个原值；任何「合计 20 条」式的说法都会在这里变红，
    // 因为把 dropped + deduped + 返回条数 加起来会得到 18，一个与 20 对不上的数字。
    expect(rendered).toContain('20')
    expect(rendered).not.toContain('18')
    expect(meta.received_count).toBe(20)
    expect(meta.dropped_count + meta.deduped_count + 3).toBe(18)
  })
})

describe('提取失败的分支文案（禁止静默降级）', () => {
  it('三个 AI 码各给各的下一步，互不混用', () => {
    const missing = new ApiError('缺少 DeepSeek API Key。', 400, 'AI_KEY_MISSING')
    const invalid = new ApiError('Key 无效。', 401, 'AI_KEY_INVALID')
    const unavailable = new ApiError('AI 暂时不可用。', 502, 'AI_SERVICE_UNAVAILABLE')

    // 缺 Key 与 Key 无效都要把用户送去「AI 设置」，但不能是同一句话（一个是去填，一个是去修）。
    expect(extractFailureText(missing)).toContain('AI 设置')
    expect(extractFailureText(invalid)).toContain('AI 设置')
    expect(extractFailureText(missing)).not.toBe(extractFailureText(invalid))
    // 上游挂了只该让他重试，**不该**让他去动 Key 或重新登录。
    expect(extractFailureText(unavailable)).toContain('稍后重试')
    expect(extractFailureText(unavailable)).not.toContain('AI 设置')
    expect(extractFailureText(unavailable)).not.toContain('重新登录')
  })

  it('第 7 情形按 `finish_reason` 分流，不是按文案', () => {
    // 同一个码、同一个 status，差别只在 `finishReason`——这正是后端给的唯一区分手段。
    const truncated = new ApiError(
      'AI 输出的技能条目过多，被服务端的 token 上限切断了。请重试，并只粘贴 JD 的核心段落，或减少期望的条数。',
      502,
      'AI_SERVICE_UNAVAILABLE',
      null,
      'length',
    )
    const ordinary = new ApiError('AI 暂时不可用。', 502, 'AI_SERVICE_UNAVAILABLE')

    const truncatedText = extractFailureText(truncated)
    expect(truncatedText).toContain('请把 JD 里真正想要的技能写清楚')
    // 与「稍后重试」那条区分开：按第 4 情形的动作去处理第 7 情形，用户会反复点出同一个 502。
    expect(truncatedText).not.toBe(extractFailureText(ordinary))
    expect(extractFailureText(ordinary)).toContain('稍后重试')

    // 反向：拿一句**含「切断」字样但没有 finish_reason** 的普通 502 去骗分流逻辑，
    // 它必须仍走「稍后重试」——证明判据是字段而不是文案（服务端措辞随时会改）。
    const lookalike = new ApiError('模型输出被切断了。', 502, 'AI_SERVICE_UNAVAILABLE')
    expect(extractFailureText(lookalike)).toContain('稍后重试')
    expect(extractFailureText(lookalike)).not.toContain('把 JD 里真正想要的技能写清楚')
  })

  it('限流 / 校验 / 断网三种非 AI 失败也各有下一步，且都没有「什么都不显示」的分支', () => {
    expect(extractFailureText(new ApiError('请求太多。', 429, 'RATE_LIMITED'))).toContain('等一分钟')
    expect(extractFailureText(new ApiError('校验失败。', 422, 'VALIDATION_ERROR'))).toContain('8000')
    expect(extractFailureText(new ApiError('连不上。', 0, 'NETWORK_ERROR'))).toContain('后端')

    // 兜底：非 ApiError（代码 bug、取消请求之类）也必须出一句人话，不能返回空串。
    const fallback = extractFailureText(new Error('boom'))
    expect(fallback).not.toBe('')
    expect(fallback).toContain('提取失败')
    // 未知 code 走 default 分支：把服务端的 message 原样带出来，并说明岗位没被动过。
    expect(extractFailureText(new ApiError('没见过的错。', 500, 'WEIRD'))).toContain('没见过的错。')
  })
})

describe('编辑态回填（`jobToFormInput`）', () => {
  const asJob = (row: Record<string, unknown>) => row as unknown as JobsContract.JobRead

  it('把详情摊平成表单：`origin` 不进表单，`evidence_text` / `confidence` 要带回来', () => {
    const form = jobToFormInput(
      asJob(
        detail('job-1', {
          skills: [
            skillRow('s1', 'Python', { origin: 'jd_verifiable', confidence: '0.9000', evidence_text: '熟练掌握 Python' }),
            skillRow('s2', '沟通能力强', { type: null }),
          ],
        }),
      ),
    )

    expect(form.company_name).toBe('示例科技')
    // 详情里 `deadline` 是 `null`（长期有效）→ 表单回到空串：`<input type="date">` 不接受 null。
    expect(form.deadline).toBe('')
    expect(form.skills).toHaveLength(2)
    // `origin` 是服务端的派生结论，不是用户能填的东西：带进表单就等于「我声称我有出处」。
    expect(form.skills[0]).not.toHaveProperty('origin')
    // 但依据与把握必须带回来：否则在编辑页点一次保存就把上一次提取的锚点清空了。
    expect(form.skills[0]).toEqual({
      name: 'Python',
      type: 'hard',
      evidence_text: '熟练掌握 Python',
      confidence: '0.9000',
    })
    // 第二条没有依据 → 两个键，`type` 为 null 转成空串（`<select>` 不接受 null）。
    expect(form.skills[1]).toEqual({ name: '沟通能力强', type: '' })
    // 读模型的 `id` 也不许流进表单行（PUT 会把整批技能先删后插，id 没有意义）。
    expect(form.skills[0]).not.toHaveProperty('id')
  })

  it('一条技能都没有时补一行空行；再投影回请求体仍是空数组（不会提交一个空名字技能）', () => {
    const form = jobToFormInput(asJob(detail('job-1', { skills: [] })))
    expect(form.skills).toHaveLength(1)
    expect(form.skills[0]).toEqual({ name: '', type: '' })

    // 空行既不提交也不参与重名判断（`meaningfulSkills` 那一条），投影后是空数组。
    expect(toWritePayload(form).skills).toEqual([])
  })

  it('编辑保存打 PUT /jobs/{id}，成功后详情页同步成新值', async () => {
    const store = useJobStore()
    await store.loadDetail('job-1')

    const saved = await store.updateJob(
      'job-1',
      { ...jobToFormInput(store.detail as never), title: '后端实习（改）' },
    )

    expect(requestsFor('put', '/jobs/job-1')).toHaveLength(1)
    const body = requestsFor('put', '/jobs/job-1')[0]?.body as Record<string, unknown>
    expect(body.title).toBe('后端实习（改）')
    expect(body, 'PUT 不许带 status').not.toHaveProperty('status')
    expect(saved.title).toBe('后端实习（改）')
    // 详情页拿的是同一份 store：改完回详情页必须看到新值，而不是上一次 GET 的缓存。
    expect(store.detail?.title).toBe('后端实习（改）')
    // 编辑保存**不刷新列表**（见 `updateJob` 注释：列表不在屏幕上，重拉会把用户回来时那一屏换掉）。
    expect(requestsFor('get', '/jobs')).toHaveLength(0)
  })

  it('编辑保存同样先过提交前自检：重名技能不发请求', async () => {
    const store = useJobStore()
    await store.loadDetail('job-1')
    const before = captured.length

    await expect(
      store.updateJob('job-1', {
        ...jobToFormInput(store.detail as never),
        skills: [{ name: 'Python', type: 'hard' }, { name: ' python ', type: 'soft' }],
      }),
    ).rejects.toThrow('技能名称不能重复，请先合并同名技能。')

    expect(captured.length).toBe(before)
  })
})

/**
 * 人岗匹配（ADR-013 §10 第 8 步）。这里测 store 与文案函数那四件事，
 * 界面上怎么渲染在 `views/__tests__/JobDetailView.spec.ts`：
 *
 * 1. `runMatch` 真的打 `POST /jobs/{id}/match`，请求体为空（用默认档案，由响应回显）；
 *    并且**一个写请求都不发**——匹配是纯计算，落库的只有用户自己填的档案。
 * 2. 在途重复调用只发一次：这条限流按用户算 10/minute，双击就是两次真实计费。
 * 3. 结果的生命周期：重算前清、换岗位清、登出清。它带着**用户经历原文的连续子串**，
 *    挂在 store 上不清就是「下一个人看见上一个人的屏幕」。
 * 4. 五种退化分句（§6.5）+ 真的失败按码分支（§3 的状态码边界：AI 失败不在这里）。
 */
describe('人岗匹配（显式点击、纯计算、退化可归因）', () => {
  it('打对端点、结果进 store、请求体为空，且没有任何写请求', async () => {
    const store = useJobStore()

    const result = await store.runMatch('job-1')

    expect(result?.score).toBeCloseTo(0.669)
    expect(store.matchResult?.covered[0]?.skill).toBe('Python')
    expect(store.matching).toBe(false)

    const requests = requestsFor('post', '/jobs/job-1/match')
    expect(requests).toHaveLength(1)
    // 空请求体 = 「用当前用户的默认档案」这条隐式选择由后端决定，前端不许指定。
    expect(requests[0]?.body).toBeUndefined()
    expect(requestsFor('put')).toEqual([])
    expect(requestsFor('delete')).toEqual([])
  })

  it('在途时重复调用返回 null 且只发一次请求', async () => {
    const gate: { resolve?: (value: AxiosResponse) => void } = {}
    http.defaults.adapter = (config: InternalAxiosRequestConfig) =>
      new Promise<AxiosResponse>((resolve) => {
        captured.push({ method: 'post', url: config.url ?? '', query: {}, body: config.data })
        gate.resolve = resolve
      })

    const store = useJobStore()
    const first = store.runMatch('job-1')
    expect(store.matching).toBe(true)

    // axios 在 promise 链里才把请求交给适配器，所以要让它先跑一拍：
    // 下一句断言的是「适配器真的被调过一次」，否则后面那句「只有一次」会因为
    // 一次都没发出去而恒成立（假绿）。
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(gate.resolve).toBeTypeOf('function')
    expect(requestsFor('post', '/jobs/job-1/match')).toHaveLength(1)

    expect(await store.runMatch('job-1')).toBeNull()
    // 第二次调用连「在途」标记都没动：它直接返回 null，也就没有第二轮计费。
    expect(store.matching).toBe(true)

    gate.resolve?.(envelope({} as InternalAxiosRequestConfig, defaultMatchEnvelope()))
    expect((await first)?.job_id).toBe('job-1')
    expect(store.matching).toBe(false)
    expect(requestsFor('post', '/jobs/job-1/match')).toHaveLength(1)
  })

  it('重算前先清空上一次结果；换岗位（loadDetail）也清', async () => {
    const store = useJobStore()
    await store.runMatch('job-1')
    expect(store.matchResult).not.toBeNull()

    matchEnvelope = defaultMatchEnvelope({ score: 0.1 })
    const pending = store.runMatch('job-1')
    expect(store.matchResult).toBeNull()
    expect((await pending)?.score).toBe(0.1)

    await store.loadDetail('job-2')
    expect(store.matchResult).toBeNull()
  })

  it('reset()（登出）连匹配结果一起清——里面有用户经历的原文片段', async () => {
    const store = useJobStore()
    await store.runMatch('job-1')
    store.reset()
    expect(store.matchResult).toBeNull()
    expect(store.matching).toBe(false)
  })

  it('AI 失败是退化不是异常：store 这边没有 catch 分支，200 原样进结果', async () => {
    const store = useJobStore()
    // 后端把五种 AI 失败翻译成 `200 + meta.degrade_reason`，所以这里**不该**有 try/catch；
    // 这一条钉的是「实现者不要顺手加一个 catch，把退化说成失败」。
    matchEnvelope = defaultMatchEnvelope({
      meta: matchMetaFixture({
        model: null,
        prompt_version: null,
        evidence_mode: 'keyword_fallback',
        degrade_reason: 'key_missing',
        candidate_pairs: 0,
        verified_pairs: 0,
        finish_reason: null,
      }),
    })
    const result = await store.runMatch('job-1')

    expect(result).not.toBeNull()
    expect(result?.meta.evidence_mode).toBe('keyword_fallback')
    expect(store.matchResult?.meta.degrade_reason).toBe('key_missing')
  })

  it('退化分句：ai_scored 一句都不给，五种原因两两不同，前两种给出去哪儿修', () => {
    expect(matchDegradeLines(degradeSubject())).toEqual([])

    const reasons = ['key_missing', 'key_invalid', 'upstream', 'timeout', 'bad_output'] as const
    const texts = reasons.map((reason) =>
      matchDegradeLines(degradeSubject({ evidence_mode: 'keyword_fallback', degrade_reason: reason })).join(
        '\n',
      ),
    )
    expect(new Set(texts).size).toBe(5)
    for (const text of texts) expect(text).toContain('关键词')
    expect(texts[0]).toContain('AI 设置')
    expect(texts[1]).toContain('AI 设置')
    // 后三种只能等：把用户支去改一个本来没坏的 Key 是最坏的那种「有帮助的文案」。
    for (const text of texts.slice(2)) {
      expect(text).not.toContain('AI 设置')
      expect(text).not.toContain('API Key')
      expect(text).toContain('稍后再算一次')
    }

    // 零候选**不是退化**（其一）：命中的技能在经历里找不到出处，根本没向模型提问。
    // 此时说「按关键词算法给分」是假的，而用户能修的动作也不是配 Key（老板 2026-11-08 实测到的那一格）。
    const nothingToScore = matchDegradeLines(
      degradeSubject({ evidence_mode: 'keyword_fallback', degrade_reason: null }, 'matched_without_evidence'),
    )
    expect(nothingToScore).toHaveLength(2)
    expect(nothingToScore.join('\n')).toContain('找不到出处')
    expect(nothingToScore.join('\n')).not.toContain('关键词')
    expect(nothingToScore.join('\n')).not.toContain('AI 设置')

    // 零候选**不是退化**（其二）：一条技能都没命中时，上面那两句是**假话**——
    // 没有「命中的技能」可解释，缺口清单自己会说话。两种 null 必须分得开。
    expect(
      matchDegradeLines(
        degradeSubject({ evidence_mode: 'keyword_fallback', degrade_reason: null }, 'no_matched_skills'),
      ),
    ).toEqual([])

    // 岗位没填技能要求时一句都不给：没有可比对的东西，解释「证据强度怎么算」是噪音。
    expect(
      matchDegradeLines(
        degradeSubject(
          { evidence_mode: 'keyword_fallback', degrade_reason: null, no_required_skills: true },
          'no_matched_skills',
        ),
      ),
    ).toEqual([])
  })

  it('真的失败按码分支：429 等一分钟、404 说岗位读不到、401 说重新登录', () => {
    expect(matchFailureText(new ApiError('请求太多。', 429, 'RATE_LIMITED'))).toContain('等一分钟')
    expect(matchFailureText(new ApiError('资源不存在。', 404, 'NOT_FOUND'))).toContain('回收站')
    expect(
      matchFailureText(new ApiError('无效或已过期的访问令牌。', 401, 'CLIENT_ERROR')),
    ).toContain('重新登录')
    expect(matchFailureText(new ApiError('连不上。', 0, 'NETWORK_ERROR'))).toContain('后端')
    // 兜底也必须是一句人话，而且要说清「资料没被动过」。
    expect(matchFailureText(new Error('boom'))).toContain('没有被改动')
    expect(matchFailureText(new ApiError('没见过的错。', 500, 'WEIRD'))).toContain('没见过的错。')
    // 反向：这条分支里不许出现「AI 不可用」之类的说法——AI 失败到不了这里（§3 状态码边界），
    // 把它说成 AI 故障会让用户以为这次连分都没算出来。
    expect(matchFailureText(new ApiError('请求太多。', 429, 'RATE_LIMITED'))).not.toContain('不可用')
  })
})
