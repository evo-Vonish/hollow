import * as React from 'react'
import { cn } from '@/lib/utils'

/** 文本输入(过滤弹层/高级设置用,非主搜索框) */
const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        'flex h-8 w-full rounded-md border border-line bg-bg-0 px-2.5 text-[13px] text-ink-0 outline-none transition-colors placeholder:text-ink-2/60 focus:border-signal disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  ),
)
Input.displayName = 'Input'

export { Input }
