# 召回质量首轮基线 + 排序层 post-v1 路线(2026-07-15)

> 用 `eval/` 评测台 + Claude 当裁判判了 6 个 query 的实际 `/v1/search` 排序(GLM 裁判到位后规模化复现)。
> **一句话**:v0.1.0 里 EN 查询的瓶颈是**召回/候选质量**,不是 rerank 权重;调权重收益有限(候选是垃圾时排不出好的)。

## 首轮基线(rel 0–3,均值 nDCG@10≈0.79 / P@5≈0.43)

| query | nDCG@10 | P@5 | max rel | 判读 |
|---|---|---|---|---|
| dev-asyncio | ~1.00 | 0.20 | 2 | 🔴 候选=SO(相关)+ 一堆 Docker Hub 镜像/MDN 词条(命中 event/loop/python);无 realpython/官方 docs |
| dev-pg-index | 0.00 | 0.00 | 0 | 🔴 全废:命中 postgres(docker镜像)/which(MDN UIEvent.which)/index(MDN z-index);零相关 |
| gen-headphones | ~1.00 | 0.00 | 1 | 🔴 全废:清一色 arxiv 量子物理"noise"论文;无产品评测 |
| acad-attention | ~0.76 | 0.40 | 3 | 🟡 排序次优:综述该 #0 却在 #1;应用论文灌水 |
| zh-libattery | ~0.96 | 1.00 | 3 | 🟢 好(bilibili) |
| zh-nabattery | ~1.00 | 1.00 | 3 | 🟢 好(bilibili) |

## 三条立得住的结论

1. **nDCG 会骗人**——它量"排序"不量"召回":候选全垃圾时它照样 ~1.0(垃圾里最不差的排前)。**必须同时看 P@5 + max-rel**。
2. **瓶颈在候选质量**:rerank 已尽责(把最不差的排前);病在①候选塞满关键词命中噪声②缺显然该有的源。
3. **病根**:(a) 场景引擎集选错——`dev` 混进 Docker Hub/MDN 这类关键词误命中源、`general`/产品查询被 arxiv 灌爆;
   (b) 退化过滤太弱——只挡"标题零命中+snippet 空",Docker Hub 的"python"照样进池。zh(bilibili)不受影响。

## post-v1 路线(按数据指向的杠杆排序)

1. **候选质量(最大杠杆)**:场景引擎集重审(arxiv 只进 academic;dev 剔除/降权 Docker Hub、MDN 词条类);
   按 query 类型选引擎;强化噪声/退化过滤(域黑名单 + "标题命中但明显离题"识别)。
2. **排序微调**:扫 `HOLLOW_RERANK_W_*` 网格(评测台 A/B),修 acad-attention 那类"综述该置顶"问题。
3. **轻语义(待定,需数据支撑)**:候选质量+排序到位后,再判断服务路径要不要加一层便宜 embedding rerank——
   那时是有 nDCG 数据的决策,不是拍脑袋。**服务路径是否引入模型是重决策,须先讨论**(裁判用模型是离线的,不算)。

> 校准前置条件:GLM 裁判接上(`HOLLOW_EVAL_JUDGE_URL`)+ query 集扩到几十条,把 6 条手判基线规模化。
