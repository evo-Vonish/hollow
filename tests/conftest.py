# -*- coding: utf-8 -*-
"""pytest 全局配置。

单元测试(默认)全部**网络无关**:纯函数 + monkeypatch,CI 里不起 SearXNG/不联网即可跑。
集成测试标 @pytest.mark.integration,需本机活网关,默认跳过——设 HOLLOW_TEST_LIVE=1 才跑。
"""
import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("HOLLOW_TEST_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="需活网关;设 HOLLOW_TEST_LIVE=1 才跑")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_live)
