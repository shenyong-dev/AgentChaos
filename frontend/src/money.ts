// Display conversion only. Preserve the original micro-USD contracts and ledgers.
export const CNY_PER_USD = 6.7156
export const CNY_REFERENCE_DATE = '2026-11-05'
export const CNY_REFERENCE_SOURCE = 'https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do'
export const CNY_NOTE = `人民币按${CNY_REFERENCE_DATE}参考汇率1美元＝${CNY_PER_USD}元换算；非实时汇率、非实际账单。原始账本与证据单位保持不变。`
export function formatCny(usd: number | null | undefined, digits = 6): string {
  if (typeof usd !== 'number' || !Number.isFinite(usd)) return '未采集'
  return `¥${(usd * CNY_PER_USD).toFixed(digits)}`
}
export function formatMicroCny(microUsd: number | null | undefined, digits = 6): string {
  return formatCny(typeof microUsd === 'number' ? microUsd / 1e6 : undefined, digits)
}
export function cnyToMicroUsd(cny: number): number {
  return Math.round(cny / CNY_PER_USD * 1e6)
}
