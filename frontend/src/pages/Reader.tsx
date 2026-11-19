import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ExternalLink } from 'lucide-react'
import { ApiError, fetchOneCached, hostOf, READER_EXTRAS, STATUS_LABEL, TIER_STYLE } from '@/lib/sdk'
import type { AssetLink, AssetMedia, FetchItem } from '@/lib/sdk'
import Markdown from '@/components/Markdown'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty'
import { cn } from '@/lib/utils'

type CleanState =
  | { kind: 'loading' }
  | { kind: 'error'; error: ApiError }
  | { kind: 'ok'; item: FetchItem }

type Tab = 'web' | 'clean'

const CLEAN_STAGE = [
  '抓取 + 净化中…',
  'static 未出正文,正在启动浏览器渲染…',
  '仍在渲染/升级,反爬较重的站点会慢一些…',
]

/**
 * 阅读模式 —— 双路赛制(用户定稿设计,保留):
 *   web  路:本地 iframe 直开原网页(用户浏览器渲染,Cookie 互通,与服务器无关)
 *   clean 路:/v1/fetch 云端净化正文
 * 两路同时加载,先就绪先展示;净化失败自动切原框;tab 无缝切换(两路都保持挂载)。
 */
export default function Reader() {
  const [params] = useSearchParams()
  const url = params.get('url') ?? ''
  const titleParam = params.get('title') ?? ''
  const backTo = params.get('q')
    ? `/?${new URLSearchParams([...params.entries()].filter(([k]) => !['url', 'title'].includes(k)))}`
    : '/'

  const [clean, setClean] = useState<CleanState>({ kind: 'loading' })
  const [webReady, setWebReady] = useState(false)
  const [tab, setTab] = useState<Tab | null>(null)
  const [stage, setStage] = useState(0)
  const [mdView, setMdView] = useState<'md' | 'txt'>('md')

  const cleanOk = clean.kind === 'ok' && clean.item.fetch_status === 'ok'
  const cleanFailed =
    clean.kind === 'error' || (clean.kind === 'ok' && clean.item.fetch_status !== 'ok')

  /* 净化路:与 iframe 同时发车 */
  useEffect(() => {
    if (!url) return
    let cancelled = false
    setClean({ kind: 'loading' })
    setWebReady(false)
    setTab(null)
    setStage(0)
    const t1 = setTimeout(() => !cancelled && setStage(1), 4000)
    const t2 = setTimeout(() => !cancelled && setStage(2), 10000)
    // 阅读页天然要图与外链:正文图片引用夹正文(include_images)+ 外链/媒体清单(2026-07-30)
    fetchOneCached(url, READER_EXTRAS)
      .then((d) => !cancelled && setClean({ kind: 'ok', item: d.items[0] }))
      .catch((e) =>
        !cancelled &&
        setClean({ kind: 'error', error: e instanceof ApiError ? e : new ApiError('未知错误', 'unknown', 0) }),
      )
    return () => {
      cancelled = true
      clearTimeout(t1)
      clearTimeout(t2)
    }
  }, [url])

  /* 赛制裁决:先就绪先展示;净化失败自动切原框(仅在用户未手动选边时) */
  useEffect(() => {
    if (tab === null) {
      if (cleanOk) setTab('clean')
      else if (cleanFailed || webReady) setTab('web')
    }
  }, [tab, cleanOk, cleanFailed, webReady])

  const shown: Tab = tab ?? 'clean' // 未定前渲染 clean 骨架(iframe 也已在后台跑)

  return (
    <div className="flex h-screen flex-col">
      {/* 顶栏:返回 + 赛制 tab + 域名 + 原站(与正文同栏) */}
      <header className="shrink-0 border-b border-line bg-bg-0/90 backdrop-blur">
        <div className="page-col flex flex-wrap items-center gap-2 py-2.5">
          <Button variant="outline" size="md" asChild className="shrink-0">
            <Link to={backTo}>
              <ArrowLeft className="size-3.5" />
              返回
            </Link>
          </Button>

          {/* 赛制 tab */}
          <div className="flex rounded-md border border-line p-0.5" role="tablist" aria-label="阅读方式">
            <button
              role="tab"
              aria-selected={shown === 'web'}
              onClick={() => setTab('web')}
              className={cn(
                'flex items-center gap-1.5 rounded px-3 py-1 text-[12px] transition-colors',
                shown === 'web' ? 'bg-bg-2 text-ink-0' : 'text-ink-2 hover:text-ink-1',
              )}
            >
              原网页
              {webReady && <span className="size-1.5 rounded-full bg-signal" title="已就绪" />}
            </button>
            <button
              role="tab"
              aria-selected={shown === 'clean'}
              onClick={() => setTab('clean')}
              className={cn(
                'flex items-center gap-1.5 rounded px-3 py-1 text-[12px] transition-colors',
                shown === 'clean' ? 'bg-bg-2 text-ink-0' : 'text-ink-2 hover:text-ink-1',
              )}
            >
              净化阅读
              {cleanOk && <span className="size-1.5 rounded-full bg-signal" title="已就绪" />}
              {cleanFailed && <span className="font-mono text-[10px] text-amber">✕</span>}
            </button>
          </div>

          <span className="hidden truncate font-mono text-[11px] text-ink-2 sm:block">{hostOf(url)}</span>
          <Button variant="outline" size="md" asChild className="ml-auto shrink-0 hover:border-cyan hover:text-cyan">
            <a href={url} target="_blank" rel="noreferrer">
              新标签打开
              <ExternalLink className="size-3.5" />
            </a>
          </Button>
        </div>
      </header>

      {/* 双路都保持挂载,切换只是显隐——无缝 */}
      <div className="relative flex-1 overflow-hidden">
        {/* web 路:本地 iframe(永远跑在用户浏览器,Cookie 互通;sandbox 禁顶跳防 frame-busting) */}
        <div className={cn('absolute inset-0', shown !== 'web' && 'invisible')}>
          {!webReady && (
            <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-bg-0">
              <p className="font-mono text-[13px] text-ink-2">原网页加载中<span className="animate-pulse">…</span></p>
              <p className="mt-2 max-w-xs text-center font-mono text-[11px] leading-relaxed text-ink-2/60">
                本地渲染 · Cookie 互通 · 与服务器无关<br />
                若一直空白:目标站可能禁止被嵌入(X-Frame-Options)或为 http 混合内容,请切净化阅读
              </p>
            </div>
          )}
          <iframe
            key={url}
            src={url}
            title="原网页"
            onLoad={() => setWebReady(true)}
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
            className="size-full border-0 bg-white"
          />
        </div>

        {/* clean 路:云端净化(阅读栏:与顶栏同列) */}
        <div className={cn('absolute inset-0 overflow-y-auto', shown !== 'clean' && 'invisible')}>
          <main className="page-col py-10">
            {clean.kind === 'loading' && (
              <div className="pt-16 text-center">
                <p className="font-mono text-[13px] text-ink-2">
                  {CLEAN_STAGE[stage]}<span className="animate-pulse"> </span>
                </p>
                <p className="mt-2 font-mono text-[11px] text-ink-2/60">
                  static → dynamic → stealthy 升级链按需升档 · 原网页同时在本地加载,可手动切换
                </p>
              </div>
            )}

            {clean.kind === 'error' && (
              <CleanFailCard
                label="请求失败"
                detail={`[${clean.error.code}] ${clean.error.message}`}
                url={url}
                onSwitch={() => setTab('web')}
              />
            )}

            {clean.kind === 'ok' &&
              (clean.item.fetch_status === 'ok' ? (
                <article>
                  <div className="mb-8 flex flex-wrap items-center gap-2.5">
                    <span className={cn('rounded-full border px-2.5 py-0.5 font-mono text-[10px]', TIER_STYLE[clean.item.engine_used]?.cls)}>
                      {TIER_STYLE[clean.item.engine_used]?.label ?? clean.item.engine_used}
                    </span>
                    <span className="font-mono text-[11px] text-ink-2">{clean.item.word_count ?? '—'} 字符</span>
                    <button
                      onClick={() => setMdView(mdView === 'md' ? 'txt' : 'md')}
                      className="ml-auto rounded-full border border-line px-2.5 py-0.5 font-mono text-[10px] text-ink-2 transition-colors hover:border-signal hover:text-signal"
                    >
                      {mdView === 'md' ? 'Markdown ▾' : '纯文本 ▾'}
                    </button>
                  </div>

                  <h1 className="font-serif text-[30px] font-bold leading-[1.4] text-ink-0">
                    {clean.item.title || titleParam || clean.item.url}
                  </h1>

                  {(clean.item.highlights ?? []).length > 0 && (
                    <div className="mt-8 border-l-2 border-signal/40 pl-5">
                      <p className="font-mono text-[11px] uppercase tracking-wider text-signal">要点</p>
                      <ul className="mt-2.5 space-y-2">
                        {(clean.item.highlights ?? []).map((h, i) => (
                          <li key={i} className="flex gap-2.5 font-serif text-[15px] leading-[1.85] text-ink-1">
                            <span className="shrink-0 font-mono text-[12px] text-signal/60">{i + 1}.</span>
                            {h}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <div className="mt-9">
                    {mdView === 'md' ? (
                      <Markdown>{clean.item.content ?? ''}</Markdown>
                    ) : (
                      <div className="space-y-5">
                        {(clean.item.content ?? '').split(/\n{2,}/).map((para, i) => (
                          <p key={i} className="font-serif text-[16.5px] leading-[1.95] text-ink-1">{para}</p>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* 页面资产:媒体墙 + 本页外链(2026-07-30,有数据才显示) */}
                  <MediaWall media={clean.item.media} />
                  <LinkPanel links={clean.item.links} />
                </article>
              ) : (
                <CleanFailCard
                  label={STATUS_LABEL[clean.item.fetch_status] ?? clean.item.fetch_status}
                  detail={clean.item.error ?? '(无错误详情)'}
                  url={url}
                  onSwitch={() => setTab('web')}
                />
              ))}
          </main>
        </div>
      </div>
    </div>
  )
}

/** 媒体墙:页面图片资源小图网格(image 类型,前 12 张;点击新标签看原图) */
function MediaWall({ media }: { media?: AssetMedia[] | null }) {
  const images = (media ?? []).filter((m) => m.type === 'image').slice(0, 12)
  if (!images.length) return null
  return (
    <details className="mt-12 rounded-md border border-line bg-bg-1 px-4 py-3">
      <summary className="cursor-pointer select-none font-mono text-[11px] uppercase tracking-wider text-ink-2 hover:text-ink-1">
        本页媒体 · {images.length}{(media?.length ?? 0) > 12 ? `（共 ${media!.length}）` : ''}
      </summary>
      <div className="mt-3 grid grid-cols-3 gap-2.5 sm:grid-cols-4">
        {images.map((m, i) => (
          <a key={i} href={m.url} target="_blank" rel="noreferrer" title={m.alt ?? m.url}
             className="group block overflow-hidden rounded border border-line bg-bg-0">
            <img src={m.url} alt={m.alt ?? ''} loading="lazy"
                 className="aspect-[4/3] w-full object-cover transition-opacity group-hover:opacity-85"
                 onError={(e) => { (e.target as HTMLImageElement).closest('a')!.style.display = 'none' }} />
          </a>
        ))}
      </div>
    </details>
  )
}

/** 本页外链:站内/站外分组清单(前 30 条;internal 无标记,站外带 ↗) */
function LinkPanel({ links }: { links?: AssetLink[] | null }) {
  const all = links ?? []
  if (!all.length) return null
  const internal = all.filter((l) => l.internal).slice(0, 20)
  const external = all.filter((l) => !l.internal).slice(0, 10)
  const Row = ({ l }: { l: AssetLink }) => (
    <li className="truncate">
      <a href={l.url} target="_blank" rel="noreferrer"
         className="text-[13px] text-cyan underline decoration-cyan/25 underline-offset-2 hover:decoration-cyan/70">
        {l.text || hostOf(l.url)}
      </a>
      <span className="ml-2 font-mono text-[10px] text-ink-2/60">{hostOf(l.url)}</span>
    </li>
  )
  return (
    <details className="mt-3 rounded-md border border-line bg-bg-1 px-4 py-3">
      <summary className="cursor-pointer select-none font-mono text-[11px] uppercase tracking-wider text-ink-2 hover:text-ink-1">
        本页外链 · {all.length}{all.length > 30 ? '（站内 20 + 站外 10 节选）' : ''}
      </summary>
      <div className="mt-3 space-y-4">
        {internal.length > 0 && (
          <div>
            <p className="font-mono text-[10px] uppercase tracking-wider text-signal/80">站内 · {internal.length}</p>
            <ul className="mt-1.5 space-y-1.5">{internal.map((l, i) => <Row key={i} l={l} />)}</ul>
          </div>
        )}
        {external.length > 0 && (
          <div>
            <p className="font-mono text-[10px] uppercase tracking-wider text-amber/90">站外 ↗ · {external.length}</p>
            <ul className="mt-1.5 space-y-1.5">{external.map((l, i) => <Row key={i} l={l} />)}</ul>
          </div>
        )}
      </div>
    </details>
  )
}

/** 净化失败的诚实卡:自动切换已发生时也作为留档说明 */
function CleanFailCard({ label, detail, url, onSwitch }: { label: string; detail: string; url: string; onSwitch: () => void }) {
  return (
    <EmptyState tone="warn" title={label} detail={detail}>
      <p className="w-full text-[13px] text-ink-2">净化路如实回报——原网页路(本地渲染、Cookie 互通)通常能开。</p>
      <Button variant="outline" onClick={onSwitch}>切到原网页</Button>
      <Button variant="outline" asChild className="hover:border-cyan hover:text-cyan">
        <a href={url} target="_blank" rel="noreferrer">新标签打开 ↗</a>
      </Button>
    </EmptyState>
  )
}
