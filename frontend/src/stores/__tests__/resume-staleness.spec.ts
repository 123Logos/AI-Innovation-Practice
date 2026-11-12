/**
 * 「失效即点亮 / 脏即有差异」的 store 单测（批次 F6 · 守则 S1 S2 S5 + ADR-018 §2.5 §6）。
 *
 * 这三条以前各烂在一处，且都是**看不出来**的那种烂：
 * - S1：`applyPolish` 不失效，接受润色后纸面还挂着上一次生成的快照；
 * - S2：`markDirty()` 恒置真，把字改回去也亮着「保存修改」，点下去发一次无变更 PUT；
 * - S5：失效之后主行动不点亮，用户不知道下一步该点哪个。
 *
 * 所以本文件的断言都落在**用户能看见的那几格**上：`dirty` / `needsRegenerate` /
 * `regenerateHint` / `polishNoteFor()`，以及「有没有偷偷发请求」。
 * 刻意不测私有函数：判据是行为，不是实现。
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const profilesApi = vi.hoisted(() => ({
  listProfiles: vi.fn(),
  getProfile: vi.fn(),
  createProfile: vi.fn(),
  updateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  setDefaultProfile: vi.fn(),
  renameProfile: vi.fn(),
  listTrashProfiles: vi.fn(),
  restoreProfile: vi.fn(),
  purgeProfile: vi.fn(),
}))

const resumesApi = vi.hoisted(() => ({ polishResume: vi.fn(), generateResume: vi.fn() }))

vi.mock('@/api/profiles', () => profilesApi)
vi.mock('@/api/resumes', () => resumesApi)

import type { ResumeCopy } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import {
  DEFAULT_LAYOUT_SECTIONS,
  LAYOUT_VERSION,
  useResumeStore,
  type PolishResult,
} from '@/stores/resume'

const PROFILE_ID = 'profile-under-test'
const ORIGINAL_DESCRIPTION = '面向校内学生的二手交易平台。'

function serverProfile(): Record<string, unknown> {
  return {
    id: PROFILE_ID,
    user_id: 'user-under-test',
    title: '投后端的那一版',
    is_default: true,
    deleted_at: null,
    paper_size: 'A4',
    theme: 'forest',
    layout_config: {
      version: LAYOUT_VERSION,
      sections: DEFAULT_LAYOUT_SECTIONS.map((section) => ({ ...section })),
    },
    full_name: '张三',
    email: 'zhangsan@example.com',
    grade: '',
    contact: '13800000000',
    target_role: '后端实习生',
    created_at: '2026-11-01T10:00:00+08:00',
    updated_at: '2026-11-01T10:00:00+08:00',
    skills: [{ id: 'skill-1', name: 'Python' }],
    experiences: [
      {
        id: 'exp-1',
        title: '校园二手交易平台',
        start_date: '2024-03-01',
        end_date: '2024-08-31',
        description: ORIGINAL_DESCRIPTION,
        responsibility: '负责后端接口。',
      },
    ],
    educations: [
      {
        id: 'edu-1',
        school: '示例大学',
        major: '计算机',
        degree: '本科',
        edu_start: '2021-09-01',
        edu_end: '2025-06-30',
      },
    ],
  }
}

/** 走真实读路径铺一份「与服务端一致」的档案：基线由 `loadFromServer` 自己建立。 */
async function seedSaved() {
  const store = useResumeStore()
  const profile = serverProfile()
  profilesApi.listProfiles.mockResolvedValue([
    {
      id: PROFILE_ID,
      title: '投后端的那一版',
      is_default: true,
      full_name: '张三',
      created_at: '2026-11-01T10:00:00+08:00',
      updated_at: '2026-11-01T10:00:00+08:00',
      deleted_at: null,
    },
  ])
  profilesApi.getProfile.mockResolvedValue(profile)
  await store.loadFromServer()
  return store
}

function polishResult(overrides: Partial<ResumeCopy['experiences'][number]> = {}): PolishResult {
  return {
    copy: {
      skills: ['Python'],
      experiences: [
        {
          index: 0,
          description: '主导校内二手交易平台的后端设计，把下单与检索收敛为统一分页协议。',
          responsibility: '负责后端架构与检索链路。',
          target_words: null,
          over_limit_by: 0,
          truncated: false,
          ...overrides,
        },
      ],
    },
    originals: [
      {
        title: '校园二手交易平台',
        description: ORIGINAL_DESCRIPTION,
        responsibility: '负责后端接口。',
      },
    ],
    originalSkills: ['Python'],
    mode: 'expand',
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
  const auth = useAuthStore()
  auth.user = {
    id: 'user-under-test',
    username: 'tester',
    email: null,
    created_at: '2026-11-10T10:00:00+08:00',
  }
})

describe('S2：脏 = 内容有差异，不是「动过」', () => {
  it('刚加载完不脏；改一个字变脏；改回去又不脏', async () => {
    const store = await seedSaved()
    expect(store.dirty).toBe(false)

    store.updateProfileField('full_name', '张三丰')
    expect(store.dirty).toBe(true)

    store.updateProfileField('full_name', '张三')
    expect(store.dirty).toBe(false)
  })

  it('只加一行空技能不算脏（空行不是内容）', async () => {
    const store = await seedSaved()
    store.addSkill()
    expect(store.dirty).toBe(false)
  })

  it('加一行空经历也不点亮主行动：没有净变化就不该催用户去生成', async () => {
    const store = await seedSaved()
    store.addExperience()
    expect(store.dirty).toBe(false)
    expect(store.needsRegenerate).toBe(false)
    expect(store.regenerateHint).toBeNull()
  })

  it('没有差异时 save() 一次请求都不发，直接把当前那份还回来', async () => {
    const store = await seedSaved()
    store.updateProfileField('full_name', '张三丰')
    store.updateProfileField('full_name', '张三')

    const returned = await store.save()

    expect(profilesApi.updateProfile).toHaveBeenCalledTimes(0)
    expect(profilesApi.createProfile).toHaveBeenCalledTimes(0)
    expect(returned.id).toBe(PROFILE_ID)
  })

  it('真有差异时照常发 PUT（上一条不是把保存整条关掉）', async () => {
    const store = await seedSaved()
    profilesApi.updateProfile.mockResolvedValue(serverProfile())
    store.updateProfileField('full_name', '张三丰')

    await store.save()

    expect(profilesApi.updateProfile).toHaveBeenCalledTimes(1)
  })
})

describe('S1 / S5：任何让生成物过期的动作都点亮同一个主行动', () => {
  it('接受润色：填进表单 + 点亮主行动 + 给一行原因，且不自动重生成', async () => {
    const store = await seedSaved()
    const result = polishResult()

    store.applyPolish(result)

    expect(store.draft.experiences[0]?.description).toBe(result.copy.experiences[0]?.description)
    expect(store.needsRegenerate).toBe(true)
    expect(store.regenerateHint).toBe('内容已更新，点击生成最新简历。')
    // 自动重生成会替用户花他自己的额度——这条是老板 2026-11-09 的原话。
    expect(resumesApi.generateResume).toHaveBeenCalledTimes(0)
    expect(profilesApi.updateProfile).toHaveBeenCalledTimes(0)
  })

  it('改主题 / 改排版 / 改文案三类入口点亮的是同一格，不是各写一份', async () => {
    const store = await seedSaved()

    store.updateTheme('navy')
    expect(store.needsRegenerate).toBe(true)
    expect(store.regenerateHint).toBe('主题已修改，需重新生成简历。')

    store.moveSectionUp('skills')
    expect(store.regenerateHint).toBe('排版已修改，需重新生成简历。')

    store.updateExperience(0, 'description', '换一段描述')
    expect(store.regenerateHint).toBe('内容已更新，点击生成最新简历。')
  })

  it('保存之后提示仍在：落库不等于生成，纸面快照还是旧的', async () => {
    const store = await seedSaved()
    profilesApi.updateProfile.mockResolvedValue(serverProfile())
    store.updateProfileField('full_name', '张三丰')
    expect(store.needsRegenerate).toBe(true)

    await store.save()

    expect(store.dirty).toBe(false)
    expect(store.needsRegenerate).toBe(true)
    expect(resumesApi.generateResume).toHaveBeenCalledTimes(0)
  })

  it('生成成功之后提示与高亮一起消失', async () => {
    const store = await seedSaved()
    resumesApi.generateResume.mockResolvedValue({
      resume_id: 'resume-1',
      title: '投后端的那一版',
      content_html: '<div>生成物</div>',
      created_at: '2026-11-11T10:00:00+08:00',
    })
    store.updateProfileField('full_name', '张三丰')
    expect(store.needsRegenerate).toBe(true)

    await store.generate()

    expect(store.needsRegenerate).toBe(false)
    expect(store.regenerateHint).toBeNull()
  })

  it('接受润色时若正停在「已生成简历」，切回实时预览（快照不会跟着表单变）', async () => {
    const store = await seedSaved()
    resumesApi.generateResume.mockResolvedValue({
      resume_id: 'resume-1',
      title: '投后端的那一版',
      content_html: '<div>旧快照</div>',
      created_at: '2026-11-11T10:00:00+08:00',
    })
    await store.generate()
    expect(store.isGeneratingResultVisible).toBe(true)

    store.applyPolish(polishResult())

    expect(store.isGeneratingResultVisible).toBe(false)
    expect(store.needsRegenerate).toBe(true)
  })
})

describe('ADR-018：超限是可见信息，不是损失', () => {
  it('超目标要报数，且跟着用户现在的文字实时算——删到目标以内自己消失', async () => {
    const store = await seedSaved()
    const result = polishResult({ target_words: 20, over_limit_by: 999 })
    store.applyPolish(result)

    const current = store.draft.experiences[0]
    const length = (current?.description.length ?? 0) + (current?.responsibility.length ?? 0)
    // 提示里的数字来自**当前文字**，不是服务端那一刻的 999。
    expect(store.polishNoteFor(0)).toBe(`比目标多 ${length - 20} 字，删掉 ${length - 20} 字再保存。`)

    store.updateExperience(0, 'description', '短描述')
    store.updateExperience(0, 'responsibility', '短职责')
    expect(store.polishNoteFor(0)).toBeNull()
  })

  it('撞契约那一道只说「被截断了」，不报少了多少字（老板第 6 轮裁定）', async () => {
    const store = await seedSaved()
    store.applyPolish(polishResult({ truncated: true }))

    const note = store.polishNoteFor(0) ?? ''
    expect(note).toContain('已被截断')
    expect(note).not.toMatch(/\d+ 字/)
  })

  it('没超过也没截断时不显示任何提示（不制造噪声）', async () => {
    const store = await seedSaved()
    store.applyPolish(polishResult({ target_words: 800, over_limit_by: 0 }))

    expect(store.polishNoteFor(0)).toBeNull()
  })

  it('换一份档案之后上一份的润色目标不跟着走', async () => {
    const store = await seedSaved()
    store.applyPolish(polishResult({ target_words: 1, over_limit_by: 99 }))
    expect(store.polishNoteFor(0)).not.toBeNull()

    await store.loadFromServer()

    expect(store.polishNoteFor(0)).toBeNull()
    expect(store.needsRegenerate).toBe(false)
  })
})

/**
 * 保存前的作品 / 证书自检（2026-11-12 补）。
 *
 * 起因是一句用户能看见、但**照着无法动手**的报错：
 * 「保存失败：字段「portfolio_links.0」未通过后端校验，请检查后重试。」
 * 根因是网址没写 `http://`（后端 `PortfolioLinkInput` 只收 http/https，防 `<a href>` 的
 * `javascript:` 通道），而当时前端一个字都没拦、422 的文案又只剩字段路径。
 *
 * 所以这组用例的判据有三条，缺一条都算没修好：
 * 1. **不发请求**——前端能拦的错不该靠一次往返去发现；
 * 2. 文案**指到第几条、缺什么**（C3：用户修得动）；
 * 3. 合法的网址必须放行（只加闸不开路等于把作品功能关掉）。
 */
describe('作品 / 证书：保存前就把「缺 http://」说成一句人话', () => {
  it('网址没写协议：拦住、不发 PUT、文案指到第 1 条并说清缺什么', async () => {
    const store = await seedSaved()
    store.addPortfolioLink()
    store.updatePortfolioLink(0, 'label', '技术博客')
    store.updatePortfolioLink(0, 'url', 'example.com/blog')

    await expect(store.save()).rejects.toThrow(
      '第 1 条作品链接的网址要以 http:// 或 https:// 开头（如 https://example.com）。',
    )
    expect(profilesApi.updateProfile).not.toHaveBeenCalled()
  })

  it('网址写全了就放行——闸不能顺手把功能关掉', async () => {
    const store = await seedSaved()
    store.addPortfolioLink()
    store.updatePortfolioLink(0, 'label', '技术博客')
    store.updatePortfolioLink(0, 'url', 'https://example.com/blog')
    profilesApi.updateProfile.mockResolvedValue(serverProfile())

    await store.save()
    expect(profilesApi.updateProfile).toHaveBeenCalledTimes(1)
  })

  it('两条作品填同一个网址：说「合并成一条」，而不是抛一个唯一键 422', async () => {
    const store = await seedSaved()
    store.addPortfolioLink()
    store.updatePortfolioLink(0, 'label', '博客')
    store.updatePortfolioLink(0, 'url', 'https://example.com')
    store.addPortfolioLink()
    store.updatePortfolioLink(1, 'label', '主页')
    store.updatePortfolioLink(1, 'url', 'https://Example.com')

    await expect(store.save()).rejects.toThrow('两条作品链接填了同一个网址，请先合并成一条。')
    expect(profilesApi.updateProfile).not.toHaveBeenCalled()
  })

  it('证书名称重复也在前端拦下（后端那条 422 同样只剩字段路径）', async () => {
    const store = await seedSaved()
    store.addCertificate()
    store.updateCertificate(0, 'name', '大学英语六级')
    store.addCertificate()
    store.updateCertificate(1, 'name', '大学英语六级')

    await expect(store.save()).rejects.toThrow('证书名称不能重复，请先合并同名证书。')
    expect(profilesApi.updateProfile).not.toHaveBeenCalled()
  })

  /**
   * 网址长度这条边（后端 `max_length=500`）此前**一条用例都没有**，是 G1 红出来才发现的。
   *
   * 判据两层：① 超长要拦、不能发请求；② 文案里**不许出现三位数字**——这条不是形式主义，
   * 那个上限正好是 `500`，写进文案会被 G1 当成裸状态码（C2）判红。守则禁止用豁免标记换绿，
   * 所以真正的修法是「不改上限、改说法」：上限留着，数字不出现在用户眼前。
   */
  it('网址超过上限：拦住、不发 PUT，且文案不把上限数字写出来', async () => {
    const store = await seedSaved()
    store.addPortfolioLink()
    store.updatePortfolioLink(0, 'label', '技术博客')
    store.updatePortfolioLink(0, 'url', `https://example.com/${'a'.repeat(500)}`)

    await expect(store.save()).rejects.toThrow(
      '第 1 条作品链接的网址太长了，请换一个更短的链接后再保存。',
    )
    expect(profilesApi.updateProfile).not.toHaveBeenCalled()

    const message = await store.save().catch((error: Error) => error.message)
    // 三位数一出现就会被 G1 读成状态码；这里把那条口径钉在用例里，防止下次有人「顺手补个数字」。
    expect(message).not.toMatch(/(?<![\w.])(?:4\d{2}|5\d{2})(?![\w.])/)
  })
})
