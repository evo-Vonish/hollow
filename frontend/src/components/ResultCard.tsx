import { Check } from 'lucide-react'
import type { SearchResultItem } from '@/lib/sdk'
import { hostInitial, prefetchFetch, READER_EXTRAS, shortHost, truncate } from '@/lib/sdk'
import { cn } from '@/lib/utils'

interface Props {
  item: SearchResultItem
  /** 点击进入阅读模式 */
  onOpen: (url: string, title: string) => void
  /** 批量选择态(不传则不显示勾选) */
  selected?: boolean
  onToggleSelect?: (url: string) => void
}

/** 搜索结果卡:hover/focus/触摸预取净化,点击秒开;批量勾选为行内流(与卡同列) */
export default function ResultCard({ item: r, onOpen, selected, onToggleSelect }: Props) {
  return (
    <li className="group flex items-start gap-3">
      {onToggleSelect && (
        <button
          type="button"
          onClick={() => onToggleSelect(r.url)}
          aria-label={selected ? '取消选择' : '选择'}
          aria-pressed={selected}
          className={cn(
            'mt-1 flex size-[18px] shrink-0 items-center justify-center rounded border transition-colors',
            selected
              ? 'border-signal bg-signal text-[#F7F5EF]'
              : 'border-line-strong bg-transparent text-transparent hover:border-ink-2',
          )}
        >
          <Check className="size-3" strokeWidth={3} />
        </button>
      )}
      <button
        onClick={() => onOpen(r.url, r.title)}
        onMouseEnter={() => prefetchFetch(r.url, READER_EXTRAS)}
        onFocus={() => prefetchFetch(r.url, READER_EXTRAS)}
        onTouchStart={() => prefetchFetch(r.url, READER_EXTRAS)}
        className="min-w-0 flex-1 text-left"
      >
        <span className="flex items-center gap-2 font-mono text-[11px] text-ink-2">
          <span className="inline-flex size-4 items-center justify-center rounded-sm bg-bg-2 text-[9px] text-ink-1">
            {hostInitial(r.url)}
          </span>
          <span className="truncate">{shortHost(r.url)}</span>
          <span className="shrink-0 text-signal/70">· {r.engine}</span>
          {r.published_date && <span className="shrink-0">· {r.published_date.slice(0, 10)}</span>}
        </span>
        <span className="mt-1.5 flex items-start gap-3">
          <span className="min-w-0 flex-1">
            <span className="block font-serif text-[18px] leading-snug text-cyan transition-colors group-hover:underline">
              {r.title || r.url}
            </span>
            {r.snippet && (
              <span className="mt-1.5 block text-[13.5px] leading-relaxed text-ink-1">
                {truncate(r.snippet, 220)}
              </span>
            )}
          </span>
          {/* 媒体模式前置:图片类结果带缩略图(2026-07-30) */}
          {(r.thumbnail || r.img_src) && (
            <img
              src={r.thumbnail ?? r.img_src ?? undefined}
              alt=""
              loading="lazy"
              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
              className="mt-0.5 size-16 shrink-0 rounded-md border border-line object-cover"
            />
          )}
        </span>
      </button>
    </li>
  )
}
