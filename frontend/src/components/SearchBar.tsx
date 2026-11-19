import { useEffect, useRef, useState } from 'react'
import { Search, X } from 'lucide-react'
import { Spinner } from '@/components/ui/spinner'
import { cn } from '@/lib/utils'

interface Props {
  initial?: string
  onSubmit: (q: string) => void
  autoFocus?: boolean
  size?: 'lg' | 'sm'
  loading?: boolean
  placeholder?: string
}

/**
 * 搜索输入框 —— 主页大框(lg) / 顶栏小框(sm)。
 * 快捷键:全局按 `/` 聚焦(输入框外),Esc 清空并失焦。
 */
export default function SearchBar({ initial = '', onSubmit, autoFocus, size = 'lg', loading, placeholder = '搜索…' }: Props) {
  const [q, setQ] = useState(initial)
  const ref = useRef<HTMLInputElement>(null)
  const big = size === 'lg'

  // initial 变化时同步(URL 导航回填,如返回结果页)
  useEffect(() => setQ(initial), [initial])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable) return
      e.preventDefault()
      ref.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (q.trim()) onSubmit(q.trim())
      }}
      className={cn(
        'flex w-full items-center gap-2 rounded-md border border-line bg-bg-1 transition-colors focus-within:border-signal',
        big ? 'px-5 py-4' : 'px-3.5 py-2.5',
      )}
    >
      {loading ? (
        <Spinner className={big ? 'size-5' : 'size-4'} />
      ) : (
        <Search className={cn('shrink-0 text-ink-2', big ? 'size-5' : 'size-4')} strokeWidth={2} />
      )}
      <input
        ref={ref}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            if (q) setQ('')
            else (e.target as HTMLInputElement).blur()
          }
        }}
        autoFocus={autoFocus}
        placeholder={placeholder}
        aria-label="搜索"
        className={cn('w-full bg-transparent text-ink-0 outline-none placeholder:text-ink-2', big ? 'text-[17px]' : 'text-[14px]')}
      />
      {q ? (
        <button type="button" onClick={() => setQ('')} className="shrink-0 text-ink-2 transition-colors hover:text-ink-0" aria-label="清空">
          <X className="size-4" />
        </button>
      ) : (
        !big && (
          <kbd className="hidden shrink-0 rounded border border-line px-1.5 font-mono text-[10px] text-ink-2/60 sm:block">/</kbd>
        )
      )}
    </form>
  )
}
