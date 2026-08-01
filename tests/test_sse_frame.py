"""SSE 帧格式守护(质检 2026-08-01:此前无任何流式测试,帧格式漂移无安全网)。"""
import json

from api.v1 import _sse_frame


def test_frame_has_native_event_line():
    frame = _sse_frame({"object": "research.event", "event": "research.item.completed", "index": 2})
    lines = frame.split("\n")
    assert lines[0] == "event: research.item.completed"      # 原生 event: 行在前
    assert lines[1].startswith("data: ")
    assert frame.endswith("\n\n")                           # SSE 帧以空行终结


def test_frame_data_payload_roundtrip():
    payload = {"object": "research.event", "event": "research.completed", "research": {"fetch": {"ok": 3}}}
    data_line = _sse_frame(payload).split("\n")[1]
    assert json.loads(data_line[len("data: "):]) == payload  # data 行 JSON 完整可解析


def test_frame_unicode_not_escaped():
    frame = _sse_frame({"event": "research.completed", "msg": "中文不转义"})
    assert "中文不转义" in frame                              # ensure_ascii=False
