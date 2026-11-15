<script setup lang="ts">
/**
 * 右侧用户抽屉（批次 F3 第二片，老板前端清单第 4 条「退出登录 / AI 设置收进右侧用户抽屉」）。
 *
 * 它替掉的是顶栏那个「更多」弹层——里面本来就只有这两项，改名成抽屉不是加一层：
 * 账户级动作（我要退出、我要换凭据）原本和「岗位库」「当前简历」混在同一排，
 * 用户想找退出时先要认出「更多」两个字。现在这一排的入口只留功能导航，账户的事在右边。
 *
 * 三个刻意：
 *
 * 1. **浮层的三件底层事（点外面 / Esc / 焦点归还）由 `OverlayPanel` 一处实现**（L3），
 *    抽屉这一形态的外壳（Teleport / 遮罩 / 贴右满高）由 `DrawerPanel` 一处实现。
 *    本组件只写账户这一屏的内容与文案，所以它既不是第三份手写监听，也不是第二份遮罩。
 * 2. **不自带业务决定**：弹窗怎么开、退出怎么调接口，全在调用方（`AppTopBar.vue`）
 *    原来的 handler 里，本组件只发意图事件。所以这一片没有第二套退出逻辑，也没有
 *    「抽屉里再确认一次」那种分叉。
 * 3. **名字只作身份确认**：这一屏不能改用户名，所以它是文字不是输入框。
 * 4. **大弹窗打开时整层挂起**（2026-11-15 第 25 轮）：`suspended` 由调用方给，
 *    基座据此忽略外部点击与 Esc——否则在弹窗里点一下会把背后的抽屉一起关掉。
 */
import DrawerPanel from '@/components/DrawerPanel.vue'

const props = defineProps<{
  /** 当前登录名，只作身份确认用，不可在这一屏改。 */
  username: string
  /** 正在退出：按钮禁用并改文案，防止连点发两次。 */
  signingOut?: boolean
  /** 上面压着一层大弹窗时置真：抽屉不接受外部点击 / Esc（第 25 轮层级修复）。 */
  suspended?: boolean
}>()

const emit = defineEmits<{
  close: []
  openProfile: []
  openSettings: []
  logout: []
}>()
</script>

<template>
  <DrawerPanel
    panel-id="user-drawer"
    label="账户"
    :suspended="props.suspended"
    @close="emit('close')"
  >
    <div class="account-body">
      <div class="drawer-heading">
        <p class="drawer-eyebrow">已登录</p>
        <p class="drawer-name">{{ props.username }}</p>
      </div>

      <!--
        三行账户动作（2026-11-15 第 25 轮）：个人资料 / 设置 / 退出登录。
        原来的「AI 设置」快捷入口删掉了——它现在是「设置」里的一个页签，
        抽屉里再晒一遍等于同一个界面上有两个入口（守则 L4 同一个毛病）。
      -->
      <button class="drawer-item" type="button" @click="emit('openProfile')">个人资料</button>
      <button class="drawer-item" type="button" @click="emit('openSettings')">设置</button>

      <button
        class="drawer-item drawer-item--danger"
        type="button"
        :disabled="props.signingOut"
        @click="emit('logout')"
      >
        {{ props.signingOut ? '退出中…' : '退出登录' }}
      </button>
    </div>
  </DrawerPanel>
</template>

<style scoped>
/* 内缩与行距归这一屏：外壳（DrawerPanel）只管贴右满高，不替内容决定留白。 */
.account-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 20px 20px 28px;
}

.drawer-heading {
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ui-border);
}

.drawer-eyebrow {
  margin: 0 0 4px;
  color: var(--ui-ink-faint);
  font-size: var(--text-xs);
  letter-spacing: 0.12em;
}

.drawer-name {
  margin: 0;
  color: var(--ui-ink);
  font-size: var(--text-lg);
  font-weight: 600;
  overflow-wrap: anywhere;
}

.drawer-item {
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  color: var(--ui-ink);
  background: var(--ui-canvas);
  font: inherit;
  font-size: var(--text-md);
  text-align: left;
  cursor: pointer;
}

.drawer-item:hover:not(:disabled) {
  border-color: var(--theme-rule);
}

.drawer-item:focus-visible {
  outline: 2px solid var(--theme-rule);
  outline-offset: 2px;
}

.drawer-item:disabled {
  color: var(--ui-ink-faint);
  cursor: default;
}

/* 退出是「不可逆但要紧」那一类：用现成的危险色三件套，不新调色板。 */
.drawer-item--danger {
  margin-top: 8px;
  border-color: var(--ui-danger-border);
  color: var(--ui-danger);
  background: var(--ui-danger-soft);
}
</style>
