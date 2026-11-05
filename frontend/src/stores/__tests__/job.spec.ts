/**
 * `useJobStore` 与岗位路由表的单测（Phase 4 · B2）。
 *
 * 分三半，各自钉住一条「坏了不会报错、只会让用户看见错数据」的链路：
 *
 * 1. **状态流转**：列表加载 → 创建 → 软删 → 回收站 → 恢复。
 *    这五步各自都会写 store 里的一份列表，任何一步漏刷新，用户就会对着一条
 *    「已经删掉却还在列表里」的岗位反复点。做法同 `api/__tests__/jobs.spec.ts`：
 *    换掉 axios 的 adapter，让真实的 store → api → 拦截器 链路跑一遍。
 * 2. **路由表自证**：四条 jobs 路由存在、`name` 是 kebab-case、`requiresAuth` 都在，
 *    并且 `/jobs/trash`、`/jobs/new` 不会被 `/jobs/:id` 吞掉。
 *    放在这里而不是单开一个 spec 文件：本轮允许新增的测试文件只有这两个，
 *    而这条断言属于「岗位切片能不能走通」，与 store 是同一条验收线。
 * 3. **技能名归一化口径（B3）**：`normalizeSkillName()` 与后端 `dedup_key` 同口径
 *    （`trim` → NFKC → 小写），并钉住它的两个实际用途——「你已具备」与录入页重名自检。
 *    这条属于「前端放过、后端 422」那一类只有比对两端才能发现的缺陷，所以跟状态流转放一起。
 *
 * 反向断言（请求体里不许出现 `evidence_text` / `confidence` / `origin` / `status`）
 * 用的是「把读模型原样当表单行塞进来」这种真实误用形态——后端是 `extra="ignore"`，
 * 传错了**不报错**，只有这里能发现。
 */

import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { http } from '@/api/client'
import type { jobsApi as JobsContract } from '@/api'
import { createEmptyJobForm, normalizeSkillName, useJobStore } from '@/stores/job'
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
 * `/profiles/me` 返回的「我的」技能名。默认那一条带首尾空格，是为了钉住 `trim`；
 * 「归一化口径」那一组用例把它改成全角写法（`mySkillNamesFromServer`），
 * 其余分支一概不动。
 */
const DEFAULT_MY_SKILL_NAMES = [' Python ']
let mySkillNamesFromServer: string[] = DEFAULT_MY_SKILL_NAMES

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

  if (url === '/profiles/me') {
    return Promise.resolve(
      envelope(config, {
        data: {
          profile: { skills: mySkillNamesFromServer.map((name, index) => ({ id: `s${index + 1}`, name, self_rating: 3 })) },
        },
        meta: { request_id: 'req-test' },
      }),
    )
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
  mySkillNamesFromServer = DEFAULT_MY_SKILL_NAMES
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
  it('创建请求体里没有 evidence_text / confidence / origin / status，空技能行也不提交', async () => {
    const store = useJobStore()
    await store.createJob(filledForm())

    // 取**那一次 POST**的请求体，不是最后一条请求（创建成功后 store 还会刷新列表）。
    const post = requestsFor('post', '/jobs')[0]
    expect(post, '没有发出 POST /jobs').toBeDefined()
    const body = post?.body as Record<string, unknown> & { skills: Record<string, unknown>[] }

    for (const key of ['status', 'origin', 'evidence_text', 'confidence', 'created_by_user_id']) {
      expect(body, `请求体不该带 ${key}`).not.toHaveProperty(key)
    }
    expect(body.skills).toHaveLength(2)
    for (const skill of body.skills) {
      expect(Object.keys(skill).sort()).toEqual(['name', 'type'])
    }
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
  it('「你已具备」按 trim + NFKC + 小写比对，命中与不命中都只读，不写任何表', async () => {
    const store = useJobStore()
    await Promise.all([store.loadDetail('job-1'), store.loadMySkillNames()])

    // `/profiles/me` 里的技能名是 `' Python '`（带空格），岗位技能是 `'Python'`。
    expect(store.mySkillNames).toEqual([' Python '])
    expect(store.matchesMySkill('Python')).toBe(true)
    expect(store.matchesMySkill('python')).toBe(true)
    expect(store.matchesMySkill('沟通能力强')).toBe(false)

    // 全程只有 GET：比对不产生写请求（人岗匹配仍冻结，见 ADR-011 2.E）。
    expect(requestsFor('post')).toEqual([])
    expect(requestsFor('put')).toEqual([])
    expect(requestsFor('delete')).toEqual([])
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

  it('用途一「你已具备」：全角写法双向都能命中，不命中依旧不命中', async () => {
    const store = useJobStore()

    // 档案（`/profiles/me`）里是半角 `Python`，岗位技能写成全角 `Ｐython`。
    await store.loadMySkillNames()
    expect(store.matchesMySkill('Ｐython')).toBe(true)
    expect(store.matchesMySkill('ＰＹＴＨＯＮ')).toBe(true)

    // 反向：档案里是全角，岗位技能是半角。
    mySkillNamesFromServer = ['Ｐython']
    await store.loadMySkillNames()
    expect(store.matchesMySkill('python')).toBe(true)
    expect(store.matchesMySkill('Python ')).toBe(true)

    // 放宽只到「全半角 + 大小写 + 首尾空格」这一档，没有变成前缀/包含匹配。
    expect(store.matchesMySkill('PyTorch')).toBe(false)
    expect(store.matchesMySkill('沟通能力强')).toBe(false)
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

describe('岗位路由表（议题 3.B 的自证）', () => {
  const JOB_ROUTES = ['job-board', 'job-create', 'job-trash', 'job-detail']

  it('四条 jobs 路由都存在、name 是 kebab-case、全部 requiresAuth', () => {
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
  })
})
