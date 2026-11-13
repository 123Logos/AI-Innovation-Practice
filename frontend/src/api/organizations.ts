/**
 * 组织管理接口（批次 B5，`docs/adr/ADR-021`）。
 *
 * 三条与后端对齐的约定：
 *
 * 1. **权限是两级判断**：这里能发出请求，不代表能拿到数据——
 *    「你有没有这个权限」由后端 403 回答，「这条记录是不是你组织的」也是后端判。
 *    前端不做本地权限推断，只如实呈现错误。
 * 2. `GET /organizations/me` 在「账号没有组织」时返回 **404**，不是空对象：
 *    空对象会让页面画出「组织：（空）」，用户以为系统坏了。
 * 3. 审计日志的 `detail` 是后端原样回传的 JSON 字符串，前端**不解析成对象**——
 *    解析就等于前端替后端决定了每种 action 的 payload 形状。
 */

import { http, unwrap } from './client'
import type {
  ApiSuccess,
  AuditLogEntry,
  OrganizationMember,
  OrganizationOverview,
  UserRole,
} from './types'

/** 管理页允许授出的角色（后端 `AssignableRole`）。`platform_admin` 不在其中。 */
export type AssignableRole = Exclude<UserRole, 'platform_admin'>

export async function fetchMyOrganization(): Promise<OrganizationOverview> {
  const response = await http.get<ApiSuccess<OrganizationOverview>>('/organizations/me')
  return unwrap(response)
}

export async function fetchOrganizationMembers(
  organizationId: string,
): Promise<OrganizationMember[]> {
  const response = await http.get<ApiSuccess<{ items: OrganizationMember[] }>>(
    `/organizations/${organizationId}/members`,
  )
  return unwrap(response).items
}

export async function changeMemberRole(
  organizationId: string,
  userId: string,
  role: AssignableRole,
): Promise<void> {
  await http.patch(`/organizations/${organizationId}/members/${userId}`, { role })
}

export interface AuditLogPage {
  items: AuditLogEntry[]
  total: number
}

export async function fetchAuditLogs(
  organizationId: string,
  options: { limit?: number; offset?: number } = {},
): Promise<AuditLogPage> {
  const response = await http.get<ApiSuccess<AuditLogPage>>(
    `/organizations/${organizationId}/audit-logs`,
    { params: { limit: options.limit ?? 50, offset: options.offset ?? 0 } },
  )
  return unwrap(response)
}


/* ------------------------- 邀请成员（B6 / ADR-023） ------------------------- */

/** 后端 `InvitationRead`：一张邀请码的当前状态。 */
export interface OrganizationInvitation {
  id: string
  organization_id: string
  code: string
  role: 'student' | 'teacher'
  max_uses: number
  used_count: number
  expires_at: string
  revoked_at: string | null
  created_at: string
  /** 兑换过这张码的用户名（第 18 轮裁定 ④：发码人要能追溯）。 */
  redeemed_by: string[]
}

export interface InvitationCreatePayload {
  role: 'student' | 'teacher'
  max_uses: number
  ttl_days: number
}

export interface InvitationRedeemResult {
  organization: { id: string; code: string; name: string; kind: string }
  role: UserRole
}

export async function fetchInvitations(
  organizationId: string,
): Promise<OrganizationInvitation[]> {
  const response = await http.get<ApiSuccess<{ items: OrganizationInvitation[] }>>(
    `/organizations/${organizationId}/invitations`,
  )
  return unwrap(response).items
}

export async function createInvitation(
  organizationId: string,
  payload: InvitationCreatePayload,
): Promise<OrganizationInvitation> {
  const response = await http.post<ApiSuccess<{ invitation: OrganizationInvitation }>>(
    `/organizations/${organizationId}/invitations`,
    payload,
  )
  return unwrap(response).invitation
}

export async function revokeInvitation(
  organizationId: string,
  invitationId: string,
): Promise<void> {
  await http.delete(`/organizations/${organizationId}/invitations/${invitationId}`)
}

/** 凭码加入组织。这是「还没有组织」的账号唯一需要调用的邀请端点。 */
export async function redeemInvitation(code: string): Promise<InvitationRedeemResult> {
  const response = await http.post<ApiSuccess<InvitationRedeemResult>>('/invitations/redeem', {
    code,
  })
  return unwrap(response)
}
