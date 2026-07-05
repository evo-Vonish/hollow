# -*- coding: utf-8 -*-
"""Pydantic 请求/响应模型(docs/design/03 §4 端点契约)。"""
from typing import Literal

from pydantic import BaseModel, Field

from api import config

FetchStatus = Literal["ok", "failed", "timeout", "blocked"]


class ResearchRequest(BaseModel):
    q: str = Field(min_length=1, description="搜索词")
    engines: list[str] | None = Field(
        default=None, description="点名引擎;缺省用精选默认集"
    )
    categories: str | None = None
    language: str = "auto"
    time_range: str | None = None
    safesearch: int = Field(default=0, ge=0, le=2)
    fetch_top_n: int = Field(
        default=config.FETCH_TOP_N_DEFAULT, ge=1, le=config.FETCH_TOP_N_MAX
    )
    purify: bool = True
    fetch_timeout: float = Field(default=config.FETCH_TIMEOUT, gt=0, le=60)


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
    requested: int
    ok: int
    failed: int
    timeout: int
    blocked: int
    took_ms: int


class ResearchMeta(BaseModel):
    search: SearchMeta
    fetch: FetchMeta


class ResearchResponse(BaseModel):
    query: str
    created_at: str
    items: list[ResearchItem]
    meta: ResearchMeta
