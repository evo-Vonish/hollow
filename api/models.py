# -*- coding: utf-8 -*-
"""Pydantic 请求/响应模型(docs/design/03 §4 端点契约)。"""
from typing import Literal

from pydantic import BaseModel, Field

from api import config

# no_content:HTTP 2xx 但净化不出正文(空壳/反爬页/SPA 未渲染)——不算成功,内容闸门(design/05)
FetchStatus = Literal["ok", "failed", "timeout", "blocked", "no_content"]


class ResearchRequest(BaseModel):
    q: str = Field(min_length=1, max_length=config.QUERY_MAX_LEN, description="搜索词")
    engines: list[str] | None = Field(
        default=None, description="点名引擎;缺省用精选默认集"
    )
    # 引擎健康熔断账目(网关内部携带,非 API 入参;2026-07-30)
    engines_degraded: list[dict] = []
    categories: str | None = None
    language: str = "auto"
    time_range: str | None = None
    safesearch: int = Field(default=0, ge=0, le=2)
    # 产品差距批(2026-07-15):分页 + 域过滤(Tavily/Exa 风格,hollow 侧后置过滤)
    page: int = Field(default=1, ge=1, le=20)
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    # fetch_top_n 语义:想要的**成功正文条数**(target ok);fast 模式凑够即停
    fetch_top_n: int = Field(
        default=config.FETCH_TOP_N_DEFAULT, ge=1, le=config.FETCH_TOP_N_MAX
    )
    purify: bool = True
    # 2026-07-06 拍板新增(可选,/v0 缺省行为不变):
    concurrency: int = Field(default=config.FETCH_CONCURRENCY, ge=1,
                             le=config.REQUEST_CONCURRENCY_MAX)  # 并行抓取数
    budget: float | None = Field(default=None, gt=0, le=300)  # 整单时间预算(秒,从收到请求起算)
    max_content_chars: int | None = Field(default=None, ge=100)  # 单条净化正文截断上限
    # 2026-07-07 拍板:一根旋钮 fast↔thorough(速度/广度 ↔ 质量/难度)
    mode: Literal["fast", "balanced", "thorough"] = config.DEFAULT_MODE
    # escalate/fetch_timeout 缺省 None = 跟随 mode 预设;显式传值则覆盖预设
    escalate: bool | None = None
    fetch_timeout: float | None = Field(default=None, gt=0, le=60)
    # 页面资产抽取(2026-07-29):外链/媒体结构化清单;默认关,不给载荷灌水
    include_links: bool = False
    include_media: bool = False
    # 正文图片(2026-07-29):include_images 正文保留 ![alt](url) 引用;
    # embed_images 再把小图转 data URI 内联(隐含 include_images)
    include_images: bool = False
    embed_images: bool = False


class EngineFailure(BaseModel):
    engine: str
    reason: str


class ResearchItem(BaseModel):
    url: str
    title: str | None = None
    engine: str | None = None
    score: float | None = None
    fetched_at: str | None = None
    fetch_status: FetchStatus
    engine_used: str = "static"  # static | dynamic | stealthy(命中档位)
    http_status: int | None = None
    word_count: int | None = None
    purified: bool | None = None
    content: str | None = None
    error: str | None = None
    relevance: float | None = None  # 词汇重排分(见 rerank.py);越大越相关
    rank: int | None = None         # 按 relevance 排序后的最终位次(0 起),非抓取完成顺序
    published_date: str | None = None  # 发布日期(SearXNG 透传;有就带没有 null,不静默丢)
    highlights: list[str] = []         # 正文中 query 最相关的几句(词汇抽取,无模型;对齐 Exa)
    highlight_scores: list[float] = []  # 与 highlights 对位的相关分(可溯源,底线③)
    # 页面资产(仅 include_links/include_media 时填充,否则 None 省略;2026-07-29)
    links: list[dict] | None = None
    media: list[dict] | None = None
    # 正文图片内联账目(仅 embed_images 时填充;2026-07-29)
    embed: dict | None = None


class SearchMeta(BaseModel):
    engines_requested: list[str]
    engines_used: list[str]
    engines_failed: list[EngineFailure]
    engines_no_results: list[str] = []  # 请求了但零产出零报错(零匹配或静默失败,不可区分;非确定失败)
    # 熔断剔除的引擎 [{engine,reason,retry_after_s,probing}];空=无熔断(2026-07-30)
    engines_degraded: list[dict] = []
    results_total: int
    took_ms: int
    q_sanitized: bool = False  # bang/filter 防护(!/:/< 前缀 token)是否改写过 q


class FetchMeta(BaseModel):
    target: int          # 想要的成功正文条数(= top_n)
    pool: int            # 候选池:实际考虑过的 URL 数(fast 模式会 > requested)
    requested: int       # 实际发起并拿到结果的条数(= len(items),各状态之和)
    ok: int              # 真拿到正文(内容闸门通过)——只有它算"成功"、计入 target
    failed: int
    timeout: int         # 单 URL 抓取超时(真实抓取结果),非整单预算
    blocked: int
    no_content: int      # HTTP 2xx 但无正文(空壳/反爬页):不算成功,不占 target(design/05)
    cancelled: int       # 够了/预算到而丢弃的候选(禁止静默丢弃:显式计数,pool = requested + cancelled)
    stopped_reason: str  # target_reached | pool_exhausted | budget
    took_ms: int


class ResearchMeta(BaseModel):
    search: SearchMeta
    fetch: FetchMeta


class ResearchResponse(BaseModel):
    query: str
    created_at: str
    items: list[ResearchItem]
    answers: list[dict] = []  # SearXNG infobox/answer 归一后的即时答案(见 searx_client.instant_answers)
    meta: ResearchMeta
