import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Search } from 'lucide-react'
import { ApiError, listEngines, listScenes } from '@/lib/sdk'
import type { EngineEntry, SceneEntry } from '@/lib/sdk'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

const STATUS_OPTIONS = [
  { id: '', label: '状态:全部' },
  { id: 'default', label: 'default 默认集' },
  { id: 'pool', label: 'pool 在池可点名' },
  { id: 'removed', label: 'removed 除名' },
]

const TIER_OPTIONS = [
  { id: '', label: '梯队:全部' },
  { id: 'T0', label: 'T0' },
  { id: 'T1', label: 'T1' },
  { id: 'T2', label: 'T2' },
  { id: 'T3', label: 'T3' },
]

/** 引擎状态色(注册表语义:default 默认集 / pool 在池可点名 / removed 除名) */
const STATUS_VARIANT: Record<string, 'signal' | 'cyan' | 'default'> = {
  default: 'signal',
  pool: 'cyan',
  removed: 'default',
}

/**
 * 引擎注册表浏览 —— /v1/engines + /v1/scenes。
 * 343 源账目全透明:可按 状态/场景/梯队 过滤 + 文本搜索;removed 带除名原因。
 */
export default function Engines() {
  const [engines, setEngines] = useState<EngineEntry[] | null>(null)
  const [scenes, setScenes] = useState<SceneEntry[] | null>(null)
  const [error, setError] = useState<ApiError | null>(null)

  const [text, setText] = useState('')
  const [status, setStatus] = useState('')
  const [scene, setScene] = useState('')
  const [tier, setTier] = useState('')

  useEffect(() => {
    Promise.all([listEngines(), listScenes()])
      .then(([e, s]) => {
        setEngines(e.data)
        setScenes(s.data)
      })
      .catch((err) => setError(err instanceof ApiError ? err : new ApiError('未知错误', 'unknown', 0)))
  }, [])

  const filtered = useMemo(() => {
    if (!engines) return null
    const t = text.trim().toLowerCase()
    return engines.filter((e) => {
      if (status && e.status !== status) return false
      if (tier && e.tier !== tier) return false
      if (scene && !(e.scenes ?? []).includes(scene)) return false
      if (t && !e.id.toLowerCase().includes(t) && !(e.note ?? '').toLowerCase().includes(t)) return false
      return true
    })
  }, [engines, text, status, scene, tier])

  const counts = useMemo(() => {
    if (!engines) return null
    const c: Record<string, number> = {}
    for (const e of engines) c[e.status] = (c[e.status] ?? 0) + 1
    return c
  }, [engines])

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-line bg-bg-0/90 backdrop-blur">
        <div className="page-col-wide flex items-center gap-3 py-3">
          <Button variant="outline" size="md" asChild className="shrink-0">
            <Link to="/">
              <ArrowLeft className="size-3.5" />
              主页
            </Link>
          </Button>
          <p className="font-display text-[16px] font-bold text-ink-0">引擎注册表</p>
          {counts && (
            <p className="font-mono text-[11px] text-ink-2">
              共 {engines!.length} · <span className="text-signal">{counts.default ?? 0} 默认集</span>
              {counts.pool ? <span className="text-cyan"> · {counts.pool} 在池</span> : null}
              {counts.removed ? <span className="text-ink-2"> · {counts.removed} 除名</span> : null}
            </p>
          )}
        </div>
        <div className="page-col-wide flex flex-wrap items-center gap-1.5 pb-3">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-2" />
            <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="搜引擎名/备注…" className="w-56 pl-8" />
          </div>
          <Select value={status || 'all'} onValueChange={(v) => setStatus(v === 'all' ? '' : v)}>
            <SelectTrigger aria-label="状态"><SelectValue /></SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((o) => <SelectItem key={o.id || 'all'} value={o.id || 'all'}>{o.label}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={tier || 'all'} onValueChange={(v) => setTier(v === 'all' ? '' : v)}>
            <SelectTrigger aria-label="梯队"><SelectValue /></SelectTrigger>
            <SelectContent>
              {TIER_OPTIONS.map((o) => <SelectItem key={o.id || 'all'} value={o.id || 'all'}>{o.label}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={scene || 'all'} onValueChange={(v) => setScene(v === 'all' ? '' : v)}>
            <SelectTrigger aria-label="场景"><SelectValue placeholder="场景:全部" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">场景:全部</SelectItem>
              {(scenes ?? []).map((s) => <SelectItem key={s.id} value={s.id}>{s.id}({s.engines.length})</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </header>

      <main className="page-col-wide py-6">
        {error && <EmptyState tone="warn" title="注册表加载失败" detail={`[${error.code}] ${error.message}`} />}

        {!engines && !error && (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 9 }, (_, i) => <Skeleton key={i} className="h-20" />)}
          </div>
        )}

        {filtered && (
          <>
            <p className="mb-4 font-mono text-[11px] text-ink-2">
              {filtered.length} / {engines!.length} 条
              {scene && scenes && (
                <span className="ml-2 text-ink-2/70">
                  · 场景 {scene} 权威映射:{scenes.find((s) => s.id === scene)?.engines.join(', ') ?? '—'}
                </span>
              )}
            </p>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((e) => (
                <div
                  key={e.id}
                  className={cn(
                    'rounded-md border border-line bg-bg-1 p-3.5',
                    e.status === 'removed' && 'opacity-60',
                  )}
                >
                  <div className="flex items-center gap-2">
                    <p className="min-w-0 truncate font-mono text-[12.5px] text-ink-0">{e.id}</p>
                    <Badge variant={STATUS_VARIANT[e.status] ?? 'default'} className="ml-auto shrink-0">{e.status}</Badge>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-ink-2">
                    <span>{e.tier}</span>
                    <span>·</span>
                    <span>{e.type}</span>
                    {(e.scenes ?? []).slice(0, 4).map((s) => (
                      <span key={s} className="rounded-sm bg-bg-2 px-1 py-px">{s}</span>
                    ))}
                    {(e.scenes ?? []).length > 4 && <span>+{e.scenes!.length - 4}</span>}
                  </div>
                  {e.removed_reason && (
                    <p className="mt-1.5 font-mono text-[10.5px] leading-relaxed text-amber/80">{e.removed_reason}</p>
                  )}
                  {e.note && <p className="mt-1.5 font-mono text-[10.5px] leading-relaxed text-ink-2">{e.note}</p>}
                </div>
              ))}
            </div>
            {filtered.length === 0 && <EmptyState className="mt-4" title="无匹配引擎" detail="放宽过滤条件或清空搜索词。" />}
          </>
        )}
      </main>
    </div>
  )
}
