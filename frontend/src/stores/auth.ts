/**
 * 登录态 store。
 *
 * Token 的唯一真相是 `localStorage`（由 `api/client.ts` 读写），
 * 这里保存的是它的内存镜像。这样刷新页面后：
 * 路由守卫读 `isAuthenticated` 仍然为 true，随后用 `/auth/me` 校验并补全用户信息。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { authApi } from '@/api'
import { clearStoredToken, getStoredToken, setStoredToken } from '@/api/client'
import type { LoginRequest, RegisterRequest, UserRead } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getStoredToken())
  const user = ref<UserRead | null>(null)
  const submitting = ref(false)

  const isAuthenticated = computed(() => Boolean(token.value))
  const displayName = computed(() => user.value?.username ?? '')

  function applyToken(value: string): void {
    token.value = value
    setStoredToken(value)
  }

  function logout(): void {
    token.value = null
    user.value = null
    clearStoredToken()
  }

  /** 登录：拿 Token → 落 localStorage → 拉取用户信息。 */
  async function login(payload: LoginRequest): Promise<UserRead> {
    submitting.value = true
    try {
      const result = await authApi.login(payload)
      applyToken(result.access_token)
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
    isAuthenticated,
    displayName,
    login,
    register,
    loadCurrentUser,
    ensureUser,
    logout,
  }
})
