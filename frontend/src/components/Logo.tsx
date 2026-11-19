import { cn } from '@/lib/utils'

/**
 * HOLLOW 品牌标 —— 中空环 + 芯点。
 * 语义:环 = hollow(中空,品牌名);芯点 = 环中寻得之物(搜索/研究找到的结果)。
 * 几何、单墨色、16px 小尺寸下依然清晰;与 favicon.svg 同稿。
 */
export function LogoMark({ size = 20, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      className={className}
    >
      <circle cx="16" cy="16" r="10.5" stroke="currentColor" strokeWidth="3" />
      <circle cx="16" cy="16" r="3.5" fill="currentColor" />
    </svg>
  )
}

/** 标 + 字标组合;字标衬线墨色,安静不抢(2026-07 替换旧描边字标) */
export default function Logo({
  size = 'md',
  className,
}: {
  size?: 'sm' | 'md' | 'lg'
  className?: string
}) {
  const mark = size === 'lg' ? 30 : size === 'md' ? 20 : 17
  const text =
    size === 'lg' ? 'text-[30px]' : size === 'md' ? 'text-[19px]' : 'text-[16px]'
  return (
    <span className={cn('inline-flex items-center gap-2 text-signal', className)}>
      <LogoMark size={mark} />
      <span className={cn('font-display font-bold leading-none tracking-tight text-ink-0', text)}>
        HOLLOW
      </span>
    </span>
  )
}
