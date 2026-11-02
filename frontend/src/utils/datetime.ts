/**
 * 日期展示工具：统一走 `Intl.DateTimeFormat` + 东八区。
 *
 * 后端返回的时间有两种：
 * - DATE 列：`2024-03-01`（无时刻，不存在时区问题）
 * - DATETIME 列：`2026-11-02T21:25:00+08:00`（已带偏移，绝对时刻）
 *
 * 纯日期一律按 UTC 解析、按 Asia/Shanghai 格式化，避免 `new Date('2024-03-01')`
 * 被当成 UTC 午夜后在本地时区（东八区是 +8，会变成 3 月 1 日 08:00，日期不变）
 * 或西半球时区回退成前一天。带偏移的时间直接交给 Intl，绝对时刻天然正确。
 */

const TIME_ZONE = 'Asia/Shanghai'

const monthFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
})

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

/** 取 `2024-03-01` / `2024-03` 里的年月部分，非法输入返回空串。 */
function toIsoMonth(value: string): string {
  if (!value) return ''
  return value.slice(0, 7)
}

/** 从 Intl 结果里取某一段（不同环境的分隔符/顺序不一致，用 formatToParts 更稳）。 */
function partsOf(formatter: Intl.DateTimeFormat, date: Date): Record<string, string> {
  return Object.fromEntries(
    formatter
      .formatToParts(date)
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value]),
  )
}

/**
 * `zh-CN` 会忽略 `month: '2-digit'`（返回「3 月」而不是「03 月」），
 * 所以数字补零必须自己做，不能指望 Intl 的 digit 选项。
 */
function pad(value: string | undefined): string {
  if (!value) return ''
  return value.padStart(2, '0')
}

/**
 * `2024-03-01` / `2024-03` → `2024.03`（与后端生成器的 `_format_month` 同口径）。
 * 空值返回空串，由调用方决定显示「至今」还是占位文案。
 */
export function formatMonth(value: string | null | undefined): string {
  const month = toIsoMonth(value ?? '')
  if (!/^\d{4}-\d{2}$/.test(month)) return ''
  const date = new Date(`${month}-01T00:00:00Z`)
  if (Number.isNaN(date.getTime())) return ''
  const parts = partsOf(monthFormatter, date)
  return `${parts.year}.${pad(parts.month)}`
}

/** `2026-11-02T21:25:00+08:00` → `2026/11/02 21:25`，用于「上次保存」这类提示。 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const parts = partsOf(dateTimeFormatter, date)
  return `${parts.year}/${pad(parts.month)}/${pad(parts.day)} ${pad(parts.hour)}:${pad(parts.minute)}`
}

/**
 * 东八区的「今天」`YYYY-MM-DD`。
 *
 * 与后端 `shanghai_today()` 同口径，用于：
 * - 日期输入框的 `max`（不允许选未来）；
 * - 生成前校验「时间不能选择未来」。
 *
 * 刻意不用 `toISOString().slice(0, 10)`：那是 UTC 日期，东八区用户在 00:00–08:00
 * 之间会比本地日期早一天，把「今天」误判成已经过去。
 */
export function todayIsoDate(): string {
  const parts = partsOf(dateFormatter, new Date())
  return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}`
}

/** 当前年月 `YYYY-MM`，用作 `<input type="month">` 的 `max`。 */
export function currentMonthValue(): string {
  return todayIsoDate().slice(0, 7)
}

/**
 * `2024-03-15` → `2024.03.15`（与后端生成器 `_format_day` 同口径，精确到日）。
 * 空值返回空串，由调用方决定显示「至今」还是占位文案。
 */
export function formatDate(value: string | null | undefined): string {
  const iso = (value ?? '').slice(0, 10)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return ''
  const date = new Date(`${iso}T00:00:00Z`)
  if (Number.isNaN(date.getTime())) return ''
  const parts = partsOf(dateFormatter, date)
  return `${parts.year}.${pad(parts.month)}.${pad(parts.day)}`
}

/**
 * 把纸面上用户手敲的日期解析成 `<input type="date">` 认的 `YYYY-MM-DD`。
 *
 * 兼容的写法：`2024.03.15` / `2024-03-15` / `2024/3/15` / `2024年3月15日`，
 * 以及**只给年月**的 `2024.03`（按当月起算补 `-01`，是「精确到日」的兜底）。
 * 无法识别、月份越界、日期不存在（如 `2024-02-31`）一律返回 `null`。
 */
export function parseDateInput(text: string): string | null {
  const match = /(\d{4})\s*[.\-/年]\s*(\d{1,2})(?:\s*[.\-/月]\s*(\d{1,2}))?/.exec(text.trim())
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = match[3] === undefined ? 1 : Number(match[3])
  if (!Number.isInteger(year) || month < 1 || month > 12) return null
  if (!Number.isInteger(day) || day < 1 || day > 31) return null
  const iso = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
  const date = new Date(`${iso}T00:00:00Z`)
  // 回读校验：`2024-02-31` 会被 Date 归一化成 3 月 2 日，这种输入必须判为非法。
  if (
    Number.isNaN(date.getTime()) ||
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null
  }
  return iso
}

/**
 * 把纸面上用户手敲的月份（`2021.09` / `2021-09` / `2021/9` / `2021年9月`）
 * 归一化成 `<input type="month">` 认的 `2021-09`；无法识别返回 `null`。
 */
export function parseMonthInput(text: string): string | null {
  const match = /(\d{4})\s*[.\-/年]\s*(\d{1,2})/.exec(text.trim())
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  if (!Number.isInteger(year) || month < 1 || month > 12) return null
  return `${year}-${String(month).padStart(2, '0')}`
}
