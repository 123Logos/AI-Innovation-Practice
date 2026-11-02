<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { isApiError } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useResumeStore } from '@/stores/resume'

const auth = useAuthStore()
const resume = useResumeStore()
const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const errorMessage = ref('')

async function submit(): Promise<void> {
  errorMessage.value = ''
  if (!username.value.trim() || !password.value) {
    errorMessage.value = '请输入用户名和密码。'
    return
  }

  try {
    // 切换账号前先清掉上一个账号残留在内存里的档案。
    resume.reset()
    await auth.login({ username: username.value.trim(), password: password.value })
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect)
  } catch (error) {
    errorMessage.value = isApiError(error) ? error.message : '登录失败，请稍后重试。'
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-card">
      <p class="eyebrow">AI 职业发展助手</p>
      <h1>登录简历工作台</h1>
      <p class="intro">登录后可保存个人档案，并基于档案一页生成 A4 简历。</p>

      <form class="auth-form" @submit.prevent="submit">
        <label class="field">
          <span>用户名</span>
          <input v-model="username" autocomplete="username" placeholder="请输入用户名" />
        </label>
        <label class="field">
          <span>密码</span>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
          />
        </label>

        <p v-if="errorMessage" class="feedback feedback--error">{{ errorMessage }}</p>

        <button class="primary-button" type="submit" :disabled="auth.submitting">
          {{ auth.submitting ? '登录中…' : '登录' }}
        </button>
      </form>

      <p class="switch-hint">
        还没有账号？
        <RouterLink :to="{ name: 'register' }">去注册</RouterLink>
      </p>
    </section>
  </main>
</template>

<style scoped>
.auth-page { display: grid; min-height: 100vh; padding: 32px 20px; background: #f4f6f5; place-items: center; }
.auth-card { width: min(420px, 100%); border: 1px solid #dde5e2; padding: 40px 36px; background: #fff; }
.eyebrow { margin: 0 0 12px; color: #6b8f8a; font-size: 12px; letter-spacing: .12em; }
h1 { margin: 0; font-size: 24px; font-weight: 650; }
.intro { margin: 10px 0 26px; color: #687875; font-size: 13px; line-height: 1.6; }
.auth-form { display: grid; gap: 16px; }
.field { display: grid; gap: 6px; }
.field span { color: #687875; font-size: 12px; }
input { width: 100%; border: 1px solid #d6dfdc; padding: 11px 12px; color: #243330; background: #fff; font: inherit; font-size: 14px; outline: 0; }
input:focus { border-color: #6b8f8a; box-shadow: 0 0 0 2px #6b8f8a22; }
.primary-button { border: 0; padding: 12px; color: #fff; background: #6b8f8a; cursor: pointer; font: inherit; font-size: 14px; }
.primary-button:hover:not(:disabled) { background: #527873; }
.primary-button:disabled { opacity: .6; cursor: progress; }
.feedback { margin: 0; font-size: 13px; line-height: 1.5; }
.feedback--error { color: #9a5e58; }
.switch-hint { margin: 22px 0 0; color: #687875; font-size: 13px; }
.switch-hint a { color: #527873; }
@media (prefers-color-scheme: dark) {
  .auth-page { background: #1f2a28; }
  .auth-card { border-color: #52635f; background: #263330; color: #e8eae9; }
  .intro, .field span, .switch-hint { color: #b7c3c0; }
  input { border-color: #52635f; color: #e8eae9; background: #1f2a28; }
}
</style>
