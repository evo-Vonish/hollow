import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Layers, X } from 'lucide-react'
import { ApiError, searchApiCached } from '@/lib/sdk'
import type { SearchResponse } from '@/lib/sdk'
import AppHeader from '@/components/AppHeader'
import SceneChips from '@/components/SceneChips'
import FilterBar from '@/components/FilterBar'
import type { FilterValues } from '@/components/FilterBar'
import EngineLedgerDrawer from '@/components/EngineLedgerDrawer'
import ResultCard from '@/components/ResultCard'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty'
import { ResultSkeleton } from '@/components/ui/skeleton'

type State =
  | { kind: 'loading' }
  | { kind: 'error'; error: ApiError }
  | { kind: 'ok'; data: SearchResponse }

const MAX_BATCH = 10 // 网关 FETCH_URLS_MAX

/** 结果页 —— 工具栏全参数 + 引擎账目抽屉 + 批量净化多选 */
export default function Results() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const q = params.get('q') ?? ''
  const scene = params.get('scene') ?? ''
  const page = Math.max(1, Math.min(20, Number(params.get('page') ?? '1') || 1))

  // URL ↔ 工具栏状态(URL 是唯一事实源,分享/前进后退都可复现)
  const filters: FilterValues = useMemo(
    () => ({
      time: params.get('t') ?? '',
      safe: Number(params.get('safe') ?? '0') || 0,
      lang: params.get('lang') ?? 'auto',
      include: (params.get('inc') ?? '').split(',').filter(Boolean),
      exclude: (params.get('exc') ?? '').split(',').filter(Boolean),
      engines: (params.get('eng') ?? '').split(',').filter(Boolean),
    }),
    [params],
  )

  const [state, setState] = useState<State>({ kind: 'loading' })
  const [picked, setPicked] = useState<string[]>([])
  /** 重试计数:URL 参数不变时 setParams 是 no-op,必须靠状态翻转触发重新请求 */
  const [retryTick, setRetryTick] = useState(0)

  // 查询变化即清空批量选择
  useEffect(() => setPicked([]), [q, scene, filters])

  useEffect(() => {
    if (!q) return
    let cancelled = false
    setState({ kind: 'loading' })
    searchApiCached({
      query: q,
      scenes: scene ? [scene] : [],
      engines: filters.engines.length ? filters.engines : undefined,
      language: filters.lang,
      time_range: filters.time || undefined,
      safesearch: filters.safe,
      include_domains: filters.include.length ? filters.include : undefined,
      exclude_domains: filters.exclude.length ? filters.exclude : undefined,
      page,
    })
      .then((data) => !cancelled && setState({ kind: 'ok', data }))
      .catch((e) => !cancelled && setState({ kind: 'error', error: e instanceof ApiError ? e : new ApiError('未知错误', 'unknown', 0) }))
    return () => {
      cancelled = true
    }
  }, [q, scene, filters, page, retryTick])

  /** 写 URL 参数(改动即触发重新搜索);page 变动单独走 */
  const writeParams = (next: { q?: string; scene?: string; f?: FilterValues; page?: number }) => {
    const f = next.f ?? filters
    const p = new URLSearchParams()
    p.set('q', next.q ?? q)
    const sc = next.scene ?? scene
    if (sc) p.set('scene', sc)
    if (f.time) p.set('t', f.time)
    if (f.safe) p.set('safe', String(f.safe))
    if (f.lang !== 'auto') p.set('lang', f.lang)
    if (f.include.length) p.set('inc', f.include.join(','))
    if (f.exclude.length) p.set('exc', f.exclude.join(','))
    if (f.engines.length) p.set('eng', f.engines.join(','))
    const pg = next.page ?? 1
    if (pg > 1) p.set('page', String(pg))
    setParams(p)
  }

  const buildModeUrl = (mode: 'search' | 'research') => {
    const p = new URLSearchParams(params)
    if (mode === 'research') p.set('mode', 'research')
    else p.delete('mode')
    return `/?${p}`
  }

  const readUrl = (url: string, title: string) =>
    `/?url=${encodeURIComponent(url)}&title=${encodeURIComponent(title)}&${params}`

  const togglePick = (url: string) =>
    setPicked((prev) => (prev.includes(url) ? prev.filter((u) => u !== url) : prev.length < MAX_BATCH ? [...prev, url] : prev))

  const meta = state.kind === 'ok' ? state.data.search : null
  const answers = state.kind === 'ok' ? state.data.answers : []
  const firstAnswer = answers.find((a) => a.content)

  return (
    <div className="min-h-screen">
      <AppHeader
        q={q}
        mode="search"
        buildModeUrl={buildModeUrl}
        onSearch={(next) => writeParams({ q: next })}
        searching={state.kind === 'loading'}
        chips={<SceneChips value={scene} onChange={(s) => writeParams({ scene: s })} nowrap />}
        toolbar={
          <div className="flex flex-wrap items-center gap-1.5">
            <FilterBar value={filters} onChange={(f) => writeParams({ f })} />
            {meta && <EngineLedgerDrawer meta={meta} />}
          </div>
        }
      />

      <main className="page-col py-6">
        {state.kind === 'loading' && <ResultSkeleton />}

        {state.kind === 'error' && (
          <EmptyState
            tone="warn"
            title={state.error.status === 429 ? '网关繁忙(在飞闸已满)——稍后重试' : '搜索失败'}
            detail={`[${state.error.code}] ${state.error.message}`}
          >
            <Button variant="outline" onClick={() => setRetryTick((t) => t + 1)}>重试</Button>
          </EmptyState>
        )}

        {state.kind === 'ok' && (
          <>
            {/* 账目行 */}
            <p className="mb-5 font-mono text-[11px] text-ink-2">
              {state.data.results.length} 条结果 · 引擎 {meta!.engines_used.length} 用
              {meta!.engines_failed.length > 0 && <span className="text-amber"> / {meta!.engines_failed.length} 失败</span>}
              {meta!.engines_no_results.length > 0 && ` / ${meta!.engines_no_results.length} 零结果`}
              {meta!.q_sanitized && ' · bang 已清洗'}
              {state.data.ignored_params?.length ? (
                <span className="text-amber"> · 未识别参数已回报:{state.data.ignored_params.join(', ')}</span>
              ) : null}
            </p>

            {/* infobox 即时答案(字段是 content,2026-07 修复) */}
            {firstAnswer?.content && (
              <div className="mb-6 rounded-md border border-signal/30 bg-signal/5 p-4">
                <p className="font-mono text-[11px] uppercase tracking-wider text-signal">
                  即时答案{firstAnswer.title ? ` · ${firstAnswer.title}` : ''}
                </p>
                <p className="mt-1.5 text-[14px] leading-relaxed text-ink-1">{firstAnswer.content}</p>
                {firstAnswer.url && (
                  <a href={firstAnswer.url} target="_blank" rel="noreferrer" className="mt-1.5 inline-block font-mono text-[11px] text-cyan hover:underline">
                    {firstAnswer.url} ↗
                  </a>
                )}
              </div>
            )}

            {state.data.results.length === 0 && (
              <EmptyState title="没有召回结果" detail="换个场景或关键词试试——引擎零结果/失败已在引擎账目里如实列出。" />
            )}

            {/* 结果卡(批量选择态) */}
            <ol className="space-y-6">
              {state.data.results.map((r, i) => (
                <ResultCard
                  key={`${r.url}-${i}`}
                  item={r}
                  onOpen={(url, title) => navigate(readUrl(url, title))}
                  selected={picked.includes(r.url)}
                  onToggleSelect={togglePick}
                />
              ))}
            </ol>

            {/* 分页 */}
            {state.data.results.length > 0 && (
              <div className="mt-10 flex items-center justify-center gap-3 font-mono text-[12px]">
                {page > 1 ? (
                  <Button variant="outline" onClick={() => writeParams({ page: page - 1 })}>← 上一页</Button>
                ) : (
                  <span className="px-4 py-1.5 text-ink-2/50">← 上一页</span>
                )}
                <span className="tabular text-ink-2">{page} / 20</span>
                {page < 20 ? (
                  <Button variant="outline" onClick={() => writeParams({ page: page + 1 })}>下一页 →</Button>
                ) : (
                  <span className="px-4 py-1.5 text-ink-2/50">下一页 →</span>
                )}
              </div>
            )}
          </>
        )}
      </main>

      {/* 批量净化浮条 */}
      {picked.length > 0 && (
        <div className="fixed inset-x-0 bottom-5 z-40 mx-auto flex w-fit items-center gap-3 rounded-md border border-line bg-bg-1/95 py-2 pl-4 pr-2 shadow-hover backdrop-blur">
          <span className="font-mono text-[12px] text-ink-1">
            <Layers className="mr-1.5 inline size-3.5 text-signal" />
            已选 <span className="tabular text-signal">{picked.length}</span> / {MAX_BATCH}
          </span>
          <Button
            size="sm"
            variant="primary"
            onClick={() => navigate(`/?urls=${encodeURIComponent(picked.join(','))}&back=${encodeURIComponent(`/?${params}`)}`)}
          >
            批量净化 →
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPicked([])} aria-label="清空选择">
            <X className="size-3.5" />
          </Button>
        </div>
      )}
    </div>
  )
}
