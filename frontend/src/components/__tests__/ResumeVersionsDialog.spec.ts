/**
 * 版本历史 / 两版对比弹窗（批次 B8 剩余，`ADR-026` §2.4 / §2.6）。
 *
 * 只钉三件事，其余交给后端与 store 的单测：
 *
 * 1. **链头单独标出来**（「当前简历」）：定制版会进历史但**不成为链头**，
 *    不标出来用户会以为最新那行就是当前简历。
 * 2. `comparable === false` 时**必须显示那句说明**、且不渲染空摘要——
 *    把「比不了」画成「两版一样」是本模块最坏的失败形态。
 * 3. `order_changed` 要说出来：按岗位定制的差异只出现在这里，
 *    不说的话那个功能看起来像什么都没做。
 *
 * element-plus 组件在本仓测试环境里没有全局注册（既有 spec 也这么处理），
 * 所以桩掉它们；el-button 桩成真按钮，才能点到「对比这两版」。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ResumeVersionsDialog from '@/components/ResumeVersionsDialog.vue'
import type { ResumeDiff, ResumeVersionSummary } from '@/api/types'
import { useResumeStore } from '@/stores/resume'

const VERSION_ONE: ResumeVersionSummary = {
  resume_id: 'resume-v1',
  version_no: 1,
  title: '通用版',
  origin: 'generated',
  is_head: true,
  target_job_id: null,
  created_at: '2026-11-12T10:00:00+08:00',
}

const VERSION_TWO: ResumeVersionSummary = {
  resume_id: 'resume-v2',
  version_no: 2,
  title: '通用版 · 面向「Python 后端实习」',
  origin: 'customized',
  is_head: false,
  target_job_id: 'job-1',
  created_at: '2026-11-13T10:00:00+08:00',
}

const COMPARABLE_DIFF: ResumeDiff = {
  from_version: VERSION_ONE,
  to_version: VERSION_TWO,
  comparable: true,
  note: null,
  summary: {
    added: 0,
    removed: 0,
    changed: 0,
    sections_changed: 1,
    section_order_changed: false,
    item_order_changed: true,
  },
  sections: [
    {
      key: 'skills',
      label: '技能',
      change: 'changed',
      title_before: '技能',
      title_after: '技能',
      order_changed: true,
      items: [],
    },
  ],
}

const UNCOMPARABLE_DIFF: ResumeDiff = {
  from_version: VERSION_ONE,
  to_version: VERSION_TWO,
  comparable: false,
  note: '其中一版的排版结构无法识别（可能是更早版本生成的），本次只能看两版的正文，不能逐条对比。',
  summary: {
    added: 0,
    removed: 0,
    changed: 0,
    sections_changed: 0,
    section_order_changed: false,
    item_order_changed: false,
  },
  sections: [],
}

let pinia: Pinia

const EL_STUBS = {
  'el-dialog': { template: '<div class="stub-dialog"><slot /></div>' },
  'el-select': { template: '<div class="stub-select"><slot /></div>' },
  'el-option': true,
  'el-button': {
    props: ['loading', 'disabled', 'type'],
    emits: ['click'],
    template: '<button class="stub-button" @click="$emit(\'click\')"><slot /></button>',
  },
}

async function mountDialog(versions: ResumeVersionSummary[]) {
  const store = useResumeStore()
  store.versions = versions
  vi.spyOn(store, 'loadVersions').mockResolvedValue(undefined)
  const compare = vi.spyOn(store, 'compareVersions').mockResolvedValue(undefined)

  const wrapper = mount(ResumeVersionsDialog, {
    props: { modelValue: true },
    global: { plugins: [pinia], stubs: EL_STUBS },
  })
  await flushPromises()
  return { wrapper, store, compare }
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('版本历史 / 两版对比（ADR-026）', () => {
  it('列出历史版本，并把链头与来源分开标出来', async () => {
    const { wrapper } = await mountDialog([VERSION_TWO, VERSION_ONE])

    const rows = wrapper.findAll('.version-row')
    expect(rows).toHaveLength(2)
    // 倒序来自后端；界面只按收到的顺序渲染，不自己再排一次。
    expect(rows[0].text()).toContain('v2')
    expect(rows[1].text()).toContain('v1')

    // 链头徽标只该出现在 v1 上——定制版进历史但**不移动链头**（§2.2）。
    expect(rows[0].find('.badge--head').exists()).toBe(false)
    expect(rows[1].find('.badge--head').exists()).toBe(true)
    // 来源徽标两行都有，且不同：这是「哪一版是定制的」的唯一线索。
    expect(rows[0].find('.badge--custom').exists()).toBe(true)
    expect(rows[1].find('.badge--generated').exists()).toBe(true)
  })

  it('点「对比这两版」带上默认的「上一版 → 最新一版」', async () => {
    const { wrapper, compare } = await mountDialog([VERSION_TWO, VERSION_ONE])

    await wrapper.find('.compare-button').trigger('click')
    await flushPromises()

    expect(compare).toHaveBeenCalledWith('resume-v1', 'resume-v2')
  })

  it('可比的差异渲染计数与「条目顺序有调整」', async () => {
    const { wrapper, store } = await mountDialog([VERSION_TWO, VERSION_ONE])
    store.versionDiff = COMPARABLE_DIFF
    await flushPromises()

    const diff = wrapper.get('[data-testid="version-diff"]')
    expect(diff.text()).toContain('新增 0')
    expect(diff.text()).toContain('删除 0')
    expect(diff.text()).toContain('修改 0')
    expect(diff.text()).toContain('条目顺序有调整')
    // 「两版完全一样」那句话不该在顺序变化时出现——它会让用户以为白定制了一次。
    expect(diff.text()).not.toContain('这两版的内容完全一样')
  })

  it('比不了时只显示说明，不渲染一份「看起来一样」的空差异', async () => {
    const { wrapper, store } = await mountDialog([VERSION_TWO, VERSION_ONE])
    store.versionDiff = UNCOMPARABLE_DIFF
    await flushPromises()

    const diff = wrapper.get('[data-testid="version-diff"]')
    expect(diff.text()).toContain('无法识别')
    expect(diff.find('.diff-summary').exists()).toBe(false)
    expect(diff.text()).not.toContain('这两版的内容完全一样')
  })

  it('只有一版时不给对比入口', async () => {
    const { wrapper } = await mountDialog([VERSION_ONE])

    expect(wrapper.find('.compare-controls').exists()).toBe(false)
    expect(wrapper.text()).toContain('目前只有一版')
  })
})
