import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 font-mono text-[10px] leading-4 transition-colors',
  {
    variants: {
      variant: {
        default: 'border-line text-ink-2',
        signal: 'border-signal/40 bg-signal/10 text-signal',
        cyan: 'border-cyan/40 bg-cyan/10 text-cyan',
        amber: 'border-amber/40 bg-amber/10 text-amber',
        danger: 'border-danger/40 bg-danger/10 text-danger',
        solid: 'border-signal bg-signal text-[#06281D] font-semibold',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}
