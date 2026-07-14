# 主流搜索 API 返回格式横评 + hollow 字段决策(2026-07-15)

> 起因:用户问"大厂主流搜索 API 返回什么格式"——当初选 OpenAI 格式只是因为熟悉、便于包成 function tool。
> 本文拉实时官方文档钉死各家字段名,横向对照,判 hollow 该不该补 `published_date` / `highlights` / `summary`。
> 结论先行:**hollow 无意中站对了位置(OpenAI 信封 + LLM 原生结果体);建议补 `published_date`(零成本)与
> `highlights`(用现有 rerank 词汇打分抽取,不上模型);`summary` 需 LLM,暂留给调用方。**

## 一、两大家族(都不是 OpenAI 的 choices/message 形状)

搜索 API 的共同内核只有:一个 `results[]`,每条 `title + url + snippet`。围绕它分两派:

**A. SERP 派**(给搜索结果页渲染):多垂类(web/news/images/videos)、SERP 特性(infobox/知识图谱/答案框/
相关问题)、跨垂类交错排序提示、`position` 排序、每条元数据重(favicon/displayUrl/profile)。
代表:Google CSE、Bing(2025-08 已停服)、Brave、Serper、SerpApi。

**B. LLM/Agent 原生派**(给模型喂料):扁平 `results[]`、相关性 `score`(0–1)、可选抽出的正文
(`content`/`text`/`raw_content`)、可选 `highlights`/`summary`、可选直答 `answer`。**hollow 的对标。**
代表:Tavily、Exa。注:Exa **把 search 与取正文(/contents)拆成两个端点**——与 hollow `/v1/search`+`/v1/fetch` 撞法一致。

唯一用 OpenAI chat/completions 形状"做搜索"的是 **Perplexity/Sonar**,但它是**答案生成 API**(返回 answer +
`citations[]` URL 列表 + `search_results[]{title,url,date}`),不是原始搜索 API。所以"OpenAI 格式"是对话形状,不是搜索形状。

## 二、字段对照(概念 → 各家确切字段名 → hollow)

| 概念 | Google CSE | Bing(停服) | Brave | Serper | Tavily | Exa | **hollow** |
|---|---|---|---|---|---|---|---|
| 标题 | `title` | `name` | `title` | `title` | `title` | `title` | `title` ✅ |
| 链接 | `link` | `url` | `url` | `link` | `url` | `url` | `url` ✅(站现代/多数派) |
| 短摘要 | `snippet` | `snippet` | `description` | `snippet` | `content` | —(用 text/summary) | `snippet`(/search) ✅ |
| 全文正文 | — | — | — | — | `raw_content` | `text` | `content`(/research·/fetch) ✅ |
| 相关性分 | —(靠 position) | —(rankingResponse) | — | `position` | `score` | `score` | `score`(原分)+`relevance`(重排)✅✅ |
| 发布日期 | — | `dateLastCrawled` | `age`/`page_age` | `date` | ✗(仅入参) | `publishedDate` | **缺** ❌ |
| 关键句高亮 | — | — | `extra_snippets` | — | — | `highlights`+`highlightScores` | **缺** ❌ |
| LLM 摘要 | — | — | — | — | — | `summary` | **缺**(需模型)❌ |
| favicon | — | — | `meta_url.favicon` | — | `favicon` | `favicon` | 缺(可选)⚠️ |
| 作者 | — | — | — | — | — | `author` | 缺(冷门)— |
| 直答/信息框 | —(spelling) | (entities) | `infobox`/`faq` | `answerBox`/`knowledgeGraph` | `answer` | — | `answers[]` ✅(归一透出) |
| 来源引擎 | `displayLink` | — | `profile`/`meta_url` | — | — | — | `engine` ✅(可溯源,底线③) |
| 账目/耗时 | `searchInformation` | `rankingResponse` | `query` | `searchParameters` | `response_time`/`usage` | `costDollars` | `search`/`fetch` 账目 ✅(更细) |

各家顶层键(备查):
- **Google CSE**:`kind` `url` `queries` `searchInformation` `items[]` `spelling` `promotions`
- **Brave**:`query` `web{results[]}` `news` `videos` `infobox` `faq` `discussions` `mixed`(交错序)
- **Serper**:`searchParameters` `organic[]` `answerBox` `knowledgeGraph` `peopleAlsoAsk` `relatedSearches`
- **Tavily**:`query` `answer` `results[]` `images` `response_time` `request_id` `auto_parameters` `usage`
- **Exa /search**:`requestId` `results[]` `costDollars` `context`(废弃) `output`(给 outputSchema 时)
- **Exa /contents**:`requestId` `results[]` `statuses[]` `costDollars`

## 三、hollow 的定位判断

1. **信封**:借了 OpenAI 的 `id/object/created` + 统一 `error` + SSE 语义事件——**留着,利于熟悉与工具化**。
   包成 function tool 与返回体形状**无关**:tool 输出可为任意自描述 JSON,不需长成 choices/message。
2. **结果体**:已落在 B 派(`results[]`+`url/title/snippet/score`,research item 带 `content/word_count`,
   `answers` 透出)。**方向正确**,连 search/fetch 拆端点都撞上 Exa。
3. **hollow 反而更强的地方**:`score`(SearXNG 原分)+`relevance`(重排分)**双分可溯源**,多数厂只给一个或没有;
   `engine` 来源标注(底线③);`search`/`fetch` 账目比各家 `response_time` 细得多(engines_failed/各状态显式,底线②)。
4. **命名**:hollow 用 snake_case(`fetch_status`/`word_count`/`fetched_at`)。补新字段**沿用 snake**,不照抄 Exa 的
   camelCase(参考≠照抄)。

## 四、字段补齐决策

| 字段 | 判断 | 成本 | 依据 |
|---|---|---|---|
| **`published_date`** | **建议补**(search.result + research.item) | 近零 | SearXNG 多引擎已返 `publishedDate`,透传即可;审计待办已列;A/B 两派几乎家家有(Exa/Serper/Bing/Brave);研究场景高价值 |
| **`highlights`** | **建议补**(research.item / fetch.item) | 低 | 用**现有 rerank 词汇打分**从净化正文里抽 query 最相关的几句(不上模型,契合零成本哲学);Exa 的差异化卖点,hollow 能廉价复刻;给调用方"最相关段落"而非只有全文 |
| `summary` | **暂不做** | 高(需 LLM) | 违背 hollow 现阶段"不在环里上模型"立场(底线④先校准);留给调用方/vonish 生成。文档标注:调用方的活 |
| `favicon` | 可选 | 极低(URL host 推导) | 纯装饰;A/B 都有;低优先,想补随手补 |
| `author` | 不做 | — | 仅 Exa 有,冷门 |

> 落地提示:`highlights` 复用 `api/rerank.py` 的 `features()`/命中打分——把净化正文切句、按 query 特征打分、取 top-K,
> 挂到 research.item 的 `highlights: [str]`(可加 `highlight_scores: [float]` 对齐 Exa 的可溯源)。`published_date` 从
> SearXNG 结果字段透传到 search.result 与 research.item,缺失则 null(禁止静默丢弃:有就带,没有显式 null)。
