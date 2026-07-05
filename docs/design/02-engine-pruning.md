# 搜索源裁剪分析 · 面向场景与需求的踢出决策

> 前置:[`01-engine-registry.md`](./01-engine-registry.md)(343 源注册表)
> 状态:**分析稿,待拍板**。拍板后落入 `data/engine_registry.yaml` 的 `status` 字段。
> 原则:踢出也要显式——每个被踢的源都记录判据,禁止静默消失(与 API 层同一条底线)。

## 一、踢出模型:三层而非一刀切

"踢出"不是一个开关,而是三层:

| 层 | 含义 | 数量 |
|---|---|---|
| **L1 彻底移除** | 不进网关引擎池,调用方点名也不可用 | **124** |
| **L2 池内待命** | 保留在池中,默认不参与,显式点名/按类型选源时可用 | **176** |
| **L3 场景默认集** | 各场景的出厂默认 | **43** |

结构:343 = L1(124) + 池(219),池 = L2(176) + L3(43)。

## 二、L1 彻底移除的五条判据(124 个,有重叠)

### 判据 1:法律/灰色地带(14 个)——无条件踢

`piratebay、1337x、kickass、nyaa、btdigg、solidtorrents、bt4g、tokyotoshokan、Torznab EZTV、annas archive、library genesis、z-library、ahmia、torch`

种子站、影子图书馆、暗网搜索。v2 风险登记册明确"定位为开源研究工具而非内容再分发"——这些源哪怕知名度高(annas archive/libgen 是 T2),对一个公开的研究 API 是纯法律负债。**files 类 20 个里 12 个死于此条**,该类型基本清空(只剩应用商店)。

### 判据 2:需 API 密钥/自建实例/私有连接器(15 个)——现阶段没法用

- 需密钥:`braveapi、youtube_api、flickr_api、wolframalpha_api、google cse`
- 需自建实例:`piped、libretranslate、lingva、mozhi`(代理前端,依赖第三方实例存活)
- 私有连接器:`elasticsearch、findfiles×4、Torznab`(搜的是**你自己的**数据,不是互联网)

这些不是质量问题,是**前提不成立**。其中 `google cse` 值得单独记一笔:它是上游默认启用的唯一 Google 文本入口,但需要自己的 CSE ID + key——**如果将来我们申请了 key,这是接回 Google 的合法通道**,放进"可复活"名单。

### 判据 3:上游标记 inactive(62 个)——上游已宣判

SearXNG 社区把这些标为"彻底停用"(挂了/解析失效/长期无人修)。上游的 inactive 是**用真实运维换来的可用性数据**,我们没理由比维护者更乐观。包括:`google`(主引擎,验证码)、`swisscows/tonline/startpagina/luxxle/tiger/heexy` 等全家、`sina/chinaso`、`pixiv/wallhaven`。

⚠️ 但其中有 6 个是**可惜的**,单列"可复活"名单,条件成熟时优先接回:
`deepl`(需密钥)、`springer nature`(需密钥)、`core.ac.uk`(需密钥)、`astrophysics data system`(需密钥)、`marginalia`(独立索引,质量好,API 变动)、`wolframalpha_api`(需密钥)。——规律很明显:**学术类的"死"多数是死于没密钥,不是死于质量**,拿到密钥就能复活。

### 判据 4:低质元搜索/玩具索引/SEO 聚合(38 个)——污染大于贡献

- 二手聚合(dogpile/zapmeta/infospace/fireball 系/presearch 系):自己不产索引,聚合别人的结果。我们本身就是聚合器,**聚合聚合器 = 双倍延迟 + 双倍重复 + 打分失真**。
- 玩具/自建索引(yacy/mwmbl/wiby/searchmysite/crowdview):索引量太小,回来的结果挤占融合排序的位置。
- 存活的 SEO 小站(fastbot/gabanza/ayo/fynd/vuhuv 系/tusksearch 系/resulthunter/reloado/searchtoday):无来源信誉可言,对"可溯源 Evidence"是反向贡献——**证据的可信度上限就是来源的可信度**。

### 判据 5:与研究定位无关(5 个)

`frinkiac、findthatmeme`(辛普森/迷因截图)、`9gag`(段子)、`geizhals、shopify stock`(购物比价)。研究浏览器不需要。(`giphy/imgur` 虽也偏娱乐,但 T1 知名且媒体检索有真实用途,降到 L2 而非踢出。)

## 三、踢完之后:池子的质量结构(219 个)

```
type         T0   T1   T2   T3  total   变化
web           4   10    4    0     18   61→18,砍掉70%,全是长尾
knowledge     9    7    9    6     31   基本无伤
academic      2    3    4    0      9   密钥类暂离场,拿钥匙可回9→12+
dev           3   16   19    6     44   几乎无伤——本来就干净
news          3    8    2    1     14   地区小报留L2
images        6   16    9   10     41   图库/图标保留待命
av            8   15   10    4     37   主流平台保留待命
social        1    1    6    1      9   reddit+fediverse
files         3    1    2    1      7   只剩应用商店(App Store/Play/F-Droid/Steam)
life          1    2    6    0      9   地图/天气/词典工具
```

关键观察:**web 类被砍得最狠(61→18)且 T3 清零**——被踢的全是元搜索和 SEO 站,证明判据 4 打得准;**dev/knowledge/academic 几乎无伤**——研究型产品最需要的三个类型天然干净。

## 四、场景视角检验:L3 默认集草案(43 个)

按场景倒推"默认该开谁",每个场景 4~10 个,少而精:

| 场景 | 默认源 | 备注 |
|---|---|---|
| **通用研究**(出厂默认) | brave、duckduckgo、startpage、mojeek、wikipedia、wikidata | 4 个独立索引 + 知识锚点;**无 Google 系**(inactive)、无 DDG 以外反爬重灾区 |
| **开发技术** | github、stackoverflow、askubuntu、superuser、mdn、hackernews、docker hub、pypi、npm、arch linux wiki | 全 T0/T1,官方 API 居多,最稳的场景 |
| **学术科研** | arxiv、semantic scholar、pubmed、crossref、openalex、google scholar | google scholar 有验证码风险,标"降级容忍":挂了靠 engines_failed 显式暴露 |
| **新闻时效** | brave.news、duckduckgo news、bing news、google news、reuters、wikinews | google/bing news 抓取风险中等,同上容忍 |
| **图片** | bing images、duckduckgo images、brave.images、wikicommons.images、unsplash、flickr | |
| **视频音频** | youtube、vimeo、dailymotion、soundcloud、wikicommons.videos | youtube 用 noapi 模块,有反爬风险 |
| **中文** | baidu、quark、sogou、bilibili | ⚠️ 全部待实测——上游默认全关(D),真实可用性未知,这是中文场景最大的坑 |

检验结论:43 个默认源**全部落在池内、无一触碰 L1 判据**,且每个场景都有 ≥1 个官方 API 型的压舱石(wikipedia/github/arxiv/pubmed/wikicommons)。

## 五、遗留决策点(需要拍板)

1. **DuckDuckGo 进不进通用默认集?** 研读结论:反爬最重(vqd、IP 级封禁),但它是独立索引里质量最好的之一。我的建议:**进,但网关侧对它单独限速**,封禁风险靠 engines_failed 透明化。
2. **中文场景**:baidu/quark/sogou 上游全关,可能意味着"社区试过、不好使"。需要 P1 起实例后第一批实测;若全灭,中文场景可能要靠 bing(国际版)+ 中文 query 兜底。
3. **alt-video 平台**(bitchute/odysee/rumble):内容风险高,但"研究这些平台本身"是正当需求。现放 L2(点名可用),要不要再收紧?
4. **应用商店/steam**:留 L2 没什么成本,但如果"研究浏览器不需要搜 App",可以再砍 5 个。
5. **密钥预算**:可复活名单里 deepl/springer/core/ADS/google cse 都卡在密钥上。如果愿意花一点钱/申请配额,学术和翻译能力会明显上一个台阶——这是花小钱办大事的点。

## 六、执行方式(拍板后)

在 `data/engine_registry.yaml` 每个条目加 `status: removed | pool | default`,`removed` 的带 `removed_reason: legal|keyed|inactive|lowq|offbrand`;默认集另加 `scenes: [general, dev, ...]`。生成脚本同步更新,保持"一个不漏、每个有账"的可复现性。
