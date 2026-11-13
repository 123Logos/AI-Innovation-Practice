<script setup lang="ts">
/**
 * 组织管理（批次 B5，`docs/adr/ADR-021`）。
 *
 * 这一页要回答三个问题，顺序不能反：
 *
 * 1. **我在哪个组织、我是什么角色**——没有组织就明确说「还没有加入」，不画空表格；
 * 2. **这个组织有谁**——学生没有查看权限，这里不假装能看（权限由后端 403 回答）；
 * 3. **谁在什么时候改了什么**——审计记录只给学校管理员看。
 *
 * 三条刻意的做法：
 *
 * - **不在前端推断权限**：`canManage` 只用于「显不显示控件」，真正的拦截在后端。
 *   前端猜权限的坏处是把 403 变成「按钮点不动且没有解释」；
 * - **404 不是错误**：`GET /organizations/me` 在「没有组织」时返回 404（ADR-021 §2.6），
 *   把它当成空状态，而不是弹一条红色错误——用户没做错任何事；
 * - **改角色失败要说清为什么**：后端那句话（例如「不能修改自己的角色」）原样上屏。
 */

import { computed, onMounted, ref } from 'vue'

import { isApiError } from '@/api'
import {
  changeMemberRole,
  createInvitation,
  fetchAuditLogs,
  fetchInvitations,
  fetchMyOrganization,
  fetchOrganizationMembers,
  redeemInvitation,
  revokeInvitation,
  type AssignableRole,
  type OrganizationInvitation,
} from '@/api/organizations'
import type { AuditLogEntry, OrganizationMember, OrganizationOverview, UserRole } from '@/api/types'
import { formatDateTime } from '@/utils/datetime'

const loading = ref(true)
const errorMessage = ref('')
const membersError = ref('')
const auditError = ref('')
const roleError = ref('')

const overview = ref<OrganizationOverview | null>(null)
const members = ref<OrganizationMember[]>([])
const auditLogs = ref<AuditLogEntry[]>([])

/** 后端 404 = 「你的账号还没有加入组织」，是空状态而不是故障。 */
const hasNoOrganization = ref(false)

const ROLE_LABELS: Record<UserRole, string> = {
  student: '学生',
  teacher: '教师',
  school_admin: '学校管理员',
  platform_admin: '平台管理员',
}

/** 可授出的角色（`platform_admin` 只能由运维在库里授予，后端会拒绝）。 */
const ASSIGNABLE_ROLES: { value: AssignableRole; label: string }[] = [
  { value: 'student', label: '学生' },
  { value: 'teacher', label: '教师' },
  { value: 'school_admin', label: '学校管理员' },
]

const ACTION_LABELS: Record<string, string> = {
  'user.register': '注册账号',
  'user.login': '登录',
  'organization.create': '创建组织',
  'member.join': '加入组织',
  'member.role_change': '调整角色',
  'invitation.create': '生成邀请码',
  'invitation.redeem': '凭邀请码加入组织',
  'invitation.revoke': '撤回邀请码',
}

const canManage = computed(() => overview.value?.role === 'school_admin')
const canReadAudit = computed(() => canManage.value)

function roleLabel(role: UserRole): string {
  return ROLE_LABELS[role] ?? role
}

function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action
}

function messageOf(error: unknown, fallback: string): string {
  if (isApiError(error)) return error.message
  if (error instanceof Error) return error.message
  return fallback
}

async function loadMembers(organizationId: string): Promise<void> {
  membersError.value = ''
  try {
    members.value = await fetchOrganizationMembers(organizationId)
  } catch (error) {
    // 学生没有查看成员名单的权限：这不是「加载失败」，如实说清谁才能看。
    membersError.value = isApiError(error) && error.status === 403
      ? '只有教师与学校管理员可以查看成员名单。'
      : messageOf(error, '成员名单暂时无法读取，请稍后重试。')
  }
}

async function loadAuditLogs(organizationId: string): Promise<void> {
  auditError.value = ''
  try {
    const page = await fetchAuditLogs(organizationId, { limit: 20 })
    auditLogs.value = page.items
  } catch (error) {
    auditError.value = messageOf(error, '操作记录暂时无法读取，请稍后重试。')
  }
}

/* --------------------------- 邀请成员（B6 / ADR-023） --------------------------- */

const invitations = ref<OrganizationInvitation[]>([])
const invitationError = ref('')
const inviteRole = ref<'student' | 'teacher'>('student')
const inviteMaxUses = ref(1)
const inviteTtlDays = ref(7)
const creatingInvitation = ref(false)

const redeemCode = ref('')
const redeemError = ref('')
const redeemNotice = ref('')
const redeeming = ref(false)

async function loadInvitations(organizationId: string): Promise<void> {
  // 只有学校管理员能读邀请列表；学生/教师连请求都不发，后端也不该收到。
  if (!canManage.value) return
  invitationError.value = ''
  try {
    invitations.value = await fetchInvitations(organizationId)
  } catch (error) {
    invitationError.value = messageOf(error, '邀请记录暂时无法读取，请稍后重试。')
  }
}

async function handleCreateInvitation(): Promise<void> {
  if (!overview.value || creatingInvitation.value) return
  creatingInvitation.value = true
  invitationError.value = ''
  try {
    await createInvitation(overview.value.organization.id, {
      role: inviteRole.value,
      max_uses: inviteMaxUses.value,
      ttl_days: inviteTtlDays.value,
    })
    await loadInvitations(overview.value.organization.id)
  } catch (error) {
    invitationError.value = messageOf(error, '邀请码没有生成，请稍后重试。')
  } finally {
    creatingInvitation.value = false
  }
}

async function handleRevoke(invitation: OrganizationInvitation): Promise<void> {
  if (!overview.value) return
  invitationError.value = ''
  try {
    await revokeInvitation(overview.value.organization.id, invitation.id)
    await loadInvitations(overview.value.organization.id)
  } catch (error) {
    invitationError.value = messageOf(error, '邀请码没有撤回，请稍后重试。')
  }
}

async function handleRedeem(): Promise<void> {
  const code = redeemCode.value.trim()
  if (!code || redeeming.value) return
  redeeming.value = true
  redeemError.value = ''
  redeemNotice.value = ''
  try {
    const result = await redeemInvitation(code)
    redeemNotice.value = `已加入「${result.organization.name}」，正在刷新…`
    await load()
  } catch (error) {
    // 失败要说人话、且必须上屏（守则 C3）；后端四种不可用给的是同一句话。
    redeemError.value = messageOf(error, '邀请码没有生效，请核对后重试。')
  } finally {
    redeeming.value = false
  }
}

async function load(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  hasNoOrganization.value = false
  try {
    const data = await fetchMyOrganization()
    overview.value = data
    await Promise.all([loadMembers(data.organization.id), loadAuditLogs(data.organization.id)])
    await loadInvitations(data.organization.id)
  } catch (error) {
    if (isApiError(error) && error.status === 404) {
      hasNoOrganization.value = true
      overview.value = null
    } else {
      errorMessage.value = messageOf(error, '组织信息暂时无法读取，请稍后重试。')
    }
  } finally {
    loading.value = false
  }
}

async function handleRoleChange(member: OrganizationMember, event: Event): Promise<void> {
  const next = (event.target as HTMLSelectElement).value as AssignableRole
  if (!overview.value || next === member.role) return
  roleError.value = ''
  try {
    await changeMemberRole(overview.value.organization.id, member.user_id, next)
    await load()
  } catch (error) {
    roleError.value = messageOf(error, '角色没有改成功，请稍后重试。')
    // 失败时把下拉框拨回真实值，否则界面会显示一个并没有生效的角色。
    ;(event.target as HTMLSelectElement).value = member.role
  }
}

onMounted(load)
</script>

<template>
  <section class="org-page">
    <header class="page-head">
      <h1>组织管理</h1>
      <p class="page-sub">查看你所在的组织、成员与操作记录。</p>
    </header>

    <p v-if="errorMessage" class="notice notice--error" role="alert">{{ errorMessage }}</p>
    <p v-if="roleError" class="notice notice--error" role="alert">{{ roleError }}</p>

    <p v-if="loading" class="notice">正在读取组织信息…</p>

    <template v-else-if="overview">
      <section class="card">
        <div class="card-head">
          <h2>{{ overview.organization.name }}</h2>
          <span class="tag">我的角色：{{ roleLabel(overview.role) }}</span>
        </div>
        <dl class="meta">
          <div class="meta-item">
            <dt>组织码</dt>
            <dd>{{ overview.organization.code }}</dd>
          </div>
          <div class="meta-item">
            <dt>成员人数</dt>
            <dd>{{ overview.member_count }} 人</dd>
          </div>
        </dl>
      </section>

      <section class="card">
        <div class="card-head">
          <h2>成员</h2>
          <span v-if="!canManage" class="hint">只有学校管理员可以调整成员角色。</span>
        </div>
        <p v-if="membersError" class="notice notice--error" role="alert">{{ membersError }}</p>
        <table v-else-if="members.length" class="table">
          <thead>
            <tr>
              <th scope="col">用户名</th>
              <th scope="col">邮箱</th>
              <th scope="col">角色</th>
              <th scope="col">加入时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="member in members" :key="member.user_id">
              <td>{{ member.username }}</td>
              <td>{{ member.email ?? '未填写' }}</td>
              <td>
                <select
                  v-if="canManage"
                  class="role-select"
                  :value="member.role"
                  :aria-label="`调整 ${member.username} 的角色`"
                  @change="handleRoleChange(member, $event)"
                >
                  <option
                    v-for="option in ASSIGNABLE_ROLES"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ option.label }}
                  </option>
                </select>
                <span v-else>{{ roleLabel(member.role) }}</span>
              </td>
              <td class="mono">{{ formatDateTime(member.joined_at) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty">这个组织还没有成员。</p>
      </section>

      <section v-if="canManage" class="card">
        <div class="card-head">
          <h2>邀请成员</h2>
          <span class="hint">邀请码可设有效期与可用次数，可随时撤回。</span>
        </div>
        <p v-if="invitationError" class="notice notice--error" role="alert">{{ invitationError }}</p>
        <div class="invite-form">
          <label class="field">
            <span>加入后的角色</span>
            <select v-model="inviteRole" class="input">
              <option value="student">学生</option>
              <option value="teacher">教师</option>
            </select>
          </label>
          <label class="field">
            <span>可用次数</span>
            <input v-model.number="inviteMaxUses" class="input" type="number" min="1" max="100" />
          </label>
          <label class="field">
            <span>有效天数</span>
            <input v-model.number="inviteTtlDays" class="input" type="number" min="1" max="30" />
          </label>
          <button
            class="button"
            type="button"
            :disabled="creatingInvitation"
            @click="handleCreateInvitation"
          >
            {{ creatingInvitation ? '正在生成…' : '生成邀请码' }}
          </button>
        </div>
        <ul v-if="invitations.length" class="invite-list">
          <li v-for="item in invitations" :key="item.id" class="invite-item">
            <span class="invite-code mono">{{ item.code }}</span>
            <span class="hint">
              {{ roleLabel(item.role) }} · 已用 {{ item.used_count }}/{{ item.max_uses }} 次 ·
              {{ formatDateTime(item.expires_at) }} 前有效
            </span>
            <span v-if="item.redeemed_by.length" class="hint invite-redeemers">
              已被 {{ item.redeemed_by.join('、') }} 兑换
            </span>
            <span v-else class="hint">还没有人使用</span>
            <span v-if="item.revoked_at" class="tag">已撤回</span>
            <button v-else class="button button--quiet" type="button" @click="handleRevoke(item)">
              撤回
            </button>
          </li>
        </ul>
        <p v-else class="empty">还没有生成过邀请码。</p>
      </section>

      <section v-if="canReadAudit" class="card">
        <div class="card-head">
          <h2>操作记录</h2>
          <span class="hint">最近 20 条，新的在前。</span>
        </div>
        <p v-if="auditError" class="notice notice--error" role="alert">{{ auditError }}</p>
        <ul v-else-if="auditLogs.length" class="log-list">
          <li v-for="entry in auditLogs" :key="entry.id" class="log-item">
            <span class="log-action">{{ actionLabel(entry.action) }}</span>
            <span class="log-time mono">{{ formatDateTime(entry.created_at) }}</span>
            <span v-if="entry.result !== 'ok'" class="log-result">被拒绝</span>
          </li>
        </ul>
        <p v-else class="empty">还没有操作记录。</p>
      </section>
    </template>

    <div v-else-if="hasNoOrganization" class="empty-state">
      <p class="empty-title">你的账号还没有加入任何组织</p>
      <p class="empty-body">
        向学校管理员索取邀请码，填在下面即可加入；加入后这一页会显示成员与操作记录。
      </p>
      <form class="redeem-form" @submit.prevent="handleRedeem">
        <label class="field">
          <span>邀请码</span>
          <input
            v-model="redeemCode"
            class="input"
            type="text"
            maxlength="64"
            placeholder="粘贴学校管理员给你的邀请码"
          />
        </label>
        <button class="button" type="submit" :disabled="redeeming || !redeemCode.trim()">
          {{ redeeming ? '正在加入…' : '加入组织' }}
        </button>
      </form>
      <p v-if="redeemError" class="notice notice--error" role="alert">{{ redeemError }}</p>
      <p v-if="redeemNotice" class="notice" role="status">{{ redeemNotice }}</p>
    </div>
  </section>
</template>

<style scoped>
.org-page { display: flex; flex-direction: column; gap: var(--space-4); max-width: 960px; }
.page-head h1 { margin: 0; color: var(--ui-ink); font-size: var(--text-2xl); font-weight: 700; }
.page-sub { margin: 6px 0 0; color: var(--ui-ink-muted); font-size: var(--text-base); }
.card {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  padding: 18px 20px;
  background: var(--ui-surface);
}
.card-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px; }
.card-head h2 { margin: 0; color: var(--ui-ink); font-size: var(--text-lg); font-weight: 650; }
.tag {
  border: 1px solid var(--theme-rule-soft);
  border-radius: var(--ui-radius);
  padding: 3px 10px;
  color: var(--theme-primary-strong);
  background: var(--theme-bg-light);
  font-size: var(--text-xs);
  font-weight: 600;
}
.hint { color: var(--ui-ink-faint); font-size: var(--text-xs); }
.meta { display: flex; flex-wrap: wrap; gap: 28px; margin: 14px 0 0; }
.meta-item { display: flex; flex-direction: column; gap: 4px; }
.meta-item dt { color: var(--ui-ink-faint); font-size: var(--text-xs); }
.meta-item dd { margin: 0; color: var(--ui-ink); font-size: var(--text-lg); font-weight: 600; }
.table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: var(--text-base); }
.table th, .table td { border-bottom: 1px solid var(--ui-border); padding: 10px 8px; text-align: left; }
.table th { color: var(--ui-ink-faint); font-size: var(--text-xs); font-weight: 600; }
.role-select {
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 5px 8px;
  color: var(--ui-ink);
  background: var(--ui-surface);
  font: inherit;
  font-size: var(--text-base);
}
.role-select:focus-visible { outline: 2px solid var(--theme-rule); outline-offset: 1px; }
.mono { color: var(--ui-ink-muted); font-family: "SF Mono", Consolas, monospace; font-size: var(--text-sm); }
.notice {
  margin: 0;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  padding: 10px 14px;
  color: var(--ui-ink-muted);
  background: var(--ui-surface-sunken);
  font-size: var(--text-base);
}
.notice--error { border-color: var(--ui-warning-border); color: var(--ui-warning); background: var(--ui-warning-soft); }
.empty { margin: 12px 0 0; color: var(--ui-ink-faint); font-size: var(--text-base); }
.empty-state {
  border: 1px dashed var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 28px;
  text-align: center;
  background: var(--ui-surface);
}
.empty-title { margin: 0; color: var(--ui-ink); font-size: var(--text-lg); font-weight: 650; }
.empty-body { margin: 8px auto 0; max-width: 460px; color: var(--ui-ink-muted); font-size: var(--text-base); line-height: 1.7; }
.log-list { margin: 12px 0 0; padding: 0; list-style: none; }
.log-item {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 12px;
  border-bottom: 1px solid var(--ui-border);
  padding: 8px 0;
  font-size: var(--text-base);
}
.log-action { color: var(--ui-ink); font-weight: 600; }
.log-time { margin-left: auto; }
.log-result { border: 1px solid var(--ui-warning-border); border-radius: var(--ui-radius); padding: 1px 8px; color: var(--ui-warning); font-size: var(--text-xs); }

.invite-form { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; margin-top: 14px; }
.redeem-form { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; margin-top: 14px; }
.field { display: flex; flex-direction: column; gap: 4px; color: var(--ui-ink-faint); font-size: var(--text-xs); }
.input {
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius);
  padding: 6px 9px;
  color: var(--ui-ink);
  background: var(--ui-surface);
  font: inherit;
  font-size: var(--text-base);
}
.button {
  border: 1px solid var(--theme-primary);
  border-radius: var(--ui-radius);
  padding: 7px 14px;
  color: var(--ui-surface);
  background: var(--theme-primary);
  font: inherit;
  font-size: var(--text-base);
  cursor: pointer;
}
.button:disabled { opacity: 0.6; cursor: not-allowed; }
.button--quiet {
  color: var(--theme-primary-strong);
  background: var(--theme-bg-light);
  border-color: var(--theme-rule-soft);
}
.invite-list { list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.invite-item {
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
  border-bottom: 1px solid var(--ui-border); padding-bottom: 8px;
}
.invite-code { color: var(--ui-ink); font-size: var(--text-base); font-weight: 600; }
.invite-redeemers { color: var(--theme-primary-strong); }
</style>
