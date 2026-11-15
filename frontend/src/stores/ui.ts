/**
 * 全局 UI 开关（2026-11-15 第 25 轮）：账户区的两个大弹窗。
 *
 * 为什么进 store 而不是留在 `AppTopBar` 的事件里：
 *
 * 1. 「设置」要被**任意页面**唤起——简历润色、岗位录入、人岗匹配、模拟面试都会在
 *    「没配 AI 凭据」时给一句「👉 点此配置 AI 密钥」（老板指令第 5 条）。那些视图
 *    离顶栏隔着一整个外框，逐层往上抛事件等于把 AppLayout 变成事件总线。
 * 2. 抽屉要能问「上面还有弹窗吗」来决定**要不要响应外部点击**（老板指令第 6 条：
 *    大弹窗开着时点遮罩不能把背后的抽屉一起关掉）。
 *
 * 两个弹窗互斥：从抽屉里同时只能进一个，所以打开一个就关另一个。
 * 状态不持久化（刷新即关）——它们是操作面板，不是偏好设置。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

export const useUiStore = defineStore('ui', () => {
  const settingsOpen = ref(false)
  const profileOpen = ref(false)

  /** 「上面还有大弹窗」：抽屉与页面用它决定外部点击的归属。 */
  const modalOpen = computed(() => settingsOpen.value || profileOpen.value)

  function openSettings(): void {
    profileOpen.value = false
    settingsOpen.value = true
  }

  function closeSettings(): void {
    settingsOpen.value = false
  }

  function openProfile(): void {
    settingsOpen.value = false
    profileOpen.value = true
  }

  function closeProfile(): void {
    profileOpen.value = false
  }

  return { settingsOpen, profileOpen, modalOpen, openSettings, closeSettings, openProfile, closeProfile }
})
