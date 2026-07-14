# 召回质量离线评测台(2026-07-15)

把"调 rerank 权重/阈值"从**肉眼猜**变成**可测量迭代**——这是落实底线④(阈值校准后上线)的前提。

## 组成
- `queries.yaml` — 跨场景代表性 query 集(dev/academic/zh/general),`note` 是人工参考(可作金标锚点)。
- `judge.py` — 相关性裁判(0–3)。**OpenAI Chat Completions 兼容,可配端点**;另有 `mock`(纯词汇,仅验管线)。
  **裁判只在离线用模型,服务路径仍零模型**(不违反"参考≠照抄/不塞模型"的底线)。
- `run_eval.py` — 打 `/v1/search` 拿排序结果 → 裁判打分 → nDCG@k / P@5 / MRR 聚合 → 存 `results/<label>.json`。

## 跑法(网关须在 127.0.0.1:8080)
```bash
# 真裁判:先把端点指向 vonish 的 GLM(OpenAI 兼容 chat/completions)
export HOLLOW_EVAL_JUDGE_URL=http://127.0.0.1:<port>/v1/chat/completions
export HOLLOW_EVAL_JUDGE_MODEL=glm-5.2         # 默认
.venv-api/bin/python eval/run_eval.py --label baseline
# 仅验管线(分无意义):
.venv-api/bin/python eval/run_eval.py --judge mock --label smoke
# A/B 调参:改权重再跑,对比 results/*.json
HOLLOW_RERANK_W_TITLE=4 .venv-api/bin/python eval/run_eval.py --label w-title-4
```

## 指标怎么读(重要)
- **nDCG@k** 量的是**排序**(把召回到的好结果排前没有),**不量召回**——召回全是垃圾时它照样能 1.0(垃圾里最不差的排在前)。
- **P@5**(前 5 里 rel≥2 的比例)和 **max rel**(有没有 rel=3)才暴露"到底有没有好结果"。**两者一起看**,别只盯 nDCG。

## 手工基线(Claude 当裁判,2026-07-15,首轮 6 query)
发现:**EN dev/general 查询的瓶颈是召回(候选集),不是排序**——rerank 已把最不差的排前(nDCG 常 1.0),
但候选里塞满关键词命中的噪声(Docker Hub 镜像、MDN 词条、arxiv 物理论文),且缺了显然该有的源(realpython、postgres 官方文档、
产品评测)。zh(bilibili)查询召回+排序都好。详见会话记录 / docs/design 后续。GLM 裁判到位后规模化复现。
