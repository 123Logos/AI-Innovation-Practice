/**
 * 审计动作的「一处定义」：后端枚举是唯一出处，组织页的中文投影必须逐项覆盖它
 * （2026-11-15 第 25 轮，老板指令「补充完整的 ACTION_LABELS 映射」）。
 *
 * 为什么用**跨语言源码扫描**：动作码只声明在 Python 的那张枚举里。若在这里抄一份
 * 字面量清单做对照，抄件与枚举会一起漂——而漂的表现恰恰是用户看到
 * `resume.export_pdf` 这种原文（`actionLabel()` 的兜底分支），没人会注意到。
 * 本仓已经有「Python 脚本读 .vue」的先例（`scripts/check_ui_copy.py`），方向反过来同理。
 *
 * 口径与 `layoutGuards.spec.ts` 一致：读源码、断写法；挂载测不出「少了一条映射」，
 * 因为兜底会把缺的那条照样渲染成一行文字。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const ROOT = resolve(process.cwd(), '..')
const ENUM_FILE = resolve(ROOT, 'app/repositories/audit_log_repository.py')
const VIEW_FILE = resolve(process.cwd(), 'src/views/OrganizationView.vue')

/** 从 `class AuditAction(StrEnum)` 块里取出 `NAME = "value"` 的 value。 */
function auditActionValues(): string[] {
  const source = readFileSync(ENUM_FILE, 'utf8')
  const block = /class AuditAction\(StrEnum\):([\s\S]*?)(?=\nclass )/.exec(source)?.[1]
  if (!block) throw new Error('没在 audit_log_repository.py 里找到 AuditAction 枚举')
  return [...block.matchAll(/^\s{4}[A-Z_]+ = "([^"]+)"/gm)].map((match) => match[1] as string)
}

/** 从视图里取出 `ACTION_LABELS` 对象的键。 */
function labelKeys(): string[] {
  const source = readFileSync(VIEW_FILE, 'utf8')
  const block = /const ACTION_LABELS: Record<string, string> = \{([\s\S]*?)\n\}/.exec(source)?.[1]
  if (!block) throw new Error('没在 OrganizationView.vue 里找到 ACTION_LABELS')
  return [...block.matchAll(/'([a-z_.]+)':/g)].map((match) => match[1] as string)
}

describe('审计动作中文名（第 25 轮）', () => {
  it('反向护栏：真的从枚举里读到了值（读空则下面那条是空检）', () => {
    const values = auditActionValues()

    expect(values.length).toBeGreaterThanOrEqual(10)
    // 三个样本各自代表一族：注册 / 组织 / 教师导出。
    expect(values).toContain('user.register')
    expect(values).toContain('member.role_change')
    expect(values).toContain('resume.export_pdf')
  })

  it('枚举里的每一个动作都有中文名，一个都不许漏', () => {
    const values = auditActionValues()
    const keys = new Set(labelKeys())
    const missing = values.filter((value) => !keys.has(value))

    expect(missing, `这些动作码会以原文出现在日志里：${missing.join(', ')}`).toEqual([])
  })

  it('反过来也不许多：表里没有枚举里不存在的动作', () => {
    const values = new Set(auditActionValues())
    const extra = labelKeys().filter((key) => !values.has(key))

    expect(extra, `这些键在后端枚举里不存在：${extra.join(', ')}`).toEqual([])
  })
})
