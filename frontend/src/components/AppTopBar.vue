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
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { isApiError } from '@/api'
import type { ProfileSortField, ProfileSummary } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { useResumeStore } from '@/stores/resume'

const auth = useAuthStore()
const resume = useResumeStore()
const router = useRouter()

/* -------------------------------- 下拉菜单 -------------------------------- */

const menuOpen = ref(false)
const menuRoot = ref<HTMLElement | null>(null)
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
  const confirmed = window.confirm('删除后可在回收站找回，确认删除？')
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
    const shouldSave = window.confirm('当前简历有未保存的修改。去回收站前先保存？')
    if (!shouldSave) return
    try {
      await resume.save()
    } catch (error) {
      window.alert(describeError(error, '保存失败，请稍后重试。'))
      return
    }
  }
  await router.push({ name: 'recycle-bin' })
}

/* -------------------------------- 全局交互 -------------------------------- */

function handleDocumentPointerDown(event: PointerEvent): void {
  if (!menuOpen.value) return
  const root = menuRoot.value
  if (root && !root.contains(event.target as Node)) closeMenu()
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  if (createVisible.value) {
    closeCreate()
    return
  }
  if (menuOpen.value) closeMenu()
}

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  document.addEventListener('keydown', handleKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  document.removeEventListener('keydown', handleKeydown)
})

// 切换成功 / 被删除后列表变了，菜单里的高亮要能立刻反映，避免留着过期的错误提示。
watch(
  () => resume.currentProfileId,
  () => {
    menuError.value = ''
  },
)

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
      <!-- 简历切换器 -->
      <div ref="menuRoot" class="profile-switcher">
        <button
          class="switcher-trigger"
          type="button"
          :aria-expanded="menuOpen"
          aria-haspopup="menu"
          :title="`当前简历：${currentTitle}`"
          @click="toggleMenu"
        >
          <span class="switcher-label">当前简历</span>
          <span class="switcher-title">{{ currentTitle }}</span>
          <span v-if="currentIsDefault" class="switcher-default">默认</span>
          <span class="switcher-caret" :class="{ 'switcher-caret--open': menuOpen }" aria-hidden="true">
            ▾
          </span>
        </button>

        <div v-if="menuOpen" class="switcher-menu" role="menu" aria-label="简历列表">
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
              {{ resume.sortDesc ? '↓' : '↑' }}
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

          <ul v-else class="menu-list">
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
        </div>
      </div>

      <span v-if="resume.dirty" class="dirty-dot" title="有未保存的修改">未保存</span>
      <span class="user-name">{{ auth.displayName || '已登录用户' }}</span>
      <button class="logout-button" type="button" :disabled="auth.signingOut" @click="handleLogout">
        {{ auth.signingOut ? '退出中…' : '退出登录' }}
      </button>
    </div>

    <!-- 新建简历弹窗：用 Teleport 到 body，避免被顶栏的 backdrop-filter 裁剪。 -->
    <Teleport to="body">
      <div v-if="createVisible" class="modal-mask" @click.self="closeCreate">
        <section
          class="modal"
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
.app-topbar { position: sticky; top: 0; z-index: 10; display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: 1px solid #dbe4e1; padding: 14px 48px; background: #ffffffeb; backdrop-filter: blur(8px); }
.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark { display: grid; width: 34px; height: 34px; color: var(--theme-on-primary); background: var(--theme-primary); font-family: "SF Mono", Consolas, monospace; font-size: 13px; place-items: center; }
.brand-text { display: grid; gap: 2px; }
.brand-title { margin: 0; color: #243330; font-size: 14px; font-weight: 650; }
.brand-sub { margin: 0; color: #879692; font-size: 11px; letter-spacing: .08em; }
.user-area { display: flex; align-items: center; gap: 12px; }
.user-name { color: #52635f; font-size: 13px; }
.dirty-dot { border: 1px solid #c9a227; padding: 2px 7px; color: #94781a; font-size: 11px; }
.logout-button { border: 1px solid #d6dfdc; padding: 7px 14px; color: #52635f; background: transparent; cursor: pointer; font: inherit; font-size: 12px; }
.logout-button:hover:not(:disabled) { border-color: var(--theme-rule); color: var(--theme-primary); }
.logout-button:disabled { opacity: .6; cursor: progress; }

/* ---- 简历切换器 ---- */
.profile-switcher { position: relative; }
.switcher-trigger { display: inline-flex; align-items: center; gap: 8px; max-width: 300px; border: 1px solid #d6dfdc; padding: 6px 12px; color: #243330; background: #fff; cursor: pointer; font: inherit; }
.switcher-trigger:hover { border-color: var(--theme-rule); }
.switcher-trigger:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.switcher-label { flex: 0 0 auto; color: #879692; font-size: 11px; letter-spacing: .04em; }
.switcher-title { overflow: hidden; max-width: 150px; font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.switcher-default { flex: 0 0 auto; border: 1px solid var(--theme-rule-soft); padding: 0 5px; color: var(--theme-primary-deep); background: var(--theme-bg-light); font-size: 10px; line-height: 1.6; }
.switcher-caret { color: #879692; font-size: 11px; transition: transform .15s ease; }
.switcher-caret--open { transform: rotate(180deg); }

.switcher-menu { position: absolute; top: calc(100% + 8px); right: 0; z-index: 30; width: 336px; border: 1px solid #d6dfdc; background: #fff; box-shadow: 0 12px 28px #2633301f; }

/* ---- 排序条 ---- */
.menu-sort { display: flex; align-items: center; gap: 6px; border-bottom: 1px solid #eef2f1; padding: 8px 10px; }
.menu-sort__label { color: #879692; font-size: 11px; }
.sort-chip { border: 1px solid #e0e7e5; padding: 3px 8px; color: #687875; background: #fff; cursor: pointer; font: inherit; font-size: 11px; }
.sort-chip:hover { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); }
.sort-chip--active { border-color: var(--theme-primary); color: var(--theme-primary-strong); background: var(--theme-bg-light); font-weight: 600; }
.sort-direction { margin-left: auto; width: 26px; border: 1px solid #d6dfdc; padding: 2px 0; color: var(--theme-primary); background: #fff; cursor: pointer; font: inherit; font-size: 12px; line-height: 1.2; }
.sort-direction:hover { border-color: var(--theme-rule); background: var(--theme-bg-hover); }
.sort-chip:focus-visible, .sort-direction:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }

/* ---- 设为默认横幅（只在当前不是默认时出现） ---- */
.default-banner { display: flex; align-items: center; justify-content: space-between; gap: 8px; border-bottom: 1px solid #e6ebe9; border-left: 3px solid #c9a227; padding: 8px 10px; background: #fdf8e8; }
.default-banner__text { color: #7a6414; font-size: 11px; }
.default-banner__button { border: 1px solid #b8961f; padding: 4px 10px; color: #7a6414; background: #fff; cursor: pointer; font: inherit; font-size: 11px; font-weight: 600; }
.default-banner__button:hover { background: #f7edc8; }
.default-banner__button:focus-visible { outline: 2px solid #c9a227; outline-offset: 1px; }

.menu-hint { margin: 0; padding: 14px 14px; color: #879692; font-size: 12px; line-height: 1.5; }
.menu-list { margin: 0; padding: 6px; list-style: none; max-height: 44vh; overflow-y: auto; }
.menu-entry + .menu-entry { margin-top: 2px; }
.menu-item { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 8px 8px 10px; cursor: pointer; }
.menu-item:hover { background: var(--theme-bg-hover); }
.menu-item:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: -2px; }
.menu-item--active { background: var(--theme-bg-light); box-shadow: inset 3px 0 0 var(--theme-primary); }
.menu-item__main { display: flex; min-width: 0; align-items: center; gap: 6px; }
.menu-item__title { overflow: hidden; color: #243330; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.menu-item--active .menu-item__title { font-weight: 650; }
.default-tag { flex: 0 0 auto; border: 1px solid var(--theme-rule-soft); padding: 1px 5px; color: var(--theme-primary-deep); background: var(--theme-bg-light); font-size: 10px; line-height: 1.4; }
.current-tag { flex: 0 0 auto; color: var(--theme-primary); font-size: 10px; }
.menu-item__actions { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 4px; }
.menu-item__busy { color: #94781a; font-size: 11px; }
/* 常驻可见（D4.3 的 bug 就出在「只有 hover 才出现」），hover 时加深以提示可点 */
.icon-button { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border: 1px solid transparent; padding: 0; color: #8fa5a1; background: transparent; cursor: pointer; }
.icon-button:hover { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); background: #fff; }
.icon-button--danger:hover { border-color: #d8c3c0; color: #9a5e58; background: #fbf1f0; }
.icon-button:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }

/* ---- 行内改名 ---- */
.rename-row { display: flex; gap: 6px; padding: 2px 8px 8px; }
.rename-input { flex: 1; min-width: 0; border: 1px solid var(--theme-rule); padding: 5px 8px; color: #243330; background: #fff; font: inherit; font-size: 12px; outline: 0; }
.rename-button { border: 1px solid var(--theme-primary); padding: 4px 10px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: 11px; }
.rename-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.rename-button:disabled { opacity: .6; cursor: progress; }
.rename-button--ghost { border-color: #d6dfdc; color: #52635f; background: #fff; }
.rename-button--ghost:hover:not(:disabled) { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); background: var(--theme-bg-hover); }

.menu-error { margin: 0; border-top: 1px solid #e6dedc; padding: 8px 14px; color: #9a5e58; font-size: 12px; line-height: 1.5; }
.menu-footer { display: flex; gap: 8px; border-top: 1px solid #e6ebe9; padding: 8px; }
.menu-action { flex: 1; border: 1px solid var(--theme-primary); padding: 8px 10px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: 12px; }
.menu-action:hover { background: var(--theme-primary-hover); }
.menu-action--ghost { border-color: #d6dfdc; color: #52635f; background: #fff; }
.menu-action--ghost:hover { border-color: var(--theme-rule); color: var(--theme-primary-deep); background: var(--theme-bg-hover); }

/* ---- 新建简历弹窗 ---- */
.modal-mask { position: fixed; inset: 0; z-index: 100; display: grid; padding: 20px; background: #1f2a2899; place-items: center; }
.modal { width: min(420px, 100%); border: 1px solid #dde5e2; padding: 24px 24px 20px; background: #fff; box-shadow: 0 20px 48px #1f2a2833; }
.modal-title { margin: 0; color: #243330; font-size: 17px; font-weight: 650; }
.modal-intro { margin: 8px 0 18px; color: #687875; font-size: 12px; line-height: 1.6; }
.modal-field { display: grid; gap: 6px; }
.modal-field span { color: #687875; font-size: 12px; }
.modal-field input { width: 100%; border: 1px solid #d6dfdc; padding: 10px 12px; color: #243330; background: #fff; font: inherit; font-size: 14px; outline: 0; }
.modal-field input:focus { border-color: var(--theme-rule); box-shadow: 0 0 0 2px var(--theme-fade); }
.modal-modes { display: grid; gap: 8px; margin: 16px 0 0; border: 0; padding: 0; }
.modal-modes legend { padding: 0 0 6px; color: #687875; font-size: 12px; }
.modal-radio { display: inline-flex; align-items: center; gap: 8px; color: #243330; font-size: 13px; cursor: pointer; }
.modal-radio input { margin: 0; accent-color: var(--theme-primary); }
.modal-radio--disabled { color: #a9b6b3; cursor: not-allowed; }
.modal-error { margin: 14px 0 0; color: #9a5e58; font-size: 12px; line-height: 1.5; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
.modal-button { border: 1px solid #d6dfdc; padding: 9px 18px; color: #52635f; background: #fff; cursor: pointer; font: inherit; font-size: 13px; }
.modal-button:hover:not(:disabled) { border-color: var(--theme-rule); color: var(--theme-primary-deep); }
.modal-button--primary { border-color: var(--theme-primary); color: var(--theme-on-primary); background: var(--theme-primary); }
.modal-button--primary:hover:not(:disabled) { background: var(--theme-primary-hover); }
.modal-button:disabled { opacity: .6; cursor: progress; }
.modal-button:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }

@media (max-width: 760px) {
  .app-topbar { padding: 12px 16px; }
  .brand-sub, .user-name { display: none; }
  .switcher-label, .switcher-default { display: none; }
  .switcher-menu { width: min(336px, calc(100vw - 32px)); }
}
@media (prefers-color-scheme: dark) {
  .app-topbar { border-color: #52635f; background: #1f2a28eb; }
  .brand-title { color: #e8eae9; }
  .brand-sub { color: #8ea09c; }
  .user-name { color: #b7c3c0; }
  .logout-button { border-color: #52635f; color: #b7c3c0; }
  .switcher-trigger { border-color: #52635f; color: #e8eae9; background: #263330; }
  .switcher-default { border-color: var(--theme-rule); color: var(--theme-primary-strong); background: var(--theme-bg-light); }
  .switcher-menu { border-color: #52635f; background: #263330; }
  .menu-sort { border-color: #3a4a46; }
  .sort-chip { border-color: #52635f; color: #b7c3c0; background: #263330; }
  .sort-chip--active { border-color: var(--theme-rule); color: #e8eae9; background: var(--theme-bg-light); }
  .sort-direction { border-color: #52635f; color: var(--theme-primary-strong); background: #263330; }
  .default-banner { border-color: #3a4a46; border-left-color: #c9a227; background: #33301f; }
  .default-banner__text { color: #e0cf8f; }
  .default-banner__button { border-color: #a08a30; color: #f0e3b0; background: #263330; }
  .menu-item:hover { background: var(--theme-bg-light); }
  .menu-item--active { background: var(--theme-bg-light); }
  .menu-item__title { color: #e8eae9; }
  .default-tag { border-color: var(--theme-rule); color: var(--theme-primary-strong); background: var(--theme-bg-light); }
  .current-tag { color: var(--theme-rule); }
  .icon-button { color: var(--theme-rule); }
  .icon-button:hover { border-color: #52635f; color: #e8eae9; background: var(--theme-bg-light); }
  .rename-input { border-color: var(--theme-rule); color: #e8eae9; background: #1f2a28; }
  .menu-footer { border-color: #52635f; }
  .menu-error { border-color: #52635f; }
  .menu-action--ghost { border-color: #52635f; color: #b7c3c0; background: #263330; }
  .modal { border-color: #52635f; background: #263330; }
  .modal-title { color: #e8eae9; }
  .modal-intro, .modal-field span, .modal-modes legend { color: #b7c3c0; }
  .modal-field input { border-color: #52635f; color: #e8eae9; background: #1f2a28; }
  .modal-radio { color: #e8eae9; }
  .modal-button { border-color: #52635f; color: #b7c3c0; background: #263330; }
}
@media print {
  .app-topbar { display: none; }
}
</style>
