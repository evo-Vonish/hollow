# 抓取模式 `mode`:一根旋钮 速度/广度 ↔ 质量/难度

> 状态:已落地(2026-07-07)。实现:`api/config.py` MODE_PRESETS、`api/orchestrator.py` 池填充循环。
> 缘起:用户提出研究型抓取的经济学——"与其在难爬 URL 上耗等待,不如召回更多、只吃容易爬的秒级 URL"。
> 决策:用**一个参数**表达"广度(速度)优先 ↔ 质量(难度)优先",而非两个正交开关。

## 一、核心思想:目标从"爬 top_n 条"改成"凑够 top_n 条成功正文"

研究场景里 URL 高度可替代——要的是"关于 X 的优质正文",不是"必须这一个 URL"。而爬取成本极不均(arxiv 0.4s vs Cloudflare 站三档 70s)。所以:**超召回一个候选池,先到先得凑够 K 条 ok 就停,难爬的还没爬完就被砍掉**——不预判哪条难,谁快谁进。

代价:放弃"只在难爬 URL 上的独家内容"(低可替代场景)。所以做成可调:默认假设可替代(研究多数如此),死磕留给 `thorough`。

## 二、`mode` 预设(config.MODE_PRESETS)

| mode | 候选池 pool | 升级链 escalate | 单URL超时 | 定位 |
|---|---|---|---|---|
| `fast` | top_n×3(封顶 24) | 关 | 8s | 广度/速度:超召回,凑够即砍,只吃 easy |
| `balanced`(默认) | top_n | 开(失败兜底) | 15s | = 本参数引入前的行为 |
| `thorough` | top_n | 全开三档 | 30s | 质量/难度:死磕每条,最有耐心 |

- **一个参数管一束**:`mode` 只给 `escalate`/`timeout`/池倍数设默认值。仍可单独传 `escalate`/`timeout` **覆盖**预设(preset 语义,不锁死)。
- **不传 mode = balanced = 今天行为**,完全向后兼容。
- `fast` 的"凑够即砍"天然实现"面向 easy/秒级 URL":一条卡在慢档时,easy 的已填满名额,慢的被 cancel。

## 三、账目(FetchMeta):被砍的候选也显式,禁止静默丢弃

```jsonc
"fetch": {
  "target": 3,               // 想要的成功正文条数(= top_n)
  "pool": 9,                 // 候选池:实际考虑过的 URL 数(fast 会 > requested)
  "requested": 3,            // 实际发起并拿到结果的条数(= len(items))
  "ok": 3, "failed": 0, "timeout": 0, "blocked": 0,
  "cancelled": 6,            // 够了/预算到而丢弃的候选(不静默:计数)
  "stopped_reason": "target_reached",  // target_reached | pool_exhausted | budget
  "took_ms": 1592
}
```

**不变量(契约,orchestrator 顶部 docstring):**
- `requested == len(items) == ok+failed+timeout+blocked`(每条 item 有状态)
- `pool == requested + cancelled`(候选无一静默丢弃)
- `ok <= target`(够了就停)

`stopped_reason` 三态显式说明为何停:凑够了 / 候选池爬完仍不够 / 预算到点。

## 四、与既有参数的关系

- `top_n`:语义从"最大抓取条数"变为"想要的**成功正文条数**(target ok)"。fast 模式下 pool 会超出它。
- `budget`:整单预算不变,到点即停,未完成的计入 `cancelled`(不再补 timeout 占位 item)。
- `escalate`/`timeout`:缺省 `None` = 跟随 mode;显式传值覆盖。
- `concurrency`:并行抓取数,与 pool 独立(pool 大但仍受 concurrency 限流)。

## 五、战略意义

fast 让爬取"只吃 easy、弃难的",于是**召回排序质量成为系统杠杆**——优化压力从"爬取层反反爬"(打不赢 Google/CF 军备)转移到"召回层排序/去重/质量信号"(可借力 SearXNG + 自研)。与"搜索借力、别硬爬"一条线。
