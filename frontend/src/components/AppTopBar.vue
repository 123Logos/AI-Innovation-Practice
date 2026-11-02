<script setup lang="ts">
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useResumeStore } from '@/stores/resume'

const auth = useAuthStore()
const resume = useResumeStore()
const router = useRouter()

async function handleLogout(): Promise<void> {
  // 先清业务数据再清登录态，避免下一个账号短暂看到上一个人的档案。
  resume.reset()
  auth.logout()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <header class="app-topbar">
    <div class="brand">
      <span class="brand-mark">AI</span>
      <div class="brand-text">
        <p class="brand-title">职业发展助手</p>
        <p class="brand-sub">简历工作台</p>
      </div>
    </div>

    <div class="user-area">
      <span v-if="resume.dirty" class="dirty-dot" title="有未保存的修改">未保存</span>
      <span class="user-name">{{ auth.displayName || '已登录用户' }}</span>
      <button class="logout-button" type="button" @click="handleLogout">退出登录</button>
    </div>
  </header>
</template>

<style scoped>
.app-topbar { position: sticky; top: 0; z-index: 10; display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: 1px solid #dbe4e1; padding: 14px 48px; background: #ffffffeb; backdrop-filter: blur(8px); }
.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark { display: grid; width: 34px; height: 34px; color: #fff; background: #6b8f8a; font-family: "SF Mono", Consolas, monospace; font-size: 13px; place-items: center; }
.brand-text { display: grid; gap: 2px; }
.brand-title { margin: 0; color: #243330; font-size: 14px; font-weight: 650; }
.brand-sub { margin: 0; color: #879692; font-size: 11px; letter-spacing: .08em; }
.user-area { display: flex; align-items: center; gap: 12px; }
.user-name { color: #52635f; font-size: 13px; }
.dirty-dot { border: 1px solid #c9a227; padding: 2px 7px; color: #94781a; font-size: 11px; }
.logout-button { border: 1px solid #d6dfdc; padding: 7px 14px; color: #52635f; background: transparent; cursor: pointer; font: inherit; font-size: 12px; }
.logout-button:hover { border-color: #6b8f8a; color: #527873; }
@media (max-width: 760px) {
  .app-topbar { padding: 12px 16px; }
  .brand-sub, .user-name { display: none; }
}
@media (prefers-color-scheme: dark) {
  .app-topbar { border-color: #52635f; background: #1f2a28eb; }
  .brand-title { color: #e8eae9; }
  .brand-sub { color: #8ea09c; }
  .user-name { color: #b7c3c0; }
  .logout-button { border-color: #52635f; color: #b7c3c0; }
}
@media print {
  .app-topbar { display: none; }
}
</style>
