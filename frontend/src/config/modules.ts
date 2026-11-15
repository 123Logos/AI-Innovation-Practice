/**
 * 模块表（守则 L4）——「产品里有哪些模块」这张表的唯一定义处。
 *
 * 一处定义、多处消费：`ModuleNav.vue`（顶栏下方的二级导航，2026-11-14 起模块切换只有这一处）
 * 与 `DashboardView.vue`（工作台首屏的入口卡片）都从这里读。
 * 视图里再出现第二份硬编码的模块名数组，就是违反 L4。
 *
 * 三条刻意的取舍：
 *
 * 1. **这里只有真的能点到的模块**（守则 L5）。未实现的模块
 *    不进表、不给禁用态——禁用态会让用户以为「快有了」，而我们对它没有排期承诺；
 *    它们落地当天才是第一次可见的时刻。「组织管理」于批次 B5（2026-11-12）、
 *    「模拟面试」于批次 B11a、「能力测评」于批次 B9（2026-11-13）均已落地，三者都在表里；
 *    教师端学情看板（B10a）与批量下载（B10b）等仍未实现的模块不进表。
 *    `src/__tests__/layoutGuards.spec.ts` 用 `router.hasRoute()` 逐条钉住这一格。
 *
 * 2. **`name` 是用户可见文案**，归批次 F1a 的文案清扫管；本批一个字都不改，
 *    所以「岗位库」这三个字仍然由 `AppTopBar.spec.ts` 的文案断言兜住。
 *
 * 3. **图标是 SVG 路径数据，不是字符**。守则 C1 禁止拿 emoji / `✓` / `▾` 这类符号当图标，
 *    所以这里存 `viewBox="0 0 24 24"` 描边风格的 `d` 串，由消费方渲染成
 *    `<svg aria-hidden="true">` 并保证相邻有文字标签（`AppTopBar.vue` 的 `.icon-button` 即此先例）。
 *    本批顶栏不渲染图标：加图标是视觉改动，归批次 F3 的导航重排。
 *
 * 与 `frontend/src/router/index.ts` 的分工：那张表管「叫什么、去哪、什么条件可见」，
 * 路由表管「挂哪个组件、要不要登录守卫、传什么 props」。两份的交点由测试核对
 * （`requiresAuth` 与 `routeName` 必须两边一致），不靠人记。
 *
 * **2026-11-15 加了第四格 `visibleToRoles`**（老板指令：基于角色的导航过滤）。
 * 这一格只决定**入口画不画**，不是权限边界——后端每个端点自己再判一次
 * （无权限 403、无组织 400），与 `stores/auth.ts::hasPermission` 上面那段纪律同一句话。
 * 判据用角色而不是权限字符串，是因为老板给的分派规则就是按角色写的；
 * 两边今天等价：`app/core/permissions.py::ROLE_PERMISSIONS` 里 `teacher` / `school_admin` /
 * `platform_admin` 都含 `dashboard:read`，与下面 `STAFF_ROLES` 三支一模一样。
 */
import type { UserRole } from '@/api/types'

/** 模块的稳定 id：给消费方当 key 用，不随文案变化，所以与 `routeName` 分开两个名字。 */
export type AppModuleKey =
  | 'dashboard'
  | 'resume-editor'
  | 'job-board'
  | 'organization'
  | 'interview'
  | 'assessment'
  | 'teacher-dashboard'

export interface AppModule {
  /** 稳定 id（见 `AppModuleKey`）。 */
  readonly key: AppModuleKey
  /** 模块名，直接上屏的用户可见文案（守则 C1/C2 约束它）。 */
  readonly name: string
  /** 路由 `name`，必须能在真实路由表里解析到（L5 的机械判据）。 */
  readonly routeName: string
  /** 可达条件：是否需要登录。与路由记录上的 `meta.requiresAuth` 由测试核对一致。 */
  readonly requiresAuth: boolean
  /** 24×24 描边图标的 `path d` 列表（C1：图标必须是 SVG，不许是字符）。 */
  readonly iconPaths: readonly string[]
  /**
   * 哪些角色能在二级导航 / 工作台入口里看到这一项。
   *
   * 空数组是不允许的（`layoutGuards.spec.ts` 钉住「每一项至少对一个角色可见」）——
   * 一个谁都看不见的入口等于 L5 禁的那种「写了但到不了」的孤儿。
   */
  readonly visibleToRoles: readonly UserRole[]
}

/**
 * 教师侧三支角色。**不是**「学生那一支的反面」写出来的表达式，而是明写出来：
 * 它与 `app/core/permissions.py::ROLE_PERMISSIONS` 里拿到 `dashboard:read` 的那三支同源，
 * `platform_admin` 由运维在库里授予，不给它任何入口会让它登录后面对一条空白导航。
 */
export const STAFF_ROLES: readonly UserRole[] = ['teacher', 'school_admin', 'platform_admin']

/** 全部登录角色（学生 + 教师侧三支）。 */
export const ALL_LOGGED_IN_ROLES: readonly UserRole[] = ['student', ...STAFF_ROLES]

/**
 * 唯一的模块数组。新增一项之前先确认它已经能到达（L5），
 * 并且没有第二份数组在别处描述同一件事（L4）。
 */
export const APP_MODULES: readonly AppModule[] = [
  {
    // 批次 F7 落地：工作台首屏（双态）。老板 2026-11-09「纳入路由，不要删」。
    // 它是登录后的落点页：顶栏已有 Logo 回首页，二级导航里再摆一个「工作台」是有意的
    // （从任意模块页一键回到工作台，比回 Logo 更明确）。
    key: 'dashboard',
    name: '工作台',
    routeName: 'dashboard',
    requiresAuth: true,
    // 工作台是登录后的落点页本身，两支角色都要有回去的路。
    // 页面内容按角色换（学生看四张统计卡，教师侧看入口卡），见 DashboardView。
    visibleToRoles: ALL_LOGGED_IN_ROLES,
    iconPaths: ['M3 10.5 12 3l9 7.5', 'M5 9.5V21h14V9.5'],
  },
  {
    key: 'resume-editor',
    name: '简历工作台',
    routeName: 'resume-editor',
    requiresAuth: true,
    visibleToRoles: ALL_LOGGED_IN_ROLES,
    iconPaths: ['M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z', 'M14 3v5h5'],
  },
  {
    key: 'job-board',
    name: '岗位库',
    routeName: 'job-board',
    requiresAuth: true,
    visibleToRoles: ALL_LOGGED_IN_ROLES,
    iconPaths: ['M3 8h18v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z', 'M9 8V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v3'],
  },
  /*
   * 批次 B5（ADR-021）起可见。
   *
   * **2026-11-15 起只对教师侧三支角色可见**（老板指令：基于角色的导航过滤）。
   * 这里改的只是「入口画不画」——后端那三道闸一格没动：成员名单要 `org:member:read`、
   * 改角色要 `org:member:manage`、审计要 `audit:read`，越权仍是 403。
   *
   * ⚠️ 一条连带后果必须说清（交付里也写了）：学生「凭邀请码加入班级」的那一屏就在本页
   *   （`OrganizationView.vue` 的 `hasNoOrganization` 分支），入口对学生隐藏之后，
   *   **学生在界面上不再有走到这条路径的链接**。加入能力本身没被删（直接访问 `/organization`
   *   仍然可用、后端仍然接受兑换），但发现路径断了。要么给学生留一个入口，
   *   要么把兑换挪到工作台——这一格等老板拍。
   */
  {
    key: 'organization',
    name: '组织管理',
    routeName: 'organization',
    requiresAuth: true,
    visibleToRoles: STAFF_ROLES,
    iconPaths: ['M3 21h18', 'M5 21V7l7-4 7 4v14', 'M9 21v-6h6v6'],
  },
  {
    // 批次 B11a（FR-07 / ADR-022）落地：模拟面试进模块表，工作区切换器自动多一张卡片（L4/L5）。
    // 由顶栏下方的二级导航呈现（2026-11-14 起模块入口只有那一处），
    // 已能到达——`layoutGuards.spec.ts` 钉住它的 routeName 必须可解析。
    key: 'interview',
    name: '模拟面试',
    routeName: 'interview-list',
    requiresAuth: true,
    visibleToRoles: ALL_LOGGED_IN_ROLES,
    iconPaths: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'],
  },
  {
    // 批次 B9（FR-04 / ADR-028）落地：能力测评进模块表。与模拟面试同一取舍——
    // 由二级导航呈现；已能到达，`layoutGuards.spec.ts` 钉住。
    key: 'assessment',
    name: '能力测评',
    routeName: 'assessment',
    requiresAuth: true,
    visibleToRoles: ALL_LOGGED_IN_ROLES,
    iconPaths: ['M9 12l2 2 4-4', 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'],
  },
  {
    // 批次 B10a / B10b（ADR-029）落地：教师端学情看板。由二级导航呈现。
    // 2026-11-15 起只对教师侧三支角色可见（老板指令）；「前端少画一张卡不构成任何保护」
    // 这句话仍然成立——真正的边界是 `GET /teacher/dashboard` 上的 `dashboard:read`（403）
    // 与 `action_gate::require_organization`（400），两者都在后端。
    key: 'teacher-dashboard',
    name: '学情看板',
    routeName: 'teacher-dashboard',
    requiresAuth: true,
    visibleToRoles: STAFF_ROLES,
    iconPaths: ['M4 20V10', 'M10 20V4', 'M16 20v-7', 'M22 20H2'],
  },
]

/**
 * 按稳定 id 取模块。取不到就直接抛：模块表是编译期枚举（`AppModuleKey`），
 * 走到运行时才炸说明表被手改坏了，静默回落只会让顶栏少一个入口而没人知道。
 */
export function moduleByKey(key: AppModuleKey): AppModule {
  const found = APP_MODULES.find((module) => module.key === key)
  if (!found) throw new Error(`模块表里没有这一项：${key}`)
  return found
}

/**
 * 这个角色算不算「教师侧」。判据就是 `STAFF_ROLES` 那一支，
 * 不在别处再写一遍 `role === 'teacher' || role === 'school_admin'`——
 * 那是第二套真相，将来加一支角色就会漏。
 */
export function isStaffRole(role: UserRole | null): boolean {
  return role !== null && STAFF_ROLES.includes(role)
}

/**
 * 某个角色能看到的模块入口。**这是入口可见性的唯一过滤器**：
 * `ModuleNav.vue` 与 `DashboardView.vue` 都调它，两边不许各写一份 `filter`。
 *
 * `role` 为 `null`（还没 `/auth/me` 回来，或未登录）时返回空数组：
 * 二级导航只在 `AppLayout` 之下渲染，而那一层全部是 `requiresAuth` 页面，
 * 守卫已经保证那一刻角色拿到了；真拿到 null 也只是暂时不画页签，
 * 比按「猜出来的学生身份」多画几个入口要好（fail-closed，与 BYOK 白名单同一取向）。
 */
export function modulesVisibleTo(role: UserRole | null): AppModule[] {
  if (!role) return []
  return APP_MODULES.filter((module) => module.visibleToRoles.includes(role))
}

/**
 * 只有教师侧才看得见的模块——工作台用它给教师拼「进入学情看板 / 进入组织管理」那两张卡，
 * 名字仍从模块表来（守则 L4：视图里不许出现写死的模块名）。
 */
export function staffOnlyModules(): AppModule[] {
  return APP_MODULES.filter((module) => !module.visibleToRoles.includes('student'))
}
