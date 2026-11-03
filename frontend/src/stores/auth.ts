/**
 * 登录态 store。
 *
 * 会话的唯一真相是 `localStorage`（由 `api/client.ts` 读写）：
 * access token、refresh token、access 的过期时刻三件套。
 * 这里保存的是它的内存镜像 —— 刷新页面后路由守卫读 `isAuthenticated`
 * 仍然为 true，随后用 `/auth/me` 校验并补全用户信息。
 *
 * 续期不在这里发起：`client.ts` 的拦截器在 401（或临近过期）时完成，
 * 并通过 `setSessionChangeHandler` 把新 Token 推回来，避免两处各写一套。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { authApi } from '@/api'
import {
  clearStoredToken,
  getStoredRefreshToken,
  getStoredToken,
  setSessionChangeHandler,
  storeSession,
  type SessionTokens,
} from '@/api/client'
import type { LoginRequest, RegisterRequest, UserRead } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getStoredToken())
  const user = ref<UserRead | null>(null)
  const submitting = ref(false)
  const signingOut = ref(false)

  const isAuthenticated = computed(() => Boolean(token.value))
  const displayName = computed(() => user.value?.username ?? '')

  /**
   * 拦截器续期成功后把新令牌同步进内存镜像；刷新失败时（`null`）一并清空。
   *
   * 只同步 token，不动 `user` —— 续期不改变「我是谁」，
   * 有效的 `user` 应该保留到真正的登出。
   */
  setSessionChangeHandler((tokens) => {
    token.value = tokens ? tokens.access_token : null
    if (!tokens) user.value = null
  })

  /** 落盘 + 更新内存镜像；登录与主动刷新共用。 */
  function applySession(tokens: SessionTokens): void {
    storeSession(tokens)
    token.value = tokens.access_token
  }

  /** 只清本地会话；不碰网络，供 401 / 刷新失败等「已经来不及等接口」的路径使用。 */
  function logout(): void {
    token.value = null
    user.value = null
    clearStoredToken()
  }

  /**
   * 用户主动登出：撤销服务端的 refresh token，再清本地。
   *
   * 顺序是「先取票、立刻清本地、再尽力撤销」：
   * 本地清理不能等网络，否则接口一慢用户就卡在已登录的页面上；
   * 撤销失败也不阻塞登出（令牌 7 天后自然过期）。
   */
  async function signOut(): Promise<void> {
    if (signingOut.value) return
    const refreshToken = getStoredRefreshToken()
    signingOut.value = true
    logout()
    try {
      if (refreshToken) await authApi.logout(refreshToken)
    } catch {
      // 服务端撤销失败不影响本地登出。
    } finally {
      signingOut.value = false
    }
  }

  /** 登录：拿双 Token → 落 localStorage → 拉取用户信息。 */
  async function login(payload: LoginRequest): Promise<UserRead> {
    submitting.value = true
    try {
      const result = await authApi.login(payload)
      applySession(result)
      return await loadCurrentUser()
    } finally {
      submitting.value = false
    }
  }

  /** 注册：仅创建账号，成功后由调用方决定是否立即登录。 */
  async function register(payload: RegisterRequest): Promise<UserRead> {
    submitting.value = true
    try {
      return await authApi.register(payload)
    } finally {
      submitting.value = false
    }
  }

  /** 用当前 Token 换取用户信息；失败即视为会话失效并清理本地状态。 */
  async function loadCurrentUser(): Promise<UserRead> {
    try {
      const current = await authApi.fetchCurrentUser()
      user.value = current
      return current
    } catch (error) {
      logout()
      throw error
    }
  }

  /** 路由守卫用：有 Token 但没有用户信息时补齐（刷新页面场景）。 */
  async function ensureUser(): Promise<boolean> {
    if (!token.value) return false
    if (user.value) return true
    try {
      await loadCurrentUser()
      return true
    } catch {
      return false
    }
  }

  return {
    token,
    user,
    submitting,
    signingOut,
    isAuthenticated,
    displayName,
    login,
    register,
    loadCurrentUser,
    ensureUser,
    logout,
    signOut,
  }
})
