# -*- coding: utf-8 -*-
"""Unit tests for api/engine_health.py(引擎自适应健康退避;纯内存状态机,无网络)。

覆盖:阈值熔断、指数退避、退避期过滤、到期探测放行(probing 标注)、
探测失败升级、成功归零恢复、账目形状、reset。
"""
import time

import pytest

from api import config, engine_health as eh


@pytest.fixture(autouse=True)
def _clean():
    eh.reset()
    yield
    eh.reset()


def _trip(engine: str, n: int):
    for _ in range(n):
        eh.record_failure(engine, "CAPTCHA (suspended_time=3600)")


def test_no_degradation_below_threshold():
    _trip("baidu", config.ENGINE_FAIL_THRESHOLD - 1)
    allowed, degraded = eh.filter_engines(["baidu", "sogou"])
    assert allowed == ["baidu", "sogou"] and degraded == []


def test_degrade_at_threshold_and_filter():
    _trip("baidu", config.ENGINE_FAIL_THRESHOLD)
    allowed, degraded = eh.filter_engines(["baidu", "sogou"])
    assert allowed == ["sogou"]
    assert degraded[0]["engine"] == "baidu"
    assert degraded[0]["probing"] is False
    assert 0 < degraded[0]["retry_after_s"] <= config.ENGINE_BACKOFF_BASE_S
    assert "CAPTCHA" in degraded[0]["reason"]


def test_backoff_escalates_on_probe_failure():
    _trip("baidu", config.ENGINE_FAIL_THRESHOLD)
    # 快进到退避到期 → 探测放行
    st = eh._state["baidu"]
    st["until"] = time.monotonic() - 1
    allowed, degraded = eh.filter_engines(["baidu"])
    assert allowed == ["baidu"] and degraded[0]["probing"] is True
    # 探测失败 → 退避升级一档(时长翻倍)
    lv0 = st["level"]
    eh.record_failure("baidu", "timeout")
    st2 = eh._state["baidu"]
    assert st2["level"] == lv0 + 1
    allowed2, _ = eh.filter_engines(["baidu"])
    assert allowed2 == []  # 重新进入退避期


def test_success_recovers():
    _trip("baidu", config.ENGINE_FAIL_THRESHOLD)
    eh.record_success("baidu")
    allowed, degraded = eh.filter_engines(["baidu"])
    assert allowed == ["baidu"] and degraded == []
    # 恢复后再失败需重新累计阈值
    eh.record_failure("baidu", "timeout")
    allowed2, _ = eh.filter_engines(["baidu"])
    assert allowed2 == ["baidu"]


def test_backoff_capped_at_max():
    st = eh._state.setdefault("x", {"fails": 99, "until": None, "level": -1, "reason": ""})
    st["level"] = 20
    assert eh._backoff_s(20) == config.ENGINE_BACKOFF_MAX_S


def test_probe_passage_marks_accounting():
    _trip("quark", config.ENGINE_FAIL_THRESHOLD)
    eh._state["quark"]["until"] = time.monotonic() - 0.5
    allowed, degraded = eh.filter_engines(["quark", "baidu"])
    assert set(allowed) == {"quark", "baidu"}
    assert degraded[0]["probing"] is True and degraded[0]["retry_after_s"] == 0
