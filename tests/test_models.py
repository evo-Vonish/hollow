# -*- coding: utf-8 -*-
"""api.models 校验(网络无关,CI 可跑)。聚焦 OpenAPI/契约收尾的 query 长度约束。"""
import pytest
from pydantic import ValidationError

from api import config
from api.models import ResearchRequest


def test_query_over_max_length_rejected():
    with pytest.raises(ValidationError):
        ResearchRequest(q="x" * (config.QUERY_MAX_LEN + 1))


def test_query_at_max_length_ok():
    r = ResearchRequest(q="x" * config.QUERY_MAX_LEN)
    assert len(r.q) == config.QUERY_MAX_LEN


def test_empty_query_rejected():
    with pytest.raises(ValidationError):
        ResearchRequest(q="")


def test_defaults_sane():
    r = ResearchRequest(q="hi")
    assert r.page == 1
    assert r.fetch_top_n >= 1
    assert r.mode == config.DEFAULT_MODE
    assert r.include_domains is None and r.exclude_domains is None


@pytest.mark.parametrize("bad", [-1, 0, 21])
def test_page_bounds(bad):
    with pytest.raises(ValidationError):
        ResearchRequest(q="hi", page=bad)


@pytest.mark.parametrize("bad", [-1, 3])
def test_safesearch_bounds(bad):
    with pytest.raises(ValidationError):
        ResearchRequest(q="hi", safesearch=bad)
