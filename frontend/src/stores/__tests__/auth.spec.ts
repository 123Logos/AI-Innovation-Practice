/**
 * `auth` store 的登出契约（此前 `signOut()` 在全仓库零调用、也零用例）。
 *
 * 这里只问三件事，因为它们都是**用户能感觉到**的后果：
 *
 * 1. 服务端撤销失败时，用户仍然要成功登出。反过来做（撤销失败就卡在登录态）
 *    会让「退出登录」按钮在最需要它的时候（网络不稳、服务抖动）失灵。
 * 2. 连点两次不得发出两次撤销。后端的 `/auth/logout` 是幂等的，多打一次不会坏，
 *    但按钮会在半秒里发两遍——那说明 `signingOut` 守卫没起作用，
 *    而同一个守卫还负责「在途时不许重复提交」。
 * 3. `logout()` 与 `signOut()` 必须是两条路。前者是 401/刷新失败这类
 *    「已经来不及等接口」的路径用的，它**不该**碰网络；如果哪天有人把它改成
 *    顺手发个请求，会话过期时就会多一次注定失败的外呼。
 *
 * 做法：只 stub 最外层的 `authApi.logout`，让真实的 store 逻辑跑完。
 * mock 掉 `signOut()` 本身会连 `signingOut` 一起测没了（见 AppTopBar.spec.ts 同一条注释）。
 */

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { authApi } from '@/api'
import {
  REFRESH_TOKEN_STORAGE_KEY,
  TOKEN_EXPIRES_AT_KEY,
  TOKEN_STORAGE_KEY,
  storeSession,
} from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const revoke = vi.fn()

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  revoke.mockReset()
  revoke.mockResolvedValue(undefined)
  vi.spyOn(authApi, 'logout').mockImplementation(revoke)
})

/**
 * 用真实的 `storeSession()` 落一份会话，而不是手搓 localStorage：
 * 三件套的键名与过期时刻的算法都属于 `client.ts`，测试自己拼字符串的话，
 * 它验证的就是「我以为的存储格式」而不是产品实际用的那份。
 */
function signedIn(): ReturnType<typeof useAuthStore> {
  storeSession({ access_token: 'access-under-test', refresh_token: 'refresh-under-test', expires_in: 900 })
  const auth = useAuthStore()
  auth.token = 'access-under-test'
  return auth
}

/**
 * 一个手动落地的 Promise。
 *
 * 不用 `let release: fn | null` + `release?.()`：赋值发生在闭包里，编译器会把它
 * 收窄成 `null`（TS2349），而 `?.` 在真的没赋值时是**静默跳过**——用例照样绿，
 * 但它谁也没等到。这里 `settle` 的初始值是空函数，忘了调用只会让这条用例超时，是响的。
 */
function deferred(): { promise: Promise<void>; settle: () => void } {
  let settle = (): void => undefined
  const promise = new Promise<void>((resolve) => {
    settle = () => resolve()
  })
  return { promise, settle }
}

describe('主动登出', () => {
  it('撤销请求带的是本地那份 refresh token，登出后三件套全部清掉', async () => {
    const auth = signedIn()

    await auth.signOut()

    expect(revoke).toHaveBeenCalledTimes(1)
    expect(revoke).toHaveBeenCalledWith('refresh-under-test')
    expect(auth.token).toBeNull()
    // 三件套都得清掉：只清 access 会留下一枚还能续期的 refresh token，
    // 用户以为退了，下一次打开又被「救回去」。
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
    expect(localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY)).toBeNull()
    expect(localStorage.getItem(TOKEN_EXPIRES_AT_KEY)).toBeNull()
  })

  it('服务端撤销失败时，本地登出照样完成（不卡在已登录状态）', async () => {
    const auth = signedIn()
    revoke.mockRejectedValue(new Error('上游抖动'))

    // 不抛：调用方（顶栏）没有 try/catch，一旦这里抛出，用户点「退出登录」会没反应。
    await expect(auth.signOut()).resolves.toBeUndefined()

    expect(revoke).toHaveBeenCalledTimes(1)
    expect(auth.token).toBeNull()
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
    expect(auth.signingOut).toBe(false)
  })

  /**
   * 守卫管住的不是「撤销只发一次」——那一半其实由 `logout()` 清掉了本地 refresh token
   * 顺带保证（第二次进来已经没有 token 可发），去掉守卫这条照样绿。
   * 守卫真正管住的是 `signingOut` 的**生命周期**：没有它，第二次调用会立刻走完自己的
   * `finally` 把标志置回 false，于是撤销还在途、按钮就重新可点，用户可以再点一次。
   */
  it('撤销还在途时，第二次调用不得提前结束「登出中」状态', async () => {
    const auth = signedIn()
    const { promise, settle } = deferred()
    revoke.mockImplementation(() => promise)

    const first = auth.signOut()
    expect(auth.signingOut).toBe(true)
    const second = auth.signOut()
    await Promise.resolve()

    // 这一句就是守卫本身：去掉 `if (signingOut.value) return`，第二次调用的
    // finally 会先把标志抹平，按钮在请求还没回来时就恢复可点。
    expect(auth.signingOut).toBe(true)

    settle()
    await Promise.all([first, second])
    expect(auth.signingOut).toBe(false)
    expect(revoke).toHaveBeenCalledTimes(1)
  })
})

describe('两条登出路径的分工', () => {
  it('logout() 只清本地，一次网络请求都不发', async () => {
    const auth = signedIn()

    auth.logout()

    expect(revoke).toHaveBeenCalledTimes(0)
    expect(auth.token).toBeNull()
  })

  it('没有 refresh token 时，signOut() 不发撤销也不报错', async () => {
    const auth = useAuthStore()
    auth.token = 'access-under-test'

    await auth.signOut()

    expect(revoke).toHaveBeenCalledTimes(0)
    expect(auth.token).toBeNull()
  })
})
