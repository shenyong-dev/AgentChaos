export const roleLabels: Record<string, string> = {
  admin: '管理员', operator: '操作员', reviewer: '审核员', user: '普通用户',
}
export const roleLabel = (role?: string) => role && roleLabels[role] || '未登录'
export const canOperate = (role?: string) => role === 'admin' || role === 'operator'
export const canReview = (role?: string) => role === 'admin' || role === 'reviewer'
export const permitsAny = (role: string, allowed: string[]) => allowed.some(
  required => role === required || (role === 'admin' && ['operator', 'reviewer'].includes(required)),
)
