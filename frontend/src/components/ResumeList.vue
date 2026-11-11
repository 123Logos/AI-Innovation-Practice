<script setup lang="ts">
/**
 * 简历列表（批次 F3 第三片，老板前端清单第 5 条「简历切换改左侧常驻 Sidebar，移动端折叠成 Drawer」）。
 *
 * 这一份是**内容**，不是外壳：宽屏它住在 `AppLayout` 的左侧常驻栏里，窄屏它住在
 * `ResumeDrawer` 里，两边读的是同一个组件、同一份 store、同一套 handler。
 * 清单要的是「换个住法」，不是「再写一份列表」——所以排序 / 切换 / 设默认 / 改名 / 删除 /
 * 回收站这六件事只在这里实现一次，两个外壳都不许自己再写一遍。
 *
 * 它原来整段长在 `AppTopBar.vue` 的一个 Popover 里。搬出来时改了两处，都是搬动逼出来的：
 *
 * 1. **行不再是 `div role="menuitem"`**。Popover 时代外面套着 `role="menu"`，那样写成立；
 *    现在常驻栏是一个 `<aside>` 地标、抽屉是 `role="group"`，里面再放 `menuitem`
 *    就是「没有 menu 的 menuitem」（读屏软件会把它报成一个孤立的菜单项）。
 *    改成「主按钮 + 兄弟动作按钮」：原生可聚焦、Enter/Space 天然生效，不需要手写 keydown，
 *    也不会有 button 套 button 的非法嵌套。
 * 2. **列表自己拉一次数据**。原来 `profiles` 只有 `ResumeEditorView` 挂载时才拉
 *    （`loadFromServer()`），所以在岗位库那几页打开切换器，看到的是「还没有保存过的简历」——
 *    一个假的空态。Popover 时代没人发现，常驻栏天天摆在眼前就藏不住了。
 *    这里只调 `refreshProfiles()`（纯列表），不碰 `loadFromServer()`：后者还会选当前简历、
 *    回填草稿，那是编辑器进门的动作，侧栏不该替它做。
 *
 * 「新建简历」不在这里开弹窗：弹窗是 `.ui-overlay` 那一档（z-index 100），
 * 而抽屉收起时本组件会被卸载——弹窗若长在本组件里，用户点进输入框的那一下
 * 会被基座判成「点外面」，抽屉连同弹窗一起消失。所以本组件只发 `create` 意图，
 * 弹窗由外框（`AppLayout`）持有，它不随抽屉开合而生死。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { isApiError } from '@/api'
import type { ProfileSortField, ProfileSummary } from '@/api/types'
import { useDialogStore } from '@/stores/dialog'
import { useResumeStore } from '@/stores/resume'
import SkeletonList from '@/components/SkeletonList.vue'

const emit = defineEmits<{
  /** 这件事做完之后容器该收起（切换成功、去回收站）。常驻栏没有可收的东西，忽略即可。 */
  close: []
  /** 用户要新建：弹窗归外框持有，理由见文件头。 */
  create: []
}>()

const resume = useResumeStore()
const dialog = useDialogStore()
const router = useRouter()

/** 首次拉列表：与 store 的 `profilesLoading` 分开，因为 `refreshProfiles()` 不置那个标志。 */
const booting = ref(true)
const listError = ref('')
/** 正在切换的目标 id：用于给被点的那一行单独显示「切换中…」。 */
const switchingId = ref<string | null>(null)

const currentIsDefault = computed(() => resume.currentProfile?.is_default ?? false)
/** 当前简历不是默认时才提示「设为默认」；默认行上再放这个按钮没有意义。 */
const needsDefault = computed(() => Boolean(resume.currentProfileId) && !currentIsDefault.value)

function describeError(error: unknown, fallback: string): string {
  if (isApiError(error)) return error.message
  if (error instanceof Error && error.message) return error.message
  return fallback
}

onMounted(async () => {
  try {
    await resume.refreshProfiles()
  } catch (error) {
    listError.value = describeError(error, '读取简历列表失败，请刷新页面重试。')
  } finally {
    booting.value = false
  }
})

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
  listError.value = ''
  try {
    await resume.setSortField(field)
  } catch (error) {
    listError.value = describeError(error, '切换排序失败，请稍后重试。')
  }
}

async function handleSortOrder(): Promise<void> {
  listError.value = ''
  try {
    await resume.toggleSortOrder()
  } catch (error) {
    listError.value = describeError(error, '切换排序失败，请稍后重试。')
  }
}

/* -------------------------------- 切换 / 默认 ------------------------------ */

async function handleSwitch(profileId: string): Promise<void> {
  if (profileId === resume.currentProfileId) {
    emit('close')
    return
  }
  listError.value = ''
  switchingId.value = profileId
  try {
    // 返回 false = 用户在「未保存」确认框里点了取消，保持列表原样即可。
    const switched = await resume.switchProfile(profileId)
    if (switched) emit('close')
  } catch (error) {
    listError.value = describeError(error, '切换简历失败，请稍后重试。')
  } finally {
    switchingId.value = null
  }
}

async function handleSetDefault(): Promise<void> {
  const profileId = resume.currentProfileId
  if (!profileId) return
  listError.value = ''
  try {
    await resume.setDefaultProfile(profileId)
  } catch (error) {
    listError.value = describeError(error, '设为默认失败，请稍后重试。')
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
  listError.value = ''
  cancelRename()
  try {
    await resume.deleteProfile(item.id)
  } catch (error) {
    listError.value = describeError(error, '删除失败，请稍后重试。')
  }
}

/* --------------------------------- 改名 ---------------------------------- */

const editingId = ref<string | null>(null)
const editingTitle = ref('')
const renaming = ref(false)

function startRename(item: ProfileSummary): void {
  listError.value = ''
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
    listError.value = '简历名称不能为空。'
    return
  }
  if (title === item.title) {
    cancelRename()
    return
  }
  renaming.value = true
  listError.value = ''
  try {
    await resume.renameProfile(item.id, title)
    cancelRename()
  } catch (error) {
    // 重名等业务错误原样展示（后端文案就是「该简历名称已存在」）。
    listError.value = describeError(error, '重命名失败，请稍后重试。')
  } finally {
    renaming.value = false
  }
}

/* --------------------------------- 回收站 --------------------------------- */

async function goTrash(): Promise<void> {
  emit('close')
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
      await dialog.askNotice(
        '没保存成功，所以停在这一页',
        describeError(error, '保存失败，请稍后重试。'),
      )
      return
    }
  }
  await router.push({ name: 'recycle-bin' })
}
</script>

<template>
  <div class="resume-list">
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

    <!-- 稳定态实测：左栏一行 40px（1280 档），行间距 2px（`.menu-entry + .menu-entry`）。 -->
    <SkeletonList
      v-if="booting || resume.profilesLoading"
      class="menu-skeleton"
      label="正在加载简历…"
      :heights="[40, 40, 40, 40]"
      :heights-compact="[40, 40, 40, 40]"
      :gap="2"
    />
    <p v-else-if="!resume.profiles.length" class="menu-hint">
      还没有保存过的简历，点下方「新建简历」开始。
    </p>

    <ul v-else class="menu-list ui-scroll">
      <li v-for="item in resume.profiles" :key="item.id" class="menu-entry">
        <div class="menu-item" :class="{ 'menu-item--active': item.id === resume.currentProfileId }">
          <button class="menu-item__main" type="button" @click="handleSwitch(item.id)">
            <span class="menu-item__title" :title="item.title">{{ item.title }}</span>
            <span v-if="item.is_default" class="default-tag">默认</span>
            <span v-if="item.id === resume.currentProfileId" class="current-tag">编辑中</span>
          </button>

          <span class="menu-item__actions">
            <span v-if="switchingId === item.id" class="menu-item__busy">切换中…</span>
            <template v-else>
              <button
                class="icon-button"
                type="button"
                title="重命名"
                aria-label="重命名"
                @click="startRename(item)"
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
                @click="handleDelete(item)"
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
          />
          <button
            class="rename-button"
            type="button"
            :disabled="renaming"
            @click="submitRename(item)"
          >
            {{ renaming ? '保存中…' : '保存' }}
          </button>
          <button class="rename-button rename-button--ghost" type="button" @click="cancelRename">
            取消
          </button>
        </div>
      </li>
    </ul>

    <p v-if="listError" class="menu-error" role="alert">{{ listError }}</p>

    <div class="menu-footer">
      <button class="menu-action" type="button" @click="emit('create')">新建简历</button>
      <button class="menu-action menu-action--ghost" type="button" @click="goTrash">回收站</button>
    </div>
  </div>
</template>

<style scoped>
/*
 * 内容自己撑满外壳给的高度：常驻栏是一整列、抽屉是一整屏，列表在中间那段滚动，
 * 排序条与底部两个行动始终看得见（原来 Popover 里写的是 `max-height: 44vh`，
 * 那是「贴在顶栏下沿的一小块」的尺寸，搬进满高容器后会让下半截空着）。
 */
.resume-list {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
}

.menu-sort { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; border-bottom: 1px solid var(--ui-surface-sunken); padding: 10px; }
.menu-sort__label { color: var(--ui-ink-faint); font-size: var(--text-xs); }
.sort-chip { border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: 3px 8px; color: var(--ui-ink-muted); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-xs); line-height: 1.5; white-space: nowrap; }
.sort-chip:hover { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); }
.sort-chip--active { border-color: var(--theme-primary); color: var(--theme-primary-strong); background: var(--theme-bg-light); font-weight: 600; }
.sort-direction { margin-left: auto; width: auto; min-width: 26px; padding: 0 6px; height: 24px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); color: var(--theme-primary); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-sm); line-height: 1; }
.sort-direction:hover { border-color: var(--theme-rule); background: var(--theme-bg-hover); }
.sort-chip:focus-visible, .sort-direction:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }

.default-banner { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; border-bottom: 1px solid var(--ui-border); border-left: 3px solid var(--ui-warning-border); padding: 10px; background: var(--ui-warning-soft); }
.default-banner__text { color: var(--ui-warning); font-size: var(--text-xs); line-height: 1.5; }
.default-banner__button { flex: 0 0 auto; border: 1px solid var(--ui-warning-border); border-radius: var(--ui-radius); padding: 4px 10px; color: var(--ui-warning); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-xs); font-weight: 600; white-space: nowrap; }
.default-banner__button:hover { border-color: var(--ui-warning); background: var(--ui-surface-soft); }
.default-banner__button:focus-visible { outline: 2px solid var(--ui-warning-border); outline-offset: 1px; }

.menu-hint { margin: 0; padding: 14px; color: var(--ui-ink-faint); font-size: var(--text-sm); line-height: 1.5; }
/* 骨架与列表本体同一层内缩（`.menu-list` 的 6px），数据到达时行不会横向挪一下。 */
.menu-skeleton { padding: 6px; }
.menu-list { flex: 1 1 auto; min-height: 0; margin: 0; padding: 6px; list-style: none; overflow-y: auto; }
.menu-entry + .menu-entry { margin-top: 2px; }
.menu-item { display: flex; align-items: center; justify-content: space-between; gap: 8px; border-radius: var(--ui-radius); padding: 8px; }
.menu-item:hover { background: var(--theme-bg-hover); }
.menu-item--active { background: var(--theme-bg-light); box-shadow: inset 3px 0 0 var(--theme-primary); }
/* 主按钮：整行的可点区域就是它，动作按钮是它的兄弟而不是子节点（button 不许套 button）。 */
.menu-item__main { display: flex; flex: 1 1 auto; min-width: 0; align-items: center; gap: 6px; border: 0; padding: 0; color: inherit; background: transparent; font: inherit; text-align: left; cursor: pointer; }
.menu-item__main:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.menu-item__title { overflow: hidden; color: var(--ui-ink); font-size: var(--text-base); text-overflow: ellipsis; white-space: nowrap; }
.menu-item--active .menu-item__title { font-weight: 650; }
.default-tag { flex: 0 0 auto; border: 1px solid var(--theme-rule-soft); border-radius: var(--ui-radius); padding: 1px 5px; color: var(--theme-primary-deep); background: var(--theme-bg-light); font-size: var(--text-2xs); line-height: 1.4; white-space: nowrap; }
.current-tag { flex: 0 0 auto; color: var(--theme-primary); font-size: var(--text-2xs); white-space: nowrap; }
.menu-item__actions { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 4px; }
.menu-item__busy { color: var(--ui-warning); font-size: var(--text-xs); white-space: nowrap; }

.icon-button { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border: 1px solid transparent; border-radius: var(--ui-radius); padding: 0; color: var(--ui-ink-faint); background: transparent; cursor: pointer; }
.icon-button:hover { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); background: var(--ui-surface); }
.icon-button--danger:hover { border-color: var(--ui-danger-border); color: var(--ui-danger); background: var(--ui-danger-soft); }
.icon-button:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }

.rename-row { display: flex; flex-wrap: wrap; gap: 6px; padding: 2px 8px 8px; }
.rename-input { flex: 1 1 120px; min-width: 0; border: 1px solid var(--theme-rule); border-radius: var(--ui-radius); padding: 5px 8px; color: var(--ui-ink); background: var(--ui-surface); font: inherit; font-size: var(--text-sm); outline: 0; }
.rename-button { flex: 0 0 auto; border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 4px 10px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: var(--text-xs); line-height: 1.5; white-space: nowrap; }
.rename-button:hover:not(:disabled) { background: var(--theme-primary-hover); }
.rename-button:disabled { opacity: .6; cursor: progress; }
.rename-button--ghost { border-color: var(--ui-border-strong); color: var(--ui-ink-muted); background: var(--ui-surface); }
.rename-button--ghost:hover:not(:disabled) { border-color: var(--theme-rule-soft); color: var(--theme-primary-deep); background: var(--theme-bg-hover); }

.menu-error { margin: 0; border-top: 1px solid var(--ui-danger-border); padding: 10px 14px; color: var(--ui-danger); font-size: var(--text-sm); line-height: 1.5; }
.menu-footer { display: flex; flex: 0 0 auto; gap: 8px; border-top: 1px solid var(--ui-border); padding: 10px; }
.menu-action { flex: 1 1 0; min-width: 0; border: 1px solid var(--theme-primary); border-radius: var(--ui-radius); padding: 8px 10px; color: var(--theme-on-primary); background: var(--theme-primary); cursor: pointer; font: inherit; font-size: var(--text-sm); line-height: 1.4; white-space: nowrap; }
.menu-action:hover { background: var(--theme-primary-hover); }
.menu-action--ghost { border-color: var(--ui-border-strong); color: var(--ui-ink-muted); background: var(--ui-surface); }
.menu-action--ghost:hover { border-color: var(--theme-rule); color: var(--theme-primary-deep); background: var(--theme-bg-hover); }
</style>
