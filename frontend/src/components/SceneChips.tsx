import { SCENES } from '@/lib/sdk'
import { cn } from '@/lib/utils'

interface Props {
  value: string
  onChange: (scene: string) => void
  className?: string
  /** 顶栏横向滚动行用:不换行、不收缩 */
  nowrap?: boolean
}

/** 场景快捷 chips(单选,主流引擎式 tab 语义)——对应后端 9 场景 + 默认集 */
export default function SceneChips({ value, onChange, className, nowrap }: Props) {
  return (
    <div
      className={cn('flex items-center gap-1.5', nowrap ? 'shrink-0 flex-nowrap' : 'flex-wrap', className)}
      role="tablist"
      aria-label="搜索场景"
    >
      {SCENES.map((s) => {
        const active = s.id === value
        return (
          <button
            key={s.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(s.id)}
            className={cn(
              'rounded-full border px-3 py-1 font-mono text-[12px] transition-colors',
              active
                ? 'border-signal bg-signal/10 text-signal'
                : 'border-line bg-bg-1 text-ink-2 hover:border-line-strong hover:text-ink-1',
            )}
          >
            {s.label}
          </button>
        )
      })}
    </div>
  )
}
