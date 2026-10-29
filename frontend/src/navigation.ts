import { Grid, Connection, Collection, VideoPlay, EditPen, CircleCheck, Monitor, Document } from '@element-plus/icons-vue'

export const navigationGroups = [
  { label: '工作区', items: [
    { path: '/workspace', label: '项目概览', hint: '查看运行概况与实验起点', icon: Grid },
    { path: '/targets', label: '对象与版本', hint: '核对冻结版本与配置', icon: Connection },
    { path: '/library', label: '开发用例库', hint: '查找已审核的用例和预期', icon: Collection },
    { path: '/workbench', label: '测试工作台', hint: '配置实验并跟进执行', icon: VideoPlay },
  ] },
  { label: '质量与审阅', items: [
    { path: '/cases', label: 'AI生成与审核', hint: '查看候选并逐条审核', icon: EditPen },
    { path: '/judging', label: '辅助评价与校准', hint: '查看独立标签与评价分歧', icon: CircleCheck },
    { path: '/reports', label: '缺陷与报告', hint: '复核缺陷、对比并导出报告', icon: Document },
  ] },
  { label: '管理', items: [{ path: '/environment', label: '工程环境', hint: '检查服务与模型角色配置', icon: Monitor }] },
]

export function navigationPath(path: string) {
  if (path.startsWith('/runs')) return '/workbench'
  if (path.startsWith('/semantic') || path.startsWith('/judging')) return '/judging'
  if (path.startsWith('/cases')) return '/cases'
  return path
}

export function pageTitle(path: string) {
  if (path.startsWith('/runs') || path === '/workbench') return '测试工作台'
  if (path.startsWith('/judging') || path.startsWith('/semantic')) return '辅助评价与校准'
  if (path.startsWith('/cases')) return 'AI生成与审核'
  return ({ '/targets': '对象与版本', '/library': '开发用例库', '/environment': '工程环境', '/reports': '缺陷与报告' } as Record<string,string>)[path] ?? '项目概览'
}
