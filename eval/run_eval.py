# -*- coding: utf-8 -*-
"""召回质量离线评测:query 集 → /v1/search 排序结果 → 裁判打相关性分 → nDCG@k / P@k / MRR。

用法(网关须在 127.0.0.1:8080):
  # 真裁判(先设 HOLLOW_EVAL_JUDGE_URL 指向 vonish GLM 的 chat/completions):
  .venv-api/bin/python eval/run_eval.py --label baseline
  # 仅验证管线(mock 裁判,分无意义):
  .venv-api/bin/python eval/run_eval.py --judge mock --label smoke
结果存 eval/results/<label>.json,便于改 rerank 权重后 A/B 对比。
"""
import argparse
import asyncio
import json
import math
import os
import statistics
from datetime import datetime, timezone

import httpx
import yaml

import judge as J

HERE = os.path.dirname(os.path.abspath(__file__))
GATEWAY = os.environ.get("HOLLOW_EVAL_GATEWAY", "http://127.0.0.1:8080")
TOP_K = int(os.environ.get("HOLLOW_EVAL_TOP_K", "10"))  # 每 query 判前 K 条
REL_THRESHOLD = 2  # P@k / MRR 里算"相关"的最低分


def _dcg(rels: list[int]) -> float:
    return sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rels))


def ndcg_at_k(rels: list[int], k: int) -> float:
    ideal = sorted(rels, reverse=True)
    idcg = _dcg(ideal[:k])
    return _dcg(rels[:k]) / idcg if idcg > 0 else 0.0


def precision_at_k(rels: list[int], k: int) -> float:
    top = rels[:k]
    return sum(1 for r in top if r >= REL_THRESHOLD) / k  # 固定分母 k(缺位算未命中)


def mrr(rels: list[int]) -> float:
    for i, r in enumerate(rels):
        if r >= REL_THRESHOLD:
            return 1.0 / (i + 1)
    return 0.0


async def _search(client: httpx.AsyncClient, query: str, scene: str) -> list[dict]:
    body = {"query": query}
    if scene and scene != "general":
        body["scenes"] = [scene]
    r = await client.post(f"{GATEWAY}/v1/search", json=body, timeout=40)
    r.raise_for_status()
    return r.json().get("results", [])


async def _eval_query(client: httpx.AsyncClient, judge_fn, q: dict, sem: asyncio.Semaphore) -> dict:
    results = await _search(client, q["query"], q.get("scene", "general"))
    top = results[:TOP_K]

    async def _j(res):
        async with sem:
            try:
                return await judge_fn(client, q["query"], res)
            except Exception as e:  # 单条裁判失败按 0 计并记下,不整轮崩
                return {"_err": str(e)[:120]}

    scored = await asyncio.gather(*[_j(r) for r in top])
    errs = [s["_err"] for s in scored if isinstance(s, dict)]
    rels = [0 if isinstance(s, dict) else int(s) for s in scored]
    return {
        "id": q["id"], "scene": q.get("scene", "general"), "query": q["query"],
        "n_results": len(results), "n_judged": len(top),
        "rels": rels,
        "ndcg@5": round(ndcg_at_k(rels, 5), 4),
        "ndcg@10": round(ndcg_at_k(rels, 10), 4),
        "p@5": round(precision_at_k(rels, 5), 4),
        "mrr": round(mrr(rels), 4),
        "errors": errs,
        "top": [{"rank": i, "rel": rels[i], "url": top[i].get("url"),
                 "title": (top[i].get("title") or "")[:80]} for i in range(len(top))],
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--judge", choices=["llm", "mock"], default="llm")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    with open(os.path.join(HERE, "queries.yaml"), encoding="utf-8") as f:
        queries = yaml.safe_load(f)
    judge_fn = J.judge_mock if args.judge == "mock" else J.judge_llm
    sem = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(trust_env=False) as client:
        per_query = []
        for q in queries:  # 逐 query 串行(裁判并发在 query 内),日志清晰、对上游温和
            row = await _eval_query(client, judge_fn, q, sem)
            per_query.append(row)
            print(f"  {row['id']:<16} nDCG@10={row['ndcg@10']:.3f} P@5={row['p@5']:.3f} "
                  f"MRR={row['mrr']:.3f}  ({row['n_results']} results)"
                  + (f"  ERR×{len(row['errors'])}" if row['errors'] else ""))

    def agg(key):
        return round(statistics.mean(r[key] for r in per_query), 4)

    scenes = sorted(set(r["scene"] for r in per_query))
    by_scene = {s: round(statistics.mean(r["ndcg@10"] for r in per_query if r["scene"] == s), 4)
                for s in scenes}
    summary = {
        "label": args.label, "judge": args.judge,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rerank_weights": {
            "title": os.environ.get("HOLLOW_RERANK_W_TITLE", "3.0(default)"),
            "snippet": os.environ.get("HOLLOW_RERANK_W_SNIPPET", "1.0(default)"),
            "prior": os.environ.get("HOLLOW_RERANK_W_PRIOR", "0.5(default)"),
        },
        "n_queries": len(per_query),
        "mean": {"ndcg@5": agg("ndcg@5"), "ndcg@10": agg("ndcg@10"),
                 "p@5": agg("p@5"), "mrr": agg("mrr")},
        "ndcg@10_by_scene": by_scene,
    }
    print("\n=== SUMMARY ===")
    print(json.dumps(summary["mean"], indent=2))
    print("ndcg@10 by scene:", json.dumps(by_scene, ensure_ascii=False))

    outdir = os.path.join(HERE, "results")
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, f"{args.label}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "per_query": per_query}, f, ensure_ascii=False, indent=2)
    print(f"saved -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
