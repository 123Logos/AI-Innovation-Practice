<script setup lang="ts">
/**
 * 顶栏：品牌 + 工作区切换器 + 一级导航 + 简历入口（只在窄屏）+ 账户入口。
 *
 * 工作区切换器（清单第 6 条，第四片）在 Logo 右侧，是 `WorkspaceSwitcher.vue` 一个组件：
 * 卡片与当前工作区名都读模块表，顶栏这里只负责把它摆在品牌旁边（`.brand-side` 那一组）。
 *
 * 批次 F3 第三片之后顶栏不再持有简历列表：清单第 5 条把「简历切换」改成左侧常驻 Sidebar、
 * 窄屏折叠成 Drawer，列表内容与全部 handler 收进 `components/ResumeList.vue` 一份，
 * 两个外壳（`AppLayout` 的左栏 / `ResumeDrawer`）共用。顶栏这里只剩一个**触发按钮**，
 * 而且只在窄屏渲染——宽屏那份列表一直摆在左边，不需要一个按钮去开它。
 *
 * 谁持有抽屉的开合：`AppLayout`。「宽屏换常驻栏、窄屏换抽屉」这个决定在那里做
 * （`useViewport()` 是全站唯一允许按宽度换组件的地方，守则 L2），触发按钮在顶栏、
 * 抽屉在外框，状态只能由两者的共同父级拿着。顶栏因此只发一个 `toggle-resumes` 意图。
 *
 * D4.3 依据人工验收重做的那套列表交互（常驻重命名/删除、排序、设为默认横幅、行内改名）
 * 一字未改地跟着列表搬走了，口径与用例见 `ResumeList.vue`。
 *
 * 账户那一排（清单第 4 条，批次 F3 第二片）：「AI 设置 / 退出登录」收进右侧用户抽屉，
 * 宽窄屏同一个入口，原来那两层 `display: contents` 的分叉已删；「岗位库」仍不收进来。
 *
 * 登出时除 `resume.reset()` 外还要 `job.reset()`——先清业务数据再清登录态这条不变量
 * 涵盖岗位数据（`stores/job.ts` 里每个 `load*()` 前的清空只是缓解，不替代它）。
 */
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { clearDeepSeekKey, hasDeepSeekKey, setDeepSeekKey } from '@/api'
import UserDrawer from '@/components/UserDrawer.vue'
import WorkspaceSwitcher from '@/components/WorkspaceSwitcher.vue'
import { TOP_BAR_MODULES } from '@/config/modules'
import { useAuthStore } from '@/stores/auth'
import { useJobStore } from '@/stores/job'
import { useResumeStore } from '@/stores/resume'

const props = defineProps<{
  /** 窄屏抽屉是否展开：由外框持有，这里只用来画 `aria-expanded` 与箭头方向。 */
  resumesOpen: boolean
  /** 宽屏左栏常驻，这个按钮就不该存在（不是藏起来，是不渲染）。 */
  showResumesTrigger: boolean
}>()

const emit = defineEmits<{ 'toggle-resumes': [] }>()

const auth = useAuthStore()
const resume = useResumeStore()
const job = useJobStore()
const router = useRouter()

/** 手机上「我在改哪一份」这句话不能因为列表换了住法就丢掉，所以触发按钮里仍带当前简历名。 */
const currentTitle = computed(() => resume.currentProfileTitle)

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
 * 账户级动作（AI 设置 / 退出登录）的入口：一个「账户」触发按钮 + 右侧抽屉（`UserDrawer.vue`）。
 *
 * **宽屏也收进抽屉**——这一点改了 ADR-011 议题 3.D 的原排版。3.D 只管「窄屏一级项从 5 个降到 3 个」，
 * 当时两个动作在宽屏仍是一级项；清单第 4 条要的是「账户的事在右边」，那是归属问题不是宽度问题。
 *
 * 抽屉的三件底层事（点外面 / Esc / 焦点归还）由 `OverlayPanel` 基座承担，外壳由 `DrawerPanel`
 * 承担，所以这里既没有 pointerdown 监听，也没有 document 级 keydown，触发按钮上也不写
 * `@pointerdown.stop`：基座认 `aria-controls` 的反向链接，只把「点自己的触发按钮」豁免掉。
 * 于是「开账户抽屉时简历抽屉还开着」不会发生——简历抽屉的基座看得见这一下（那个按钮的
 * `aria-controls` 指的是账户面板，不是它），判成「点外面」就关了。互斥因此不需要谁去关谁。
 */
const drawerOpen = ref(false)

function closeDrawer(): void {
  drawerOpen.value = false
}

function toggleDrawer(): void {
  drawerOpen.value = !drawerOpen.value
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
    <div class="brand-side">
      <div class="brand">
        <span class="brand-mark">AI</span>
        <div class="brand-text">
          <p class="brand-title">职业发展助手</p>
        </div>
      </div>
      <!-- 工作区切换器（清单第 6 条）：Logo 右侧，卡片与文案都读模块表，见组件文件头。 -->
      <WorkspaceSwitcher />
    </div>

    <div class="user-area">
      <!--
        一级导航入口（ADR-011 议题 3.A + 守则 L4）。

        模块名与路由一律来自模块表 `@/config/modules`，这里不写死任何模块名：
        「一张表」的判据是**新增加一个模块时只改那张表**，而不是「这张表旁边另有一份也能用」。

        其余取舍不变（议题 3.A / 3.D 裁定）：模块导航只有一条文字入口、不降级成图标——
        手机是学生的主力设备，这条功能的入口藏起来等于没有。
        3.A 当年那句「不加侧边栏」管的是**模块导航**，与清单第 5 条的简历列表左栏不是一件事：
        左栏里是用户自己的简历（内容），不是产品模块（导航），模块表仍然只有这一处消费者。
        岗位自己的回收站不在这里（简历的「回收站」在列表底部，两个混在一层会让人
        分不清删掉的是什么），入口在 `/jobs` 列表页顶部的「我的删除」。

        `class="jobs-entry"` 这个类名沿用不改：`AppTopBar.spec.ts` 的一级项顺序断言按它取值。
        模块表的 `iconPaths` 仍不渲染——加图标是视觉改动，归批次 F4。
      -->
      <RouterLink
        v-for="item in TOP_BAR_MODULES"
        :key="item.key"
        class="jobs-entry"
        :to="{ name: item.routeName }"
      >
        {{ item.name }}
      </RouterLink>
      <!--
        简历入口：只在窄屏渲染（宽屏那份列表常驻在左栏，不需要按钮）。
        `aria-controls` 只在展开时给：收起态抽屉不在 DOM 里，指一个不存在的 id 是假声明。
      -->
      <button
        v-if="props.showResumesTrigger"
        class="resumes-trigger"
        type="button"
        :aria-expanded="props.resumesOpen"
        aria-haspopup="dialog"
        :aria-controls="props.resumesOpen ? 'resume-drawer' : undefined"
        :title="`当前简历：${currentTitle}`"
        @click="emit('toggle-resumes')"
      >
        <span class="resumes-trigger__label">简历</span>
        <span class="resumes-trigger__title">{{ currentTitle }}</span>
        <span
          class="resumes-trigger__caret"
          :class="{ 'resumes-trigger__caret--open': props.resumesOpen }"
          aria-hidden="true"
        >
          ▾
        </span>
      </button>

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
        @click="toggleDrawer"
      >
        <span class="account-name">{{ auth.displayName || '已登录用户' }}</span>
        <span class="account-word">账户</span>
        <span class="account-caret" :class="{ 'account-caret--open': drawerOpen }" aria-hidden="true">
          ▾
        </span>
      </button>

      <!-- 收起态整个不渲染（原来是恒在 + 靠 CSS 类收起，那是为了宽屏把两个按钮当一级项用）。 -->
      <Transition name="ui-fade">
        <UserDrawer
          v-if="drawerOpen"
          :username="auth.displayName || '已登录用户'"
          :signing-out="auth.signingOut"
          @close="closeDrawer"
          @open-ai-settings="openAiSettings"
          @logout="handleLogout"
        />
      </Transition>
    </div>

    <!-- AI 设置弹窗：BYOK（ADR-009），Key 只进 sessionStorage。 -->
    <Teleport to="body">
      <Transition name="ui-fade">
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
      </Transition>
    </Teleport>
  </header>
</template>
<style scoped>
/*
 * 顶栏允许换行：375px 要装「品牌 + 简历入口 + 账户」几组内容，一行放不下时整组换行。
 * 原来的 nowrap flex 让浏览器**逐字**断行，「退出登录」排成 4 行
 * （实测按钮 47×77，整条顶栏 101px 高）。现在标签一律 `nowrap` 保形状，
 * 放不下时由 `.user-area` 整组换到第二行。
 *
 * 底色两行写法：第一行是不支持 `color-mix` 时的实心底，第二行才是
 * 「92% 表面色」的半透明底（配 `backdrop-filter`，滚动内容从底下穿过而不是一堵实墙）。
 * 注意这层 `backdrop-filter` 会给所有后代生成 fixed 定位的包含块——
 * 所以抽屉必须 Teleport 出去（`DrawerPanel`），留在顶栏里只有顶栏那么高（两档实测过）。
 */
.app-topbar { --topbar-inset: 48px; position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 16px; padding: 14px var(--topbar-inset); border-bottom: 1px solid var(--ui-border); background: var(--ui-surface); background: color-mix(in srgb, var(--ui-surface) 92%, transparent); backdrop-filter: blur(8px); }
/* 品牌与工作区切换器同属左边一组：顶栏仍是「左一组 / 右一组」两个直接子项。 */
.brand-side { display: flex; flex: 0 1 auto; min-width: 0; align-items: center; gap: 12px; }
.brand { display: flex; flex: 0 1 auto; min-width: 0; align-items: center; gap: 12px; }
.brand-mark { display: grid; width: 34px; height: 34px; color: var(--theme-on-primary); background: var(--theme-primary); font-family: "SF Mono", Consolas, monospace; font-size: var(--text-base); place-items: center; }
/*
 * 品牌只留产品名一行（老板 2026-11-11 裁定，§6 第 9 条）：原来那行副标题写死「简历工作台」，
 * 而它右边的工作区切换器显示的是**当前**工作区——站在 /jobs 上时副标题就是事实错误。
 * 「跟着当前工作区走」被明确否掉（与切换器完全冗余），所以整行删掉，不是藏起来。
 */
.brand-text { display: grid; min-width: 0; }
.brand-title { margin: 0; overflow: hidden; color: var(--ui-ink); font-size: var(--text-md); font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.user-area { display: flex; flex: 1 1 auto; flex-wrap: wrap; min-width: 0; align-items: center; justify-content: flex-end; gap: 10px 12px; }
.dirty-dot { flex: 0 0 auto; border: 1px solid var(--ui-warning-border); border-radius: var(--ui-radius); padding: 2px 7px; color: var(--ui-warning); font-size: var(--text-xs); white-space: nowrap; }
/*
 * 顶栏上的控件统一 32px 高（简历入口 / 账户 / 岗位库）：与 `el-button`（润色弹窗里）
 * 同一档高度，跨弹层看上去才是同一套控件。用 `height` 而不是靠 padding 堆，
 * 字号变了也不会互相错开。
 */
/*
 * 账户入口。名字（`.account-name`）与「账户」二字（`.account-word`）恒在 DOM 里，
 * 宽屏显名字、窄屏显那两个字——纯显示差异，不进 JS 判断视口（守则 L2：宽度驱动的结构差异归 CSS）。
 * 名字原来是一个不说话的 `<span class="user-name">`，现在它是按钮的一部分：
 * 点名字本来就该想到「这是我账号的地方」。
 */
.account-trigger { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 6px; height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 12px; color: var(--ui-ink); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-sm); white-space: nowrap; }
.account-trigger:hover { border-color: var(--theme-rule); color: var(--theme-primary); }
.account-trigger:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.account-name { overflow: hidden; max-width: 16ch; color: var(--ui-ink-muted); font-size: var(--text-base); text-overflow: ellipsis; white-space: nowrap; }
.account-word { display: none; color: var(--ui-ink-muted); font-size: var(--text-base); }
.account-caret { flex: 0 0 auto; color: var(--ui-ink-faint); font-size: var(--text-xs); line-height: 1; transition: transform var(--motion-base) var(--motion-ease); }
.account-caret--open { transform: rotate(180deg); }
/*
 * 岗位库入口：与上面控件同规格（32px 高、`--ui-radius`、`--ui-*` 令牌），
 * 但它是导航而不是动作，所以底色留空、hover 只加下划线级别的提示。
 */
.jobs-entry { display: inline-flex; flex: 0 0 auto; align-items: center; height: 32px; border: 1px solid transparent; border-radius: var(--ui-radius); padding: 0 10px; color: var(--ui-ink-muted); font-size: var(--text-sm); text-decoration: none; white-space: nowrap; }
.jobs-entry:hover { border-color: var(--theme-rule); color: var(--theme-primary); }
.jobs-entry[aria-current='page'] { border-color: var(--ui-border-strong); color: var(--theme-primary-deep); background: var(--theme-bg-light); font-weight: 600; }
.ai-settings-status { margin: 0; color: var(--ui-ink-faint); font-size: var(--text-sm); }
/* ---- 简历入口（只在窄屏渲染，宽屏那份列表常驻在左栏） ---- */
.resumes-trigger { display: inline-flex; flex: 0 1 auto; min-width: 0; align-items: center; gap: 8px; max-width: min(300px, 100%); height: 32px; border: 1px solid var(--ui-border-strong); border-radius: var(--ui-radius); padding: 0 12px; color: var(--ui-ink); background: var(--ui-surface); cursor: pointer; font: inherit; font-size: var(--text-sm); }
.resumes-trigger:hover { border-color: var(--theme-rule); }
.resumes-trigger:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 2px; }
.resumes-trigger__label { flex: 0 0 auto; color: var(--ui-ink-faint); font-size: var(--text-xs); letter-spacing: .04em; white-space: nowrap; }
.resumes-trigger__title { overflow: hidden; min-width: 0; max-width: 150px; font-size: var(--text-base); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.resumes-trigger__caret { flex: 0 0 auto; color: var(--ui-ink-faint); font-size: var(--text-xs); line-height: 1; transition: transform var(--motion-base) var(--motion-ease); }
.resumes-trigger__caret--open { transform: rotate(180deg); }

@media (max-width: 760px) {
  /* 内缩值只写一处：工作区弹层的左右边界也读它（`--topbar-inset`），改这里两处一起动。 */
  /*
   * 375px 的横向预算（实测，不是估）：可用 343px。加上工作区切换器之后，
   * 原来那套间距与内缩会让顶栏换成两行（实测 101px，比第三片的 59px 多吃 42px）。
   * 下面这几条把预算压回一行：间距 16/12 → 10/8、三个控件内缩各收 2–4px、
   * 简历入口去掉「简历」二字只留名字（名字本身就在说它是哪一份）。
   * 名字宽度是**封顶**的，所以长名字也不会把顶栏挤成两行。
   */
  .app-topbar { --topbar-inset: 16px; gap: 10px 10px; padding: 12px var(--topbar-inset); }
  .brand-side { gap: 8px; }
  .user-area { gap: 8px; }
  .jobs-entry { padding: 0 8px; }
  .account-trigger { padding: 0 10px; }
  /*
   * 手机上只留品牌方块：连同品牌文字实测要 458px（可用 343px），顶栏被挤成两行、
   * 高 102px，占掉 375×812 视口的 12.6%。收起文字后同一行放得下（~314px），
   * 顶栏回到 57px；产品名在正文标题里还在，不会因此丢失上下文。
   */
  .brand-text { display: none; }
  /* 手机上名字让位给「账户」二字：这一排只有 343px，名字最长 16ch 会把简历入口挤到换行。 */
  .account-name { display: none; }
  .account-word { display: inline; }
  /* 窄屏先压缩简历名的显示宽度，整条入口再跟着容器收缩，保证省略号生效而不是撑破顶栏。 */
  .resumes-trigger { max-width: min(140px, 100%); padding: 0 8px; }
  .resumes-trigger__label { display: none; }
  .resumes-trigger__title { max-width: 56px; }
  /* 简历列表在窄屏是外框持有的满高抽屉，不再需要「把弹层从按钮上挪到顶栏」那套挂载点特例。 */
}
@media print {
  .app-topbar { display: none; }
}
</style>
