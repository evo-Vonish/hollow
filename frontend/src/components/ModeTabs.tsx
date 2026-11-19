import { Link } from 'react-router-dom'
import { FlaskConical, Search } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Props {
  /** 当前模式 */
  mode: 'search' | 'research'
  /** 保留现有查询串构造目标 URL */
  buildUrl: (mode: 'search' | 'research') => string
}

/** 模式 tab(主流引擎 "全部|图片|新闻" 语义):搜索 ↔ 深研 */
export default function ModeTabs({ mode, buildUrl }: Props) {
  const items = [
    { id: 'search' as const, label: '搜索', icon: Search },
    { id: 'research' as const, label: '深研', icon: FlaskConical },
  ]
  return (
    <div className="flex shrink-0 rounded-md border border-line p-0.5" role="tablist" aria-label="模式">
      {items.map((it) => {
        const active = mode === it.id
        const Icon = it.icon
        return (
          <Link
            key={it.id}
            to={buildUrl(it.id)}
            role="tab"
            aria-selected={active}
            className={cn(
              'flex items-center gap-1.5 rounded px-3 py-1 text-[12px] transition-colors',
              active ? 'bg-bg-2 text-ink-0' : 'text-ink-2 hover:text-ink-1',
            )}
          >
            <Icon className="size-3.5" />
            {it.label}
          </Link>
        )
      })}
    </div>
  )
}
