import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ChevronDown, ChevronUp, CircleStop, ExternalLink, RotateCcw } from 'lucide-react'
import { ApiError, RESEARCH_MODES, researchStream, STATUS_LABEL, STOP_REASON_LABEL, TIER_STYLE, fmtMs, shortHost, truncate } from '@/lib/sdk'
import type { ResearchItem, ResearchMode, ResearchObject, SearchMeta } from '@/lib/sdk'
import AppHeader from '@/components/AppHeader'
import SceneChips from '@/components/SceneChips'
import EngineLedgerDrawer from '@/components/EngineLedgerDrawer'
import Markdown from '@/components/Markdown'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty'
import { ResultSkeleton, Skeleton } from '@/components/ui/skeleton'
import { Slider } from '@/components/ui/slider'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'

type Phase = 'connecting' | 'searching' | 'streaming' | 'done' | 'error' | 'cancelled'

const BUDGET_OPTIONS = [
  { id: '', label: '预算不限' },
  { id: '30', label: '预算 30s' },
  { id: '60', label: '预算 60s' },
  { id: '120', label: '预算 120s' },
  { id: '300', label: '预算 300s' },
]

/**
 * 深研模式 —— /v1/research SSE 流式旗舰页。
 * 事件序列:research.search.completed → n×research.item.completed → research.completed → [DONE]。
 * 可中途停止(AbortController 断流,网关侧收尾取消未完成抓取)。
 */
export default function Research() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const q = params.get('q') ?? ''
  const scene = params.get('scene') ?? ''
  const rmode = (params.get('rmode') ?? 'balanced') as ResearchMode
  const topN = Math.max(1, Math.min(20, Number(params.get('n') ?? '5') || 5))
  const budget = params.get('budget') ?? ''
  // 页面资产与正文图片开关(2026-07-30):assets=外链/媒体清单,imgs=正文图片引用
  const assets = params.get('assets') === '1'
  const imgs = params.get('imgs') === '1'

  const [phase, setPhase] = useState<Phase>('connecting')
  const [searchMeta, setSearchMeta] = useState<SearchMeta | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [items, setItems] = useState<ResearchItem[]>([])
  const [finalObj, setFinalObj] = useState<ResearchObject | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const handleRef = useRef<{ cancel: () => void } | null>(null)
  /** 重跑计数:URL 参数不变时 setParams 是 no-op,必须靠状态翻转触发重新开流 */
  const [runTick, setRunTick] = useState(0)

  const live = phase === 'connecting' || phase === 'searching' || phase === 'streaming'

  /* 开流/重开:q/scene/rmode/topN/budget 任一变化即整单重启 */
  useEffect(() => {
    if (!q) return
    setPhase('connecting')
    setSearchMeta(null)
    setSelected(null)
    setItems([])
    setFinalObj(null)
    setError(null)

    const handle = researchStream(
      {
        query: q,
        scenes: scene ? [scene] : [],
        mode: rmode,
        top_n: topN,
        budget: budget ? Number(budget) : undefined,
        include_links: assets || undefined,
        include_media: assets || undefined,
        include_images: imgs || undefined,
      },
      (evt) => {
        if (evt.event === 'research.search.completed') {
          setSearchMeta(evt.search)
          setSelected(evt.selected)
          setPhase('streaming')
        } else if (evt.event === 'research.item.completed') {
          setItems((prev) => [...prev, evt.item])
        } else if (evt.event === 'research.completed') {
          setFinalObj(evt.research)
          setPhase('done')
        }
      },
    )
    handleRef.current = handle
    handle.done.catch((e) => {
      const err = e instanceof ApiError ? e : new ApiError('流中断', 'stream_error', 0)
      if (err.code === 'cancelled') setPhase((p) => (p === 'done' ? p : 'cancelled'))
      else {
        setError(err)
        setPhase('error')
      }
    })
    return () => handle.cancel()
  }, [q, scene, rmode, topN, budget, assets, imgs, runTick])

  /* 计时(仅进行中) */
  useEffect(() => {
    if (!live) return
    const t0 = Date.now()
    setElapsed(0)
    const t = setInterval(() => setElapsed(Date.now() - t0), 200)
    return () => clearInterval(t)
  }, [live, q, scene, rmode, topN, budget, assets, imgs])

  const writeParams = (patch: Record<string, string | null>) => {
    const p = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === '') p.delete(k)
      else p.set(k, v)
    }
    setParams(p)
  }

  const buildModeUrl = (mode: 'search' | 'research') => {
    const p = new URLSearchParams(params)
    if (mode === 'research') p.set('mode', 'research')
    else {
      p.delete('mode')
      p.delete('rmode')
      p.delete('n')
      p.delete('budget')
    }
    return `/?${p}`
  }

  const okCount = items.filter((i) => i.fetch_status === 'ok').length
  const fetchMeta = finalObj?.fetch

  return (
    <div className="min-h-screen">
      <AppHeader
        q={q}
        mode="research"
        buildModeUrl={buildModeUrl}
        onSearch={(next) => writeParams({ q: next })}
        searching={live}
        chips={<SceneChips value={scene} onChange={(s) => writeParams({ scene: s || null })} nowrap />}
        toolbar={
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex rounded-md border border-line p-0.5" role="radiogroup" aria-label="深研模式">
              {RESEARCH_MODES.map((m) => (
                <button
                  key={m.id}
                  role="radio"
                  aria-checked={rmode === m.id}
                  title={m.desc}
                  onClick={() => writeParams({ rmode: m.id === 'balanced' ? null : m.id })}
                  className={cn(
                    'rounded px-2.5 py-1 font-mono text-[11px] transition-colors',
                    rmode === m.id ? 'bg-bg-2 text-signal' : 'text-ink-2 hover:text-ink-1',
                  )}
                >
                  {m.id}
                </button>
              ))}
            </div>

            <label className="flex items-center gap-2 font-mono text-[11px] text-ink-2">
              正文条数 <span className="tabular w-5 text-signal">{topN}</span>
              <Slider
                className="w-28"
                value={[topN]}
                min={1}
                max={20}
                step={1}
                onValueChange={([v]) => writeParams({ n: v === 5 ? null : String(v) })}
              />
            </label>

            <Select value={budget || 'none'} onValueChange={(v) => writeParams({ budget: v === 'none' ? null : v })}>
              <SelectTrigger aria-label="整单预算">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {BUDGET_OPTIONS.map((b) => (
                  <SelectItem key={b.id || 'none'} value={b.id || 'none'}>{b.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* 资产开关(2026-07-30):外链/媒体清单 + 正文图片引用 */}
            {([
              { k: 'assets', on: assets, label: '外链/媒体', title: '抽取每条来源的外链与媒体资源清单' },
              { k: 'imgs', on: imgs, label: '正文图片', title: '正文原位置保留图片引用' },
            ] as const).map((o) => (
              <button
                key={o.k}
                onClick={() => writeParams({ [o.k]: o.on ? null : '1' })}
                title={o.title}
                aria-pressed={o.on}
                className={cn(
                  'rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors',
                  o.on ? 'border-signal/50 bg-signal/10 text-signal' : 'border-line text-ink-2 hover:text-ink-1',
                )}
              >
                {o.label}
              </button>
            ))}

            {live ? (
              <Button variant="danger" size="sm" onClick={() => handleRef.current?.cancel()}>
                <CircleStop className="size-3.5" />
                停止
              </Button>
            ) : (
              <Button variant="outline" size="sm" onClick={() => setRunTick((t) => t + 1)}>
                <RotateCcw className="size-3.5" />
                重跑
              </Button>
            )}

            <span className="font-mono text-[11px] tabular text-ink-2">{fmtMs(elapsed)}</span>
            {searchMeta && <EngineLedgerDrawer meta={searchMeta} />}
          </div>
        }
      />

      <main className="page-col py-6">
        {/* 阶段条 */}
        <PhaseBar phase={phase} selected={selected} arrived={items.length} okCount={okCount} target={topN} />

        {phase === 'searching' || phase === 'connecting' ? (
          <div className="mt-6 space-y-4">
            <div className="flex items-center gap-3 font-mono text-[12px] text-ink-2">
              <Skeleton className="h-3 w-24" />
              {phase === 'connecting' ? '建立 SSE 连接…' : '搜索召回中,引擎账目马上来…'}
            </div>
            <ResultSkeleton rows={3} />
          </div>
        ) : null}

        {phase === 'error' && error && (
          <EmptyState
            tone="warn"
            title={error.status === 429 ? '网关繁忙(在飞闸已满)' : '深研中断'}
            detail={`[${error.code}] ${error.message}${items.length ? `——已抵达的 ${items.length} 条保留在下方` : ''}`}
            className="mt-6"
          >
            <Button variant="outline" onClick={() => setRunTick((t) => t + 1)}>重试</Button>
          </EmptyState>
        )}

        {phase === 'cancelled' && (
          <EmptyState tone="default" title="已手动停止" detail={`已抵达 ${items.length} 条正文保留在下方;网关侧已收尾取消未完成抓取。`} className="mt-6">
            <Button variant="outline" onClick={() => setRunTick((t) => t + 1)}>重跑</Button>
          </EmptyState>
        )}

        {/* 汇总账目(done) */}
        {phase === 'done' && fetchMeta && (
          <div className="mt-6 rounded-md border border-line bg-bg-1 p-4">
            <p className="font-mono text-[11px] uppercase tracking-wider text-signal">
              完成 · {STOP_REASON_LABEL[fetchMeta.stopped_reason] ?? fetchMeta.stopped_reason}
            </p>
            <p className="mt-2 font-mono text-[12px] leading-relaxed text-ink-1">
              <span className="text-signal">{fetchMeta.ok} 成功</span>
              {fetchMeta.failed > 0 && <span className="text-danger"> · {fetchMeta.failed} 失败</span>}
              {fetchMeta.timeout > 0 && <span className="text-amber"> · {fetchMeta.timeout} 超时</span>}
              {fetchMeta.blocked > 0 && <span className="text-amber"> · {fetchMeta.blocked} 拦截</span>}
              {fetchMeta.no_content > 0 && <span> · {fetchMeta.no_content} 无正文</span>}
              {fetchMeta.cancelled > 0 && <span className="text-ink-2"> · {fetchMeta.cancelled} 取消</span>}
              <span className="text-ink-2"> · 池 {fetchMeta.pool} · 耗时 {fmtMs(fetchMeta.took_ms)}</span>
            </p>
          </div>
        )}

        {/* 逐条抵达的正文卡 */}
        {items.length > 0 && (
          <ol className="mt-6 space-y-4">
            {items.map((it, i) => (
              <ResearchItemCard
                key={`${it.url}-${i}`}
                item={it}
                onOpenReader={() =>
                  navigate(`/?url=${encodeURIComponent(it.url)}&title=${encodeURIComponent(it.title ?? '')}&${params}`)
                }
              />
            ))}
          </ol>
        )}

        {phase === 'done' && items.length === 0 && (
          <EmptyState className="mt-6" title="整单零产出" detail="所有候选都失败/超时/无正文——引擎账目里有逐引擎原因,换个场景或模式再试。" />
        )}
      </main>
    </div>
  )
}

/* ---------- 阶段条:连接 → 搜索 → 抓取流 → 汇总 ---------- */

function PhaseBar({ phase, selected, arrived, okCount, target }: {
  phase: Phase
  selected: number | null
  arrived: number
  okCount: number
  target: number
}) {
  const steps = useMemo(
    () => [
      { id: 'search', label: '搜索召回', done: phase !== 'connecting' && phase !== 'searching' },
      { id: 'fetch', label: `抓取净化 ${arrived}${selected != null ? `/${selected}` : ''}`, done: phase === 'done' },
      { id: 'sum', label: phase === 'done' ? '完成' : `凑够 ${target} 条即止`, done: phase === 'done' },
    ],
    [phase, selected, arrived, target],
  )
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-ink-2">
      {steps.map((s, i) => (
        <span key={s.id} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-line-strong">→</span>}
          <span className={cn('size-1.5 rounded-full', s.done ? 'bg-signal' : 'bg-ink-2/40', !s.done && phase !== 'error' && phase !== 'cancelled' && 'animate-pulse')} />
          <span className={s.done ? 'text-ink-1' : undefined}>{s.label}</span>
        </span>
      ))}
      {okCount > 0 && phase !== 'done' && <span className="text-signal">· 已凑 {okCount} 条正文</span>}
    </div>
  )
}

/* ---------- 单条正文卡:要点 + 可展开正文 + 阅读模式入口 ---------- */

function ResearchItemCard({ item: it, onOpenReader }: { item: ResearchItem; onOpenReader: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const ok = it.fetch_status === 'ok'
  const tier = TIER_STYLE[it.engine_used]

  return (
    <li className={cn('rounded-md border bg-bg-1 p-4', ok ? 'border-line' : 'border-line/60 opacity-80')}>
      {/* 头部:域名 + 档位 + 状态 + 字数 */}
      <div className="flex flex-wrap items-center gap-2">
        <a
          href={it.url}
          target="_blank"
          rel="noreferrer"
          className="flex min-w-0 items-center gap-1 font-mono text-[11px] text-ink-2 transition-colors hover:text-cyan"
        >
          <span className="truncate">{shortHost(it.url)}</span>
          <ExternalLink className="size-3 shrink-0" />
        </a>
        {it.engine && <Badge variant="default">{it.engine}</Badge>}
        {tier && <Badge className={tier.cls}>{it.engine_used}</Badge>}
        {ok ? (
          <span className="font-mono text-[11px] text-ink-2">{it.word_count ?? '—'} 字符</span>
        ) : (
          <Badge variant="amber">{STATUS_LABEL[it.fetch_status] ?? it.fetch_status}</Badge>
        )}
        {it.rank != null && <span className="ml-auto font-mono text-[10px] text-ink-2/60">rank #{it.rank + 1}</span>}
      </div>

      {/* 标题 */}
      <p className={cn('mt-2 text-[15px] leading-snug', ok ? 'text-ink-0' : 'text-ink-1')}>
        {it.title || it.url}
      </p>

      {/* 失败原因 */}
      {!ok && it.error && (
        <p className="mt-1.5 break-all font-mono text-[11px] leading-relaxed text-amber/80">{truncate(it.error, 200)}</p>
      )}

      {/* 要点(词汇抽取,无模型) */}
      {ok && it.highlights.length > 0 && (
        <ul className="mt-2.5 space-y-1 border-l-2 border-signal/30 pl-3">
          {it.highlights.slice(0, 3).map((h, i) => (
            <li key={i} className="text-[12.5px] leading-relaxed text-ink-1">{truncate(h, 180)}</li>
          ))}
        </ul>
      )}

      {/* 展开正文 + 阅读模式 */}
      {ok && (
        <div className="mt-3 flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setExpanded((v) => !v)}>
            {expanded ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
            {expanded ? '收起正文' : '展开正文'}
          </Button>
          <Button variant="outline" size="sm" onClick={onOpenReader}>
            阅读模式打开 →
          </Button>
        </div>
      )}
      {ok && expanded && (
        <div className="mt-3 max-h-[480px] overflow-y-auto rounded-md border border-line bg-bg-0 p-4">
          <Markdown>{it.content ?? ''}</Markdown>
        </div>
      )}

      {/* 页面资产(assets 开关开启且有数据才显示;2026-07-30) */}
      {ok && ((it.links?.length ?? 0) > 0 || (it.media?.length ?? 0) > 0) && (
        <details className="mt-3 rounded-md border border-line/70 bg-bg-0/60 px-3 py-2">
          <summary className="cursor-pointer select-none font-mono text-[10px] uppercase tracking-wider text-ink-2 hover:text-ink-1">
            资产 · 外链 {it.links?.length ?? 0} · 媒体 {it.media?.length ?? 0}
          </summary>
          {(it.media?.length ?? 0) > 0 && (
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {it.media!.filter((m) => m.type === 'image').slice(0, 8).map((m, i) => (
                <a key={i} href={m.url} target="_blank" rel="noreferrer">
                  <img src={m.url} alt={m.alt ?? ''} loading="lazy"
                       className="size-14 rounded border border-line object-cover"
                       onError={(e) => { (e.target as HTMLImageElement).closest('a')!.style.display = 'none' }} />
                </a>
              ))}
            </div>
          )}
          {(it.links?.length ?? 0) > 0 && (
            <ul className="mt-2.5 space-y-1">
              {it.links!.slice(0, 10).map((l, i) => (
                <li key={i} className="truncate">
                  <a href={l.url} target="_blank" rel="noreferrer"
                     className="text-[12px] text-cyan underline decoration-cyan/25 underline-offset-2 hover:decoration-cyan/70">
                    {l.text || shortHost(l.url)}
                  </a>
                  {!l.internal && <span className="ml-1.5 font-mono text-[9px] text-amber/80">↗</span>}
                </li>
              ))}
              {it.links!.length > 10 && (
                <li className="font-mono text-[10px] text-ink-2/60">…共 {it.links!.length} 条</li>
              )}
            </ul>
          )}
        </details>
      )}
    </li>
  )
}
