<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { isApiError } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useResumeStore } from '@/stores/resume'

/** 与后端 `RegisterRequest` 的约束保持一致：用户名 3–100 且不含空白。 */
const USERNAME_MIN = 3
const PASSWORD_MIN = 8
const PASSWORD_MAX_BYTES = 72

const auth = useAuthStore()
const resume = useResumeStore()
const router = useRouter()

const username = ref('')
const email = ref('')
const password = ref('')
const confirmPassword = ref('')
const errorMessage = ref('')

/**
 * 密码可见性：两个输入框各自独立。
 *
 * 与登录页完全一致的做法——自建常驻眼睛按钮，而不是浏览器的 reveal
 * （后者在输入为空 / 失焦时会自己消失，两个框还会互相抢状态）。
 * `false` = 隐藏（显示闭眼图标），`true` = 可见（显示睁眼图标）。
 */
const passwordVisible = ref(false)
const confirmVisible = ref(false)

function localValidate(): string | null {
  const name = username.value.trim()
  if (name.length < USERNAME_MIN) return `用户名至少 ${USERNAME_MIN} 个字符。`
  if (/\s/.test(name)) return '用户名不能包含空格。'
  if (password.value.length < PASSWORD_MIN) return `密码至少 ${PASSWORD_MIN} 位。`
  if (new TextEncoder().encode(password.value).length > PASSWORD_MAX_BYTES) {
    return '密码过长（UTF-8 编码后不得超过 72 字节）。'
  }
  if (password.value !== confirmPassword.value) return '两次输入的密码不一致。'
  return null
}

async function submit(): Promise<void> {
  errorMessage.value = ''
  const invalid = localValidate()
  if (invalid) {
    errorMessage.value = invalid
    return
  }

  try {
    resume.reset()
    await auth.register({
      username: username.value.trim(),
      password: password.value,
      email: email.value.trim() ? email.value.trim() : null,
    })
    // 注册接口只返回用户，不签发 Token；这里直接登录，避免让用户再输一次密码。
    await auth.login({ username: username.value.trim(), password: password.value })
    await router.replace({ name: 'resume-editor' })
  } catch (error) {
    errorMessage.value = isApiError(error) ? error.message : '注册失败，请稍后重试。'
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-card">
      <p class="eyebrow">AI 职业发展助手</p>
      <h1>创建账号</h1>
      <p class="intro">注册后即可填写档案并生成简历，档案保存在数据库中，换设备也能继续编辑。</p>

      <form class="auth-form" @submit.prevent="submit">
        <label class="field">
          <span>用户名</span>
          <input v-model="username" autocomplete="username" placeholder="3 个字符以上，不含空格" />
          <!-- 提前告知归一规则，避免用户注册后看到用户名「变小写」以为出错了。 -->
          <small class="field-hint">提示：用户名不区分大小写，将以小写形式保存。</small>
        </label>
        <label class="field">
          <span>邮箱（选填）</span>
          <input v-model="email" type="email" autocomplete="email" placeholder="用于找回账号" />
        </label>
        <label class="field">
          <span>密码</span>
          <div class="password-field">
            <input
              v-model="password"
              :type="passwordVisible ? 'text' : 'password'"
              autocomplete="new-password"
              placeholder="至少 8 位"
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
              <!-- 睁眼：当前密码可见，点击隐藏（与登录页同一套 SVG） -->
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
        <label class="field">
          <span>确认密码</span>
          <div class="password-field">
            <input
              v-model="confirmPassword"
              :type="confirmVisible ? 'text' : 'password'"
              autocomplete="new-password"
              placeholder="再输入一次"
            />
            <button
              class="password-toggle"
              type="button"
              :aria-label="confirmVisible ? '隐藏密码' : '显示密码'"
              :aria-pressed="confirmVisible"
              :title="confirmVisible ? '隐藏密码' : '显示密码'"
              @mousedown.prevent
              @click.stop.prevent="confirmVisible = !confirmVisible"
            >
              <svg
                v-if="confirmVisible"
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
          {{ auth.submitting ? '注册中…' : '注册并登录' }}
        </button>
      </form>

      <p class="switch-hint">
        已有账号？
        <RouterLink :to="{ name: 'login' }">去登录</RouterLink>
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
/* 提示文案：比字段标签再淡一档，不抢注意力，也不与错误文案的红色混淆。 */
.field-hint { color: #8fa5a1; font-size: 11px; line-height: 1.5; }
input { width: 100%; border: 1px solid #d6dfdc; padding: 11px 12px; color: #243330; background: #fff; font: inherit; font-size: 14px; outline: 0; }
input:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }

/* ---- 密码可见性切换（与 LoginView 完全一致） ---- */
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
  .field-hint { color: #8fa5a1; }
  input { border-color: #52635f; color: #e8eae9; background: #1f2a28; }
  .password-toggle { color: var(--theme-rule); }
  .password-toggle:hover, .password-toggle:active { color: var(--theme-primary-strong); }
}
</style>
