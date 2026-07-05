# -*- coding: utf-8 -*-
"""加载 data/engine_registry.yaml:全量条目、场景默认集、L1 removed 账目。

注册表是"一个不漏、每个有账"的权威源(docs/design/02);网关按场景选源、
校验点名引擎(拒 L1、拒未知名——SearXNG 对未知 engines 会静默回退默认集,
必须在网关层挡住)都以此为准。导入期加载,文件缺失/损坏直接快速失败。
"""
from pathlib import Path

import yaml

_REG_PATH = Path(__file__).resolve().parent.parent / "data" / "engine_registry.yaml"
_data = yaml.safe_load(_REG_PATH.read_text(encoding="utf-8"))

# 全量条目(dict:name/module/tier/type/cats/status/removed_reason?/scenes?/note?)
ENGINES: list[dict] = list(_data["engines"])

# 场景 -> 默认引擎名列表(L3,docs/design/02 §四 + 2026-07-06 拍板)
SCENES: dict[str, list[str]] = {k: list(v) for k, v in _data["meta"]["scenes"].items()}

ALL_NAMES: set[str] = {e["name"] for e in ENGINES}

# L1 彻底移除:name -> removed_reason(点名也不可用)
REMOVED: dict[str, str] = {
    e["name"]: e.get("removed_reason", "removed")
    for e in ENGINES if e.get("status") == "removed"
}
