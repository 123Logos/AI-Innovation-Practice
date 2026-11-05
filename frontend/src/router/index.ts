import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { setUnauthorizedHandler } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import JobBoardView from '@/views/JobBoardView.vue'
import JobCreateView from '@/views/JobCreateView.vue'
import JobDetailView from '@/views/JobDetailView.vue'
import JobTrashView from '@/views/JobTrashView.vue'
import LoginView from '@/views/LoginView.vue'
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
  {
    path: '/',
    name: 'resume-editor',
    component: ResumeEditorView,
    meta: { requiresAuth: true },
  },
  {
    path: '/trash',
    name: 'recycle-bin',
    component: RecycleBinView,
    meta: { requiresAuth: true },
  },
  /*
   * 岗位库四条路由（ADR-011 议题 3.B）。
   *
   * 声明顺序是硬要求：`/jobs/new`、`/jobs/trash` 这类静态段必须排在 `/jobs/:id`
   * **之前**。Vue Router 本身按段的静态/动态权重打分排序，写错顺序未必立刻出问题，
   * 但 `/jobs/trash` 一旦落到 `/:id` 后面，就会被当成「id = trash 的详情页」——
   * 后端 `app/api/v1/jobs.py:3-5` 记录过同一个坑，这里按同一条规则排布，
   * 并在 `stores/__tests__/job.spec.ts` 里留一条 `resolve()` 断言钉住它。
   */
  {
    path: '/jobs',
    name: 'job-board',
    component: JobBoardView,
    meta: { requiresAuth: true },
  },
  {
    path: '/jobs/new',
    name: 'job-create',
    component: JobCreateView,
    meta: { requiresAuth: true },
  },
  {
    path: '/jobs/trash',
    name: 'job-trash',
    component: JobTrashView,
    meta: { requiresAuth: true },
  },
  {
    path: '/jobs/:id',
    name: 'job-detail',
    component: JobDetailView,
    meta: { requiresAuth: true },
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
 */
setUnauthorizedHandler(() => {
  const auth = useAuthStore()
  auth.logout()
  const current = router.currentRoute.value
  if (current.name === 'login') return
  void router.replace({ name: 'login', query: { redirect: current.fullPath } })
})

export default router
