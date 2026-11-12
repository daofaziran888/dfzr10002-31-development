/** 审计视角：部门与角色由请求头传给后端，越权判定与脱敏都以后端结果为准。 */
export type AuditDepartment = { code: string; label: string }

export const AUDIT_DEPARTMENTS: AuditDepartment[] = [
  { code: 'D-ANALYTICS', label: '数据分析部（数据归属部门）' },
  { code: 'D-DATA', label: '数据治理部（共享只读）' },
  { code: 'D-FINANCE', label: '财务部（无归属，演示越权拒绝）' },
  { code: 'D-OPS', label: '运营部（无归属）' },
]

export const AUDIT_ROLES = [
  { code: 'auditor', label: '审计员' },
  { code: 'auditor_admin', label: '合规审计管理员' },
  { code: 'guest', label: '只读访客' },
]

export const DEFAULT_DEPARTMENT = 'D-ANALYTICS'
export const DEFAULT_ROLE = 'auditor'

export function auditHeaders(department: string, role: string): Record<string, string> {
  return { 'X-Audit-Dept': department, 'X-Audit-Role': role }
}
