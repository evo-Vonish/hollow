import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** 空态/错误态卡片(全站统一诚实卡) */
export function EmptyState({
  icon,
  title,
  detail,
  children,
  tone = 'default',
  className,
}: {
  icon?: ReactNode
  title: string
  detail?: string
  children?: ReactNode
  tone?: 'default' | 'warn' | 'danger'
  className?: string
}) {
  const toneCls = tone === 'warn' ? 'text-amber' : tone === 'danger' ? 'text-danger' : 'text-ink-0'
  return (
    <div className={cn('rounded-md border border-line bg-bg-1 p-6', className)}>
      <div className={cn('flex items-center gap-2 font-mono text-[13px]', toneCls)}>
        {icon}
        {title}
      </div>
      {detail && <p className="mt-2 break-all font-mono text-[12px] leading-relaxed text-ink-2">{detail}</p>}
      {children && <div className="mt-4 flex flex-wrap gap-3">{children}</div>}
    </div>
  )
}
