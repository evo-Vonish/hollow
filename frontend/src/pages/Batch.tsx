import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ExternalLink } from 'lucide-react'
import { ApiError, STATUS_LABEL, TIER_STYLE, fetchBatch, fmtMs, shortHost, truncate } from '@/lib/sdk'
import type { FetchResponse } from '@/lib/sdk'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty'
import { ResultSkeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

type State =
  | { kind: 'loading' }
  | { kind: 'error'; error: ApiError }
  | { kind: 'ok'; data: FetchResponse }

const MAX_BATCH = 10

/** 批量净化 —— ?urls=a,b,c(≤10),一次 /v1/fetch 数组调用,逐条状态如实入账 */
export default function Batch() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const backTo = params.get('back') ?? '/'
  const urls = useMemo(
    () => (params.get('urls') ?? '').split(',').map((u) => u.trim()).filter(Boolean).slice(0, MAX_BATCH),
    [params],
  )

  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    if (!urls.length) return
    let cancelled = false
    setState({ kind: 'loading' })
    fetchBatch(urls)
      .then((data) => !cancelled && setState({ kind: 'ok', data }))
      .catch((e) => !cancelled && setState({ kind: 'error', error: e instanceof ApiError ? e : new ApiError('未知错误', 'unknown', 0) }))
    return () => {
      cancelled = true
    }
  }, [urls])

  const meta = state.kind === 'ok' ? state.data.fetch : null

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-line bg-bg-0/90 backdrop-blur">
        <div className="page-col flex items-center gap-3 py-3">
          <Button variant="outline" size="md" asChild className="shrink-0">
            <Link to={backTo}>
              <ArrowLeft className="size-3.5" />
              返回结果
            </Link>
          </Button>
          <p className="font-mono text-[13px] text-ink-1">
            批量净化 <span className="tabular text-signal">{urls.length}</span> 条
          </p>
          {meta && (
            <span className="ml-auto font-mono text-[11px] text-ink-2">
              <span className="text-signal">{meta.ok} 成功</span>
              {meta.failed > 0 && <span className="text-danger"> · {meta.failed} 失败</span>}
              {meta.timeout > 0 && <span className="text-amber"> · {meta.timeout} 超时</span>}
              {meta.blocked > 0 && <span className="text-amber"> · {meta.blocked} 拦截</span>}
              {meta.no_content > 0 && <span> · {meta.no_content} 无正文</span>}
              {meta.deduped > 0 && <span className="text-ink-2"> · {meta.deduped} 去重</span>}
              {meta.budget_cut > 0 && <span className="text-amber"> · {meta.budget_cut} 预算切断</span>}
              <span> · {fmtMs(meta.took_ms)}</span>
            </span>
          )}
        </div>
      </header>

      <main className="page-col py-6">
        {urls.length === 0 && <EmptyState title="没有要净化的 URL" detail="在结果页勾选结果后点「批量净化」进来。" />}

        {state.kind === 'loading' && urls.length > 0 && (
          <>
            <p className="mb-5 font-mono text-[12px] text-ink-2">
              并发抓取 + 净化中(整单预算 90s,三档升级链按需升档)<span className="animate-pulse">…</span>
            </p>
            <ResultSkeleton rows={Math.min(urls.length, 4)} />
          </>
        )}

        {state.kind === 'error' && (
          <EmptyState tone="warn" title="批量请求失败" detail={`[${state.error.code}] ${state.error.message}`} />
        )}

        {state.kind === 'ok' && (
          <ol className="space-y-3">
            {state.data.items.map((it, i) => {
              const ok = it.fetch_status === 'ok'
              const tier = TIER_STYLE[it.engine_used]
              return (
                <li key={`${it.url}-${i}`} className={cn('rounded-md border bg-bg-1 p-4', ok ? 'border-line' : 'border-line/60')}>
                  <div className="flex flex-wrap items-center gap-2">
                    <a href={it.url} target="_blank" rel="noreferrer" className="flex min-w-0 items-center gap-1 font-mono text-[11px] text-ink-2 transition-colors hover:text-cyan">
                      <span className="truncate">{shortHost(it.url)}</span>
                      <ExternalLink className="size-3 shrink-0" />
                    </a>
                    {tier && <Badge className={tier.cls}>{it.engine_used}</Badge>}
                    {ok ? (
                      <span className="font-mono text-[11px] text-ink-2">{it.word_count ?? '—'} 字符 · HTTP {it.http_status ?? '—'}</span>
                    ) : (
                      <Badge variant="amber">{STATUS_LABEL[it.fetch_status] ?? it.fetch_status}</Badge>
                    )}
                    {ok && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="ml-auto"
                        onClick={() => navigate(`/?url=${encodeURIComponent(it.url)}&back=${encodeURIComponent(`/search?${params}`)}`)}
                      >
                        阅读模式 →
                      </Button>
                    )}
                  </div>
                  <p className="mt-1.5 break-all font-mono text-[11px] text-ink-2/70">{truncate(it.url, 120)}</p>
                  {!ok && it.error && (
                    <p className="mt-1.5 break-all font-mono text-[11px] leading-relaxed text-amber/80">{truncate(it.error, 200)}</p>
                  )}
                  {ok && it.content && (
                    <p className="mt-2 text-[12.5px] leading-relaxed text-ink-1">{truncate(it.content.replace(/[#*`>\[\]]/g, ''), 240)}</p>
                  )}
                </li>
              )
            })}
          </ol>
        )}
      </main>
    </div>
  )
}
