import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { armSessionExpiredNotice, setUnauthorizedHandler } from '@/api/client'
import AppLayout from '@/components/AppLayout.vue'
import { useAuthStore } from '@/stores/auth'
import JobBoardView from '@/views/JobBoardView.vue'
import JobCreateView from '@/views/JobCreateView.vue'
import JobDetailView from '@/views/JobDetailView.vue'
import JobTrashView from '@/views/JobTrashView.vue'
import LoginView from '@/views/LoginView.vue'
import OrganizationView from '@/views/OrganizationView.vue'
import RecycleBinView from '@/views/RecycleBinView.vue'
import RegisterView from '@/views/RegisterView.vue'
import ResumeEditorView from '@/views/ResumeEditorView.vue'
import InterviewListView from '@/views/InterviewListView.vue'
import InterviewDetailView from '@/views/InterviewDetailView.vue'
import AssessmentView from '@/views/AssessmentView.vue'
import DashboardView from '@/views/DashboardView.vue'
import TeacherDashboardView from '@/views/TeacherDashboardView.vue'

declare module 'vue-router' {
  interface RouteMeta {
    /** 需要登录；未登录跳 `/login` 并带上 redirect。 */
    requiresAuth?: boolean
    /** 仅未登录可见；已登录访问会直接送去编辑器。 */
    guestOnly?: boolean
  }
}

const routes: RouteRecordRaw[] = [
  /*
   * 需要顶栏的页面统一收在 `AppLayout` 之下（守则 L1）。
   *
   * 外层这条记录**没有 name**：它是外框不是目的地，给它一个名字只会多出一个
   * 「谁都能跳、跳过去却什么都没摆」的假入口。`meta.requiresAuth` 逐条写在 children 上，
   * 不靠外层继承——`router.getRoutes()` 返回的是每条记录**自己**的 meta，
   * `stores/__tests__/job.spec.ts` 的「五条 jobs 路由全部 requiresAuth」读的就是这一格；
   * 而 `to.meta` 在守卫里是合并视图，所以守卫与路由表两边读到的是同一个事实。
   *
   * 批次 F7（2026-11-14）起，`views/DashboardView.vue` **已在这棵树里**：登录态是
   * 下面的 `dashboard`，访客态是文件末尾的 `/welcome`。它不再是守则 §11 的规划期资产，
   * `scripts/check_route_reachability.py` 的豁免名单已随之清空。
   */
  {
    path: '/',
    component: AppLayout,
    children: [
      {
        /*
         * 简历工作台（2026-11-14 从 `path: ''` 挪到 `/resume`）。
         * 挪的理由是「默认落地」这件事：根路径 `/` 现在要按登录态分流
         * （未登录 → `/welcome`，已登录 → `/dashboard`），它不能再同时是编辑器本身。
         * 编辑器必须有自己的一条路径，否则 `{ name: 'resume-editor' }` 解析出来的还是 `/`，
         * 分流规则会把它自己也劫走。
         */
        path: 'resume',
        name: 'resume-editor',
        component: ResumeEditorView,
        meta: { requiresAuth: true },
      },
      {
        path: 'trash',
        name: 'recycle-bin',
        component: RecycleBinView,
        meta: { requiresAuth: true },
      },
      /*
       * 岗位库五条路由（ADR-011 议题 3.B）。
       *
       * 声明顺序是硬要求：`jobs/new`、`jobs/trash` 这类静态段必须排在 `jobs/:id`
       * **之前**。Vue Router 本身按段的静态/动态权重打分排序，写错顺序未必立刻出问题，
       * 但 `jobs/trash` 一旦落到 `/:id` 后面，就会被当成「id = trash 的详情页」——
       * 后端 `app/api/v1/jobs.py:3-5` 记录过同一个坑，这里按同一条规则排布，
       * 并在 `stores/__tests__/job.spec.ts` 里留一条 `resolve()` 断言钉住它。
       *
       * `job-create` 与 `job-edit` **共用一个组件**（`JobCreateView.vue`），差别由 `props` 传进去。
       * 两条路由各自给一份 props 工厂而不是让组件去猜 `route.params.id` 的有无：
       * 「有 id 就是编辑态」是一种会被下一次加路由打破的隐式约定，而显式 `mode` 会。
       */
      {
        path: 'jobs',
        name: 'job-board',
        component: JobBoardView,
        meta: { requiresAuth: true },
      },
      {
        path: 'jobs/new',
        name: 'job-create',
        component: JobCreateView,
        props: () => ({ mode: 'create' as const }),
        meta: { requiresAuth: true },
      },
      {
        path: 'jobs/trash',
        name: 'job-trash',
        component: JobTrashView,
        meta: { requiresAuth: true },
      },
      {
        path: 'jobs/:id/edit',
        name: 'job-edit',
        component: JobCreateView,
        props: (route) => ({ mode: 'edit' as const, jobId: String(route.params.id ?? '') }),
        meta: { requiresAuth: true },
      },
      {
        path: 'jobs/:id',
        name: 'job-detail',
        component: JobDetailView,
        meta: { requiresAuth: true },
      },
      /*
       * 组织管理（批次 B5）。路径是静态段、不与 `jobs/:id` 同级，所以没有排序陷阱。
       * 模块表 `config/modules.ts` 里那一项的 `routeName` 必须与这里逐字一致，
       * `src/__tests__/layoutGuards.spec.ts` 用 `router.hasRoute()` 钉住它（守则 L5）。
       */
      {
        path: 'organization',
        name: 'organization',
        component: OrganizationView,
        meta: { requiresAuth: true },
      },
      /*
       * 模拟面试两条路由（FR-07 / B11a，ADR-022）。
       *
       * 声明顺序与岗位库同一硬要求：`interviews` 静态段排在 `interviews/:id` 之前。
       * 两条都挂在 `AppLayout` 下、都带 `requiresAuth`，与模块表里的 `interview`
       * 那一项（routeName=`interview-list`、requiresAuth=true）由 `layoutGuards.spec.ts`
       * 逐条核对一致（L4/L5）。`interview-detail` 是 `interview-list` 的下钻页，
       * 不进模块表的 routeName（详情不需要一级入口）。
       */
      {
        path: 'interviews',
        name: 'interview-list',
        component: InterviewListView,
        meta: { requiresAuth: true },
      },
      {
        path: 'interviews/:id',
        name: 'interview-detail',
        component: InterviewDetailView,
        meta: { requiresAuth: true },
      },
      /*
       * 能力测评（FR-04 / B9，ADR-028）：一屏三段（目录 / 答题 / 结果），
       * 所以只有一条路由，没有下钻页。路径是静态段、不与 `jobs/:id` 同级，
       * 没有排序陷阱；模块表 `config/modules.ts` 里那一项的 `routeName` 必须与这里
       * 逐字一致（`layoutGuards.spec.ts` 用 `router.hasRoute()` 钉住）。
       */
      {
        path: 'assessments',
        name: 'assessment',
        component: AssessmentView,
        meta: { requiresAuth: true },
      },
      /*
       * 教师端学情看板（FR-08 / B10a、B10b，ADR-029）：同样只有一条静态路由。
       * 由工作区切换器进入；是否可用由后端权限与组织闸判，前端不重复判一次。
       */
      {
        path: 'teacher',
        name: 'teacher-dashboard',
        component: TeacherDashboardView,
        meta: { requiresAuth: true },
      },
      /*
       * 工作台首屏的**登录态**（批次 F7 / 守则 §11 那行的收口）。
       *
       * 与访客态用同一个组件、两条路由：状态由路由给（`props.mode`），组件不自己猜。
       * `requiresAuth: true` 必须在路由上、而不是组件里 `v-if` 一下——
       * 那样「未登录能进哪个 URL」就由渲染逻辑决定，守卫与模块表（`requiresAuth`）
       * 都读不到它，`layoutGuards.spec.ts` 的两表一致断言也就失去意义。
       */
      {
        path: 'dashboard',
        name: 'dashboard',
        component: DashboardView,
        props: { mode: 'member' as const },
        meta: { requiresAuth: true },
      },
    ],
  },
  /*
   * 工作台首屏的**访客态**（批次 F7）。老板 2026-11-09 第 5 轮：
   * 「dashboard 也需要有已登录的状态」——反过来说，没登录时它也得有一屏可看的东西，
   * 而不是把每个 URL 都当成「请先登录」。
   *
   * 为什么不挂在 `AppLayout` 下：那个外框顶栏的第一格就是工作区切换器与账户抽屉，
   * 它们的前提都是「我知道你是谁」。给访客硬塞一个空顶栏，等于让他先看一堆点不动的
   * 入口；访客要的是「这是什么、怎么开始」，本组件在 `mode="guest"` 下只画这些。
   */
  {
    path: '/welcome',
    name: 'welcome',
    component: DashboardView,
    props: { mode: 'guest' as const },
  },
  {
    path: '/login',
    name: 'login',
    component: LoginView,
    meta: { guestOnly: true },
  },
  {
    path: '/register',
    name: 'register',
    component: RegisterView,
    meta: { guestOnly: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

/**
 * 路由守卫：所有受保护页面在进入前确认「有 Token 且 Token 仍有效」。
 *
 * 刷新页面后 Pinia 是空的，但 Token 还在 localStorage，
 * 所以这里用 `/auth/me` 做一次真实校验，而不是盲信本地 Token。
 */
router.beforeEach(async (to) => {
  const auth = useAuthStore()

  /*
   * 默认落地（2026-11-14，UI 收尾第 1 条）：根路径按登录态分流。
   *
   * 判据放在守卫最前面、而不是给 `/` 写一个 `redirect` 函数：`redirect` 里读 store 要求
   * Pinia 已经装好，而路由表是模块顶层代码（`main.ts` 里 `use(router)` 才装插件）；
   * 守卫在导航时才执行，那一刻 store 一定可用。
   *
   * 为什么 `/` 不再直接是编辑器：老板要的是「未登录看访客页、已登录进工作台」，
   * 而 `/` 同时当编辑器的话，这条分流就没有落脚点了。编辑器已挪到 `/resume`。
   */
  /*
   * 根路径与未知路径走同一条分流：
   * - `/` 已经不再是任何页面（编辑器挪到 `/resume`），它只表示「按登录态决定去哪」；
   * - `to.matched.length === 0` 是敲错地址的情况，对用户来说与「打开应用首页」没有区别，
   *   不该把他丢到登录页（原来通配路由 `redirect: resume-editor` 的副作用）。
   *
   * 两个分支都跳**具名路由**，不写 `redirect` 链，所以不会出现重定向成环。
   */
  if (to.path === '/' || to.matched.length === 0) {
    return auth.isAuthenticated ? { name: 'dashboard' } : { name: 'welcome' }
  }

  if (to.meta.requiresAuth) {
    if (!auth.isAuthenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    const sessionValid = await auth.ensureUser()
    if (!sessionValid) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
  }

  if (to.meta.guestOnly && auth.isAuthenticated) {
    // 已登录的人再去看登录/注册页：送回默认落地页（与 `/` 的分流同一个去向）。
    return { name: 'dashboard' }
  }

  return true
})

/**
 * 任意接口返回 401 时的统一出口：清掉本地会话并回到登录页。
 * 放在这里而不是 `main.ts`，是为了不改动 T7.4 已验收的启动文件。
 *
 * 「会话过期」提示只在这里 arm，判据是这个回调**跑到第几行**，不是调用方传进来的标记：
 *
 * 1. `current.name === 'login'` 时直接 return——此刻用户**已经站在登录页上**，
 *    这次 401 只能是登录本身没成功（密码打错：`/auth/login` 也在 `expireSession()`
 *    的分支里）。对一个刚把密码敲错的人说「你的登录状态过期了」是撒谎，
 *    所以这里既不跳转也不挂提示。
 * 2. 走到下面这一行，就意味着「他原来在某个受保护页面上，是被服务端请回来的」，
 *    这才是那句提示唯一成立的场合。
 *
 * 为什么主动点「退出登录」不会误挂这条提示（看不出来就是推不出来，故写下）：
 * 登出走 `AppTopBar.handleLogout()` → `resume.reset()` / `job.reset()` →
 * `auth.signOut()`（它内部先 `logout()` 清本地、再尽力撤销服务端 refresh token），
 * 全程**一行都不进这个回调**——`signOut()` 不触发 `unauthorizedHandler`。
 * 两条路径在结构上就是分开的，所以不需要给登出额外传一个「我是自愿的」的标记；
 * 反过来，将来谁把「跳登录页」抽成公共函数并在这里统一 arm，
 * `views/__tests__/LoginView.spec.ts` 的「登出 vs 过期」那组用例立刻会变红。
 */
setUnauthorizedHandler(() => {
  const auth = useAuthStore()
  auth.logout()
  const current = router.currentRoute.value
  if (current.name === 'login') return
  armSessionExpiredNotice()
  void router.replace({ name: 'login', query: { redirect: current.fullPath } })
})

export default router
