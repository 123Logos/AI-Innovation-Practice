/**
 * 骨架屏单测（批次 F4 第二片，守则 M2）。
 *
 * 能量的是**结构与尺寸来源**：块数、读屏文案、`aria-busy`、以及高度是不是从 props
 * 变成样式变量传下去的。真实像素（375 档 187px / 1280 档 158px 那一类）jsdom 量不了，
 * 归两档实测——那些数值本身就是从实测抄进调用点的，不是这里断言出来的。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import SkeletonList from '@/components/SkeletonList.vue'

function mountSkeleton(props: Record<string, unknown> = {}) {
  return mount(SkeletonList, {
    props: {
      label: '正在加载岗位…',
      heights: [158, 158, 158],
      heightsCompact: [187, 187, 187],
      ...props,
    },
  })
}

describe('骨架屏：先占位后填充', () => {
  it('块数等于传入的高度个数，每一块把两档高度都写成样式变量', () => {
    const wrapper = mountSkeleton()
    const blocks = wrapper.findAll('.ui-skeleton')

    expect(blocks).toHaveLength(3)
    expect(blocks[0]?.attributes('style')).toContain('--row-h: 158px')
    expect(blocks[0]?.attributes('style')).toContain('--row-h-compact: 187px')
  })

  it('窄屏那一档缺一项时按宽屏兜底，不会渲染出一个没有高度的块', () => {
    const wrapper = mountSkeleton({ heights: [71, 71, 71], heightsCompact: [140] })
    const blocks = wrapper.findAll('.ui-skeleton')

    expect(blocks[0]?.attributes('style')).toContain('--row-h-compact: 140px')
    expect(blocks[2]?.attributes('style')).toContain('--row-h-compact: 71px')
  })

  it('加载中这个状态读屏读得到：role=status + aria-busy + 原来那句文案', () => {
    const wrapper = mountSkeleton()
    const root = wrapper.find('.skeleton-list')

    expect(root.attributes('role')).toBe('status')
    expect(root.attributes('aria-busy')).toBe('true')
    // 文案一个字没改，只是从可见文字挪进读屏可见的那一行（M3：加载中不许被骨架吞掉）。
    expect(wrapper.find('.ui-sr-only').text()).toBe('正在加载岗位…')
  })

  it('列数与间距也是变量：岗位列表两列，其余一列', () => {
    const grid = mountSkeleton({ columns: 2, gap: 14 })
    expect(grid.find('.skeleton-list').attributes('style')).toContain('--skeleton-cols: 2')
    expect(grid.find('.skeleton-list').attributes('style')).toContain('--skeleton-gap: 14px')

    const single = mountSkeleton()
    expect(single.find('.skeleton-list').attributes('style')).toContain('--skeleton-cols: 1')
    expect(single.find('.skeleton-list').attributes('style')).toContain('--skeleton-gap: 0px')
  })

  it('骨架自己不带任何文案节点（除了读屏那一行），也不带按钮', () => {
    const wrapper = mountSkeleton()

    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.findAll('p')).toHaveLength(0)
    expect(wrapper.findAll('.ui-sr-only')).toHaveLength(1)
  })
})