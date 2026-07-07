# -*- coding: utf-8 -*-
"""Pydantic 请求/响应模型(docs/design/03 §4 端点契约)。"""
from typing import Literal

from pydantic import BaseModel, Field

from api import config

# no_content:HTTP 2xx 但净化不出正文(空壳/反爬页/SPA 未渲染)——不算成功,内容闸门(design/05)
FetchStatus = Literal["ok", "failed", "timeout", "blocked", "no_content"]


class ResearchRequest(BaseModel):
    q: str = Field(min_length=1, description="搜索词")
    engines: list[str] | None = Field(
        default=None, description="点名引擎;缺省用精选默认集"
    )
    categories: str | None = None
    language: str = "auto"
    time_range: str | None = None
    safesearch: int = Field(default=0, ge=0, le=2)
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
    engine_used: str = "static"  # 本期恒为 static;二版起 static/dynamic/stealthy
    http_status: int | None = None
    word_count: int | None = None
    purified: bool | None = None
    content: str | None = None
    error: str | None = None


class SearchMeta(BaseModel):
    engines_requested: list[str]
    engines_used: list[str]
    engines_failed: list[EngineFailure]
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
    meta: ResearchMeta
