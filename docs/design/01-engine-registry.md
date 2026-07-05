# 搜索源注册表设计 · 第一波改造

> 数据文件:[`data/engine_registry.yaml`](../../data/engine_registry.yaml)(343 个源,全量打标)
> 生成脚本思路:解析 `vendor/searxng/searx/settings.yml` → 按"基础名 + 变体后缀继承"分类 → 人工分级字典
> 状态:v1 初稿,tier/type 是可编辑的人工策展数据,欢迎调整

## 两条排序规则

### 规则一:知名度分级(tier)

判断依据是**运营方的知名度与老牌程度**——大厂、老牌的源往往更优质、可靠、抗风险(不会突然消失、结果质量有保障)。

| 级别 | 定义 | 数量 | 典型代表 |
|---|---|---|---|
| **T0** | 全球巨头/顶级机构官方服务,家喻户晓 | 48 | Google 系、Bing、Baidu、Yandex、Yahoo、Wikipedia/Wikimedia 全家、YouTube、GitHub、Reddit、Reuters、PubMed、Apple、Adobe、eBay |
| **T1** | 老牌知名服务或主流生态官方入口 | 88 | DuckDuckGo、Brave、Startpage、Qwant、StackOverflow 系、arXiv、Semantic Scholar、npm/PyPI/crates/Docker Hub、MDN、HuggingFace、IMDb、SoundCloud、Vimeo、OpenStreetMap、DeepL |
| **T2** | 垂直领域知名站/国家级门户/社区大站 | 108 | Mojeek、Anna's Archive、Codeberg、OpenAlex、Pixabay、Bandcamp、Lemmy、tagesschau |
| **T3** | 小众站/自建实例/元搜索代理/暗网/长尾 | 99 | yacy、wiby、mwmbl、piped、各类 xpath 自配小站、onion 搜索 |

**用途**:默认引擎集只从 T0/T1 里选;结果融合排序时可作为权重因子(tier 越高权重越大);T3 默认不启用,仅显式点名可用。

### 规则二:内容类型(type,10 类)

判断依据是**内容域**——搜出来的是什么。注意变体归内容不归运营方:`google images` 属于图片类而非通用类。

| 类型 | 中文 | 数量 | 典型代表 |
|---|---|---|---|
| `web` | 通用网页 | 61 | google、bing、duckduckgo、brave、startpage |
| `knowledge` | 知识百科 | 33 | wikipedia、wikidata、wiktionary、IMDb、openlibrary、词典类 |
| `academic` | 学术科研 | 12 | arxiv、google scholar、semantic scholar、pubmed、crossref、openalex |
| `dev` | 开发技术 | 49 | github、stackoverflow、npm/pypi、MDN、docker hub、arch wiki、huggingface |
| `news` | 新闻资讯 | 27 | google news、bing news、reuters、brave.news、wikinews |
| `images` | 图片设计 | 60 | google images、flickr、unsplash、artstation、图标库、反向搜图 |
| `av` | 视频音频 | 54 | youtube、vimeo、bilibili、soundcloud、bandcamp、播客/电台 |
| `social` | 社交论坛 | 11 | reddit、lemmy、mastodon、sogou wechat |
| `files` | 文件资源 | 20 | 种子站、annas archive、libgen、应用商店(App Store/Play/F-Droid) |
| `life` | 生活工具 | 16 | 地图(OSM/Apple)、天气、翻译(DeepL)、汇率、购物比价 |

### 类型 × 分级 矩阵

```
type         T0   T1   T2   T3  total
web           6   11   10   34     61
knowledge     9    8   10    6     33
academic      2    5    5    0     12
dev           5   17   20    7     49
news          4    9    8    6     27
images        6   18   15   21     60
av           10   15   16   13     54
social        1    1    7    2     11
files         3    1    9    7     20
life          2    3    8    3     16
合计         48   88  108   99    343
```

几个值得注意的分布特征:

- **学术类没有 T3**——学术源天然都是机构背书的,这个类型整体最可靠。
- **web 类 T3 最多(34 个)**——SearXNG 收录了大量小众/自建/元搜索,这正是默认引擎集要绕开的长尾。
- **T0+T1 合计 136 个**,足够覆盖所有 10 个类型的高质量需求。

## 分类方法(可复现)

1. **基础名字典**:每个引擎家族(如 `google`、`brave`)人工指定 `(tier, base_type)`;
2. **变体继承**:`google images`/`brave.news` 这类变体自动继承家族 tier,type 按后缀覆盖(`images→images`、`videos/music→av`、`news→news`、`weather→life`…);
3. **全量校验**:任何未覆盖的名字会让生成脚本报错退出——保证 343 个一个不漏,没有静默丢弃(与 API 层同一条底线)。

## 与网关 API 的衔接(设计意图)

- `POST /v0/search` 的 `engines` 参数不传时,默认集 = **从注册表按 `tier ∈ {T0,T1}` + 类型均衡挑选的精选集**(而非上游 SearXNG 的默认 85 个);
- 未来可支持 `types: ["dev","academic"]` 这样的**按类型选源**参数——把"搜什么类型的内容"作为一等公民,比让调用方背 343 个引擎名友好得多;
- 融合排序时 tier 可作为权重因子(如 T0=1.5 / T1=1.2 / T2=1.0 / T3=0.8),与 SearXNG 原生 score 相乘;
- 注册表里保留了 `upstream_default`(上游默认启停)与 `inactive`(上游已停用,如 google 主引擎因验证码被标 inactive)——**上游的启停状态反映了抓取可用性的现实**,挑默认集时必须参考。

## 已知的策展争议点(欢迎调整)

- `pinterest` 给了 T1(上市大公司,但内容质量有争议);
- `piratebay`/`annas archive`/`z-library` 按知名度给了 T2,但有法律灰色属性,默认集绝不纳入;
- `quark`(阿里)、`360search`、`sogou` 按中国大厂给了 T1,实际抓取可用性待实测;
- 词典类(duden/jisho/wordnik)归了 knowledge 而非单独开"词典类",为守住 ≤10 类的约束;
- `google` 主引擎上游标记 inactive(验证码问题),但 `google cse`(自定义搜索 API)是启用的——这印证了研读结论:Google 直抓在数据中心 IP 上基本不可行。
