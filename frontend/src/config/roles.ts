/**
 * 角色码 → 中文名（一处定义，两处消费：组织管理页的成员表 / 个人资料弹窗的「身份角色」）。
 *
 * 键与后端 `app/core/permissions.py::UserRole` 逐项对应。写在 config 里而不是各视图里：
 * 同一个 `school_admin` 在两张界面上叫「学校管理员」和「校长」时，用户会以为这是两种身份。
 */
import type { UserRole } from '@/api/types'

export const ROLE_LABELS: Readonly<Record<UserRole, string>> = {
  student: '学生',
  teacher: '教师',
  school_admin: '学校管理员',
  platform_admin: '平台管理员',
}
