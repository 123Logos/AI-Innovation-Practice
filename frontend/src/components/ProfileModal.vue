<script setup lang="ts">
/**
 * 个人资料弹窗（2026-11-15 第 25 轮，老板指令第 3 条）。
 *
 * 只做**档案表头**的编辑：姓名 / 手机号 / 邮箱 / 期望职位。技能、经历、教育这些
 * 结构化内容仍在简历工作台里改——把它们塞进一个弹窗只会让两边都有半套编辑。
 * 学号与个人简介不在 `ProfileRead` 里（没有这两列，加列要先过 ADR），所以不做假控件。
 *
 * **保存走 `PUT /profiles/me`，而它是整体覆盖**：请求体少带子表就是清空子表。
 * 因此这里用 `stores/resume.ts` 导出的 `draftFromProfile()` + `payloadFromDraft()`
 * 把整份档案原样带上，只替换表头那四格——不复用那份组装逻辑，迟早会把用户的技能清掉。
 *
 * **清空的字段不覆盖旧值**（老板原话「实现空值不覆盖旧值」）。注意机制：在这个 PUT 契约下
 * 「不发这个键」等于发默认值（`email` 的默认是 `None`、`contact` 的默认是 `""`），
 * 也就是**会**清掉旧值。所以这里做的是「把旧值填回去再发」——用户的清空动作在请求体里
 * 不存在，旧值因此在库里一格没动。要真正支持「清空」，得先给后端加 PATCH 语义，那是另一批。
 */
import { computed, reactive, ref, watch } from 'vue'

import { isApiError, profilesApi } from '@/api'
import { fetchMyOrganization } from '@/api/organizations'
import type { ProfileRead } from '@/api/types'
import { ROLE_LABELS } from '@/config/roles'
import { useAuthStore } from '@/stores/auth'
import { draftFromProfile, payloadFromDraft } from '@/stores/resume'
import { useUiStore } from '@/stores/ui'

const PHONE_PATTERN = /^1[3-9]\d{9}$/
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const ui = useUiStore()
const auth = useAuthStore()

type LoadState = 'loading' | 'ready' | 'failed'

const state = ref<LoadState>('loading')
const loadError = ref('')
const profile = ref<ProfileRead | null>(null)
/** `null` = 后端 404（没有组织）；`undefined` = 没读到（不替用户下结论）。 */
const orgName = ref<string | null | undefined>(undefined)
const saving = ref(false)
const saveError = ref('')
const savedNotice = ref('')

const form = reactive({ full_name: '', contact: '', email: '', target_role: '' })
/** 打开弹窗时那四格的原始值：判断「清空」以及「有没有改动」都靠它。 */
const original = reactive({ full_name: '', contact: '', email: '', target_role: '' })

const roleLabel = computed(() => (auth.role ? ROLE_LABELS[auth.role] : '—'))
const orgText = computed(() =>
  orgName.value === undefined ? '暂时读不到' : (orgName.value ?? '未加入组织'),
)
const changed = computed(() =>
  (['full_name', 'contact', 'email', 'target_role'] as const).some(
    (key) => form[key].trim() !== original[key],
  ),
)

function copyInto(target: typeof form, source: Record<string, string>): void {
  target.full_name = source.full_name ?? ''
  target.contact = source.contact ?? ''
  target.email = source.email ?? ''
  target.target_role = source.target_role ?? ''
}

async function load(): Promise<void> {
  state.value = 'loading'
  loadError.value = ''
  saveError.value = ''
  savedNotice.value = ''
  orgName.value = undefined

  // 组织名与档案各读一次：组织 404 是「没有组织」这一种状态，不是故障（ADR-021 §2.6）。
  const [orgResult, profileResult] = await Promise.allSettled([
    fetchMyOrganization(),
    profilesApi.getMyProfile(),
  ])

  if (orgResult.status === 'fulfilled') {
    orgName.value = orgResult.value.organization.name
  } else if (isApiError(orgResult.reason) && orgResult.reason.status === 404) {
    orgName.value = null
  }

  if (profileResult.status === 'rejected') {
    state.value = 'failed'
    loadError.value = isApiError(profileResult.reason)
      ? profileResult.reason.message
      : '个人资料暂时读不到，请稍后重试。'
    return
  }

  const loaded = profileResult.value
  profile.value = loaded
  copyInto(original, {
    full_name: loaded?.full_name ?? '',
    contact: loaded?.contact ?? '',
    email: loaded?.email ?? '',
    target_role: loaded?.target_role ?? '',
  })
  copyInto(form, { ...original })
  state.value = 'ready'
}

/** 原本有值、现在被清空 → 交回旧值；其余情况原样返回（前后空白不算内容）。 */
function keepIfCleared(next: string, previous: string): string {
  const trimmed = next.trim()
  return trimmed === '' && previous.trim() !== '' ? previous : trimmed
}

function validate(): string | null {
  const contact = form.contact.trim()
  // 手机号原本有值且这次被清空时，请求里发的是旧值，所以不必拦。
  if (contact && !PHONE_PATTERN.test(contact)) {
    return '手机号格式不正确，请填写 11 位中国大陆手机号（如 13800000000）。'
  }
  const email = form.email.trim()
  if (email && !EMAIL_PATTERN.test(email)) return '邮箱格式不正确，请检查后再保存。'
  return null
}

async function save(): Promise<void> {
  if (saving.value) return
  saveError.value = ''
  savedNotice.value = ''

  const invalid = validate()
  if (invalid) {
    saveError.value = invalid
    return
  }

  saving.value = true
  try {
    const current = profile.value
    const payload = current
      ? payloadFromDraft({
          ...draftFromProfile(current),
          full_name: keepIfCleared(form.full_name, original.full_name),
          contact: keepIfCleared(form.contact, original.contact),
          email: keepIfCleared(form.email, original.email),
          target_role: keepIfCleared(form.target_role, original.target_role),
        })
      : {
          // 还没有档案：没有子表可保，只提交这四格（其余走后端默认值）。
          full_name: form.full_name.trim(),
          contact: form.contact.trim(),
          email: form.email.trim() || null,
          target_role: form.target_role.trim() || null,
          skills: [],
          experiences: [],
          educations: [],
          certificates: [],
          portfolio_links: [],
          city_preferences: [],
        }

    const updated = await profilesApi.saveMyProfile(payload)
    profile.value = updated
    copyInto(original, {
      full_name: updated.full_name,
      contact: updated.contact,
      email: updated.email ?? '',
      target_role: updated.target_role ?? '',
    })
    copyInto(form, { ...original })
    savedNotice.value = '已保存。'
  } catch (error) {
    saveError.value = isApiError(error) ? error.message : '保存失败，请稍后重试。'
  } finally {
    saving.value = false
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') ui.closeProfile()
}

// 打开时读一次；关掉不清理状态，下一次打开会重新读（避免显示上一次的旧值）。
watch(
  () => ui.profileOpen,
  (open) => {
    if (open) void load()
  },
)
</script>

<template>
  <Teleport to="body">
    <Transition name="ui-fade">
      <div
        v-if="ui.profileOpen"
        class="ui-overlay"
        data-testid="profile-modal"
        @click.self="ui.closeProfile()"
        @keydown="onKeydown"
      >
        <section
          class="ui-panel profile-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby="profile-heading"
        >
          <h2 id="profile-heading" class="modal-title">个人资料</h2>

          <p v-if="state === 'loading'" class="modal-intro">正在读取…</p>
          <p v-else-if="state === 'failed'" class="modal-error" role="alert">{{ loadError }}</p>

          <template v-else>
            <dl class="profile-facts">
              <div class="profile-fact">
                <dt>用户名</dt>
                <dd>{{ auth.displayName || '—' }}</dd>
              </div>
              <div class="profile-fact">
                <dt>组织</dt>
                <dd>{{ orgText }}</dd>
              </div>
              <div class="profile-fact">
                <dt>身份</dt>
                <dd>{{ roleLabel }}</dd>
              </div>
            </dl>

            <form class="profile-form" @submit.prevent="save">
              <label class="modal-field">
                <span>姓名</span>
                <input v-model="form.full_name" maxlength="100" placeholder="暂未填写姓名" />
              </label>
              <label class="modal-field">
                <span>手机号</span>
                <input
                  v-model="form.contact"
                  maxlength="255"
                  inputmode="numeric"
                  placeholder="暂未填写手机号"
                />
              </label>
              <label class="modal-field">
                <span>邮箱</span>
                <input
                  v-model="form.email"
                  type="email"
                  maxlength="254"
                  placeholder="暂未填写邮箱"
                />
              </label>
              <label class="modal-field">
                <span>期望职位</span>
                <input v-model="form.target_role" maxlength="200" placeholder="暂未填写期望职位" />
              </label>

              <p v-if="saveError" class="modal-error" role="alert">{{ saveError }}</p>
              <p v-else-if="savedNotice" class="profile-notice" role="status">{{ savedNotice }}</p>

              <div class="modal-actions">
                <button class="modal-button" type="button" @click="ui.closeProfile()">关闭</button>
                <button
                  class="modal-button modal-button--primary"
                  type="submit"
                  :disabled="saving || !changed"
                  data-testid="profile-save"
                >
                  {{ saving ? '保存中…' : '保存' }}
                </button>
              </div>
            </form>
          </template>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.profile-panel {
  width: min(560px, 100%);
}

/* 只读三格：它们是身份事实，不是可编辑项，所以做成表格而不是禁用输入框。 */
.profile-facts {
  display: grid;
  gap: 6px;
  margin: 12px 0 16px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  padding: 10px 14px;
  background: var(--ui-surface-soft);
}

.profile-fact {
  display: flex;
  gap: 12px;
  font-size: var(--text-base);
}

.profile-fact dt {
  flex: 0 0 56px;
  color: var(--ui-ink-faint);
}

.profile-fact dd {
  margin: 0;
  color: var(--ui-ink);
  overflow-wrap: anywhere;
}

.profile-form {
  display: grid;
  gap: 10px;
}

.profile-notice {
  margin: 0;
  color: var(--theme-primary-deep);
  font-size: var(--text-base);
}
</style>
