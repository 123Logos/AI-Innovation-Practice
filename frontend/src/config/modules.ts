/**
 * 模块表（守则 L4）——「产品里有哪些模块」这张表的唯一定义处。
 *
 * 一处定义、多处消费：顶栏（今天已消费）、批次 F3 的右侧用户抽屉与 Logo 旁工作区切换器
 * （还没建）都从这里读。视图里再出现第二份硬编码的模块名数组，就是违反 L4。
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
 */

/** 模块的稳定 id：给消费方当 key 用，不随文案变化，所以与 `routeName` 分开两个名字。 */
export type AppModuleKey =
  | 'resume-editor'
  | 'job-board'
  | 'organization'
  | 'interview'
  | 'assessment'

export interface AppModule {
  /** 稳定 id（见 `AppModuleKey`）。 */
  readonly key: AppModuleKey
  /** 模块名，直接上屏的用户可见文案（守则 C1/C2 约束它）。 */
  readonly name: string
  /** 路由 `name`，必须能在真实路由表里解析到（L5 的机械判据）。 */
  readonly routeName: string
  /** 可达条件：是否需要登录。与路由记录上的 `meta.requiresAuth` 由测试核对一致。 */
  readonly requiresAuth: boolean
  /**
   * 是否出现在顶栏一级。`false` 的模块由批次 F3 的抽屉 / 切换器呈现。
   * 简历工作台标 `false` 不是漏项：它就是顶栏所在的工作台本身，
   * 在一级导航里再摆一份「回自己」的入口，正是守则 V2 要挪走的东西。
   */
  readonly inTopBar: boolean
  /** 24×24 描边图标的 `path d` 列表（C1：图标必须是 SVG，不许是字符）。 */
  readonly iconPaths: readonly string[]
}

/**
 * 唯一的模块数组。新增一项之前先确认它已经能到达（L5），
 * 并且没有第二份数组在别处描述同一件事（L4）。
 */
export const APP_MODULES: readonly AppModule[] = [
  {
    key: 'resume-editor',
    name: '简历工作台',
    routeName: 'resume-editor',
    requiresAuth: true,
    inTopBar: false,
    iconPaths: ['M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z', 'M14 3v5h5'],
  },
  {
    key: 'job-board',
    name: '岗位库',
    routeName: 'job-board',
    requiresAuth: true,
    inTopBar: true,
    iconPaths: ['M3 8h18v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z', 'M9 8V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v3'],
  },
  /*
   * 批次 B5（ADR-021）起可见。**对所有登录用户开放**，不按角色隐藏：
   * 学生进来看到的是「我在哪个组织」与「你没有查看成员名单的权限」，
   * 这比一个凭空消失的入口更容易解释——而真正的拦截在后端（403），不靠这里少画一个入口。
   */
  {
    key: 'organization',
    name: '组织管理',
    routeName: 'organization',
    requiresAuth: true,
    inTopBar: true,
    iconPaths: ['M3 21h18', 'M5 21V7l7-4 7 4v14', 'M9 21v-6h6v6'],
  },
  {
    // 批次 B11a（FR-07 / ADR-022）落地：模拟面试进模块表，工作区切换器自动多一张卡片（L4/L5）。
    // `inTopBar` 取 false：这一批不占顶栏一级入口（避免顶栏项过多），由工作区切换器呈现，
    // 已能到达——`layoutGuards.spec.ts` 钉住它的 routeName 必须可解析。
    key: 'interview',
    name: '模拟面试',
    routeName: 'interview-list',
    requiresAuth: true,
    inTopBar: false,
    iconPaths: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'],
  },
  {
    // 批次 B9（FR-04 / ADR-028）落地：能力测评进模块表。与模拟面试同一取舍——
    // `inTopBar` 取 false，由工作区切换器呈现；已能到达，`layoutGuards.spec.ts` 钉住。
    key: 'assessment',
    name: '能力测评',
    routeName: 'assessment',
    requiresAuth: true,
    inTopBar: false,
    iconPaths: ['M9 12l2 2 4-4', 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'],
  },
]

/** 顶栏一级导航读这一份：批次 F3 加抽屉时也是加过滤条件，不是再抄一份数组。 */
export const TOP_BAR_MODULES: readonly AppModule[] = APP_MODULES.filter(
  (module) => module.inTopBar,
)

/**
 * 按稳定 id 取模块。取不到就直接抛：模块表是编译期枚举（`AppModuleKey`），
 * 走到运行时才炸说明表被手改坏了，静默回落只会让顶栏少一个入口而没人知道。
 */
export function moduleByKey(key: AppModuleKey): AppModule {
  const found = APP_MODULES.find((module) => module.key === key)
  if (!found) throw new Error(`模块表里没有这一项：${key}`)
  return found
}
