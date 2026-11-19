/**
 * HOLLOW API 全量类型定义 —— 与网关契约一一对齐。
 * 契约来源:box-39 /opt/hollow/api/v1.py + models.py(2026-07 延迟治理后版本)。
 * 本文件零依赖、零 React——后续客户端(Tauri/Electron)直接复用。
 */

/* ============================== 公共 ============================== */

/** 三档抓取引擎(命中档位) */
export type Tier = 'static' | 'dynamic' | 'stealthy'

/** 抓取结果状态 */
export type FetchStatus = 'ok' | 'failed' | 'timeout' | 'blocked' | 'no_content'

/** 模式旋钮:fast 广度/速度 | balanced 默认 | thorough 质量/难度 */
export type ResearchMode = 'fast' | 'balanced' | 'thorough'

/** 引擎失败明细(注意:是对象数组,不是 Record!) */
export interface EngineFailure {
  engine: string
  reason: string
}

/** 熔断剔除的引擎(2026-07-30 引擎健康退避;probing=true 为到期探测放行) */
export interface DegradedEngine {
  engine: string
  reason: string
  retry_after_s: number
  probing: boolean
}

/** 页面外链(api/extractor.py;internal=同 host 或子域) */
export interface AssetLink {
  url: string
  text: string | null
  internal: boolean
}

/** 页面媒体资源(source: tag=页面标签 | meta=og:/twitter:) */
export interface AssetMedia {
  url: string
  type: 'image' | 'video' | 'audio' | 'embed'
  source: 'tag' | 'meta'
  alt?: string | null
}

/** 正文小图内联账目(api/embedder.py;失败一律保留原 URL 引用) */
export interface EmbedStats {
  candidates: number
  embedded: number
  skipped_too_large: number
  skipped_fetch_failed: number
  skipped_ssrf: number
  skipped_bad_ct: number
  skipped_quota: number
  error?: string
}

/** 搜索账目(与 SearchMeta 对齐) */
export interface SearchMeta {
  engines_requested: string[]
  engines_used: string[]
  engines_failed: EngineFailure[]
  engines_no_results: string[]
  /** 熔断剔除/探测中的引擎;空数组=无熔断(2026-07-30) */
  engines_degraded: DegradedEngine[]
  results_total: number
  took_ms: number
  q_sanitized: boolean
}

/** 即时答案(infobox/answer 归一;注意正文字段是 content 不是 answer) */
export interface Answer {
  object?: string
  type?: 'infobox' | 'answer'
  title?: string | null
  content?: string | null
  url?: string | null
  img_src?: string | null
  engine?: string | null
}

/** OpenAI 风格用量账目 */
export interface Usage {
  searches: number
  engines_queried: number
  results_returned?: number
  fetches?: number
  results_ok?: number
}

/* ============================== /v1/search ============================== */

export interface SearchResultItem {
  object?: string
  url: string
  title: string
  engine: string
  /** SearXNG 原分(可溯源) */
  score: number
  /** 词汇重排分(排序依据) */
  relevance: number
  snippet: string
  published_date: string | null
  /** 媒体模式前置(2026-07-30):images 类引擎图直链;其余 null */
  img_src: string | null
  /** 缩略图;缺省 null */
  thumbnail: string | null
}

export interface SearchResponse {
  id: string
  object: string
  created: number
  query: string
  scenes: string[] | null
  engines: string[]
  page: number
  results: SearchResultItem[]
  answers: Answer[]
  search: SearchMeta
  usage: Usage
  ignored_params?: string[]
}

/* ============================== /v1/fetch ============================== */

export interface FetchItem {
  object?: string
  /** 客户端点名的原始 URL(条目顺序 == 输入顺序) */
  url: string
  /** 重定向后的最终落点(仅当与 url 不同才返回) */
  final_url?: string | null
  fetch_status: FetchStatus
  engine_used: Tier
  http_status: number | null
  /**
   * 注意:/v1/fetch 的 item 不返回 title/highlights(那是 /v1/research item 的字段)——
   * 全部按可选处理,禁止未判空直接访问!(2026-07-27 黑屏事故教训)
   */
  title?: string | null
  /** 净化正文——API 输出即 Markdown(trafilatura output_format="markdown") */
  content: string | null
  highlights?: string[]
  highlight_scores?: number[]
  word_count: number | null
  purified?: boolean | null
  error: string | null
  fetched_at?: string | null
  /** 外链清单(仅 include_links 请求时存在;2026-07-29) */
  links?: AssetLink[] | null
  /** 媒体清单(仅 include_media 请求时存在) */
  media?: AssetMedia[] | null
  /** 小图内联账目(仅 embed_images 请求时存在) */
  embed?: EmbedStats | null
  /** 外链自动展开的子孙树(仅 expand_links>0 时存在;子孙条目带 depth 层号) */
  children?: FetchItem[]
  /** 展开层号(父条目隐式 0,仅子孙条目携带) */
  depth?: number
}

export interface FetchMeta {
  submitted: number
  requested: number
  deduped: number
  ok: number
  failed: number
  timeout: number
  blocked: number
  no_content: number
  /** 因整单预算切断的条数(⊆ timeout) */
  budget_cut: number
  /** 外链展开实际抓取的子孙总数(2026-07-29) */
  expanded: number
  took_ms: number
}

export interface FetchResponse {
  id: string
  object: string
  created: number
  items: FetchItem[]
  fetch: FetchMeta
  ignored_params?: string[]
}

/* ============================== /v1/research ============================== */

export interface ResearchItem {
  object?: string
  url: string
  title: string | null
  engine: string | null
  score: number | null
  /** 词汇重排分;越大越相关 */
  relevance: number | null
  /** 按 relevance 排序后的最终位次(0 起),非抓取完成顺序 */
  rank: number | null
  published_date: string | null
  fetch_status: FetchStatus
  engine_used: Tier
  http_status: number | null
  word_count: number | null
  purified: boolean | null
  content: string | null
  error: string | null
  fetched_at: string | null
  /** 正文中 query 最相关的几句(词汇抽取,无模型) */
  highlights: string[]
  highlight_scores: number[]
  /** 外链/媒体/内联账目(仅对应 include_* 请求时存在;2026-07-29) */
  links?: AssetLink[] | null
  media?: AssetMedia[] | null
  embed?: EmbedStats | null
}

export interface ResearchFetchMeta {
  /** 想要的成功正文条数(= top_n) */
  target: number
  /** 候选池:实际考虑过的 URL 数(fast 模式会 > requested) */
  pool: number
  /** 实际发起并拿到结果的条数(= len(items)) */
  requested: number
  /** 真拿到正文(内容闸门通过)——只有它算"成功"、计入 target */
  ok: number
  failed: number
  timeout: number
  blocked: number
  no_content: number
  /** 够了/预算到而丢弃的候选(pool = requested + cancelled) */
  cancelled: number
  stopped_reason: 'target_reached' | 'pool_exhausted' | 'budget' | string
  took_ms: number
}

export interface ResearchObject {
  id: string
  object: string
  created: number
  query: string
  scenes: string[] | null
  engines: string[]
  page: number
  items: ResearchItem[]
  answers: Answer[]
  search: SearchMeta
  fetch: ResearchFetchMeta
  usage: Usage
  ignored_params?: string[]
}

/* ---------- SSE 语义化事件(与 v1.py _sse() 逐帧对齐) ---------- */

/** 搜索完成:携带搜索账目与候选池大小(注意 selected 是数量,不是 URL 列表) */
export interface ResearchSearchEvent {
  object: 'research.event'
  event: 'research.search.completed'
  id: string
  created: number
  search: SearchMeta
  selected: number
}

/** 每条来源抓完净化完立即推送;index 是选取顺位(完成顺序 ≠ index 顺序) */
export interface ResearchItemEvent {
  object: 'research.event'
  event: 'research.item.completed'
  id: string
  index: number
  item: ResearchItem
}

/** 汇总:不重复携带正文(content=null),只留账目与占位 */
export interface ResearchCompletedEvent {
  object: 'research.event'
  event: 'research.completed'
  id: string
  research: ResearchObject
}

export type ResearchEvent = ResearchSearchEvent | ResearchItemEvent | ResearchCompletedEvent

/* ============================== /v1/engines /v1/scenes ============================== */

export interface EngineEntry {
  id: string
  object: string
  tier: string
  type: string
  status: string
  removed_reason?: string
  scenes?: string[]
  note?: string
}

export interface SceneEntry {
  id: string
  object: string
  engines: string[]
}

/* ============================== 请求参数 ============================== */

export interface SearchParams {
  query: string
  /** 场景,可多选,取并集 */
  scenes?: string[]
  /** 自定义引擎点名,并入场景集 */
  engines?: string[]
  language?: string
  /** day | week | month | year */
  time_range?: string
  /** 0 关 | 1 中 | 2 严 */
  safesearch?: number
  page?: number
  include_domains?: string[]
  exclude_domains?: string[]
}

export interface FetchParams {
  /** 单个或数组(≤10) */
  urls: string | string[]
  mode?: ResearchMode
  timeout?: number
  escalate?: boolean
  purify?: boolean
  max_content_chars?: number
  concurrency?: number
  budget?: number
  /** 抽取外链清单 [{url,text,internal}](2026-07-29) */
  include_links?: boolean
  /** 抽取媒体清单 [{url,type,source,alt?}] */
  include_media?: boolean
  /** 净化正文保留图片引用(![alt](url),夹在原位置;2026-07-30) */
  include_images?: boolean
  /** 小图(≤32KB)转 data URI 内联正文,正文自包含(隐含 include_images) */
  embed_images?: boolean
  /** 每页自动跟进的外链数(0=关,≤10);子孙进 children 树 */
  expand_links?: number
  /** 展开递归层数(1=只跟一层,≤3) */
  expand_depth?: number
  /** 展开范围:internal 只跟站内(默认) | all 任意外链 */
  expand_scope?: 'internal' | 'all'
}

export interface ResearchParams extends SearchParams {
  /** 想要的成功正文条数(1-20,凑够即停) */
  top_n?: number
  mode?: ResearchMode
  /** 整单时间预算(秒,≤300) */
  budget?: number
  concurrency?: number
  max_content_chars?: number
  timeout?: number
  escalate?: boolean
  purify?: boolean
  /** 抽取每条来源的外链清单(2026-07-29) */
  include_links?: boolean
  /** 抽取每条来源的媒体清单 */
  include_media?: boolean
  /** 净化正文保留图片引用 */
  include_images?: boolean
  /** 小图转 data URI 内联正文(隐含 include_images) */
  embed_images?: boolean
}
