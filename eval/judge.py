# -*- coding: utf-8 -*-
"""相关性裁判(离线评测用)。**只在离线校准用模型**,服务路径仍零模型(不违反底线)。

裁判是 OpenAI Chat Completions 兼容的,可配端点——vonish 的本地 GLM 给出 API 地址后一行 env 接上:
  HOLLOW_EVAL_JUDGE_URL=http://127.0.0.1:<port>/v1/chat/completions
  HOLLOW_EVAL_JUDGE_MODEL=glm-5.2      (默认)
  HOLLOW_EVAL_JUDGE_KEY=<若需鉴权>     (默认空)
另有 mock 裁判(纯词汇)仅用于验证跑分器管线,不产生有意义的质量分。
"""
import json
import os
import re

import httpx

JUDGE_URL = os.environ.get("HOLLOW_EVAL_JUDGE_URL", "").strip()
JUDGE_MODEL = os.environ.get("HOLLOW_EVAL_JUDGE_MODEL", "glm-5.2").strip()
JUDGE_KEY = os.environ.get("HOLLOW_EVAL_JUDGE_KEY", "").strip()

RUBRIC = (
    "You are a strict search-relevance judge. Given a QUERY and one SEARCH RESULT "
    "(title + snippet + url), rate how well the result matches the query's intent, 0-3:\n"
    "3 = highly relevant, directly answers/addresses the query\n"
    "2 = relevant, clearly on-topic and useful\n"
    "1 = marginally related / tangential\n"
    "0 = irrelevant / off-topic / spam / pure navigation or listing page\n"
    "Judge ONLY from the title+snippet+url shown (do not assume unshown content). "
    'Reply with ONLY a compact JSON object: {"score": <int 0-3>, "reason": "<=12 words"}.'
)


def _result_text(r: dict) -> str:
    return (f"QUERY: {r['_query']}\n\nRESULT:\n"
            f"title: {r.get('title')}\n"
            f"url: {r.get('url')}\n"
            f"snippet: {(r.get('snippet') or '')[:500]}")


def _parse_score(text: str) -> int:
    try:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            obj = json.loads(m.group(0))
            s = int(obj.get("score"))
            return max(0, min(3, s))
    except (ValueError, TypeError):
        pass
    m = re.search(r"[0-3]", text)  # 兜底:第一个 0-3 数字
    return int(m.group(0)) if m else 0


async def judge_llm(client: httpx.AsyncClient, query: str, result: dict) -> int:
    if not JUDGE_URL:
        raise RuntimeError(
            "HOLLOW_EVAL_JUDGE_URL 未设置。给出 vonish GLM 的 OpenAI 兼容 chat/completions 端点,"
            "或用 --judge mock 仅验证管线。")
    payload = {
        "model": JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": _result_text({**result, "_query": query})},
        ],
        "temperature": 0,
        "max_tokens": 60,
    }
    headers = {"Authorization": f"Bearer {JUDGE_KEY}"} if JUDGE_KEY else {}
    resp = await client.post(JUDGE_URL, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_score(content)


# --- mock 裁判:纯词汇重叠伪分,仅用于验证跑分器管线(不产生真实质量信号)---
_WORD = re.compile(r"[a-z0-9一-鿿]{2,}")


async def judge_mock(client: httpx.AsyncClient, query: str, result: dict) -> int:
    q = set(_WORD.findall(query.lower()))
    t = set(_WORD.findall(((result.get("title") or "") + " " + (result.get("snippet") or "")).lower()))
    if not q:
        return 0
    hit = len(q & t) / len(q)
    return 3 if hit >= 0.6 else 2 if hit >= 0.35 else 1 if hit > 0 else 0
