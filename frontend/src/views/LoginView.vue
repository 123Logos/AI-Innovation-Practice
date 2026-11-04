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

/**
 * 密码可见性状态。
 *
 * - `false` = 密码处于隐藏状态 → 显示「闭眼」图标，点击后转为可见
 * - `true`  = 密码处于可见状态 → 显示「睁眼」图标，点击后转为隐藏
 *
 * 注意：这里是自己实现的常驻按钮，不是 `el-input` 的 `show-password`，
 * 也不是浏览器自带的 reveal 按钮（后者在输入为空或失焦时会自己消失）。
 */
const passwordVisible = ref(false)

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
          <div class="password-field">
            <input
              v-model="password"
              :type="passwordVisible ? 'text' : 'password'"
              autocomplete="current-password"
              placeholder="请输入密码"
            />
            <button
              class="password-toggle"
              type="button"
              :aria-label="passwordVisible ? '隐藏密码' : '显示密码'"
              :aria-pressed="passwordVisible"
              :title="passwordVisible ? '隐藏密码' : '显示密码'"
              @mousedown.prevent
              @click.stop.prevent="passwordVisible = !passwordVisible"
            >
              <!-- 睁眼：当前密码可见，点击隐藏 -->
              <svg
                v-if="passwordVisible"
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
              <!-- 闭眼：当前密码隐藏，点击显示 -->
              <svg
                v-else
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
                <circle cx="12" cy="12" r="3" />
                <line x1="4" y1="20" x2="20" y2="4" />
              </svg>
            </button>
          </div>
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
.eyebrow { margin: 0 0 12px; color: var(--theme-rule); font-size: 12px; letter-spacing: .12em; }
h1 { margin: 0; font-size: 24px; font-weight: 650; }
.intro { margin: 10px 0 26px; color: #687875; font-size: 13px; line-height: 1.6; }
.auth-form { display: grid; gap: 16px; }
.field { display: grid; gap: 6px; }
.field span { color: #687875; font-size: 12px; }
input { width: 100%; border: 1px solid #d6dfdc; padding: 11px 12px; color: #243330; background: #fff; font: inherit; font-size: 14px; outline: 0; }
input:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }

/* ---- 密码可见性切换（常驻） ---- */
.password-field { position: relative; display: block; }
.password-field input { padding-right: 44px; }
/* 关掉浏览器/Vendor 自带的 reveal 按钮，避免与自建按钮并存或自行消失。 */
.password-field input::-ms-reveal,
.password-field input::-ms-clear { display: none; width: 0; height: 0; }
.password-toggle {
  position: absolute;
  top: 50%;
  right: 4px;
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 0;
  padding: 0;
  color: var(--theme-rule);
  background: transparent;
  cursor: pointer;
}
.password-toggle:hover { color: var(--theme-primary-deep); }
.password-toggle:active { color: #243330; }
.password-toggle:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }
.primary-button { border: 0; padding: 12px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: 14px; }
.primary-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.primary-button:disabled { opacity: .6; cursor: progress; }
.feedback { margin: 0; font-size: 13px; line-height: 1.5; }
.feedback--error { color: #9a5e58; }
.switch-hint { margin: 22px 0 0; color: #687875; font-size: 13px; }
.switch-hint a { color: var(--theme-primary); }
@media (prefers-color-scheme: dark) {
  .auth-page { background: #1f2a28; }
  .auth-card { border-color: #52635f; background: #263330; color: #e8eae9; }
  .intro, .field span, .switch-hint { color: #b7c3c0; }
  input { border-color: #52635f; color: #e8eae9; background: #1f2a28; }
  .password-toggle { color: var(--theme-rule); }
  .password-toggle:hover, .password-toggle:active { color: var(--theme-primary-strong); }
}
</style>
