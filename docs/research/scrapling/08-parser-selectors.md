# 08 · Parser 与选择器(Selector / TextHandler / translator)

> 研读范围(相对 `vendor/scrapling/`,v0.4.10 未改源码):
> `scrapling/parser.py`、`scrapling/core/custom_types.py`、`scrapling/core/translator.py`、
> `docs/parsing/`、`docs/api-reference/selector.md`
>
> 写给以后维护 AI Research Browser 网关封装的自己。结论均标注源码出处;源码与 docs 冲突以源码为准。

---

## 0. 一句话结论(给赶时间的自己)

- **Fetcher 返回的 `Response` 就是一个 `Selector` 子类**(`engines/toolbelt/custom.py:28` `class Response(Selector)`)。所以 `resp.css(...)`、`resp.get_all_text()`、`resp.body` 等解析能力**开箱即用**,不用再 `Selector(resp.html_content)` 包一层。
- **P2 判定"正文过薄"最省心可靠的写法**:`len(resp.get_all_text(strip=True))`(默认已忽略 `<script>/<style>`)。SPA 空壳这个值会非常小。
- **P3 的正文抽取交给 trafilatura**,喂给它 **`resp.body`(原始未改动的 HTML bytes)**。Scrapling 的 `get_all_text()` 只是把所有可见文本节点拼起来,**不做正文/样板剔除**(nav、footer、广告全都在里面),不能替代 trafilatura。

---

## 1. 职责概述

`parser.py` 是 Scrapling 的纯解析层,底层是 `lxml.html`。核心两个类:

| 类 | 定义 | 作用 |
|---|---|---|
| `Selector` | `parser.py:64` | 单个 HTML 文档 / 单个元素的包装器。CSS/XPath/文本/正则/自适应选择都在这里。 |
| `Selectors` | `parser.py:1202` | `list[Selector]` 子类,选择结果的容器,附带 `.css/.xpath/.re/.filter/.first/.get` 等批量方法。 |
| `Adaptor / Adaptors` | `parser.py:1382-1383` | 旧名字别名,`Adaptor = Selector`。老文档里的 `Adaptor` 就是现在的 `Selector`。 |

设计上**没有**继承 `lxml.html.HtmlElement`(注释 `parser.py:98-101`:lxml 元素不可 pickle),而是持有一个 `_root`(`HtmlElement` 或文本节点),对外提供简化接口。

配套的文本类型在 `custom_types.py`:

| 类 | 定义 | 作用 |
|---|---|---|
| `TextHandler` | `custom_types.py:29` | `str` 子类,附加 `.clean()`、`.re()`、`.re_first()`、`.json()`。所有 `.text`/`get_all_text()` 返回它。 |
| `TextHandlers` | `custom_types.py:210` | `list[TextHandler]`,附带 `.re/.re_first/.get/.getall`。 |
| `AttributesHandler` | `custom_types.py:285` | 只读属性映射(`MappingProxyType`),`.attrib` 返回它。 |

`translator.py` 只干一件事:把 CSS 翻成 XPath,并**扩展了 `::text` 和 `::attr(name)` 两个伪元素**(`translator.py:80-119`),对齐 Parsel/Scrapy 语法。`css_to_xpath()` 带 `lru_cache(256)`(`translator.py:131`)。

---

## 2. 关键 API / 参数详解

### 2.1 构造 `Selector`(`parser.py:80-181`)

```python
Selector(content=None, url="", encoding="utf-8", huge_tree=True,
         root=None, keep_comments=False, keep_cdata=False,
         adaptive=False, storage=SQLiteStorageSystem, storage_args=None)
```

要点(源码):
- `content` 必须是 `str` 或 `bytes`,否则 `TypeError`(`parser.py:139`);`content` 与 `root` 至少给一个,否则 `ValueError`(`parser.py:118-119`)。
- 解析时会 `replace("\x00","")` 去空字节;空串回退成 `<html/>`(`parser.py:135-137`)。
- **底层 lxml `HTMLParser` 参数**(`parser.py:142-151`):`recover=True`、`remove_blank_text=True`、`remove_comments=(not keep_comments)`、`strip_cdata=(not keep_cdata)`、`huge_tree=True`。也就是说**默认丢弃注释、丢弃 CDATA、合并空白**。
- `_raw_body = content`(`parser.py:154`):原始输入被完整保存,后面 `.body` / `.json()` 会用它。
- `adaptive=False` 是全局开关,关掉时所有 auto-match 相关参数被忽略并 `log.warning`(`parser.py:661-686`)。

### 2.2 选择方法

| 方法 | 定义 | 返回 | 备注 |
|---|---|---|---|
| `css(selector, identifier="", adaptive=False, auto_save=False, percentage=40)` | `parser.py:568` | `Selectors` | CSS3 → 内部转 XPath 再走 `xpath()`。支持 `::text` / `::attr(x)`。非法选择器抛 `SelectorSyntaxError`(`parser.py:622-626`)。 |
| `xpath(selector, identifier="", adaptive=False, auto_save=False, percentage=40, **kwargs)` | `parser.py:628` | `Selectors` | `**kwargs` 作为 XPath 变量传入。非法抛 `SelectorSyntaxError`(`parser.py:690-696`)。 |
| `find_all(*args, **kwargs)` | `parser.py:698` | `Selectors` | BS4 风格。args 可为标签名/标签名可迭代/`dict` 属性/`re.Pattern`/可调用过滤器;kwargs 为属性过滤(保留字用 `class_`/`for_`,`parser.py:51-54`)。 |
| `find(*args, **kwargs)` | `parser.py:792` | `Selector \| None` | `find_all` 的第一个。 |
| `find_by_text(text, first_match=True, partial=False, case_sensitive=False, clean_match=True)` | `parser.py:1096` | `Selector \| Selectors` | 按文本内容找。 |
| `find_by_regex(query, first_match=True, case_sensitive=False, clean_match=True)` | `parser.py:1162` | `Selector \| Selectors` | 按正则找元素。 |
| `find_similar(similarity_threshold=0.2, ignore_attributes=('href','src'), match_text=False)` | `parser.py:1015` | `Selectors` | 找结构相似的兄弟元素(AutoScraper 思路)。 |

导航属性:`parent`/`children`/`siblings`/`next`/`previous`/`below_elements`/`path`/`iterancestors()`/`find_ancestor()`(`parser.py:385-462`)。`below_elements`(`parser.py:391`)= 当前元素下所有后代元素。

`Selectors` 容器额外方法(`parser.py:1202-1378`):`.css/.xpath/.re/.re_first`(逐元素并 flatten)、`.filter(func)`、`.search(func)`、`.first`、`.last`、`.length`、`.get(default)`、`.getall()`(别名 `.extract_first`/`.extract`)。

### 2.3 文本 / 序列化提取(**P2/P3 最关心**)

| 成员 | 定义 | 返回 | 语义 |
|---|---|---|---|
| `.text` (property) | `parser.py:268-277` | `TextHandler` | **只取该元素的直接文本**(`self._root.text`),不含子孙。文本节点则返回其字符串。 |
| `get_all_text(separator="\n", strip=False, ignore_tags=("script","style"), valid_values=True)` | `parser.py:279-329` | `TextHandler` | **递归**收集所有后代可见文本节点并拼接。默认忽略 `script`/`style` 及其内部;`valid_values=True` 跳过空白节点。 |
| `.html_content` (property) | `parser.py:344-352` | `TextHandler` | 元素**外部(outer)HTML**——含元素自身开合标签(lxml `tostring(self._root, method="html", with_tail=False)` 序列化元素本身,**重新序列化**,注释已在解析期被剔除)。源码 docstring 写的"inner HTML"是笔误,实测返回的是 outer。 |
| `.body` (property) | Selector: `parser.py:354-359` / Response 覆盖: `custom.py:83-86` | `str\|bytes`(Selector)/ `bytes`(Response) | **原始未处理**的输入体(`_raw_body`)。Response 上永远是原始 bytes。 |
| `.prettify()` | `parser.py:361-374` | `TextHandler` | 美化后的内部 HTML。 |
| `.get()` / `.getall()` | `parser.py:464-475` | `TextHandler`/`TextHandlers` | 序列化:元素→outer HTML(=`html_content`),文本节点→其值。别名 `extract_first`/`extract`。 |
| `.json()` | `parser.py:917-931` | `dict` | 优先用 `_raw_body`,否则 `.text`,再否则 `get_all_text(strip=True)`,交给 orjson。 |
| `.re(regex, ...)` / `.re_first(...)` | `parser.py:933-965` | `TextHandlers`/`TextHandler` | **注意:只对 `.text`(直接文本)跑正则**(`parser.py:947` `return self.text.re(...)`),不是全文!要全文正则用 `get_all_text().re(...)` 或 `find_by_regex`。 |

**关于 `clean_text`——它不存在。** 全仓库没有 `clean_text` 方法(已 grep 确认,仅 `custom_types.py:104` 有 `TextHandler.clean()`)。任务描述里的 "clean_text" 实际对应两个东西:
- `TextHandler.clean(remove_entities=False)`(`custom_types.py:104-109`):去掉 `\t\r\n`、合并连续空格、`strip()`,返回新 `TextHandler`。
- `get_all_text(strip=True)`:提取时顺带 strip。

即:要"干净的纯文本",用 `resp.get_all_text(strip=True)`;要"把一段文本压平空白",用 `some_text.clean()`。

### 2.4 `translator.py`(CSS→XPath)

- `HTMLTranslator`(`translator.py:122`)继承 cssselect,`css_to_xpath(css, prefix="descendant-or-self::")`。
- 扩展伪元素:`::text`(`xpath_text_simple_pseudo_element`,`translator.py:116`)→ 选文本节点;`::attr(NAME)`(`xpath_attr_functional_pseudo_element`,`translator.py:109`)→ 选属性值。
- 例:`resp.css('h1::text').get()` 直接拿标题文本;`resp.css('a::attr(href)').getall()` 拿所有链接。

---

## 3. 逐问题解答

### Q1 · Selector 的 css/xpath/re 等选择能力概览

- **CSS3**:`css()`(`parser.py:568`),内部 `css_to_xpath` 转 XPath;支持标准 CSS3 + Scrapling 扩展的 `::text` / `::attr(x)`。
- **XPath**:`xpath()`(`parser.py:628`),`**kwargs` 作为 XPath 变量注入。
- **BS4 风格**:`find` / `find_all`(`parser.py:698/792`),支持标签名、属性 dict、`re.Pattern`、可调用过滤器混用。
- **文本/正则定位**:`find_by_text`(`parser.py:1096`)、`find_by_regex`(`parser.py:1162`)。
- **正则抽取**:`Selector.re/.re_first`(`parser.py:933/949`,**仅作用于直接 `.text`**);`TextHandler.re/.re_first`(`custom_types.py:148/184`,可加 `clean_match`/`case_sensitive`/`check_match`)。
- **结构相似**:`find_similar`(`parser.py:1015`)。
- 结果都是 `Selectors`,可链式 `.css().css()`、`.filter()`,再 `.get()/.getall()` 出文本/HTML。
- 非法选择器统一抛 `cssselect.SelectorSyntaxError`(`parser.py:622-626`、`690-696`)——封装时要 catch。

### Q2 · text / get_all_text 等文本提取方法

见 §2.3 表。关键区分(封装里最容易踩坑):
- `.text` = **直接**文本(不递归);容器元素上常常是空串(docs `main_classes.md:106-109` 明确演示 `article.text == ''`)。
- `get_all_text()` = **递归**全文,默认剔除 script/style,`strip=True` 去空白。**这是判定"页面有多少可读文本"的正确入口。**
- `.clean()` 只是空白规整,不做抽取。
- `.body` = 原始 HTML(给 trafilatura);`.html_content` = lxml 重序列化的 **outer HTML**(含元素自身标签、会丢注释、空白已合并,**不建议**当作"原样 HTML"喂下游)。

### Q3 · 自适应选择(auto-match / adaptive)是什么

一句话:**站点改版导致选择器失效时,靠先前存下的元素"指纹"在新页面里按相似度重新定位同一个元素。** 相关源码:

- 开关:构造时 `adaptive=True`(`parser.py:163-181`),需要一个 lru_cache 包裹的 storage 类(默认 `SQLiteStorageSystem`,存 `elements_storage.db`,`parser.py:47`)。关掉时相关参数被忽略并告警。
- 存/取:`save(element, identifier)`(`parser.py:881`)把元素的 tag/text/attributes/path/parent/siblings 等唯一属性写库;`retrieve(identifier)`(`parser.py:902`)读回。
- 重定位:`relocate(element, percentage=40, selector_type=False)`(`parser.py:519`)遍历全树,用 `__calculate_similarity_score`(`parser.py:807`,基于 `SequenceMatcher` 对 tag/text/各属性/path/parent/siblings 打分)找最高分且 ≥ `percentage%` 的节点。
- 触发链:`css/xpath(..., adaptive=True, auto_save=True)` 时——正常选中就(可选)`auto_save`;选不中且库里有该 `identifier` 的旧指纹,就自动 `relocate`(`parser.py:659-688`)。
- docs:`docs/parsing/adaptive.md` 有完整教程。

**对我们价值有限**:auto-match 面向"长期跟踪固定站点、选择器要抗改版"的爬虫。AI Research Browser 是**通用抓取 + 交给 trafilatura 抽正文**,不写站点专属选择器,也不需要跨会话持久化指纹。**P2 建议全程 `adaptive=False`(默认),避免 SQLite 落盘副作用**(每个 URL 都建/写库会拖慢并产生磁盘 IO,VPS 上不划算)。

### Q4 · 这套 parser 与 P3 trafilatura 的分工

| 职责 | 归属 | 理由 |
|---|---|---|
| 抓取、拿到原始 HTML(bytes) | Scrapling Fetcher/Dynamic/Stealthy | 引擎层的活。 |
| 快速判"正文够不够长 / 是不是 SPA 空壳" | **Scrapling `get_all_text()`** | 一行拿到全文长度,零额外依赖,足够做布尔门槛。 |
| 拿到干净原始 HTML 交给下游 | **Scrapling `resp.body`** | `_raw_body` 是未改动的原始字节,最适合喂 trafilatura。 |
| **正文抽取 / 样板(nav、广告、footer)剔除 / 元数据** | **trafilatura(P3)** | Scrapling `get_all_text()` **只拼接所有文本节点,没有正文密度/可读性算法**,会把导航和页脚一起收进来,不能当正文。 |
| 结构化字段选择(需要时) | Scrapling css/xpath | 若某天要精准抓某字段(而非整篇正文)才用。 |

**分界线**:Scrapling 负责"HTML 到手 + 粗粒度文本量测",trafilatura 负责"从 HTML 里挑出正文"。二者不重叠——不要用 `get_all_text()` 的结果当最终正文,也不要指望 trafilatura 帮你判断该不该升级引擎(那时你还没抽正文)。

### Q5 · P2 判定"内容过薄"最简单可靠的取文本方式

**首选**:

```python
text = resp.get_all_text(strip=True)   # 默认 ignore_tags=('script','style'), valid_values=True
if len(text) < MIN_TEXT_LEN:           # 疑似 SPA 空壳 / 静态取不到内容 → 升级引擎
    ...
```

为什么可靠:
- `get_all_text` 递归遍历 `.//text()`(`parser.py:61,324`),默认剔除 `script`/`style`,`valid_values=True` 跳过纯空白节点(`parser.py:309`),`strip=True` 去首尾空白 —— SPA 首屏若只有 `<div id="app"></div>` 之类空壳,长度会接近 0。
- 不依赖任何选择器命中,不受站点结构影响,不触发 adaptive/存储。
- 返回 `TextHandler`(str 子类),`len()` 直接可用。

**不要**用 `len(resp.body)` 判薄:body 含全部 HTML 标记 + 内联 JS,一个纯 SPA 的 body 可能几十 KB 但可读文本为 0,会误判为"内容充足"。

可选加严:`len(resp.get_all_text(strip=True).clean())`(再压一遍空白),或统计词数 `len(text.split())`,阈值更稳。具体阈值(字符数 vs 词数)需实测,见 §5。

---

## 4. 对 P2 的影响与行动建议(可运行最小片段)

**前提**:Fetcher 返回的对象就是 `Response`(= `Selector` 子类),下面 `resp` 即 fetcher 返回值。

### 4.1 统一的"内容量测 + 升级判定"辅助函数

```python
from scrapling.parser import Selector  # Response 亦可,类型上是 Selector 子类

MIN_TEXT_CHARS = 200   # 起点值,需实测校准(见 open_questions)

def visible_text_len(resp) -> int:
    """粗粒度可读文本长度,用于升级链判薄。零额外依赖。"""
    # get_all_text 默认已忽略 <script>/<style>,strip 去空白
    return len(resp.get_all_text(strip=True))

def looks_too_thin(resp) -> bool:
    return visible_text_len(resp) < MIN_TEXT_CHARS
```

### 4.2 三档升级链里的用法(伪代码)

```python
resp = await static_fetch(url)          # Fetcher
if resp is not None and resp.status < 400 and not looks_too_thin(resp):
    return ok(resp, engine="static")

resp = await dynamic_fetch(url)         # DynamicFetcher(浏览器渲染 SPA)
if resp is not None and resp.status < 400 and not looks_too_thin(resp):
    return ok(resp, engine="dynamic")

resp = await stealth_fetch(url)         # StealthyFetcher(反爬/CF)
return ok(resp, engine="stealth") if resp is not None else blocked(url)
```

> 注:`blocked` 的判定(Cloudflare/反爬)不在本子系统,见引擎层笔记(05-stealth-engine)。这里只负责"正文薄不薄"。

### 4.3 交给下游 trafilatura 的载荷

```python
raw_html_bytes = resp.body            # 原始未改动 HTML(bytes)——喂 trafilatura
# P3: trafilatura.extract(raw_html_bytes.decode(resp.encoding, "replace"), ...)
```

- **用 `resp.body`,不要用 `resp.html_content`**:后者是 lxml 重序列化、已丢注释/合并空白的版本(`parser.py:344-352`),不是"原样 HTML"。
- 若下游要 str:`resp.body.decode(resp.encoding, errors="replace")`(`resp.encoding` 来自构造,默认 utf-8)。

### 4.4 落地注意

- 全程 `adaptive=False`(默认即是),别开自适应/存储——省磁盘 IO,避免 `elements_storage.db` 落盘。
- 解析层是**纯 CPU、无 IO、无浏览器**,不吃你的浏览器实例池/信号量预算;`get_all_text()` 只是遍历 lxml 树。可放心在每个 URL 上调用。
- catch `cssselect.SelectorSyntaxError`(如果你在网关里跑用户提供的选择器);本期若只用 `get_all_text()`/`body` 则无此风险。
- `content` 传入非 str/bytes 会 `TypeError`;但走 Fetcher 时 Response 已处理好,不用操心。

---

## 5. 待实测确认的问题

1. **`MIN_TEXT_CHARS` 阈值到底取多少**:200 字符只是拍脑袋。需拿一批真实样本(正常文章 / 已知 SPA 空壳 / 短页/404 软墙)测 `len(get_all_text(strip=True))` 分布再定。可能要区分"字符数"与"词数"(CJK 无空格,`split()` 词数对中文页面不适用)。
2. **`get_all_text` 会把 nav/footer/cookie 横幅一起算进长度**,某些"正文很短但导航很长"的页面可能被误判为"内容充足"从而不升级。是否需要在判薄前先粗剔除(比如只统计 `<main>/<article>/<body>` 下文本,或对比 body 长度与文本长度比值)待测。
3. **静态 Fetcher 对 JS 渲染页返回的 HTML 里,`<noscript>`/骨架占位文字**是否会被 `get_all_text` 计入,导致空壳也有几十字符 —— 需针对典型 React/Vue 首屏实测,必要时把 `noscript`/`template` 加进 `ignore_tags`。
4. **`resp.body` 在 DynamicFetcher/StealthyFetcher(浏览器渲染)路径下到底存的是什么**:是渲染后的 DOM 序列化还是原始响应体?本笔记只确认了 `Response._raw_body = content`(`custom.py:57` 先 encode 成 bytes),但"content"由各引擎传入,渲染引擎传的是 `page.content()`(渲染后 DOM)还是原始 HTTP body,需到引擎层(03/04/05 笔记)对齐,直接影响 trafilatura 拿到的是不是渲染后 HTML。
5. **`resp.encoding` 是否总是正确**:构造默认 `"utf-8"`,若引擎没根据响应头/meta 纠正编码,`decode` 可能乱码。喂 trafilatura 前建议实测非 UTF-8 站点(如部分 gbk 中文站)。
