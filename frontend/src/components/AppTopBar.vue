<script setup lang="ts">
/**
 * 顶栏：品牌 + 简历切换器 + 用户区。
 *
 * D4.3 依据人工验收重做了下拉列表，四条都围绕「用户找得到、看得懂」：
 * - 每行常驻「重命名 / 删除」两个图标（原来只在 hover 时出现，等于没有）；
 * - 顶部先给排序切换（更新时间 / 创建时间 + 升降序），顺序不再由后端随机决定；
 * - 「设为默认」从行内搬到**当前简历不是默认**时的一条横幅，默认行只留 [默认] 标签；
 * - 改名字走行内输入框，重名由后端 400 拦下并原样提示。
 *
 * 组件仍然只负责交互与文案：切简历、改名、删除全部委托给 store，
 * 未保存拦截与并发保护都在 `stores/resume.ts` 里（见 `activateProfile`）。
 *
 * B3 依据 ADR-011 议题 3.D 重排了窄屏的入口组织：`≤760px` 时「AI 设置 / 退出登录」
 * 收进一个「更多」菜单（一级项 5 → 3），「岗位库」保持文字入口不收。
 * 批次 F3 第二片把它换成**右侧用户抽屉**（老板前端清单第 2 条），宽窄屏同一个入口，
 * 于是那两层 `display: contents` 的分叉一并删掉；「岗位库」仍不收进来（理由没变）。
 * 登出时除 `resume.reset()` 外还要 `job.reset()`——先清业务数据再清登录态这条不变量
 * 涵盖岗位数据（`stores/job.ts` 里每个 `load*()` 前的清空只是缓解，不替代它）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { clearDeepSeekKey, hasDeepSeekKey, isApiError, setDeepSeekKey } from '@/api'
import type { ProfileSortField, ProfileSummary } from '@/api/types'
import OverlayPanel from '@/components/OverlayPanel.vue'
import UserDrawer from '@/components/UserDrawer.vue'
import { TOP_BAR_MODULES } from '@/config/modules'
import { useAuthStore } from '@/stores/auth'
import { useDialogStore } from '@/stores/dialog'
import { useJobStore } from '@/stores/job'
import { useResumeStore } from '@/stores/resume'

const auth = useAuthStore()
const dialog = useDialogStore()
const resume = useResumeStore()
const job = useJobStore()
const router = useRouter()

/* -------------------------------- 下拉菜单 -------------------------------- */

const menuOpen = ref(false)
/** 正在切换的目标 id：用于给被点的那一行单独显示「切换中…」。 */
const switchingId = ref<string | null>(null)
const menuError = ref('')

const currentTitle = computed(() => resume.currentProfileTitle)
const currentIsDefault = computed(() => resume.currentProfile?.is_default ?? false)
/** 当前简历不是默认时才提示「设为默认」；默认行上再放这个按钮没有意义。 */
const needsDefault = computed(
  () => Boolean(resume.currentProfileId) && !currentIsDefault.value,
)

function closeMenu(): void {
  menuOpen.value = false
  menuError.value = ''
  cancelRename()
}

function toggleMenu(): void {
  if (menuOpen.value) {
    closeMenu()
    return
  }
  // 与右侧用户抽屉互斥：两个浮层都贴在顶栏下沿（见 `toggleDrawer`）。
  closeDrawer()
  menuError.value = ''
  menuOpen.value = true
}

function describeError(error: unknown, fallback: string): string {
  if (isApiError(error)) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}

/* --------------------------------- 排序 ---------------------------------- */

const SORT_LABELS: Record<ProfileSortField, string> = {
  updated_at: '更新时间',
  created_at: '创建时间',
}
/** 渲染顺序即产品顺序：先「最近改过」再「最近创建」。 */
const SORT_FIELDS: ProfileSortField[] = ['updated_at', 'created_at']

const sortLabel = computed(() => SORT_LABELS[resume.sortBy])
const sortDirectionLabel = computed(() => (resume.sortDesc ? '最新在前' : '最早在前'))

async function handleSortField(field: ProfileSortField): Promise<void> {
  if (field === resume.sortBy) return
  menuError.value = ''
  try {
    await resume.setSortField(field)
  } catch (error) {
    menuError.value = describeError(error, '切换排序失败，请稍后重试。')
  }
}

async function handleSortOrder(): Promise<void> {
  menuError.value = ''
  try {
    await resume.toggleSortOrder()
  } catch (error) {
    menuError.value = describeError(error, '切换排序失败，请稍后重试。')
  }
}

/* -------------------------------- 切换 / 默认 ------------------------------ */

async function handleSwitch(profileId: string): Promise<void> {
  if (profileId === resume.currentProfileId) {
    closeMenu()
    return
  }
  menuError.value = ''
  switchingId.value = profileId
  try {
    // 返回 false = 用户在「未保存」确认框里点了取消，保持菜单打开即可。
    const switched = await resume.switchProfile(profileId)
    if (switched) closeMenu()
  } catch (error) {
    menuError.value = describeError(error, '切换简历失败，请稍后重试。')
  } finally {
    switchingId.value = null
  }
}

async function handleSetDefault(): Promise<void> {
  const profileId = resume.currentProfileId
  if (!profileId) return
  menuError.value = ''
  try {
    await resume.setDefaultProfile(profileId)
  } catch (error) {
    menuError.value = describeError(error, '设为默认失败，请稍后重试。')
  }
}

/* --------------------------------- 删除 ---------------------------------- */

async function handleDelete(item: ProfileSummary): Promise<void> {
  const confirmed = await dialog.askConfirm(
    '删除这份简历？',
    '删除后它进回收站，30 天内可以恢复，技能条目一行都不会丢。',
    { subject: item.title, confirmLabel: '确认删除', tone: 'danger' },
  )
  if (!confirmed) return
  menuError.value = ''
  cancelRename()
  try {
    await resume.deleteProfile(item.id)
  } catch (error) {
    menuError.value = describeError(error, '删除失败，请稍后重试。')
  }
}

/* --------------------------------- 改名 ---------------------------------- */

const editingId = ref<string | null>(null)
const editingTitle = ref('')
const renaming = ref(false)

function startRename(item: ProfileSummary): void {
  menuError.value = ''
  editingId.value = item.id
  editingTitle.value = item.title
}

function cancelRename(): void {
  editingId.value = null
  editingTitle.value = ''
}

async function submitRename(item: ProfileSummary): Promise<void> {
  if (renaming.value) return
  const title = editingTitle.value.trim()
  if (!title) {
    menuError.value = '简历名称不能为空。'
    return
  }
  if (title === item.title) {
    cancelRename()
    return
  }
  renaming.value = true
  menuError.value = ''
  try {
    await resume.renameProfile(item.id, title)
    cancelRename()
  } catch (error) {
    // 重名等业务错误原样展示（后端文案就是「该简历名称已存在」）。
    menuError.value = describeError(error, '重命名失败，请稍后重试。')
  } finally {
    renaming.value = false
  }
}

/* -------------------------------- 新建弹窗 -------------------------------- */

type CreateMode = 'blank' | 'copy'

const createVisible = ref(false)
const createTitle = ref('')
const createMode = ref<CreateMode>('blank')
const creating = ref(false)
const createError = ref('')

/**
 * 标题的「自动递增」默认值：我的简历 → 我的简历 2 → 我的简历 3。
 *
 * 与后端 `_get_next_default_title()` 同规则，这里先算好是为了**让用户看得见**；
 * 用户把标题清空时仍交给后端命名。另外后端会拦重名，所以即便前端算错也不会写脏。
 */
function nextDefaultTitle(): string {
  const titles = new Set(resume.profiles.map((profile) => profile.title))
  if (!titles.has('我的简历')) return '我的简历'
  let index = 2
  while (titles.has(`我的简历 ${index}`)) index += 1
  return `我的简历 ${index}`
}

function openCreate(): void {
  closeMenu()
  createVisible.value = true
  createTitle.value = nextDefaultTitle()
  // 有当前简历时默认「复制当前」——大多数新简历是现有简历的一个变体。
  createMode.value = resume.currentProfileId ? 'copy' : 'blank'
  createError.value = ''
}

function closeCreate(): void {
  if (creating.value) return
  createVisible.value = false
  createError.value = ''
}

async function submitCreate(): Promise<void> {
  if (creating.value) return
  createError.value = ''
  creating.value = true
  try {
    await resume.createProfile({
      title: createTitle.value,
      copyCurrent: createMode.value === 'copy',
    })
    createVisible.value = false
  } catch (error) {
    createError.value = describeError(error, '新建简历失败，请稍后重试。')
  } finally {
    creating.value = false
  }
}

/* --------------------------------- 回收站 --------------------------------- */

async function goTrash(): Promise<void> {
  closeMenu()
  // 离开编辑器前先处理未保存的修改：否则回收站走一圈回来，
  // 编辑器会用服务端数据覆盖内存里的 draft，用户以为丢了。
  if (resume.dirty) {
    const shouldSave = await dialog.askConfirm(
      '去回收站前先保存修改？',
      '不保存就离开编辑器，回来时看到的是上一次保存的那一版，你刚才改的会丢。',
      { confirmLabel: '保存并前往', cancelLabel: '先不去' },
    )
    if (!shouldSave) return
    try {
      await resume.save()
    } catch (error) {
      // 原来这里是 window.alert：它会阻塞主线程，自动化验收停在那一步走不下去。
      // 失败仍然要**当面**告诉用户（守则 C3），只是换成同一个弹窗组件。
      await dialog.askNotice('没保存成功，所以停在这一页', describeError(error, '保存失败，请稍后重试。'))
      return
    }
  }
  await router.push({ name: 'recycle-bin' })
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  // 只留「新建简历」这一条：它是顶栏自己管的内联表单，不是浮层。
  // 切换器菜单与用户抽屉的 Esc 都由 `OverlayPanel` 基座负责（守则 L3 说的「一个基础件」）。
  if (createVisible.value) closeCreate()
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
})

// 切换成功 / 被删除后列表变了，菜单里的高亮要能立刻反映，避免留着过期的错误提示。
watch(
  () => resume.currentProfileId,
  () => {
    menuError.value = ''
  },
)

/* ------------------------------ AI 设置（ADR-009 BYOK） ----------------- */

const aiSettingsVisible = ref(false)
/**
 * 输入框草稿：打开弹窗时**不回填**已存的 Key。
 * Key 只在 sessionStorage 里活着，没必要再把它显示到屏幕上（肩窥 / 屏幕录制 / 截图）。
 */
const aiKeyDraft = ref('')
const aiKeySaved = ref(hasDeepSeekKey())
const aiSettingsError = ref('')

function openAiSettings(): void {
  aiKeyDraft.value = ''
  aiSettingsError.value = ''
  aiKeySaved.value = hasDeepSeekKey()
  aiSettingsVisible.value = true
}

function closeAiSettings(): void {
  aiSettingsVisible.value = false
}

function submitAiKey(): void {
  const value = aiKeyDraft.value.trim()
  if (!value) {
    // ui-copy:allow C2 唯一豁免：这句话指向用户自己去 DeepSeek 复制的那串凭据，改名他会找不到
    aiSettingsError.value = '这里还空着——把你的 DeepSeek API Key 粘进来才行。'
    return
  }
  setDeepSeekKey(value)
  aiKeySaved.value = true
  // 保存后立刻清空输入框：屏幕上看不到完整 Key，也避免下次打开时留着一份明文。
  aiKeyDraft.value = ''
  aiSettingsError.value = ''
}

function forgetAiKey(): void {
  clearDeepSeekKey()
  aiKeySaved.value = false
  aiKeyDraft.value = ''
  aiSettingsError.value = ''
}

/* ---------------------------- 右侧用户抽屉 ------------------------------- */

/**
 * 账户级动作（AI 设置 / 退出登录）的入口：顶栏放一个「账户」触发按钮，点开右侧抽屉
 * （`UserDrawer.vue`）。老板前端清单第 2 条，批次 F3 第二片。
 *
 * **宽屏也收进抽屉**——这一点改了 ADR-011 议题 3.D 的原排版。3.D 只管「窄屏一级项从 5 个降到 3 个」，
 * 当时两个动作在宽屏仍是一级项；清单第 2 条要的是「账户的事在右边」，那是归属问题不是宽度问题，
 * 所以宽屏不再有这两个按钮。换来的第二个好处：原来靠 `.user-more` / `.user-actions`
 * 两层 `display: contents` 拼出来的「窄屏是弹层、宽屏是一级项」分叉整块消失，
 * 窄屏不再需要为一个弹层单独写挂载点（那正是守则 L2 说的行为分支散在 CSS 里）。
 *
 * 「岗位库」**不进来**：3.D 那句理由仍然成立——它是主力导航，藏进抽屉等于没有入口。
 *
 * 抽屉里的三件底层事（点外面 / Esc / 焦点归还）由 `OverlayPanel` 基座承担，
 * 所以这里没有 pointerdown 监听，也不再有第二个 document keydown 分支。
 */
const drawerOpen = ref(false)

function closeDrawer(): void {
  drawerOpen.value = false
}

function toggleDrawer(): void {
  if (drawerOpen.value) {
    closeDrawer()
    return
  }
  // 两个浮层都贴在顶栏下沿同一位置（简历切换器向左下、抽屉向右），同时开着会打架：开一个就关另一个。
  closeMenu()
  drawerOpen.value = true
}

async function handleLogout(): Promise<void> {
  // 先清业务数据再清登录态，避免下一个账号短暂看到上一个人的档案与岗位数据。
  resume.reset()
  job.reset()
  // `signOut()` 而不是 `logout()`：后者只清本地，前者会去撤销服务端的 refresh token。
  // 点「退出登录」的人表达的是「我的会话到此为止」，只清本地等于把这句话留在客户端——
  // refresh token 在服务端还能用满 7 天。机房/共用电脑上登出的人尤其依赖这个语义。
  await auth.signOut()
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
      <!--
        一级导航入口（ADR-011 议题 3.A + 守则 L4）。

        模块名与路由一律来自模块表 `@/config/modules`，这里不写死任何模块名：
        「一张表」的判据是**新增加一个模块时只改那张表**，而不是「这张表旁边另有一份也能用」。

        其余取舍不变（议题 3.A / 3.D 裁定）：顶栏一条文字入口、不加侧边栏；
        「岗位库」不进账户抽屉、也不降级成图标——手机是学生的主力设备，
        这条功能的入口藏起来等于没有。窄屏省下的宽度预算从「AI 设置 / 退出登录」出。
        岗位自己的回收站不在这里（顶栏的「回收站」属于简历，两个混在一层会让人
        分不清删掉的是什么），入口在 `/jobs` 列表页顶部的「我的删除」。

        `class="jobs-entry"` 这个类名沿用不改：`AppTopBar.spec.ts` 的一级项顺序断言按它取值，
        而那条断言在批次 F3 第二片已按裁定改写（一级项 4 → 3：岗位库 / 切换器 / 账户）。
        模块表的 `iconPaths` 本批也不渲染——加图标是视觉改动，同样归批次 F3。
      -->
      <RouterLink
        v-for="item in TOP_BAR_MODULES"
        :key="item.key"
        class="jobs-entry"
        :to="{ name: item.routeName }"
      >
        {{ item.name }}
      </RouterLink>

      <!-- 简历切换器 -->
      <div class="profile-switcher">
        <button
          class="switcher-trigger"
          type="button"
          :aria-expanded="menuOpen"
          aria-haspopup="menu"
          :title="`当前简历：${currentTitle}`"
          @pointerdown.stop
          @click="toggleMenu"
        >
          <span class="switcher-label">当前简历</span>
          <span class="switcher-title">{{ currentTitle }}</span>
          <span v-if="currentIsDefault" class="switcher-default">默认</span>
          <span class="switcher-caret" :class="{ 'switcher-caret--open': menuOpen }" aria-hidden="true">
            ▾
          </span>
        </button>

        <OverlayPanel
          v-if="menuOpen"
          panel-class="switcher-menu"
          label="简历列表"
          @close="closeMenu"
        >
          <!-- 排序：字段二选一 + 方向切换 -->
          <div class="menu-sort">
            <span class="menu-sort__label">排序</span>
            <button
              v-for="field in SORT_FIELDS"
              :key="field"
              class="sort-chip"
              :class="{ 'sort-chip--active': resume.sortBy === field }"
              type="button"
              @click="handleSortField(field)"
            >
              {{ SORT_LABELS[field] }}
            </button>
            <button
              class="sort-direction"
              type="button"
              :title="`当前：${sortLabel}${sortDirectionLabel}，点击切换方向`"
              :aria-label="`当前按${sortLabel}${sortDirectionLabel}，点击切换方向`"
              @click="handleSortOrder"
            >
              {{ sortDirectionLabel }}
            </button>
          </div>

          <!-- 当前简历不是默认时才出现的醒目入口 -->
          <div v-if="needsDefault" class="default-banner">
            <span class="default-banner__text">当前简历不是默认简历</span>
            <button class="default-banner__button" type="button" @click="handleSetDefault">
              设为默认
            </button>
          </div>

          <p v-if="resume.profilesLoading" class="menu-hint">正在加载简历…</p>
          <p v-else-if="!resume.profiles.length" class="menu-hint">
            还没有保存过的简历，点下方「新建简历」开始。
          </p>

          <ul v-else class="menu-list ui-scroll">
            <li v-for="item in resume.profiles" :key="item.id" class="menu-entry">
              <div
                class="menu-item"
                :class="{ 'menu-item--active': item.id === resume.currentProfileId }"
                role="menuitem"
                tabindex="0"
                @click="handleSwitch(item.id)"
                @keydown.enter.prevent="handleSwitch(item.id)"
              >
                <span class="menu-item__main">
                  <span class="menu-item__title" :title="item.title">{{ item.title }}</span>
                  <span v-if="item.is_default" class="default-tag">默认</span>
                  <span v-if="item.id === resume.currentProfileId" class="current-tag">编辑中</span>
                </span>

                <span class="menu-item__actions">
                  <span v-if="switchingId === item.id" class="menu-item__busy">切换中…</span>
                  <template v-else>
                    <button
                      class="icon-button"
                      type="button"
                      title="重命名"
                      aria-label="重命名"
                      @click.stop="startRename(item)"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <path d="M12 20h9" />
                        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                      </svg>
                    </button>
                    <button
                      class="icon-button icon-button--danger"
                      type="button"
                      title="删除（可进回收站找回）"
                      aria-label="删除"
                      @click.stop="handleDelete(item)"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <path d="M3 6h18" />
                        <path d="M8 6V4h8v2" />
                        <path d="M19 6l-1 14H6L5 6" />
                        <path d="M10 11v5M14 11v5" />
                      </svg>
                    </button>
                  </template>
                </span>
              </div>

              <!-- 行内改名：不弹窗、不打断列表上下文 -->
              <div v-if="editingId === item.id" class="rename-row">
                <input
                  v-model="editingTitle"
                  class="rename-input"
                  maxlength="200"
                  autofocus
                  placeholder="新的简历名称"
                  @keydown.enter.prevent="submitRename(item)"
                  @keydown.esc.prevent="cancelRename"
                  @click.stop
                />
                <button
                  class="rename-button"
                  type="button"
                  :disabled="renaming"
                  @click.stop="submitRename(item)"
                >
                  {{ renaming ? '保存中…' : '保存' }}
                </button>
                <button class="rename-button rename-button--ghost" type="button" @click.stop="cancelRename">
                  取消
                </button>
              </div>
            </li>
          </ul>

          <p v-if="menuError" class="menu-error" role="alert">{{ menuError }}</p>

          <div class="menu-footer">
            <button class="menu-action" type="button" @click="openCreate">新建简历</button>
            <button class="menu-action menu-action--ghost" type="button" @click="goTrash">
              回收站
            </button>
          </div>
        </OverlayPanel>
      </div>

      <span v-if="resume.dirty" class="dirty-dot" title="有未保存的修改">未保存</span>

      <!--
        账户入口（老板前端清单第 2 条）：一个触发按钮 + 右侧抽屉，「AI 设置」「退出登录」都在抽屉里。
        不写 `role="menu"` / `role="menuitem"`：抽屉是账户面板（`role="group"`），
        里面两项各自带对话框与不可逆语义，声明成菜单项反而读起来像一个可以方向键走的列表。
        `aria-controls` 只在展开时给：收起态抽屉根本不在 DOM 里，指一个不存在的 id 是假声明。
      -->
      <button
        class="account-trigger"
        type="button"
        :aria-expanded="drawerOpen"
        aria-haspopup="dialog"
        :aria-controls="drawerOpen ? 'user-drawer' : undefined"
        @pointerdown.stop
        @click="toggleDrawer"
      >
        <span class="account-name">{{ auth.displayName || '已登录用户' }}</span>
        <span class="account-word">账户</span>
        <span class="account-caret" :class="{ 'account-caret--open': drawerOpen }" aria-hidden="true">
          ▾
        </span>
      </button>

      <!-- 收起态整个不渲染（原来是恒在 + 靠 CSS 类收起，那是为了宽屏把两个按钮当一级项用）。 -->
      <UserDrawer
        v-if="drawerOpen"
        :username="auth.displayName || '已登录用户'"
        :signing-out="auth.signingOut"
        @close="closeDrawer"
        @open-ai-settings="openAiSettings"
        @logout="handleLogout"
      />
    </div>

    <!-- AI 设置弹窗：BYOK（ADR-009），Key 只进 sessionStorage。 -->
    <Teleport to="body">
      <div v-if="aiSettingsVisible" class="ui-overlay" @click.self="closeAiSettings">
        <section
          class="ui-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby="ai-settings-heading"
        >
          <h2 id="ai-settings-heading" class="modal-title">AI 设置</h2>
          <p class="modal-intro">
            <!-- ui-copy:allow 老板 2026-11-09 定的 C2 唯一豁免：用户必须亲手去 DeepSeek 拿这把凭据，
                 界面里的原词（DeepSeek API Key）可以出现；删掉它他反而认不出要粘什么。 -->
            「AI 润色」用的是你自己申请的 DeepSeek API Key；「生成简历」是纯模板，不需要这把 Key。
            润色失败只会提示你重试，不会改动表单。
            Key 只留在这个标签页里：关掉标签页就没了，不会存进你的账号，也不会被记录或转发。
          </p>

          <p class="ai-settings-status">
            {{ aiKeySaved ? '这个标签页里已经填好了' : '这个标签页里还没填' }}
          </p>

          <label class="modal-field">
            <!-- ui-copy:allow 输入框标签：与他 DeepSeek 控制台里的叫法一致，是 C2 的唯一豁免 -->
            <span>DeepSeek API Key</span>
            <input
              v-model="aiKeyDraft"
              type="password"
              autocomplete="off"
              spellcheck="false"
              placeholder="sk-……"
              @keydown.enter.prevent="submitAiKey"
            />
          </label>

          <p v-if="aiSettingsError" class="modal-error" role="alert">{{ aiSettingsError }}</p>

          <div class="modal-actions">
            <button class="modal-button" type="button" @click="forgetAiKey">清除 Key</button>
            <button class="modal-button" type="button" @click="closeAiSettings">关闭</button>
            <button class="modal-button modal-button--primary" type="button" @click="submitAiKey">
              保存 Key
            </button>
          </div>
        </section>
      </div>
    </Teleport>

    <!-- 新建简历弹窗：用 Teleport 到 body，避免被顶栏的 backdrop-filter 裁剪。 -->
    <Teleport to="body">
      <div v-if="createVisible" class="ui-overlay" @click.self="closeCreate">
        <section
          class="ui-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-profile-heading"
        >
          <h2 id="create-profile-heading" class="modal-title">新建简历</h2>
          <p class="modal-intro">
            每份简历独立保存，可用于投不同岗位；生成简历时使用当前选中的这一份。
          </p>

          <label class="modal-field">
            <span>简历标题</span>
            <input
              v-model="createTitle"
              maxlength="200"
              placeholder="留空则自动命名为「我的简历 N」"
              @keydown.enter.prevent="submitCreate"
            />
          </label>

          <fieldset class="modal-modes">
            <legend>内容来源</legend>
            <label class="modal-radio">
              <input v-model="createMode" type="radio" value="blank" />
              <span>空白新建</span>
            </label>
            <label class="modal-radio" :class="{ 'modal-radio--disabled': !resume.currentProfileId }">
              <input
                v-model="createMode"
                type="radio"
                value="copy"
                :disabled="!resume.currentProfileId"
              />
              <span>复制当前简历</span>
            </label>
          </fieldset>

          <p v-if="createError" class="modal-error" role="alert">{{ createError }}</p>

          <div class="modal-actions">
            <button class="modal-button" type="button" :disabled="creating" @click="closeCreate">
              取消
            </button>
            <button
              class="modal-button modal-button--primary"
              type="button"
              :disabled="creating"
              @click="submitCreate"
            >
              {{ creating ? '创建中…' : '创建' }}
            </button>
          </div>
        </section>
      </div>
    </Teleport>
  </header>
</template>

<style scoped>
/*
 * 顶栏允许换行：375px 要装「品牌 + 简历切换器 + AI 设置 + 退出登录」四组内容，
 * 一行放不下。原来的 nowrap flex 让浏览器**逐字**断行，「退出登录」排成 4 行
 * （实测按钮 47×77，整条顶栏 101px 高）。现在标签一律 `nowrap` 保形状，
 * 放不下时由 `.user-area` 整组换到第二行。
 *
 * 底色两行写法：第一行是不支持 `color-mix` 时的实心底，第二行才是
 * 「92% 表面色」的半透明底（配 `backdrop-filter`，滚动内容从底下穿过而不是一堵实墙）。
 */
.app-topbar { position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 16px; padding: 14px 48px; border-bottom: 1px solid var(--ui-border); background: var(--ui-surface); background: color-mix(in srgb, var(--ui-surface) 92%, transparent); backdrop-filter: blur(8px); }
.brand { display: flex; flex: 0 1 auto; min-width: 0; align-items: center; gap: 12px; }
.brand-mark { display: grid; width: 34px; height: 34px; color: var(--theme-on-primary); background: var(--theme-primary); font-family: "SF Mono", Consolas, monospace; font-size: 13px; place-items: center; }
.brand-text { display: grid; min-width: 0; gap: 2px; }
.brand-title { margin: 0; overflow: hidden; color: var(--ui-ink); font-size: 14px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.brand-sub { margin: 0; color: var(--ui-ink-faint); font-size: 11px; letter-spacing: .08em; }
.user-area { display: flex; flex: 1 1 auto; flex-wrap: wrap; min-width: 0; align-items: center; justify-content: flex-end; gap: 10px 12px; }
.dirty-dot { flex: 0 0 auto; border: 1px solid var(--ui-warning-border); border-radius: var(--ui-radius); padding: 2px 7px; color: var(--ui-warning); font-size: 11px; white-space: nowrap; }
/*
 * 顶栏上的控件统一 32px 高（切换器 / 账户 / 岗位库）：与 `el-button`（润色弹窗里）
 * 同一档高度，跨弹层看上去才是同一套控件。用 `height` 而不是靠 padding 堆，
 * 字号变了也不会互相错开。
 */
/*
 * 账户入口。名字（`.account-name`）与「账户」二字（`.account-word`）恒在 DOM 里，
 * 宽屏显名字、窄屏显那两个字——纯显示差异，不进 JS 判断视口（守则 L2：宽度驱动的结构差异归 CSS）。
 * 名字原来是一个不说话的 `<span class="user-name">`，现在它是按钮的一部分：
 * 点名字本来就该想到「这是我账号的地方」。
 */
.account-trigger { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 6px; height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 12px; color: var(--ui-ink); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 12px; white-space: nowrap; }
.account-trigger:hover { border-color: var(--theme-rule); color: var(--theme-primary); }
.account-trigger:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.account-name { overflow: hidden; max-width: 16ch; color: var(--ui-ink-muted); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.account-word { display: none; color: var(--ui-ink-muted); font-size: 13px; }
.account-caret { flex: 0 0 auto; color: var(--ui-ink-faint); font-size: 11px; line-height: 1; transition: transform .15s ease; }
.account-caret--open { transform: rotate(180deg); }
/*
 * 岗位库入口：与上面控件同规格（32px 高、`--ui-radius`、`--ui-*` 令牌），
 * 但它是导航而不是动作，所以底色留空、hover 只加下划线级别的提示。
 */
.jobs-entry { display: inline-flex; flex: 0 0 auto; align-items: center; height: 32px; border: 1px solid transparent; border-radius: var(--ui-radius); padding: 0 10px; color: var(--ui-ink-muted); font-size: 12px; text-decoration: none; white-space: nowrap; }
.jobs-entry:hover { border-color: var(--theme-rule); color: var(--theme-primary); }
.jobs-entry[aria-current='page'] { border-color: var(--ui-border-strong); color: var(--theme-primary-deep); background: var(--theme-bg-light); font-weight: 600; }
.ai-settings-status { margin: 0; color: var(--ui-ink-faint); font-size: 12px; }

/* ---- 简历切换器 ---- */
.profile-switcher { position: relative; flex: 0 1 auto; min-width: 0; }
.switcher-trigger { display: inline-flex; align-items: center; gap: 8px; max-width: min(300px, 100%); height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 12px; color: var(--ui-ink); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 12px; }
.switcher-trigger:hover { border-color: var(--theme-rule); }
.switcher-trigger:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.switcher-label { flex: 0 0 auto; color: var(--ui-ink-faint); font-size: 11px; letter-spacing: .04em; white-space: nowrap; }
.switcher-title { overflow: hidden; min-width: 0; max-width: 150px; font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.switcher-default { flex: 0 0 auto; border: 1px solid var(--theme-rule-soft); border-radius: var(--ui-radius); padding: 0 5px; color: var(--theme-primary-deep); background: var(--theme-bg-light); font-size: 10px; line-height: 1.6; white-space: nowrap; }
.switcher-caret { flex: 0 0 auto; color: var(--ui-ink-faint); font-size: 11px; line-height: 1; transition: transform .15s ease; }
.switcher-caret--open { transform: rotate(180deg); }

.switcher-menu { position: absolute; top: calc(100% + 8px); right: 0; z-index: 30; width: 336px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); background: var(--ui-surface); box-shadow: var(--ui-shadow-menu); }

/* ---- 排序条 ---- */
.menu-sort { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; border-bottom: 1px solid var(--ui-surface-sunken); padding: 10px; }
.menu-sort__label { color: var(--ui-ink-faint); font-size: 11px; }
.sort-chip { border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 3px 8px; color: var(--ui-ink-muted); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 11px; line-height: 1.5; white-space: nowrap; }
.sort-chip:hover { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); }
.sort-chip--active { border-color: var(--theme-primary); color: var(--theme-primary-strong); background: var(--theme-bg-light); font-weight: 600; }
.sort-direction { margin-left: auto; width: auto; min-width: 26px; padding: 0 6px; height: 24px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); color: var(--theme-primary); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 12px; line-height: 1; }
.sort-direction:hover { border-color: var(--theme-rule); background: var(--theme-bg-hover); }
.sort-chip:focus-visible, .sort-direction:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }

/* ---- 设为默认横幅（只在当前不是默认时出现） ---- */
.default-banner { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; border-bottom: 1px solid var(--ui-border); border-left: 3px solid var(--ui-warning-border); padding: 10px; background: var(--ui-warning-soft); }
.default-banner__text { color: var(--ui-warning); font-size: 11px; line-height: 1.5; }
.default-banner__button { flex: 0 0 auto; border: 1px solid var(--ui-warning-border); border-radius: var(--ui-radius); padding: 4px 10px; color: var(--ui-warning); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 11px; font-weight: 600; white-space: nowrap; }
.default-banner__button:hover { border-color: var(--ui-warning); background: var(--ui-surface-soft); }
.default-banner__button:focus-visible { outline: 2px solid var(--ui-warning-border); outline-offset: 1px; }

.menu-hint { margin: 0; padding: 14px; color: var(--ui-ink-faint); font-size: 12px; line-height: 1.5; }
.menu-list { margin: 0; padding: 6px; list-style: none; max-height: 44vh; overflow-y: auto; }
.menu-entry + .menu-entry { margin-top: 2px; }
.menu-item { display: flex; align-items: center; justify-content: space-between; gap: 8px; border-radius: var(--ui-radius); padding: 8px; cursor: pointer; }
.menu-item:hover { background: var(--theme-bg-hover); }
.menu-item:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: -2px; }
.menu-item--active { background: var(--theme-bg-light); box-shadow: inset 3px 0 0 var(--theme-primary); }
.menu-item__main { display: flex; flex: 1 1 auto; min-width: 0; align-items: center; gap: 6px; }
.menu-item__title { overflow: hidden; color: var(--ui-ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.menu-item--active .menu-item__title { font-weight: 650; }
.default-tag { flex: 0 0 auto; border: 1px solid var(--theme-rule-soft); border-radius: var(--ui-radius); padding: 1px 5px; color: var(--theme-primary-deep); background: var(--theme-bg-light); font-size: 10px; line-height: 1.4; white-space: nowrap; }
.current-tag { flex: 0 0 auto; color: var(--theme-primary); font-size: 10px; white-space: nowrap; }
.menu-item__actions { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 4px; }
.menu-item__busy { color: var(--ui-warning); font-size: 11px; white-space: nowrap; }
/* 常驻可见（D4.3 的 bug 就出在「只有 hover 才出现」），hover 时加深以提示可点 */
.icon-button { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border: 1px solid transparent; border-radius: var(--ui-radius); padding: 0; color: var(--ui-ink-faint); background: transparent; cursor: pointer; }
.icon-button:hover { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); background: var(--ui-surface); }
.icon-button--danger:hover { border-color: var(--ui-danger-border); color: var(--ui-danger); background: var(--ui-danger-soft); }
.icon-button:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }

/* ---- 行内改名 ---- */
.rename-row { display: flex; flex-wrap: wrap; gap: 6px; padding: 2px 8px 8px; }
.rename-input { flex: 1 1 120px; min-width: 0; border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 5px 8px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: 12px; outline: 0; }
.rename-button { flex: 0 0 auto; border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 4px 10px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: 11px; line-height: 1.5; white-space: nowrap; }
.rename-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.rename-button:disabled { opacity: .6; cursor: progress; }
.rename-button--ghost { border-color: var(--ui-border-strong); color: var(--ui-ink-muted); background: var(--ui-surface); }
.rename-button--ghost:hover:not(:disabled) { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); background: var(--theme-bg-hover); }

.menu-error { margin: 0; border-top: 1px solid var(--ui-danger-border); padding: 10px 14px; color: var(--ui-danger); font-size: 12px; line-height: 1.5; }
.menu-footer { display: flex; gap: 8px; border-top: 1px solid var(--ui-border); padding: 10px; }
.menu-action { flex: 1 1 0; min-width: 0; border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 8px 10px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: 12px; line-height: 1.4; white-space: nowrap; }
.menu-action:hover { background: var(--theme-primary-hover); }
.menu-action--ghost { border-color: var(--ui-border-strong); color: var(--ui-ink-muted); background: var(--ui-surface); }
.menu-action--ghost:hover { border-color: var(--theme-rule); color: var(--theme-primary-deep); background: var(--theme-bg-hover); }

/* ---- 自建弹窗（AI 设置 / 新建简历）：外壳用全局的 .ui-overlay / .ui-panel ---- */
.modal-title { margin: 0; color: var(--ui-ink); font-size: 17px; font-weight: 650; }
.modal-intro { margin: 8px 0 18px; color: var(--ui-ink-muted); font-size: 12px; line-height: 1.6; }
.modal-intro code { padding: 1px 4px; border: 1px solid var(--ui-border); border-radius: var(--ui-radius); background: var(--ui-surface-soft); font-family: "SF Mono", Consolas, monospace; font-size: 11px; }
.modal-field { display: grid; gap: 6px; }
.modal-field span { color: var(--ui-ink-muted); font-size: 12px; }
.modal-field input { width: 100%; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 10px 12px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: 14px; outline: 0; }
.modal-field input:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
.modal-modes { display: grid; gap: 8px; margin: 16px 0 0; border: 0; padding: 0; }
.modal-modes legend { padding: 0 0 6px; color: var(--ui-ink-muted); font-size: 12px; }
.modal-radio { display: inline-flex; align-items: center; gap: 8px; color: var(--ui-ink); font-size: 13px; cursor: pointer; }
.modal-radio input { margin: 0; accent-color: var(--theme-primary); }
.modal-radio--disabled { color: var(--el-text-color-disabled); cursor: not-allowed; }
.modal-error { margin: 14px 0 0; color: var(--ui-danger); font-size: 12px; line-height: 1.5; }
.modal-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 10px; margin-top: 20px; }
/*
 * 高度写死 32px（= EP `.el-button` 的默认高度）：AI 设置 / 新建简历是手工弹层，
 * 里面却是 `el-button`（润色两个弹窗）——同一个产品里两种主按钮高度（原 38 vs 32）
 * 就是「按钮不统一」的来源。用 `height` 而不是 `padding` 推，是为了不受字号影响。
 */
.modal-button { flex: 0 0 auto; height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 16px; color: var(--ui-ink-muted); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: 13px; line-height: 1; white-space: nowrap; }
.modal-button:hover:not(:disabled) { border-color: var(--theme-rule); color: var(--theme-primary-deep); }
.modal-button--primary { border-color: var(--theme-primary); color: var(--theme-on-primary); background: var(--theme-primary); }
.modal-button--primary:hover:not(:disabled) { background: var(--theme-primary-hover); }
.modal-button:disabled { opacity: .6; cursor: progress; }
.modal-button:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }

@media (max-width: 760px) {
  .app-topbar { padding: 12px 16px; }
  /*
   * 手机上只留品牌方块：连同品牌文字实测要 458px（可用 343px），顶栏被挤成两行、
   * 高 102px，占掉 375×812 视口的 12.6%。收起文字后同一行放得下（~314px），
   * 顶栏回到 57px；产品名在正文标题里还在，不会因此丢失上下文。
   */
  .brand-text { display: none; }
  /* 手机上名字让位给「账户」二字：这一排只有 343px，名字最长 16ch 会把切换器挤到换行。 */
  .account-name { display: none; }
  .account-word { display: inline; }
  .switcher-label, .switcher-default { display: none; }
  /* 窄屏先压缩简历标题的显示宽度，整条切换器再跟着容器收缩，保证省略号生效而不是撑破顶栏。 */
  .switcher-trigger { max-width: min(240px, 100%); }
  .switcher-title { max-width: 120px; }
  /*
   * 下拉菜单改挂在顶栏上（`position: sticky` 本身就是包含块），不再挂在切换器上。
   * 挂在切换器时，菜单右对齐的锚点在 x≈174，336px 宽的列表会伸到 x=-162：
   * 视口左边没有负向滚动，「新建简历 / 回收站 / 重命名」整块够不着。
   * `left:0 / right:0 / top:100%` 让它正好贴着顶栏下沿铺满一行，不写死任何像素值。
   */
  .profile-switcher { position: static; }
  .switcher-menu { top: 100%; left: 0; right: 0; width: auto; }
  /*
   * 账户抽屉不需要在这里改挂载点：它是 `position: fixed` 的满屏盒子（贴在视口右边），
   * 从来就不挂在触发按钮上——原来那段「窄屏要把弹层从按钮上挪到顶栏」的理由随
   * `.user-actions` 一起删掉了。窄屏这一排因此是 3 个一级项：岗位库、简历切换器、账户。
   */
}
@media print {
  .app-topbar { display: none; }
}
</style>
