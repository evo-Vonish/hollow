/**
 * HOLLOW SDK —— 高层 API 面。UI 只从这里取数;
 * 这一层零 React 依赖,后续客户端整体搬运 sdk/ 即可。
 */
import { get, post, ApiError } from './client'
import { postSSE } from './sse'
import { cached, cacheGet, isInflight } from './cache'
import type {
  EngineEntry,
  FetchParams,
  FetchResponse,
  ResearchEvent,
  ResearchParams,
  SceneEntry,
  SearchParams,
  SearchResponse,
} from './types'

export * from './types'
export * from './client'
export * from './format'
export { postSSE } from './sse'
export { cacheGet, cacheSet, setStorage } from './cache'
export type { KVStorage } from './cache'

/* ============================== /v1/search ============================== */

export function searchApi(params: SearchParams): Promise<SearchResponse> {
  const body: Record<string, unknown> = { query: params.query, page: params.page ?? 1 }
  if (params.scenes?.length) body.scenes = params.scenes
  if (params.engines?.length) body.engines = params.engines
  if (params.language && params.language !== 'auto') body.language = params.language
  if (params.time_range) body.time_range = params.time_range
  if (params.safesearch) body.safesearch = params.safesearch
  if (params.include_domains?.length) body.include_domains = params.include_domains
  if (params.exclude_domains?.length) body.exclude_domains = params.exclude_domains
  return post<SearchResponse>('/v1/search', body, 30000)
}

function searchKey(p: SearchParams): string {
  return `s:${JSON.stringify([
    p.query, p.scenes ?? [], p.engines ?? [], p.language ?? 'auto',
    p.time_range ?? '', p.safesearch ?? 0, p.page ?? 1,
    p.include_domains ?? [], p.exclude_domains ?? [],
  ])}`
}

export function searchApiCached(params: SearchParams): Promise<SearchResponse> {
  return cached(searchKey(params), () => searchApi(params))
}

/* ============================== /v1/fetch ============================== */

export function fetchApi(params: FetchParams): Promise<FetchResponse> {
  return post<FetchResponse>('/v1/fetch', params, (params.budget ?? 45) * 1000 + 15000)
}

/** 阅读模式附加项:外链/媒体清单、正文图片引用、小图内联、外链展开(2026-07-29/30) */
export type FetchExtras = Partial<
  Pick<FetchParams, 'include_links' | 'include_media' | 'include_images' | 'embed_images' |
                      'expand_links' | 'expand_depth' | 'expand_scope'>
>

/** 阅读页标准资产参数(Reader 请求与结果卡预取共用,缓存键必须一致) */
export const READER_EXTRAS: FetchExtras = { include_links: true, include_media: true, include_images: true }

/** 阅读模式:单 URL 直取,budget 45s 覆盖升级链;正文截 2 万字符够读 */
export function fetchOne(url: string, extras: FetchExtras = {}): Promise<FetchResponse> {
  const budget = extras.expand_links ? 90 : 45  // 展开要多抓子孙页,预算放宽
  return fetchApi({ urls: url, budget, max_content_chars: 20000, ...extras })
}

export function fetchOneCached(url: string, extras: FetchExtras = {}): Promise<FetchResponse> {
  const key = `f:${url}:${JSON.stringify(extras)}`
  return cached(key, () => fetchOne(url, extras))
}

/** 批量净化(≤10 条,网关上限 FETCH_URLS_MAX=10) */
export function fetchBatch(urls: string[]): Promise<FetchResponse> {
  return fetchApi({ urls, budget: 90, max_content_chars: 8000 })
}

/** hover/focus 预取:结果卡悬停时后台净化,点击秒开(结果丢弃,仅暖缓存) */
export function prefetchFetch(url: string, extras: FetchExtras = {}) {
  const key = `f:${url}:${JSON.stringify(extras)}`
  if (!cacheGet(key) && !isInflight(key)) {
    fetchOneCached(url, extras).catch(() => undefined)
  }
}

/* ============================== /v1/research(SSE) ============================== */

export interface ResearchStreamHandle {
  /** 中途停止(中止 HTTP 流;网关侧会收尾取消未完成抓取) */
  cancel: () => void
  /** 完成 promise:resolve 于 [DONE];reject 于错误/取消 */
  done: Promise<void>
}

/**
 * 深研流式:事件逐帧回调。
 * onEvent 顺序:research.search.completed → n×research.item.completed → research.completed。
 */
export function researchStream(
  params: ResearchParams,
  onEvent: (evt: ResearchEvent) => void,
): ResearchStreamHandle {
  const ctrl = new AbortController()
  const body: Record<string, unknown> = {
    query: params.query,
    stream: true,
    top_n: params.top_n ?? 5,
    mode: params.mode ?? 'balanced',
  }
  if (params.scenes?.length) body.scenes = params.scenes
  if (params.engines?.length) body.engines = params.engines
  if (params.language && params.language !== 'auto') body.language = params.language
  if (params.time_range) body.time_range = params.time_range
  if (params.safesearch) body.safesearch = params.safesearch
  if (params.budget) body.budget = params.budget
  if (params.concurrency) body.concurrency = params.concurrency
  if (params.max_content_chars) body.max_content_chars = params.max_content_chars
  if (params.include_domains?.length) body.include_domains = params.include_domains
  if (params.exclude_domains?.length) body.exclude_domains = params.exclude_domains
  // 页面资产与正文图片(2026-07-29/30)
  if (params.include_links) body.include_links = true
  if (params.include_media) body.include_media = true
  if (params.include_images) body.include_images = true
  if (params.embed_images) body.embed_images = true

  const done = postSSE<ResearchEvent>('/v1/research', body, onEvent, { signal: ctrl.signal })
  return { cancel: () => ctrl.abort(), done }
}

/* ============================== /v1/engines /v1/scenes ============================== */

export interface EngineFilter {
  status?: string
  scene?: string
  type?: string
  tier?: string
}

export function listEngines(filter: EngineFilter = {}): Promise<{ object: string; data: EngineEntry[] }> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(filter)) if (v) qs.set(k, v)
  const suffix = qs.size ? `?${qs}` : ''
  // 注册表基本静态,复用会话缓存即可
  return cached(`engines:${suffix}`, () => get(`/v1/engines${suffix}`))
}

export function listScenes(): Promise<{ object: string; data: SceneEntry[] }> {
  return cached('scenes:', () => get('/v1/scenes'))
}

/* ============================== 常量(与后端对齐) ============================== */

/** 场景 chip 定义:id 对应后端 scenes 参数,''= 默认集 */
export const SCENES: { id: string; label: string }[] = [
  { id: '', label: '全部' },
  { id: 'zh', label: '中文' },
  { id: 'dev', label: '开发' },
  { id: 'academic', label: '学术' },
  { id: 'news', label: '新闻' },
  { id: 'social', label: '社区' },
  { id: 'knowledge', label: '百科' },
  { id: 'general', label: '通用' },
  { id: 'images', label: '图片' },
  { id: 'av', label: '视频' },
]

export const TIME_RANGES: { id: string; label: string }[] = [
  { id: '', label: '时间不限' },
  { id: 'day', label: '一天内' },
  { id: 'week', label: '一周内' },
  { id: 'month', label: '一月内' },
  { id: 'year', label: '一年内' },
]

export const SAFESEARCH_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: '安全搜索:关' },
  { value: 1, label: '安全搜索:中' },
  { value: 2, label: '安全搜索:严' },
]

export const LANGUAGES: { id: string; label: string }[] = [
  { id: 'auto', label: '语言自动' },
  { id: 'zh-CN', label: '中文' },
  { id: 'en', label: 'English' },
  { id: 'ja', label: '日本語' },
]

export const RESEARCH_MODES: { id: 'fast' | 'balanced' | 'thorough'; label: string; desc: string }[] = [
  { id: 'fast', label: 'fast · 快速', desc: '广度优先:超召回 + 凑够即停 + 不升级' },
  { id: 'balanced', label: 'balanced · 均衡', desc: '默认预设:质量与速度兼顾' },
  { id: 'thorough', label: 'thorough · 深磕', desc: '质量优先:死磕每条 + 升级链全开' },
]

export { ApiError }
