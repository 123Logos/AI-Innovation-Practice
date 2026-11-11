/**
 * AI 润色的 store 单测（ADR-010 §5「草稿态确认」）。
 *
 * 这里钉的是**这条流程唯一的安全承诺**：
 *
 * 1. `polish()` 成功之后，`draft` 一个字段都不许变、`dirty` 必须是 false。
 *    一旦这里被改成「拿到结果就写进表单」，用户会在没看过的情况下被换掉自己写的文字，
 *    而且下一次「保存修改」会把 AI 的产出当成用户自己的内容落库。
 * 2. `applyPolish()` 必须同时做到：改写描述与职责、**不碰标题与日期**、按 `index` 对齐、
 *    按服务端给的顺序重排技能、置 `dirty`，并且**不发任何保存请求**。
 * 3. `discardPolish()` 之后表单必须与润色前逐字相同。
 * 4. `max_words` 只在 `expand` 档带上，缺省补 200 —— 与服务端校验层同一口径，
 *    两边走样会让「界面上写的字数」和「服务端截断用的字数」不是同一个数。
 *
 * `@/api/resumes` 整体被 mock：本文件验的是 store 的编排，不是 axios。
 */

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, setDeepSeekKey } from '@/api/client'
import type { ResumeCopy } from '@/api/types'
import { useResumeStore } from '@/stores/resume'

// vitest 会把 `vi.mock` 提升到所有 import 之前，所以写在下面也一样生效。
const polishResume = vi.hoisted(() => vi.fn())
const generateResume = vi.hoisted(() => vi.fn())

vi.mock('@/api/resumes', () => ({ polishResume, generateResume }))

const TEST_KEY = 'sk-store-test'

const FIRST_POLISHED = '主导校内二手交易平台的后端设计，把下单与检索收敛为统一分页协议'
const SECOND_POLISHED = '负责知识图谱的实体对齐与召回链路'

function copyFixture(): ResumeCopy {
  return {
    skills: ['SQL', 'Python'],
    experiences: [
      {
        index: 0,
        description: FIRST_POLISHED,
        responsibility: '负责后端架构与检索',
        target_words: null,
        over_limit_by: 0,
        truncated: false,
      },
      {
        index: 1,
        description: SECOND_POLISHED,
        responsibility: '负责图谱构建',
        target_words: null,
        over_limit_by: 0,
        truncated: false,
      },
    ],
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  localStorage.clear()
  setDeepSeekKey(TEST_KEY)
  // `clearMocks` 只清调用记录、不清实现，所以「上一条用例塞的失败」会漏到下一条。
  // 这里显式重置并给出默认成功结果，需要失败的用例改用 `mockRejectedValueOnce`。
  polishResume.mockReset().mockResolvedValue(copyFixture())
  generateResume.mockReset()
})

/**
 * 直接铺草稿，而不是走 `addSkill` / `updateExperience`：
 * 那些方法会顺手走 `markContentChanged()`，而本文件要用「dirty 是否为 true」证明
 * 润色流程自己有没有偷偷改表单——起始状态必须是干净的。
 */
function seedDraft() {
  const store = useResumeStore()
  store.draft.full_name = '张三'
  store.draft.contact = '13800000000'
  store.draft.skills = [
    { name: 'Python' },
    { name: '' },
    { name: 'SQL' },
  ]
  store.draft.experiences = [
    {
      title: '校园二手交易平台',
      start_date: '2024-03-01',
      end_date: '2024-06-30',
      description: '面向校内学生的二手交易 Web 应用',
      responsibility: '负责后端 API 与数据库设计',
    },
    {
      title: '知识图谱问答系统',
      start_date: '2024-09-01',
      end_date: '',
      description: '基于图谱的问答系统',
      responsibility: '负责检索',
    },
  ]
  store.dirty = false
  return store
}

describe('polish 的前置拦截', () => {
  it('没有 DeepSeek Key 时抛 AI_KEY_MISSING，且一个请求都不发', async () => {
    const store = seedDraft()
    setDeepSeekKey('')

    const error = await store.polish({ mode: 'medium' }).catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('AI_KEY_MISSING')
    expect(polishResume).not.toHaveBeenCalled()
  })

  it('技能为空 / 经历为空 / 经历一个字都没写，都给出人话提示且不开弹窗', () => {
    const store = useResumeStore()
    store.draft.skills = []
    expect(store.validateForPolish()).toContain('技能')

    store.draft.skills = [{ name: 'Python' }]
    store.draft.experiences = []
    expect(store.validateForPolish()).toContain('项目经历')

    store.draft.experiences = [
      { title: '有标题没内容', start_date: '', end_date: '', description: '  ', responsibility: '' },
    ]
    expect(store.validateForPolish()).toContain('先写几句')
  })
})

describe('polish 的请求形状', () => {
  it('只发有效行（丢弃空技能行），档位与非 expand 的 max_words 一并带清', async () => {
    const store = seedDraft()
    polishResume.mockResolvedValue(copyFixture())

    await store.polish({ mode: 'heavy', maxWords: 80 })

    const payload = polishResume.mock.calls[0]![0]
    expect(payload.mode).toBe('heavy')
    // 非 expand 档必须显式传 null：后端也会置空，但前端不能留着让人误以为生效了。
    expect(payload.max_words).toBeNull()
    expect(payload.skills.map((skill: { name: string }) => skill.name)).toEqual(['Python', 'SQL'])
    expect(payload.experiences.map((item: { title: string }) => item.title)).toEqual([
      '校园二手交易平台',
      '知识图谱问答系统',
    ])
    expect(payload.profile).toEqual({
      full_name: '张三',
      contact: '13800000000',
      email: null,
    })
  })

  it('expand 缺省补 200，传了就照搬', async () => {
    const store = seedDraft()
    polishResume.mockResolvedValue(copyFixture())

    await store.polish({ mode: 'expand' })
    expect(polishResume.mock.calls[0]![0].max_words).toBe(200)

    await store.polish({ mode: 'expand', maxWords: 320 })
    expect(polishResume.mock.calls[1]![0].max_words).toBe(320)
  })
})

describe('润色结果在用户确认之前不得改动表单', () => {
  it('polish 成功后 draft 逐字不变、dirty 仍为 false，只留下待确认快照', async () => {
    const store = seedDraft()
    const before = JSON.parse(JSON.stringify(store.draft))
    polishResume.mockResolvedValue(copyFixture())

    const result = await store.polish({ mode: 'medium' })

    expect(JSON.parse(JSON.stringify(store.draft))).toEqual(before)
    expect(store.dirty).toBe(false)
    expect(store.polishResult).not.toBeNull()
    // 左栏用的是「发出去那一刻」的快照，而不是接受时再读一次 draft。
    expect(result.originals[0]!.description).toBe('面向校内学生的二手交易 Web 应用')
    expect(result.originalSkills).toEqual(['Python', 'SQL'])
    expect(result.mode).toBe('medium')
  })

  it('polish 失败时不落 polishResult，也不碰表单', async () => {
    const store = seedDraft()
    polishResume.mockRejectedValueOnce(
      new ApiError('AI 服务当前不可用', 502, 'AI_SERVICE_UNAVAILABLE'),
    )

    const before = JSON.parse(JSON.stringify(store.draft))
    await expect(store.polish({ mode: 'medium' })).rejects.toThrow()

    expect(store.polishResult).toBeNull()
    expect(JSON.parse(JSON.stringify(store.draft))).toEqual(before)
    expect(store.dirty).toBe(false)
  })
})

describe('applyPolish：接受并同步到表单', () => {
  it('按 index 改写描述与职责，标题与日期一个字段都不动，并置 dirty', async () => {
    const store = seedDraft()
    const result = await store.polish({ mode: 'medium' })

    store.applyPolish(result)

    const [first, second] = store.draft.experiences
    expect(first!.description).toBe(FIRST_POLISHED)
    expect(first!.responsibility).toBe('负责后端架构与检索')
    expect(second!.description).toBe(SECOND_POLISHED)
    // 事实字段必须由用户自己改：AI 无权改标题与起止时间。
    expect(first!.title).toBe('校园二手交易平台')
    expect(first!.start_date).toBe('2024-03-01')
    expect(first!.end_date).toBe('2024-06-30')
    expect(second!.end_date).toBe('')
    expect(store.dirty).toBe(true)
    expect(store.polishResult).toBeNull()
  })

  it('按服务端给的顺序重排技能，不新增、不丢行，空白行留在尾部', async () => {
    const store = seedDraft()
    const result = await store.polish({ mode: 'medium' })

    store.applyPolish(result)

    expect(store.draft.skills.map((skill) => skill.name)).toEqual(['SQL', 'Python', ''])
  })

  it('接受这一步绝不落库：不发保存、也不发生成请求', async () => {
    const store = seedDraft()
    const result = await store.polish({ mode: 'medium' })

    store.applyPolish(result)

    expect(generateResume).not.toHaveBeenCalled()
    expect(polishResume).toHaveBeenCalledTimes(1)
  })
})

describe('discardPolish：放弃就什么都不改', () => {
  it('表单逐字保持润色前原样，dirty 仍为 false', async () => {
    const store = seedDraft()
    const before = JSON.parse(JSON.stringify(store.draft))
    await store.polish({ mode: 'medium' })

    store.discardPolish()

    expect(store.polishResult).toBeNull()
    expect(JSON.parse(JSON.stringify(store.draft))).toEqual(before)
    expect(store.dirty).toBe(false)
  })
})

describe('切换简历与登出都要丢掉待确认的候选文案', () => {
  it('reset() 之后 polishResult 为空：A 的建议不能被「接受」到 B 上', async () => {
    const store = seedDraft()
    await store.polish({ mode: 'medium' })
    expect(store.polishResult).not.toBeNull()

    store.reset()

    expect(store.polishResult).toBeNull()
  })
})
