<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { isApiError } from '@/api'
import { consumeSessionExpiredNotice } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useResumeStore } from '@/stores/resume'

/**
 * 会话过期被送回登录页时的提示文案。
 *
 * 三条口径由监督者定下，改动前先对齐：
 * - 必须带上下一步动作（「请重新登录」），只说「过期了」等于把问题丢回给用户；
 * - 不得出现「令牌 / token / session」这类内部词——用户不认识它们，
 *   认识了也修不了任何东西；
 * - 必须说清重新登录之后会怎样（回到刚才那一页），否则用户会以为刚才白干了。
 * 这里的 `redirect` 查询参数正是 `router/index.ts` 被踢时带上的那一个，
 * `submit()` 登录成功后会用它跳回原页面，所以这句话是可兑现的承诺。
 *
 * 为什么不在 `client.ts` 复用 `SESSION_EXPIRED_MESSAGE`：那句是**抛给调用方的错误消息**
 * （`ApiError.message`，出现在任何接口的失败提示里），这一句是**登录页的一处界面文案**，
 * 要额外说明「登录后回到刚才的页面」。把两者合并成一句，等于让所有接口的错误文案
 * 都长出一段与它们无关的话。
 */
const SESSION_EXPIRED_NOTICE = '你的登录状态已过期。请重新登录，登录后会回到你刚才正在看的页面。'

const auth = useAuthStore()
const resume = useResumeStore()
const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const errorMessage = ref('')

/**
 * 一次性读取「刚刚是不是被会话失效踢回来的」。
 *
 * 读放在 setup 里而不是 `onMounted`：setup 每个实例只跑一次，
 * 天然等于「这一屏最多显示一次」；`consume…()` 取完即清，所以刷新页面、
 * 从别的页面再回到登录页都不会重复显示（提示本身也不在任何存储里）。
 */
const sessionExpiredNotice = ref(consumeSessionExpiredNotice())

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

      <!--
        会话过期提示：放在表单**之前**而不是和 `errorMessage` 挤在一起——
        它解释的是「你为什么在这儿」，是用户读页面时第一眼要拿到的上下文；
        表单校验失败那句话是对用户**这一次操作**的反馈，两者混在一个位置会让人
        以为「过期」是自己密码敲错造成的。
      -->
      <p v-if="sessionExpiredNotice" class="expired-notice" role="status">
        {{ SESSION_EXPIRED_NOTICE }}
      </p>

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
/*
 * 居中用 flex + `margin:auto`，不用 `grid + place-items:center`。
 * 注册页的卡片比登录页高，在 375×667 这类矮视口上会超过一屏；
 * `place-items:center` 此时让卡片**向上溢出且滚不到**（标题被切掉），
 * 而 `margin:auto` 在放不下时自动退化成顶部对齐，滚动照常。
 */
.auth-page { display: flex; min-height: 100vh; padding: 32px 20px; background: var(--ui-canvas); }
.auth-card { margin: auto; width: min(420px, 100%); border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 40px 36px; background: var(--ui-surface); }
.eyebrow { margin: 0 0 12px; color: var(--theme-rule); font-size: var(--text-sm); letter-spacing: .12em; }
h1 { margin: 0; font-size: var(--text-5xl); font-weight: 650; }
.intro { margin: 10px 0 26px; color: var(--ui-ink-muted); font-size: var(--text-base); line-height: 1.6; }
.auth-form { display: grid; gap: 16px; }
.field { display: grid; gap: 6px; }
.field span { color: var(--ui-ink-muted); font-size: var(--text-sm); }
input { width: 100%; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 11px 12px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: var(--text-md); outline: 0; }
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
.password-toggle:active { color: var(--ui-ink); }
.password-toggle:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }
.primary-button { border: 0; border-radius: var(--ui-radius); padding: 12px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: var(--text-md); line-height: 1.4; }
.primary-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.primary-button:disabled { opacity: .6; cursor: progress; }
.feedback { margin: 0; font-size: var(--text-base); line-height: 1.5; }
.feedback--error { color: var(--ui-danger); }
/*
 * 会话过期提示走 warning 家族而不是 danger 红：这条说的是「状态没了、需要你做一件事」，
 * 不是「你做错了」。红色在这里会把「重新登录」读成「登录失败」，
 * 而用户本来就怀疑是自己账号坏了。规格与 `.draft-banner`（编辑器里的草稿恢复条）同档：
 * 1px 描边 + 4px 左强调条 + `--ui-radius`（= 2px）+ 13px 正文，全部取 `--ui-*` 令牌。
 */
.expired-notice { margin: 0 0 20px; border: 1px solid var(--ui-warning-border); border-left: 4px solid var(--ui-warning-border); border-radius: var(--ui-radius); padding: 12px 16px; color: var(--ui-warning); background: var(--ui-warning-soft); font-size: var(--text-base); line-height: 1.5; }
.switch-hint { margin: 22px 0 0; color: var(--ui-ink-muted); font-size: var(--text-base); }
.switch-hint a { color: var(--theme-primary); }
</style>
