import { cn } from '@/lib/utils'

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('animate-pulse rounded bg-bg-2', className)} {...props} />
}

/** 结果列表骨架(Results/Research 共用) */
export function ResultSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-5" aria-label="加载中">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i}>
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="mt-2 h-3 w-1/4" />
          <Skeleton className="mt-2 h-3 w-full" />
        </div>
      ))}
    </div>
  )
}
