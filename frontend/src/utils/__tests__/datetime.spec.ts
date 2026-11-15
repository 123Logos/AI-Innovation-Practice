/**
 * 时间展示的口径（`utils/datetime.ts`）。
 *
 * 这一组只钉「组织管理页要的那一格」：`YYYY-MM-DD HH:mm`，且**时区固定东八区**。
 * 时区那一半靠的是输入带不带偏移量：后端现在返回 `+08:00`（`app/core/datetime_display.py`），
 * 所以这里用同一形状的输入，断言在任何运行环境下结果都不变（CI 的机器时区不是我们能控的）。
 */
import { describe, expect, it } from 'vitest'

import { formatDateTime, formatDateTimeMinute } from '@/utils/datetime'

describe('formatDateTimeMinute（第 25 轮：组织页统一 YYYY-MM-DD HH:mm）', () => {
  it('带偏移的 ISO 串 → 横线版分钟精度，且是东八区', () => {
    // 21:25+08:00 就是北京时间 21:25，无论测试机在哪个时区。
    expect(formatDateTimeMinute('2026-11-02T21:25:00+08:00')).toBe('2026-11-02 21:25')
    // UTC 的 13:25 == 北京 21:25：换算这一步必须发生，不能只换分隔符。
    expect(formatDateTimeMinute('2026-11-02T13:25:00Z')).toBe('2026-11-02 21:25')
  })

  it('补零：月 / 日 / 时 / 分都是两位', () => {
    expect(formatDateTimeMinute('2026-01-05T09:07:00+08:00')).toBe('2026-01-05 09:07')
  })

  it('空值与非法值返回空串（调用方决定显示「—」还是兜底原文）', () => {
    expect(formatDateTimeMinute(null)).toBe('')
    expect(formatDateTimeMinute(undefined)).toBe('')
    expect(formatDateTimeMinute('')).toBe('')
    expect(formatDateTimeMinute('不是时间')).toBe('')
  })

  it('斜杠版仍在：组织页之外的五个页面没跟着改（这一轮只点名了组织页）', () => {
    expect(formatDateTime('2026-11-02T21:25:00+08:00')).toBe('2026/11/02 21:25')
  })
})
