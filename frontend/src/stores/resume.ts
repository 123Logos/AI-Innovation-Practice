/**
 * 档案 + 简历生成 store（T7.3 起接真实后端）。
 *
 * 字段命名与后端 `ProfileUpsertRequest` / `ProfileRead` 严格一致（snake_case），
 * 唯一例外是 `ExperienceDraft.end_date` / `EducationDraft.edu_start` 用空串表示「至今」
 * 或「未填」——`<input type="date">` / `<input type="month">` 不接受 null，
 * 所以在提交时由 `toUpsertPayload()` 转换：
 *   经历 end_date  空串 → null（至今）
 *   教育 edu_start `YYYY-MM` → `YYYY-MM-01`（后端是 DATE）
 *   教育 edu_end   空串 → null（至今）
 *
 * 技能熟练度（`self_rating`）与年级（`grade`）已从 UI 移除：
 * 前者类型保留、提交时统一写 `DEFAULT_SKILL_RATING`（3）；
 * 后者前后端都不再传写，数据库列保留但不再参与业务。
 *
 * 专业（`major`）自 T7.6 起只属于「教育经历」的每一段，主表不再有该字段，
 * 因此 `ProfileDraft` 里也没有顶层的 `major`；旧草稿里残留的顶层 `major`
 * 会在恢复时被 `normalizeDraft()` 重建对象时自然丢弃。
 *
 * 邮箱（`email`，用户反馈轮）：是**简历上展示的邮箱**，
 * 与登录邮箱 `users.email` 无关，可空。
 *
 * 校验时机（用户反馈轮 / 保存 422 修复轮）：
 * - 保存档案**不拦必填**：只提交「填了锚点字段」的行（技能名 / 项目名 / 学校），
 *   空行直接丢弃，全空就等于存一份空档案；
 * - 但保存要过一道**契约自检** `validateForSave()`：手机号填了就必须合法、
 *   教育入学时间 / 项目开始日期不能为空也不能是未来（后端是必填 `date`）、
 *   技能名不能重复。这些是「后端一定会 422」的硬错误，提前用中文提示拦下来；
 * - 生成简历才是完整的严格时刻，见 `validateForGenerate()`：
 *   姓名 / 手机号必填，教育四要素齐全，技能与项目各至少 1 条。
 *   开始时间不得晚于今天（与后端 `shanghai_today()` 同口径），
 *   结束时间允许未来（应届生的「预计毕业」、在做的项目「预计结束」），
 *   只要求不早于开始时间。
 *
 * 教育经历（T7.5）是一对多子表，前端与后端的渲染顺序都是「入学时间倒序」。
 *
 * 本地草稿：**按「用户 id + 简历 id」隔离**存在 `localStorage`，只写不覆盖服务端数据；
 * 用户点预览区上方的「保存修改」（`save()`）成功后立即清除。
 *
 * 为什么必须带简历维度（D4.2）：D4.1 之前 key 只有 `resume_draft:${userId}`，
 * 一个用户的所有简历共用同一格草稿，切到 B 再切回 A，A 会被 B 的草稿顶掉。
 * 现在的 key 是 `resume_draft:${userId}:${profileId}`；还没落库的「草稿态简历」
 * 用哨兵 `new`（`resume_draft:${userId}:new`），第一次保存后即迁移到真实 id。
 * 老版本留下的 `resume_draft:${userId}` 在首次进入时**单向迁移**到默认简历，
 * 迁移完就删掉旧键，避免它继续被误读。
 *
 * 切换简历的固定顺序（`switchProfile()`，D4.2）：先拦未保存 → 解绑 watcher →
 * 清 `pendingDraft` / 生成结果 → 换 `currentProfileId` → 重新拉取并绑定 watcher；
 * 全程用 `requestSeq` 丢弃过期响应，快速连点也不会出现「A 的数据落进 B」。
 *
 * 保存入口自 D3 起只有一个：纸面编辑与左侧表单都只改 Pinia，
 * `dirty` 为真即「未落库」，由 `ResumePreview.vue` 工具栏的按钮统一提交。
 */

import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { profilesApi, resumesApi } from '@/api'
import type {
  Degree,
  LayoutConfig,
  LayoutSection,
  PaperSize,
  ProfileRead,
  ProfileSortField,
  ProfileSummary,
  ProfileUpsertRequest,
  ResumeRead,
  SectionId,
  ThemeId,
} from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { applyTheme } from '@/stores/theme'
import { currentMonthValue, todayIsoDate } from '@/utils/datetime'

/**
 * 纸张物理尺寸（D5.1）。
 *
 * `widthMm` / `heightMm` 是**排版坐标**的唯一来源：`.paper` 用 mm 定尺，
 * 屏幕上的缩放只由 `transform: scale()` 完成，所以「屏幕像素」在任何视口下
 * 都等于「mm × 96/25.4 × zoom」，与打印保持同一套坐标。
 *
 * Letter 按英寸定义精确换算：8.5in × 11in = 215.9 × 279.4mm
 * （常写作 216 × 279，差 0.1mm 会让页数计算多算/少算一点点，所以用精确值）。
 */
export const PAPER_SPECS: Record<PaperSize, { label: string; widthMm: number; heightMm: number }> =
  {
    A4: { label: 'A4', widthMm: 210, heightMm: 297 },
    Letter: { label: 'Letter', widthMm: 215.9, heightMm: 279.4 },
  }

/** 1mm 在 CSS 里恒等于 96/25.4 px（CSS 规范定义，与设备 DPI 无关）。 */
export const PX_PER_MM = 96 / 25.4

/**
 * 配色预设（D5.2）。与后端 `app/core/document_style.py::THEMES` **逐字一致**，
 * 由 `tests/test_profile_contract.py::test_theme_palette_matches_frontend` 比对。
 *
 * `accent` 只染 `h2`（13px 正文级字号 → 必须 ≥4.5:1），`rule` 只染页眉下边框
 * （2px 非文本 UI → ≥3:1）。8 套都是离线挑好并实测过的，所以这里没有、也不要加
 * 「随便输入一个颜色」的入口：那样就必须在运行时判断对比度，而双端钳制必然漂移。
 */
export interface ThemeOption {
  id: ThemeId
  /** 面板上展示的名字（与后端 `DocumentTheme.name` 同源）。 */
  name: string
  accent: string
  rule: string
}

/** 展示顺序即后端 `THEME_IDS` 的顺序。 */
export const THEME_OPTIONS: readonly ThemeOption[] = [
  { id: 'forest', name: '青绿', accent: '#527873', rule: '#6B8F8A' },
  { id: 'ink', name: '墨黑', accent: '#263330', rule: '#727A78' },
  { id: 'navy', name: '藏青', accent: '#2F4A6B', rule: '#78899F' },
  { id: 'wine', name: '酒红', accent: '#7A3B3B', rule: '#A98080' },
  { id: 'plum', name: '深紫', accent: '#4B3A6B', rule: '#8A7F9F' },
  { id: 'cocoa', name: '深棕', accent: '#6B4A2F', rule: '#9F8978' },
  { id: 'slate', name: '石板灰蓝', accent: '#3D4F5C', rule: '#818D95' },
  { id: 'olive', name: '橄榄', accent: '#4A5A32', rule: '#89947A' },
]

/** 缺省主题：与后端 `DEFAULT_THEME`、迁移的 `server_default` 三处同源。 */
export const DEFAULT_THEME: ThemeId = 'forest'

/**
 * 排版默认顺序与默认标题（D5.3）。
 *
 * 与后端 `app/core/document_style.py::DEFAULT_SECTIONS` **逐字一致**，由
 * `tests/test_profile_contract.py::test_default_layout_matches_frontend` 比对。
 * 两处都有的原因：前端在「还没有后端响应」的空白骨架阶段就要渲染，不能等接口；
 * 而生成器在 `layout=None` 时也要有默认值。契约测试保证它们不会各说各话。
 */
export const DEFAULT_LAYOUT_SECTIONS: readonly LayoutSection[] = [
  { id: 'educations', title: '教育经历' },
  { id: 'skills', title: '技能' },
  { id: 'experiences', title: '项目经历' },
]

/** 后端 `LAYOUT_VERSION` 的同源常量；版本不匹配时后端会整份回退默认顺序。 */
export const LAYOUT_VERSION = 1

/**
 * 标题长度上限（ADR-007 §6.8(4)）：`h2` 在 360px 视口最多 15 字不折行，取 12 留余量。
 * 输入框的 `maxlength` 与后端 `LayoutSectionInput.title` 的 `max_length` 用的是同一个数。
 */
export const LAYOUT_TITLE_MAX_LENGTH = 12

/** 已知模块 id，用于把脏数据里的未知 id 挡在外面（顺序同默认）。 */
const SECTION_IDS = DEFAULT_LAYOUT_SECTIONS.map((section) => section.id)

/** id → 默认标题；标题被清空或类型不对时回落到它。 */
const DEFAULT_SECTION_TITLES: Record<SectionId, string> = {
  educations: '教育经历',
  skills: '技能',
  experiences: '项目经历',
}

/**
 * 按**码点**截断标题。
 *
 * 不用 `String.prototype.slice`：它会按 UTF-16 码元切，正好切在代理对中间时
 * 会留下半个 emoji（渲染成 �）。后端 Python 的 `str[:12]` 是按码点的，
 * 两边口径要一致，否则「输入框里 12 个字、生成物里 11 个字」这种差异无法解释。
 */
function clampTitle(value: string): string {
  return Array.from(value).slice(0, LAYOUT_TITLE_MAX_LENGTH).join('')
}

/** 一份全新的默认排版（每次返回新对象，避免多份草稿共享同一个数组）。 */
function createDefaultLayout(): LayoutConfig {
  return {
    version: LAYOUT_VERSION,
    sections: DEFAULT_LAYOUT_SECTIONS.map((section) => ({ ...section })),
  }
}

/**
 * 把任意来源的 `layout_config` 收敛成**可直接渲染**的配置。
 *
 * 三个来源都可能是脏的：`localStorage` 里的旧草稿、D5.3 之前的后端响应
 * （没有这个字段）、以及「将来删除某个模块又回滚」的残留。
 *
 * 注意这里**不是**后端 `resolve_layout()` 的完整复刻：后端读接口已经保证
 * 返回合法配置，前端的这层只是兜底（未知 id 丢弃 / 重复丢弃 / 缺的补到末尾 /
 * 空标题回落默认）。完整降级矩阵只在后端维护一份，避免两套实现漂移。
 */
function toLayoutConfig(value: unknown): LayoutConfig {
  const rawSections = (value as { sections?: unknown } | null | undefined)?.sections
  if (!Array.isArray(rawSections)) return createDefaultLayout()

  const sections: LayoutSection[] = []
  for (const entry of rawSections) {
    if (!entry || typeof entry !== 'object') continue
    const { id, title } = entry as Partial<LayoutSection>
    if (!id || !SECTION_IDS.includes(id)) continue
    if (sections.some((section) => section.id === id)) continue
    const normalized = typeof title === 'string' ? clampTitle(title.trim()) : ''
    sections.push({ id, title: normalized || DEFAULT_SECTION_TITLES[id] })
  }
  for (const fallback of DEFAULT_LAYOUT_SECTIONS) {
    if (!sections.some((section) => section.id === fallback.id)) {
      sections.push({ ...fallback })
    }
  }
  return { version: LAYOUT_VERSION, sections }
}

/**
 * 当前排版是否就是「默认顺序 + 默认标题」。
 *
 * 保存时用它决定发 `null` 还是发完整快照：发 `null` 让库里的 `layout_config`
 * 保持 `NULL`（= 「从未自定义」），将来默认顺序升级时这份简历能自动跟上。
 * 若一律发快照，用户哪怕只是顺手点了保存，也会被钉死在今天的默认顺序上。
 */
function isDefaultLayout(value: LayoutConfig): boolean {
  if (value.sections.length !== DEFAULT_LAYOUT_SECTIONS.length) return false
  return value.sections.every((section, index) => {
    const expected = DEFAULT_LAYOUT_SECTIONS[index]
    return section.id === expected.id && section.title.trim() === expected.title
  })
}

/** 排版进入可比序列：只投影 id 与标题，不投影 version（它恒定）。 */
function layoutComparable(value: unknown): [SectionId, string][] {
  return toLayoutConfig(value).sections.map((section) => [section.id, section.title.trim()])
}

/**
 * 把某个主题刷到整个 Web UI 上（D5.2 补充）。
 *
 * 唯一入口：`applyDraft()`（切简历 / 首次加载 / 恢复草稿）与 `updateTheme()`。
 * 拒绝在组件里直接 `setProperty`——那样「当前主题」就有了两个来源，
 * 切简历时必然出现「纸变了、按钮没变」的错位。
 */
function syncGlobalTheme(themeId: ThemeId): void {
  const option = THEME_OPTIONS.find((item) => item.id === themeId) ?? THEME_OPTIONS[0]
  applyTheme({ accent: option.accent, rule: option.rule })
}

/** 与后端 `ProfileUpsertRequest` 的 Field 约束保持一致（只有上限，保存不拦必填）。 */
export const SKILL_LIMIT = { min: 0, max: 8 } as const
export const EXPERIENCE_LIMIT = { min: 0, max: 3 } as const
/** 后端 `educations` 最多 5 段；UI 至少保留 1 行空的，避免区段消失。 */
export const EDUCATION_LIMIT = { min: 1, max: 5 } as const

/** 生成简历时的最低要求（后端 `ResumeService` 对技能/经历为空会返回 400）。 */
export const GENERATE_MIN = { skills: 1, experiences: 1, educations: 1 } as const

/** 学历下拉选项，顺序与后端枚举声明一致。 */
export const DEGREE_OPTIONS: readonly Degree[] = ['专科', '本科', '硕士', '博士']

/** UI 已不再采集熟练度；提交时一律用它填后端必填字段。 */
export const DEFAULT_SKILL_RATING = 3

/**
 * 中国大陆手机号；与后端 `app/schemas/profile.py::PHONE_PATTERN` 同源，
 * 两边必须同步修改，否则前端放过、后端 422。
 */
export const PHONE_PATTERN = /^1[3-9]\d{9}$/

const DRAFT_STORAGE_PREFIX = 'resume_draft:'
const DRAFT_VERSION = 1
/** 输入停顿多久后落本地草稿（毫秒）。 */
const DRAFT_WRITE_DELAY = 400
/**
 * 「还没落库的新简历」在草稿 key 里的哨兵 id。
 *
 * 用户第一次进编辑器时库里还没有任何简历（只有空白骨架），此时也必须能存草稿，
 * 所以给一个不会被后端 uuid 撞上的固定串；第一次 `save()` 建出真身之后，
 * 这格草稿会被清掉，后续都写在真实 id 下。
 */
const DRAFT_NEW_PROFILE_KEY = 'new'

/** 后端 `DEFAULT_PROFILE_TITLE` 的同源文案：新建第一份简历时用它。 */
const DEFAULT_PROFILE_TITLE = '我的简历'

/**
 * 「上次打开的是哪一份简历」的 localStorage 前缀（D4.3）。
 *
 * 存在的原因：进入 `/trash` 再返回编辑器时，`loadFromServer()` 会重新跑一遍，
 * 若只按「默认简历」选，用户明明在看「投算法」却被切回「我的简历」。
 * 这里记住 id，下次优先恢复；id 已不存在（被删 / 被彻底删）时再回退默认。
 */
const LAST_PROFILE_STORAGE_PREFIX = 'resume_last_profile:'

export interface SkillDraft {
  name: string
  self_rating: number
}

export interface ExperienceDraft {
  title: string
  start_date: string
  /** 空串表示「至今」，提交时转 null。 */
  end_date: string
  description: string
  responsibility: string
}

export interface EducationDraft {
  school: string
  major: string
  degree: Degree
  /** `YYYY-MM`，与 `<input type="month">` 的取值格式一致；提交时补 `-01`。 */
  edu_start: string
  /** `YYYY-MM`，空串表示「至今」。 */
  edu_end: string
}

export interface ProfileDraft {
  full_name: string
  /** 简历上展示的邮箱（可空），与登录邮箱无关。 */
  email: string
  contact: string
  /**
   * 纸张尺寸（D5.1）。
   *
   * 必须放在 draft 里而不是单独的 `ref`：`comparableOf()` 只投影 draft 的字段，
   * 放外面会导致「改纸张不触发 dirty、切简历纸张残留」（ADR-007 §3.3）。
   */
  paper_size: PaperSize
  /**
   * 配色主题（D5.2）。与 `paper_size` 同理必须在 draft 里：
   * 否则「改了颜色不算未保存修改」，切简历时颜色还会串到下一份。
   */
  theme: ThemeId
  /**
   * 排版（D5.3）：模块顺序 + 每段的自定义标题。
   *
   * 同样必须在 draft 里（而不是单独的 ref）：`comparableOf()` 只投影 draft 的字段，
   * 放外面会导致「调了顺序不算未保存修改」，切简历时顺序还会串到下一份。
   *
   * 只放元信息，绝不把 `skills` / `experiences` 数组复制进来——
   * 那会立刻出现「两份内容、以谁为准」的问题。
   */
  layout_config: LayoutConfig
  skills: SkillDraft[]
  experiences: ExperienceDraft[]
  educations: EducationDraft[]
}

/**
 * 新教育经历的默认入学时间：**当前年份的 4 年前的 9 月**。
 *
 * 面向本科大四/应届生：4 年前 9 月入学，正好覆盖「现在毕业」这段学历，
 * 比默认当前月份少一次手工改。
 */
function defaultEducationStart(): string {
  return `${Number(currentMonthValue().slice(0, 4)) - 4}-09`
}

/** 后端返回 `2021-09-01`，`<input type="month">` 只认 `2021-09`。 */
function toMonth(value: string | null | undefined): string {
  if (!value) return ''
  return value.slice(0, 7)
}

/** 提交给后端的 DATE：月份补 `-01`；空串返回 null。 */
function toIsoDate(month: string): string | null {
  if (!month) return null
  return month.length === 7 ? `${month}-01` : month
}

/**
 * 把任意值收敛到两档纸张。
 *
 * `localStorage` 里的草稿、旧版后端响应都可能是 `undefined` 或未知字符串
 * （比如将来支持过 Legal 又回滚），一律回落 A4 —— 纸张是装饰性字段，
 * 不能因为它让整份草稿恢复失败。
 */
function toPaperSize(value: unknown): PaperSize {
  return value === 'Letter' ? 'Letter' : 'A4'
}

/**
 * 把任意值收敛到 8 套预设之一，未知值回落 `forest`。
 *
 * 三个来源都可能是脏的：`localStorage` 里的旧草稿、还没升级的后端响应、
 * 以及「支持过第 9 套主题又回滚」的残留。配色是装饰性字段，
 * 不能因为它让整份草稿恢复失败或整页崩掉。
 */
function toTheme(value: unknown): ThemeId {
  const found = THEME_OPTIONS.find((option) => option.id === value)
  return found ? found.id : DEFAULT_THEME
}

function createEmptyEducation(): EducationDraft {
  return {
    school: '',
    major: '',
    degree: '本科',
    edu_start: defaultEducationStart(),
    edu_end: '',
  }
}

/**
 * 首次进入时的空白骨架。
 *
 * 技能给 3 行只是「常见条数」的顺手默认，不是后端要求——保存不拦必填，
 * 空行会在 `toUpsertPayload()` 里被丢弃。
 */
function createEmptyDraft(): ProfileDraft {
  return {
    full_name: '',
    email: '',
    contact: '',
    paper_size: 'A4',
    theme: DEFAULT_THEME,
    layout_config: createDefaultLayout(),
    skills: Array.from({ length: 3 }, () => ({
      name: '',
      self_rating: DEFAULT_SKILL_RATING,
    })),
    experiences: [
      {
        title: '',
        start_date: todayIsoDate(),
        end_date: '',
        description: '',
        responsibility: '',
      },
    ],
    educations: [createEmptyEducation()],
  }
}

function draftFromProfile(profile: ProfileRead): ProfileDraft {
  return {
    full_name: profile.full_name,
    email: profile.email ?? '',
    contact: profile.contact,
    // 后端保证读侧是具体值；`??` 只是为了兼容「D5.1 之前缓存的响应对象」。
    paper_size: profile.paper_size ?? 'A4',
    theme: toTheme(profile.theme),
    // D5.3：后端读侧恒返回解析后的配置；`?? null` 只是兼容「D5.2 之前缓存的响应」。
    layout_config: toLayoutConfig(profile.layout_config),
    skills: profile.skills.map((skill) => ({
      name: skill.name,
      self_rating: skill.self_rating,
    })),
    experiences: profile.experiences.map((experience) => ({
      title: experience.title,
      start_date: experience.start_date,
      end_date: experience.end_date ?? '',
      description: experience.description,
      responsibility: experience.responsibility,
    })),
    educations: (profile.educations ?? []).map((education) => ({
      school: education.school,
      major: education.major,
      degree: education.degree,
      edu_start: toMonth(education.edu_start),
      edu_end: toMonth(education.edu_end),
    })),
  }
}

/* ------------------------------ 本地草稿工具 ------------------------------ */

/** 写入 `localStorage` 的草稿信封，带版本号以便未来结构升级时安全丢弃旧草稿。 */
export interface LocalDraft {
  version: number
  /** 草稿写入时间（epoch ms），仅用于提示文案。 */
  saved_at: number
  data: ProfileDraft
}

/**
 * 草稿 key：**用户 + 简历**两维隔离（D4.2 修掉的串号 bug）。
 *
 * D4.1 及之前是 `resume_draft:${userId}`，同一用户的多份简历共用一格，
 * 切换后会互相覆盖。`profileId` 为 null 表示「还没有落库的空白简历」，
 * 用 `new` 占位，保证第一次进入也能存草稿。
 */
function draftStorageKey(ownerId: string, profileId: string | null): string {
  return `${DRAFT_STORAGE_PREFIX}${ownerId}:${profileId ?? DRAFT_NEW_PROFILE_KEY}`
}

/** D4.1 及之前的老 key（没有简历维度），只用于一次性迁移。 */
function legacyDraftStorageKey(ownerId: string): string {
  return `${DRAFT_STORAGE_PREFIX}${ownerId}`
}

/** `localStorage` 的内容不可信：任何字段不符就当作没有草稿。 */
function isProfileDraft(value: unknown): value is ProfileDraft {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<ProfileDraft>
  return (
    typeof candidate.full_name === 'string' &&
    typeof candidate.contact === 'string' &&
    Array.isArray(candidate.skills) &&
    candidate.skills.every(
      (skill: unknown) =>
        Boolean(skill) &&
        typeof skill === 'object' &&
        typeof (skill as SkillDraft).name === 'string',
    ) &&
    Array.isArray(candidate.experiences) &&
    candidate.experiences.every(
      (experience: unknown) =>
        Boolean(experience) &&
        typeof experience === 'object' &&
        typeof (experience as ExperienceDraft).title === 'string',
    ) &&
    // T7.5 之前写入的草稿没有 educations，缺省视为合法（恢复时补空数组）；
    // 但只要有这个字段，就必须是数组，避免脏数据把渲染层搞崩。
    (candidate.educations === undefined || Array.isArray(candidate.educations)) &&
    // 用户反馈轮才新增 email，旧草稿没有该字段同样合法；有则必须是字符串。
    (candidate.email === undefined || typeof candidate.email === 'string') &&
    // D5.1 才新增 paper_size：**旧草稿没有它必须依然合法**，否则用户升级后
    // 会被判定成「没有草稿」，正在编辑的内容就悄悄丢了。有则必须是两档之一。
    (candidate.paper_size === undefined ||
      candidate.paper_size === 'A4' ||
      candidate.paper_size === 'Letter') &&
    // D5.2 的 theme 同理：D5.1 及更早的草稿没有它，缺省合法。
    (candidate.theme === undefined ||
      THEME_OPTIONS.some((option) => option.id === candidate.theme)) &&
    // D5.3 的 layout_config 同理：D5.2 及更早的草稿没有它，缺省合法。
    // 有则必须是对象（不能是数组 / 字符串）——否则说明草稿被改坏，宁可当作没有草稿。
    (candidate.layout_config === undefined ||
      (typeof candidate.layout_config === 'object' &&
        candidate.layout_config !== null &&
        !Array.isArray(candidate.layout_config)))
  )
}

function readDraft(ownerId: string, profileId: string | null): LocalDraft | null {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(draftStorageKey(ownerId, profileId))
  } catch {
    return null
  }
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<LocalDraft>
    if (parsed.version !== DRAFT_VERSION || !isProfileDraft(parsed.data)) return null
    return {
      version: DRAFT_VERSION,
      saved_at: typeof parsed.saved_at === 'number' ? parsed.saved_at : 0,
      data: parsed.data,
    }
  } catch {
    return null
  }
}

function removeDraft(ownerId: string, profileId: string | null): void {
  try {
    localStorage.removeItem(draftStorageKey(ownerId, profileId))
  } catch {
    // localStorage 被禁用时忽略，草稿是增益能力，不能阻断主流程。
  }
}

/**
 * 把 D4.1 的单槽草稿迁移到「默认简历」这一格，随后删掉旧键。
 *
 * 为什么值得写这段：老用户升级那一刻 `localStorage` 里正躺着一份没保存的草稿，
 * 直接改 key 规则等于静默丢掉他的输入。迁移是**单向且只做一次**：
 * - 只在当前操作的是真实简历（`profileId` 非空）时迁移，避免写进 `new` 哨兵；
 * - 目标格已有草稿就不覆盖（新格式优先），但仍然删旧键，防止它被反复误读。
 */
function migrateLegacyDraft(ownerId: string, profileId: string | null): void {
  if (!profileId) return
  const legacyKey = legacyDraftStorageKey(ownerId)
  try {
    const legacy = localStorage.getItem(legacyKey)
    if (!legacy) return
    const targetKey = draftStorageKey(ownerId, profileId)
    if (localStorage.getItem(targetKey) === null) {
      localStorage.setItem(targetKey, legacy)
    }
    localStorage.removeItem(legacyKey)
  } catch {
    // 无痕模式 / 配额满：迁移失败就保持原样，不能影响进入编辑器。
  }
}

/** 记住「上次打开的是哪一份简历」，按用户隔离。`null` 表示清掉记录。 */
function rememberProfileId(ownerId: string, profileId: string | null): void {
  const key = `${LAST_PROFILE_STORAGE_PREFIX}${ownerId}`
  try {
    if (profileId) {
      localStorage.setItem(key, profileId)
    } else {
      localStorage.removeItem(key)
    }
  } catch {
    // 存储不可用时忽略：记住上次打开的是锦上添花，不能阻断进入编辑器。
  }
}

function readRememberedProfileId(ownerId: string): string | null {
  try {
    return localStorage.getItem(`${LAST_PROFILE_STORAGE_PREFIX}${ownerId}`)
  } catch {
    return null
  }
}

/**
 * 补齐缺失字段，让旧草稿也能安全恢复。
 *
 * - 已下线的 `grade`（年级）直接丢弃；
 * - T7.6 已删除主表 `major`（专业），旧草稿里的顶层 `major` 一并丢弃——
 *   重建对象时不拷贝该键，运行时自然消失；
 * - 已下线的 `self_rating` 抹平为默认值；
 * - T7.5 之前的草稿没有 `educations`，补空数组而不是报错；
 * - 用户反馈轮之前的草稿没有 `email`，补空串；
 * - D5.1 之前的草稿没有 `paper_size`，补 A4（否则升级后弹出的「恢复草稿」
 *   横幅会带着 `undefined` 一路飘到请求体里，被后端 422 拒掉）；
 * - D5.2 之前的草稿没有 `theme`，补 `forest`（同上）。
 */
function normalizeDraft(raw: ProfileDraft): ProfileDraft {
  return {
    full_name: raw.full_name ?? '',
    email: raw.email ?? '',
    contact: raw.contact ?? '',
    paper_size: toPaperSize(raw.paper_size),
    theme: toTheme(raw.theme),
    // D5.3 之前的草稿没有 layout_config，补齐默认排版（同上）。
    layout_config: toLayoutConfig(raw.layout_config),
    skills: (raw.skills ?? [])
      .filter((skill) => skill && typeof skill.name === 'string')
      .map((skill) => ({ name: skill.name, self_rating: DEFAULT_SKILL_RATING })),
    experiences: (raw.experiences ?? [])
      .filter((experience) => experience && typeof experience.title === 'string')
      .map((experience) => ({
        title: experience.title,
        start_date: experience.start_date ?? '',
        end_date: experience.end_date ?? '',
        description: experience.description ?? '',
        responsibility: experience.responsibility ?? '',
      })),
    educations: (raw.educations ?? [])
      .filter((education) => education && typeof education.school === 'string')
      .map((education) => ({
        school: education.school,
        major: education.major ?? '',
        degree: DEGREE_OPTIONS.includes(education.degree) ? education.degree : '本科',
        edu_start: toMonth(education.edu_start),
        edu_end: toMonth(education.edu_end),
      })),
  }
}

/* ------------------------------ 行有效性过滤 ------------------------------ */

/*
 * 保存不再拦必填，但也不能把用户没开始填的空行提交上去（后端 `name` / `title` /
 * `school` 是必填锚点）。规则：锚点字段为空即视为「UI 占位行」，提交与比较时都跳过。
 */

function meaningfulSkills(source: ProfileDraft): SkillDraft[] {
  return (source.skills ?? []).filter((skill) => skill.name.trim() !== '')
}

function meaningfulExperiences(source: ProfileDraft): ExperienceDraft[] {
  return (source.experiences ?? []).filter((experience) => experience.title.trim() !== '')
}

function meaningfulEducations(source: ProfileDraft): EducationDraft[] {
  return (source.educations ?? []).filter((education) => education.school.trim() !== '')
}

/**
 * 用于「草稿 vs 服务端」比较的归一化序列化。
 *
 * 刻意忽略 `self_rating`：UI 已不采集它，提交后恒为 3，
 * 若把它算进比较，一份本来没改过的草稿也会被判成「有未保存修改」。
 * 同时也只比较「有效行」，否则一份 3 个空技能行的空白骨架
 * 会跟服务端的 0 条技能判成「有差异」，每次进页面都弹恢复提示。
 * 顶层 `major` 已不存在（T7.6），无需再参与比较。
 *
 * 所有字段都用 `?? ''` 兜底：草稿来自 `localStorage`，可能是任何一个旧版本的结构
 * （比如本轮之前没有 `email`），这里绝不能因为缺一个键就抛异常。
 */
function comparableOf(source: ProfileDraft): string {
  return JSON.stringify({
    full_name: (source.full_name ?? '').trim(),
    email: (source.email ?? '').trim(),
    contact: (source.contact ?? '').trim(),
    // `?? 'A4'`：D5.1 之前的草稿没有这个键，缺省必须与服务器默认值一致，
    // 否则「什么都没改」的旧草稿会立刻被判成「有未保存的修改」（ADR-007 §11.5）。
    paper_size: toPaperSize(source.paper_size),
    // 同上，`theme` 缺省要与服务端默认一致，否则改颜色会被判成「没改」。
    theme: toTheme(source.theme),
    // 排版（D5.3）：顺序与标题都要参与比较，否则「只调顺序」会被判成没有修改。
    layout_config: layoutComparable(source.layout_config),
    skills: meaningfulSkills(source).map((skill) => skill.name.trim()),
    experiences: meaningfulExperiences(source).map((experience) => [
      experience.title,
      experience.start_date,
      experience.end_date,
      experience.description,
      experience.responsibility,
    ]),
    educations: meaningfulEducations(source).map((education) => [
      education.school,
      education.major,
      education.degree,
      education.edu_start,
      education.edu_end,
    ]),
  })
}

export const useResumeStore = defineStore('resume', () => {
  const draft = reactive<ProfileDraft>(createEmptyDraft())
  const savedProfile = ref<ProfileRead | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const generating = ref(false)
  const dirty = ref(false)
  const lastSavedAt = ref<string | null>(null)

  /* -------------------------------- 草稿状态 -------------------------------- */
  const draftOwnerId = ref<string | null>(null)
  /** 检测到的、尚未被用户决定的本地草稿。 */
  const pendingDraft = ref<LocalDraft | null>(null)
  const autosavedAt = ref<number | null>(null)

  const generatedHtml = ref<string | null>(null)
  const generatedResumeId = ref<string | null>(null)
  const generatedTitle = ref<string | null>(null)

  /**
   * 真实页数（D5.1）。
   *
   * 由 `ResumePreview.vue` 量出 `.paper` 的 `scrollHeight` 后回写，`StylePanel`
   * 只读展示——两处各量一遍不仅要写两份 ResizeObserver，还可能量出不同结果。
   * 默认 1：还没测量时不能说「0 页」。
   */
  const pageCount = ref(1)

  /**
   * 样式 / 排版类改动（配色、模块顺序、标题）把「已生成简历」顶掉之后的提示（D5.2 / D5.3）。
   *
   * 放在 store 而不是组件里：改动的入口可能不止一处（面板、顶栏、快捷键），
   * 提示文案必须只有一份，否则「有的入口提示、有的入口静默」会被当成 bug。
   */
  const styleNotice = ref<string | null>(null)

  function dismissStyleNotice(): void {
    styleNotice.value = null
  }

  /** 当前纸张的物理尺寸（mm）；预览与面板共用，避免两处各写一份数字。 */
  const paperSpec = computed(() => PAPER_SPECS[draft.paper_size])

  /**
   * 当前主题的色值（D5.2）。
   *
   * `draft.theme` 已经过 `toTheme()` 收敛，理论上必然命中；`?? THEME_OPTIONS[0]`
   * 只是让「将来删掉某套主题但有人还存着旧草稿」时不会拿到 `undefined`。
   */
  const themeSpec = computed(
    () => THEME_OPTIONS.find((option) => option.id === draft.theme) ?? THEME_OPTIONS[0],
  )

  function setPageCount(value: number): void {
    pageCount.value = Math.max(1, Math.round(value))
  }

  const hasSavedProfile = computed(() => savedProfile.value !== null)
  const isGeneratingResultVisible = computed(() => generatedHtml.value !== null)

  /* ------------------------------ 多份简历状态 ------------------------------ */
  /*
   * `profiles` 是档案库的「行」（只含 title / is_default / 时间），
   * 明细永远按需 `GET /profiles/{id}` 单份拉取——列表不背整份简历。
   */
  const profiles = ref<ProfileSummary[]>([])
  /** 当前正在编辑的简历 id；null = 库里一份都没有，正处于「未落库的空白骨架」。 */
  const currentProfileId = ref<string | null>(null)
  const profilesLoading = ref(false)
  /** 回收站列表：只在打开回收站时拉取，不跟着编辑器一起加载。 */
  const trashProfiles = ref<ProfileSummary[]>([])
  const trashLoading = ref(false)

  /** 列表排序（D4.3）：字段 + 方向；默认「最近修改的在前」。 */
  const sortBy = ref<ProfileSortField>('updated_at')
  const sortDesc = ref(true)

  /**
   * 这批数据属于哪个用户。
   *
   * 首屏 `loadFromServer()` 时视图还没调用 `startDraftAutosave(ownerId)`，
   * `draftOwnerId` 仍是 null，于是回落到 auth store——路由守卫进页面前已经用
   * `/auth/me` 校验过，此刻 `user` 必定存在。auth 不反向依赖本 store，无循环引用。
   */
  function resolveOwnerId(): string | null {
    return draftOwnerId.value ?? useAuthStore().user?.id ?? null
  }

  /**
   * 切换 / 加载的并发序号（D4.2 快速连点的保护）。
   *
   * 每发起一次「换简历」就 `++requestSeq`，响应回来时若序号已被更新的请求顶掉，
   * 直接丢弃——否则慢的 A 响应会覆盖刚切过去的 B，表现为「切到 B 却看到 A 的内容」。
   * 用普通变量而不是 `ref`：它只是并发标记，不该触发任何渲染。
   */
  let requestSeq = 0

  const currentProfile = computed(
    () => profiles.value.find((profile) => profile.id === currentProfileId.value) ?? null,
  )
  /** 顶栏与生成标题都用它；没有简历时退回默认文案。 */
  const currentProfileTitle = computed(
    () => currentProfile.value?.title ?? DEFAULT_PROFILE_TITLE,
  )
  const hasProfiles = computed(() => profiles.value.length > 0)

  /** 后端只接受 3–8 条技能，这里同步限制 UI，避免用户删到 2 条才发现被拒。 */
  const canAddSkill = computed(() => draft.skills.length < SKILL_LIMIT.max)
  const canRemoveSkill = computed(() => draft.skills.length > SKILL_LIMIT.min)
  const canAddExperience = computed(() => draft.experiences.length < EXPERIENCE_LIMIT.max)
  const canRemoveExperience = computed(() => draft.experiences.length > EXPERIENCE_LIMIT.min)
  /** 教育经历：至少保留 1 段，最多与后端 `max_length=5` 对齐。 */
  const canAddEducation = computed(() => draft.educations.length < EDUCATION_LIMIT.max)
  const canRemoveEducation = computed(() => draft.educations.length > EDUCATION_LIMIT.min)

  function markDirty(): void {
    dirty.value = true
  }

  function applyDraft(next: ProfileDraft, profile: ProfileRead | null): void {
    draft.full_name = next.full_name
    draft.email = next.email
    draft.contact = next.contact
    draft.paper_size = next.paper_size
    draft.theme = next.theme
    draft.layout_config = next.layout_config
    // 全局换肤跟着**数据**走，而不是跟着点击走：切简历 / 首次加载 / 恢复草稿
    // 都会经过这里，一处同步就够；否则切到 B 时按钮还停在 A 的颜色。
    syncGlobalTheme(next.theme)
    draft.skills = next.skills
    draft.experiences = next.experiences
    draft.educations = next.educations
    savedProfile.value = profile
    dirty.value = false
    // 换了一份数据，上一条样式提示就过期了（否则切到 B 还挂着 A 的提示）。
    styleNotice.value = null
  }

  function updateProfileField(field: 'full_name' | 'email' | 'contact', value: string): void {
    draft[field] = value
    markDirty()
  }

  /**
   * 切换纸张（D5.1）。走 `draft` 而不是直接调接口：它和改名字、改手机号一样
   * 属于「未保存的修改」，要跟着「保存修改」一起落库，也才会触发本地草稿。
   */
  function updatePaperSize(size: PaperSize): void {
    if (draft.paper_size === size) return
    draft.paper_size = size
    markDirty()
  }

  /**
   * 切换配色（D5.2）。
   *
   * 和改纸张一样只写 `draft`，跟着「保存修改」一起落库。两条额外联动：
   * 1. 整个 Web UI 立即换肤（按钮 / 顶栏 / 选中态 / Element Plus 主色）；
   * 2. 「已生成简历」里的颜色是**生成那一刻**内联进 HTML 的，不会跟着 draft 变；
   *    继续停在这个视图里，用户会以为「点了圆点没反应」。
   *    所以直接切回实时预览，并留一条提示说明要重新生成才会更新生成物。
   */
  function updateTheme(theme: ThemeId): void {
    if (draft.theme === theme) return
    draft.theme = theme
    syncGlobalTheme(theme)
    markDirty()
    invalidateGeneratedResult('主题已修改，需重新生成简历。')
  }

  /**
   * 样式类改动（配色 / 排版）把「已生成简历」顶掉时的统一处理。
   *
   * 生成物里的颜色与顺序都是**生成那一刻**内联进 HTML 的快照，不会跟着 draft 变。
   * 继续停在生成结果视图里，用户会以为「点了没反应」；所以切回实时预览并留一条提示。
   * 两个入口共用这一处，避免「配色有提示、排版静默」这种不一致。
   */
  function invalidateGeneratedResult(message: string): void {
    if (generatedHtml.value === null) return
    showLivePreview()
    styleNotice.value = message
  }

  /* ------------------------------ 排版（D5.3） ------------------------------ */

  /** 当前模块顺序 + 标题；面板与预览都只读它，不各自去翻 `draft.layout_config`。 */
  const layoutSections = computed(() => draft.layout_config.sections)

  /** 把某个模块上移 / 下移一格；到边界就什么都不做（按钮本身也会禁用）。 */
  function moveSection(id: SectionId, offset: -1 | 1): void {
    const sections = draft.layout_config.sections
    const index = sections.findIndex((section) => section.id === id)
    const target = index + offset
    if (index < 0 || target < 0 || target >= sections.length) return
    const [moved] = sections.splice(index, 1)
    sections.splice(target, 0, moved)
    markDirty()
    invalidateGeneratedResult('排版已修改，需重新生成简历。')
  }

  function moveSectionUp(id: SectionId): void {
    moveSection(id, -1)
  }

  function moveSectionDown(id: SectionId): void {
    moveSection(id, 1)
  }

  /**
   * 改模块标题（面板输入框逐字触发）。
   *
   * 刻意**不 trim、也不回落默认值**：用户正在输入时，把「项目 」的尾空格当场裁掉
   * 会让下一个字直接贴到「项目」后面，空格永远敲不进去（输入框的 value 被重渲染）。
   * 空白与空标题的兜底放在 `toUpsertPayload()`（提交那一刻）和渲染层
   * （`section.title || 默认标题`），输入过程保持"所见即所打"。
   *
   * 长度只做码点级截断兜底：`maxlength` 已经挡在前面，这里防的是粘贴 / 输入法
   * 一次性灌入超长文本时 `maxlength` 在某些浏览器上的失效。
   */
  function updateSectionTitle(id: SectionId, title: string): void {
    const section = draft.layout_config.sections.find((item) => item.id === id)
    if (!section) return
    const next = clampTitle(title)
    if (section.title === next) return
    section.title = next
    markDirty()
    invalidateGeneratedResult('排版已修改，需重新生成简历。')
  }

  /** 渲染层用：空标题不能让纸面上出现一个空 `h2`。 */
  function sectionTitle(section: LayoutSection): string {
    return section.title.trim() || DEFAULT_SECTION_TITLES[section.id]
  }

  function updateSkill(index: number, field: keyof SkillDraft, value: string | number): void {
    const skill = draft.skills[index]
    if (!skill) return
    if (field === 'self_rating') {
      skill.self_rating = Math.min(5, Math.max(1, Number(value) || DEFAULT_SKILL_RATING))
    } else {
      skill.name = String(value)
    }
    markDirty()
  }

  function addSkill(): void {
    if (!canAddSkill.value) return
    draft.skills.push({ name: '', self_rating: DEFAULT_SKILL_RATING })
    markDirty()
  }

  function removeSkill(index: number): void {
    if (!canRemoveSkill.value) return
    draft.skills.splice(index, 1)
    markDirty()
  }

  function updateExperience(index: number, field: keyof ExperienceDraft, value: string): void {
    const experience = draft.experiences[index]
    if (!experience) return
    experience[field] = value
    markDirty()
  }

  function addExperience(): void {
    if (!canAddExperience.value) return
    draft.experiences.push({
      title: '',
      start_date: todayIsoDate(),
      end_date: '',
      description: '',
      responsibility: '',
    })
    markDirty()
  }

  function removeExperience(index: number): void {
    if (!canRemoveExperience.value) return
    draft.experiences.splice(index, 1)
    markDirty()
  }

  function updateEducation(index: number, field: keyof EducationDraft, value: string): void {
    const education = draft.educations[index]
    if (!education) return
    if (field === 'degree') {
      if (!DEGREE_OPTIONS.includes(value as Degree)) return
      education.degree = value as Degree
    } else {
      education[field] = value
    }
    markDirty()
  }

  function addEducation(): void {
    if (!canAddEducation.value) return
    draft.educations.push(createEmptyEducation())
    markDirty()
  }

  function removeEducation(index: number): void {
    if (!canRemoveEducation.value) return
    draft.educations.splice(index, 1)
    markDirty()
  }

  /**
   * 组装请求体：去首尾空格，日期空串转 null，熟练度固定 3，年级/专业不再提交。
   *
   * 只提交「有效行」（锚点字段非空），所以整份全空就是空数组——
   * 后端已去掉技能/经历的下限，这种「空档案」可以正常保存。
   */
  function toUpsertPayload(): ProfileUpsertRequest {
    return {
      full_name: draft.full_name.trim(),
      // 空串等同「没填」，交给后端存 NULL。
      email: draft.email.trim() || null,
      contact: draft.contact.trim(),
      // 显式传值（而不是省略）：前端永远知道当前选的是哪档纸，
      // 省略只会让后端走「保留原值」分支，万一本地和服务端不同步就再也改不回来。
      paper_size: draft.paper_size,
      // 同上：配色也显式传，理由与纸张一致。
      theme: draft.theme,
      /*
       * 排版（D5.3）：**是默认顺序就发 null，否则发完整快照**。
       *
       * 不一律发快照的理由：`layout_config = NULL` 的语义是「从未自定义」，
       * 将来默认顺序升级（新增第 4 个模块）时这类简历能自动跟上；
       * 而「自定义成今天的默认顺序」会被钉死。用户只是顺手保存一次就永久失去
       * 这条升级路径，代价和收益完全不成比例。
       *
       * 标题在这里兜底成默认值：输入框允许暂时为空（正在删改），
       * 但库里的标题不能为空——后端 `LayoutSectionInput.title` 会 422。
       */
      layout_config: isDefaultLayout(draft.layout_config)
        ? null
        : {
            version: LAYOUT_VERSION,
            sections: draft.layout_config.sections.map((section) => ({
              id: section.id,
              title: sectionTitle(section),
            })),
          },
      skills: meaningfulSkills(draft).map((skill) => ({
        name: skill.name.trim(),
        // 后端 `SkillInput.self_rating` 仍必填（1–5），但 UI 不再采集，统一写 3。
        self_rating: DEFAULT_SKILL_RATING,
      })),
      experiences: meaningfulExperiences(draft).map((experience) => ({
        title: experience.title.trim(),
        start_date: experience.start_date,
        end_date: experience.end_date === '' ? null : experience.end_date,
        description: experience.description.trim(),
        responsibility: experience.responsibility.trim(),
      })),
      educations: meaningfulEducations(draft).map((education) => ({
        school: education.school.trim(),
        major: education.major.trim(),
        degree: education.degree,
        // 后端是 DATE：`YYYY-MM` 补成当月 1 号；空值由生成前校验拦住。
        edu_start: toIsoDate(education.edu_start) ?? '',
        edu_end: toIsoDate(education.edu_end),
      })),
    }
  }

  /** 宽松的邮箱格式检查：只拦明显不是邮箱的输入，不做 RFC 全量校验。 */
  function looksLikeEmail(value: string): boolean {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)
  }

  /**
   * 生成简历前的完整性校验（严格）。
   *
   * 保存已经不拦必填了，所以「能不能生成」的全部判断都收敛到这里：
   * 姓名 / 手机号必填且号码合法，邮箱填了就必须像邮箱，
   * 教育经历至少 1 段且四要素齐全，技能与项目各至少 1 条并不超上限；
   * 开始时间不得晚于今天（与后端 `shanghai_today()` 同口径），
   * 结束时间允许未来——应届生要写「预计毕业时间」，只校验不早于开始时间。
   * 返回 null 表示通过。
   */
  function validateForGenerate(): string | null {
    if (!draft.full_name.trim()) return '请先填写姓名，再生成简历。'
    const contact = draft.contact.trim()
    if (!contact) return '请先填写手机号，再生成简历。'
    if (!PHONE_PATTERN.test(contact)) {
      return '手机号格式不正确，请填写 11 位中国大陆手机号（如 13800000000）。'
    }
    const email = draft.email.trim()
    if (email && !looksLikeEmail(email)) return '邮箱格式不正确，请检查后再生成。'

    const today = todayIsoDate()
    // 教育经历是「年月」精度，比较基准也截到月，否则 2026-11 与 2026-11-02 会误判。
    const todayMonth = today.slice(0, 7)

    const skills = meaningfulSkills(draft)
    if (skills.length < GENERATE_MIN.skills) return '至少填写 1 条技能，再生成简历。'
    if (skills.length > SKILL_LIMIT.max) return `技能最多 ${SKILL_LIMIT.max} 条。`
    const skillNames = skills.map((skill) => skill.name.trim())
    if (new Set(skillNames).size !== skillNames.length) return '技能名称不能重复。'

    const educations = meaningfulEducations(draft)
    if (educations.length < GENERATE_MIN.educations) {
      return '至少填写 1 段教育经历（学校、专业、学历、入学时间），再生成简历。'
    }
    if (educations.length > EDUCATION_LIMIT.max) {
      return `教育经历最多 ${EDUCATION_LIMIT.max} 段。`
    }
    for (const [index, education] of educations.entries()) {
      const label = `第 ${index + 1} 段教育经历`
      if (!education.major.trim()) return `${label}缺少专业。`
      if (!education.degree) return `${label}缺少学历。`
      if (!education.edu_start) return `${label}缺少入学时间。`
      if (education.edu_start > todayMonth) return `${label}的入学时间不能选择未来。`
      // 毕业时间允许未来（应届生填「预计毕业时间」），只要求不早于入学时间。
      if (education.edu_end && education.edu_end < education.edu_start) {
        return `${label}的毕业时间不能早于入学时间。`
      }
    }

    const experiences = meaningfulExperiences(draft)
    if (experiences.length < GENERATE_MIN.experiences) return '至少填写 1 段项目经历，再生成简历。'
    if (experiences.length > EXPERIENCE_LIMIT.max) {
      return `项目经历最多 ${EXPERIENCE_LIMIT.max} 条。`
    }
    for (const [index, experience] of experiences.entries()) {
      const label = `第 ${index + 1} 段经历`
      if (!experience.start_date) return `${label}缺少开始日期。`
      if (!experience.description.trim()) return `${label}缺少项目描述。`
      if (!experience.responsibility.trim()) return `${label}缺少个人职责。`
      if (experience.start_date > today) return `${label}的开始时间不能选择未来。`
      // 结束时间允许未来（「预计结束」），只要求不早于开始时间。
      if (experience.end_date && experience.end_date < experience.start_date) {
        return `${label}的结束日期不能早于开始日期。`
      }
    }
    return null
  }

  /**
   * 保存前的「契约自检」：只拦**会让后端 422** 的输入，不拦必填。
   *
   * 背景（保存 422 修复轮）：保存是草稿式的，`full_name` 可以空；但后端的
   * `contact` 仍是手机号强校验、`edu_start` / `start_date` 是 `date` 必填
   * （库里 NOT NULL），格式错或空串照样 422。与其把请求发出去再翻译
   * 一堆 `loc` 路径，不如在点「保存修改」时就给出人话提示。
   *
   * 与 `validateForGenerate()` 的分工：
   * - 这里只管「能不能落库」，不管完整性（不要求姓名/技能/项目齐全）；
   * - 姓名、手机号、教育四要素等完整性仍在生成时拦。
   * 返回 null 表示可以提交。
   */
  function validateForSave(): string | null {
    const contact = draft.contact.trim()
    if (contact && !PHONE_PATTERN.test(contact)) {
      return '手机号格式不正确：请填 11 位中国大陆手机号（如 13800000000），或先清空再保存。'
    }

    const today = todayIsoDate()
    // 教育经历是「年月」精度，比较基准也截到月（与 validateForGenerate 同口径）。
    const todayMonth = today.slice(0, 7)

    const skills = meaningfulSkills(draft)
    if (skills.length > SKILL_LIMIT.max) return `技能最多 ${SKILL_LIMIT.max} 条。`
    const skillNames = skills.map((skill) => skill.name.trim())
    if (new Set(skillNames).size !== skillNames.length) return '技能名称不能重复，请先合并同名技能。'
    if (skillNames.some((name) => name.length > 100)) return '技能名称最长 100 个字符。'

    const educations = meaningfulEducations(draft)
    if (educations.length > EDUCATION_LIMIT.max) {
      return `教育经历最多 ${EDUCATION_LIMIT.max} 段。`
    }
    for (const [index, education] of educations.entries()) {
      const label = `第 ${index + 1} 段教育经历`
      // 后端 `edu_start: date` 必填且非未来；空串会直接 422 date 解析失败。
      if (!education.edu_start) return `${label}缺少入学时间，请先补全再保存。`
      if (education.edu_start > todayMonth) return `${label}的入学时间不能选择未来。`
      if (education.edu_end && education.edu_end < education.edu_start) {
        return `${label}的毕业时间不能早于入学时间。`
      }
      if (education.school.trim().length > 200) return `${label}的学校名称最长 200 个字符。`
      if (education.major.trim().length > 100) return `${label}的专业最长 100 个字符。`
    }

    const experiences = meaningfulExperiences(draft)
    if (experiences.length > EXPERIENCE_LIMIT.max) {
      return `项目经历最多 ${EXPERIENCE_LIMIT.max} 条。`
    }
    for (const [index, experience] of experiences.entries()) {
      const label = `第 ${index + 1} 段项目经历`
      // 后端 `start_date: date` 必填且非未来。
      if (!experience.start_date) return `${label}缺少开始日期，请先补全再保存。`
      if (experience.start_date > today) return `${label}的开始时间不能选择未来。`
      if (experience.end_date && experience.end_date < experience.start_date) {
        return `${label}的结束日期不能早于开始日期。`
      }
      if (experience.title.trim().length > 200) return `${label}的项目名称最长 200 个字符。`
    }

    return null
  }

  /* ------------------------------ 多份简历操作 ------------------------------ */

  /** 当前排序的查询参数，列表的每一次刷新都用它。 */
  function listQuery(): { orderBy: ProfileSortField; order: 'asc' | 'desc' } {
    return { orderBy: sortBy.value, order: sortDesc.value ? 'desc' : 'asc' }
  }

  /** 列表刷新（不含回收站）：新建 / 删除 / 恢复 / 设默认 / 改名之后都要同步一次。 */
  async function refreshProfiles(): Promise<void> {
    profiles.value = await profilesApi.listProfiles(listQuery())
  }

  /** 切换排序字段（D4.3）；同字段重复点是「换方向」，不在这里处理。 */
  async function setSortField(field: ProfileSortField): Promise<void> {
    if (sortBy.value === field) return
    sortBy.value = field
    await refreshProfiles()
  }

  /** 切换升 / 降序。 */
  async function toggleSortOrder(): Promise<void> {
    sortDesc.value = !sortDesc.value
    await refreshProfiles()
  }

  /**
   * 重命名（D4.3）。
   *
   * 重名由后端返回 400「该简历名称已存在」，这里原样抛给 UI 展示——
   * 前端可以先做一次本地查重来省一次请求，但那只是体验优化，
   * 真正的约束必须在服务端（多标签页并发时前端列表是过期的）。
   */
  async function renameProfile(profileId: string, title: string): Promise<void> {
    const updated = await profilesApi.renameProfile(profileId, title)
    if (savedProfile.value?.id === profileId) {
      savedProfile.value.title = updated.title
      savedProfile.value.updated_at = updated.updated_at
    }
    await refreshProfiles()
  }

  /**
   * 未保存修改的拦截（切简历、新建简历前都先走这里）。
   *
   * 返回 false = 用户放弃本次操作，调用方必须真的停下。
   * 保存失败会带着中文原因抛出去交给 UI 展示——绝不能吞掉错误后硬切，
   * 否则用户会以为自己的修改已经落库。
   */
  async function ensureSavedBeforeSwitch(): Promise<boolean> {
    if (!dirty.value) return true
    const shouldSave = window.confirm('当前简历有未保存的修改。切换前需要先保存，是否现在保存？')
    if (!shouldSave) return false
    await save()
    return true
  }

  /**
   * 真正执行「换一份简历」，**不弹确认**（供已确认过的调用方复用）。
   *
   * 顺序是硬要求（D4.2）：
   * 1. 先停 watcher —— 否则下一步清空 draft 时，会把空白骨架写成上一份的草稿；
   * 2. 清 `pendingDraft` 与生成结果 —— 防止 A 的草稿横幅、A 的 HTML 出现在 B 里；
   * 3. 换 `currentProfileId` —— 之后草稿 key、保存目标都跟着变；
   * 4. 拉新数据 → `applyDraft` → 重新绑定 watcher；
   * 5. 每个 await 之后都用 `requestSeq` 确认自己还是「最新的一次切换」。
   */
  async function activateProfile(targetId: string | null): Promise<void> {
    const owner = resolveOwnerId()
    const previousId = currentProfileId.value
    const seq = ++requestSeq

    // 1. 解绑 autosave（内含取消失衡的延迟写入，避免旧 draft 被写进新 key）。
    stopDraftAutosave()
    // 2. 丢掉属于「上一份」的瞬时状态。
    pendingDraft.value = null
    showLivePreview()
    // 3. 换指针。
    currentProfileId.value = targetId
    loading.value = true

    try {
      const profile = targetId ? await profilesApi.getProfile(targetId) : null
      if (seq !== requestSeq) return
      applyDraft(profile ? draftFromProfile(profile) : createEmptyDraft(), profile)
      if (owner) {
        // 3.5 记住「上次打开的是哪一份」：进回收站再回来要能回到这一份（D4.3）。
        rememberProfileId(owner, targetId)
        // 4. 绑定新简历的 watcher；草稿判定也必须在新数据之后。
        startDraftAutosave(owner)
        detectLocalDraft(owner)
      }
    } catch (error) {
      // 拉取失败：把指针退回去并恢复 watcher，避免「id 是 B、内容是 A」的撕裂状态。
      if (seq === requestSeq) {
        currentProfileId.value = previousId
        if (owner) startDraftAutosave(owner)
      }
      throw error
    } finally {
      if (seq === requestSeq) loading.value = false
    }
  }

  /** 顶栏点击切换的入口：先拦未保存再切。返回 false = 用户取消。 */
  async function switchProfile(targetId: string): Promise<boolean> {
    if (targetId === currentProfileId.value) return true
    const ready = await ensureSavedBeforeSwitch()
    if (!ready) return false
    await activateProfile(targetId)
    return true
  }

  /**
   * 新建一份简历（空白 or 复制当前）。
   *
   * `copy_from_id` 交给后端深拷贝，**不要**前端 GET 再 PUT：两次请求之间源简历
   * 可能被改，拼出来会是一份「半新半旧」的混合体。
   */
  async function createProfile(
    options: { title?: string; copyCurrent?: boolean } = {},
  ): Promise<ProfileRead> {
    const ready = await ensureSavedBeforeSwitch()
    if (!ready) throw new Error('已取消：当前简历的修改尚未保存。')

    const created = await profilesApi.createProfile({
      title: options.title?.trim() ? options.title.trim() : null,
      copy_from_id: options.copyCurrent ? currentProfileId.value : null,
    })

    await refreshProfiles()
    await activateProfile(created.id)
    return created
  }

  /** 设为默认：后端会把其余未删除简历的 `is_default` 一起清掉。 */
  async function setDefaultProfile(profileId: string): Promise<void> {
    await profilesApi.setDefaultProfile(profileId)
    // 默认是「一群人里唯一的那个」，必须整表刷新，不能只改本地一条。
    await refreshProfiles()
    if (savedProfile.value?.id === profileId) savedProfile.value.is_default = true
  }

  /**
   * 删除（软删除进回收站，30 天内可恢复）。
   *
   * 删的是当前简历时自动切到默认 / 最新一份；一份不剩就回到空白骨架。
   * 这里刻意**不经过** `ensureSavedBeforeSwitch()`：简历都要删了，
   * 再问「要不要先保存」只会让用户困惑。
   */
  async function deleteProfile(profileId: string): Promise<void> {
    await profilesApi.deleteProfile(profileId)
    await refreshProfiles()

    const owner = resolveOwnerId()
    // 这份已经进回收站，它的本地草稿也必须清掉，否则恢复后又会弹「恢复草稿」。
    if (owner) removeDraft(owner, profileId)

    if (currentProfileId.value !== profileId) return
    const next = profiles.value.find((profile) => profile.is_default) ?? profiles.value[0] ?? null
    await activateProfile(next?.id ?? null)
  }

  /** 拉回收站列表（只在打开回收站时调用）。 */
  async function loadTrash(): Promise<void> {
    trashLoading.value = true
    try {
      trashProfiles.value = await profilesApi.listTrashProfiles()
    } finally {
      trashLoading.value = false
    }
  }

  /** 从回收站恢复：恢复后档案库与回收站都要刷新。 */
  async function restoreProfile(profileId: string): Promise<void> {
    await profilesApi.restoreProfile(profileId)
    await refreshProfiles()
    await loadTrash()
  }

  /** 彻底删除（不可恢复）：同时清掉该简历的本地草稿。 */
  async function purgeProfile(profileId: string): Promise<void> {
    await profilesApi.purgeProfile(profileId)
    const owner = resolveOwnerId()
    if (owner) removeDraft(owner, profileId)
    await loadTrash()
  }

  /* -------------------------------- 读写主线 -------------------------------- */

  /** 进入编辑器时调用：拉列表 → 恢复上次打开的那份（否则默认简历）→ 回填数据。 */
  async function loadFromServer(): Promise<void> {
    const seq = ++requestSeq
    const owner = resolveOwnerId()
    // 首屏就把归属绑好：后续 `activateProfile` / 草稿 key 都要用它。
    if (owner) draftOwnerId.value = owner
    loading.value = true
    profilesLoading.value = true
    try {
      const list = await profilesApi.listProfiles(listQuery())
      if (seq !== requestSeq) return
      profiles.value = list

      /*
       * 选哪一份（D4.3）：上次打开的那份 > 默认简历 > 列表第一份。
       *
       * 「上次打开的」只在**当前用户的列表里找得到**时才用：从回收站回来要回到
       * 原来那份，但被删掉 / 被彻底删掉之后必须能自动回退，不能卡在 404 上。
       */
      const rememberedId = owner ? readRememberedProfileId(owner) : null
      const remembered = rememberedId
        ? list.find((profile) => profile.id === rememberedId)
        : undefined
      const target = remembered ?? list.find((profile) => profile.is_default) ?? list[0] ?? null

      currentProfileId.value = target?.id ?? null
      if (owner) rememberProfileId(owner, target?.id ?? null)

      const profile = target ? await profilesApi.getProfile(target.id) : null
      if (seq !== requestSeq) return
      applyDraft(profile ? draftFromProfile(profile) : createEmptyDraft(), profile)
    } finally {
      if (seq === requestSeq) {
        loading.value = false
        profilesLoading.value = false
      }
    }
  }

  /**
   * 唯一的保存动作（表单 + 纸面共用）：PUT 整体替换**当前这份**简历。
   *
   * 库里一份都没有时先 `POST /profiles` 建一份默认简历再 PUT——
   * 这样「只打开页面、什么都不填」不会在库里留下空行（与 D4.1 的 `/me` 语义一致），
   * 一旦真的点了保存，就一定有一份真简历接住这些数据。
   *
   * 提交前先跑 `validateForSave()`：有硬错误就直接抛，**不发请求**——
   * 这样用户看到的是「第 1 段教育经历缺少入学时间」而不是一个 422。
   */
  async function save(): Promise<ProfileRead> {
    const problem = validateForSave()
    if (problem) throw new Error(problem)
    saving.value = true
    const owner = draftOwnerId.value
    try {
      let profile: ProfileRead
      if (currentProfileId.value) {
        profile = await profilesApi.updateProfile(currentProfileId.value, toUpsertPayload())
      } else {
        const created = await profilesApi.createProfile({ is_default: true })
        profile = await profilesApi.updateProfile(created.id, toUpsertPayload())
        currentProfileId.value = created.id
        // 空白骨架那格草稿（`:new`）已经没有意义，别留着让下次误提示。
        if (owner) removeDraft(owner, null)
      }
      applyDraft(draftFromProfile(profile), profile)
      // 列表里的 updated_at / 标题要跟着刷新；失败不影响「已保存」这个事实。
      try {
        await refreshProfiles()
      } catch {
        // 忽略：下次进页面会重新拉。
      }
      lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
      // 已经落库，本地草稿失去意义，立刻删除，避免下次进来误提示恢复。
      clearDraft()
      return profile
    } finally {
      saving.value = false
    }
  }

  /**
   * 生成简历：内容由后端按 `profile_id` 从库里组装。
   *
   * 标题优先用当前简历的 `title`（「投后端」生成出来就叫《投后端》），
   * 传参只在「还没有任何简历」时兜底——`StylePanel` 仍在传 '我的简历'，
   * 保持旧调用点不动，但不再用它覆盖真实的简历标题。
   */
  async function generate(title?: string): Promise<ResumeRead> {
    generating.value = true
    try {
      const resume = await resumesApi.generateResume({
        title: currentProfileTitle.value || title?.trim() || DEFAULT_PROFILE_TITLE,
        profile_id: currentProfileId.value,
      })
      generatedHtml.value = resume.content_html
      generatedResumeId.value = resume.resume_id
      generatedTitle.value = resume.title
      // 生成物已经带上最新配色，「需重新生成」的提示到此为止。
      styleNotice.value = null
      return resume
    } finally {
      generating.value = false
    }
  }

  /** 回到「实时预览」模式，丢掉已生成的 HTML（不影响数据库里的简历）。 */
  function showLivePreview(): void {
    generatedHtml.value = null
    generatedResumeId.value = null
    generatedTitle.value = null
  }

  /* -------------------------------- 本地草稿 -------------------------------- */

  let autosaveTimer: ReturnType<typeof setTimeout> | null = null
  let stopDraftWatcher: (() => void) | null = null

  function cancelScheduledWrite(): void {
    if (autosaveTimer !== null) {
      clearTimeout(autosaveTimer)
      autosaveTimer = null
    }
  }

  function persistNow(): void {
    const owner = draftOwnerId.value
    if (!owner) return
    try {
      const envelope: LocalDraft = {
        version: DRAFT_VERSION,
        saved_at: Date.now(),
        // 深拷贝：draft 是 reactive，直接 JSON.stringify 会带上 Proxy 语义，
        // 且草稿必须是写入那一刻的快照，不能被后续编辑改写。
        data: JSON.parse(JSON.stringify(draft)) as ProfileDraft,
      }
      // key 必须带当前简历 id：切到 B 之后，A 的草稿不能写进 B 的格子。
      localStorage.setItem(
        draftStorageKey(owner, currentProfileId.value),
        JSON.stringify(envelope),
      )
      autosavedAt.value = envelope.saved_at
    } catch {
      // 超出配额 / 无痕模式禁用 storage：草稿是增益能力，失败必须静默。
    }
  }

  function scheduleWrite(): void {
    cancelScheduledWrite()
    autosaveTimer = setTimeout(() => {
      autosaveTimer = null
      persistNow()
    }, DRAFT_WRITE_DELAY)
  }

  /**
   * 绑定草稿归属用户并开启自动写入。
   *
   * 只在 `dirty` 为真时写草稿——`loadFromServer()` / `reset()` 回填数据时
   * `dirty` 是 false，所以「刚进页面」不会凭空生成一份草稿。
   */
  function startDraftAutosave(ownerId: string): void {
    draftOwnerId.value = ownerId
    stopDraftWatcher?.()
    stopDraftWatcher = watch(
      draft,
      () => {
        if (!dirty.value) return
        scheduleWrite()
      },
      { deep: true },
    )
  }

  function stopDraftAutosave(): void {
    stopDraftWatcher?.()
    stopDraftWatcher = null
    cancelScheduledWrite()
  }

  /**
   * 读取本地草稿并判断是否值得提示恢复。
   *
   * 判定用「内容是否与服务端不同」（忽略 self_rating），而不是裸比时间戳：
   * 后端 `updated_at` 是 UTC naive 字符串，前端 `Date.parse` 会按本地时区解析，
   * 直接比时间会稳定偏 8 小时，误判成「草稿更新」。时间只用于提示文案。
   */
  function detectLocalDraft(ownerId: string): void {
    // 老用户升级上来的第一格草稿还在旧 key 里，先搬到当前简历名下再判定。
    migrateLegacyDraft(ownerId, currentProfileId.value)

    const found = readDraft(ownerId, currentProfileId.value)
    if (!found) {
      pendingDraft.value = null
      return
    }
    if (comparableOf(found.data) === comparableOf(draft)) {
      // 和服务器一模一样，说明上次其实已经保存成功，草稿是残留，直接清掉。
      removeDraft(ownerId, currentProfileId.value)
      pendingDraft.value = null
      return
    }
    pendingDraft.value = found
  }

  /** 用户选择「恢复」：把草稿灌回表单，并标记为未保存。 */
  function restoreDraft(): void {
    const pending = pendingDraft.value
    if (!pending) return
    applyDraft(normalizeDraft(pending.data), savedProfile.value)
    dirty.value = true
    pendingDraft.value = null
  }

  /** 用户选择「丢弃」：删本地草稿，保留服务端数据。 */
  function discardDraft(): void {
    const owner = draftOwnerId.value
    if (owner) removeDraft(owner, currentProfileId.value)
    pendingDraft.value = null
  }

  function clearDraft(): void {
    cancelScheduledWrite()
    const owner = draftOwnerId.value
    if (owner) removeDraft(owner, currentProfileId.value)
    autosavedAt.value = null
    pendingDraft.value = null
  }

  /**
   * 离开编辑器前把「还在 400ms 防抖里」的改动立刻落一次草稿。
   *
   * 新增回收站路由后，页面可以被切走；若只 `stopDraftAutosave()`，
   * 最后几次输入会连草稿都没写就被丢弃。这里先 flush 再解绑，
   * 回到编辑器时走的是「检测到未保存草稿 → 是否恢复」的正常路径。
   */
  function flushDraft(): void {
    if (!dirty.value || !draftOwnerId.value) return
    cancelScheduledWrite()
    persistNow()
  }

  /** 登出时清空，避免下一个账号看到上一个人的简历列表与档案。 */
  function reset(): void {
    // 让在途的加载 / 切换全部作废（否则登出后回来的响应还会写进 store）。
    requestSeq += 1
    // 先解绑归属，再清空 draft：否则监听器会把空白骨架写成上一个人的草稿。
    draftOwnerId.value = null
    cancelScheduledWrite()
    pendingDraft.value = null
    autosavedAt.value = null
    profiles.value = []
    currentProfileId.value = null
    trashProfiles.value = []
    applyDraft(createEmptyDraft(), null)
    lastSavedAt.value = null
    showLivePreview()
  }

  return {
    draft,
    savedProfile,
    loading,
    saving,
    generating,
    dirty,
    lastSavedAt,
    generatedHtml,
    generatedResumeId,
    generatedTitle,
    pendingDraft,
    autosavedAt,
    hasSavedProfile,
    isGeneratingResultVisible,
    /* 纸张与页数（D5.1） */
    pageCount,
    paperSpec,
    setPageCount,
    updatePaperSize,
    /* 配色（D5.2） */
    themeSpec,
    updateTheme,
    styleNotice,
    dismissStyleNotice,
    /* 排版（D5.3） */
    layoutSections,
    sectionTitle,
    moveSectionUp,
    moveSectionDown,
    updateSectionTitle,
    /* 多份简历 */
    profiles,
    currentProfile,
    currentProfileId,
    currentProfileTitle,
    profilesLoading,
    hasProfiles,
    trashProfiles,
    trashLoading,
    switchProfile,
    createProfile,
    deleteProfile,
    setDefaultProfile,
    renameProfile,
    sortBy,
    sortDesc,
    setSortField,
    toggleSortOrder,
    refreshProfiles,
    loadTrash,
    restoreProfile,
    purgeProfile,
    canAddSkill,
    canRemoveSkill,
    canAddExperience,
    canRemoveExperience,
    canAddEducation,
    canRemoveEducation,
    updateProfileField,
    updateSkill,
    addSkill,
    removeSkill,
    updateExperience,
    addExperience,
    removeExperience,
    updateEducation,
    addEducation,
    removeEducation,
    validateForGenerate,
    validateForSave,
    toUpsertPayload,
    loadFromServer,
    save,
    generate,
    showLivePreview,
    startDraftAutosave,
    stopDraftAutosave,
    detectLocalDraft,
    restoreDraft,
    discardDraft,
    clearDraft,
    flushDraft,
    reset,
  }
})
