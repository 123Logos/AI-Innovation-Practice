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
   * `views/DashboardView.vue` 至今不在这棵树里：它是守则 §11 登记的规划期资产
   * （批次 F7 才对齐视觉体系并挂路由），`scripts/check_route_reachability.py` 的豁免名单收着它。
   */
  {
    path: '/',
    component: AppLayout,
    children: [
      {
        path: '',
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
    ],
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
  {
    path: '/:pathMatch(.*)*',
    redirect: { name: 'resume-editor' },
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
    return { name: 'resume-editor' }
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
