/** 展示层纯函数(零依赖,可单测) */

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}

/** host 去 www. 前缀(展示用) */
export function shortHost(url: string): string {
  return hostOf(url).replace(/^www\./, '')
}

/** 站点图标字母(取 host 首字符大写) */
export function hostInitial(url: string): string {
  return (shortHost(url)[0] ?? '?').toUpperCase()
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function fmtInt(n: number | null | undefined): string {
  return n == null ? '—' : n.toLocaleString('en-US')
}

export function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + '…' : s
}

/** 域过滤输入解析:逗号/空格/换行分隔,去协议去路径,去重 */
export function parseDomains(input: string): string[] {
  const out: string[] = []
  for (const raw of input.split(/[\s,;]+/)) {
    const t = raw.trim()
    if (!t) continue
    const d = t.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0].toLowerCase()
    if (d && !out.includes(d)) out.push(d)
  }
  return out
}

/** 抓取状态中文名 */
export const STATUS_LABEL: Record<string, string> = {
  ok: '成功',
  failed: '抓取失败',
  timeout: '抓取超时',
  blocked: '被目标站拦截',
  no_content: '净化不出正文',
}

/** 三档样式(全站统一) */
export const TIER_STYLE: Record<string, { label: string; cls: string }> = {
  static: { label: 'static · 静态直取', cls: 'text-signal border-signal/40 bg-signal/10' },
  dynamic: { label: 'dynamic · 浏览器渲染', cls: 'text-cyan border-cyan/40 bg-cyan/10' },
  stealthy: { label: 'stealthy · 反检测', cls: 'text-amber border-amber/40 bg-amber/10' },
}

/** research 停止原因中文名 */
export const STOP_REASON_LABEL: Record<string, string> = {
  target_reached: '凑够目标条数',
  pool_exhausted: '候选池耗尽',
  budget: '整单预算到点',
}
