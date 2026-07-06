# 生成 hollow 的搜索源注册表:知名度分级 (tier) + 内容类型 (type) + 裁剪状态 (status)
# 数据源:vendor/searxng/searx/settings.yml @ a643858
# status 依据:docs/design/02-engine-pruning.md(2026-07-06 拍板)
#   L1 removed(五条判据,优先级 legal > keyed > inactive > lowq > offbrand)
#   L2 pool(池内待命,点名可用) / L3 default(场景默认集,scenes 标场景)
# 末尾五道断言闸:removed=124、池矩阵逐格=design/02 §三、defaults=42、
# 判据计数 legal14/keyed15/inactive52/lowq38/offbrand5 —— 任何一格不对即退出报错
import yaml, sys, collections

TYPES = {
    "web":       "通用网页",
    "knowledge": "知识百科",
    "academic":  "学术科研",
    "dev":       "开发技术",
    "news":      "新闻资讯",
    "images":    "图片设计",
    "av":        "视频音频",
    "social":    "社交论坛",
    "files":     "文件资源",
    "life":      "生活工具",
}

TIERS = {
    "T0": "全球巨头/顶级机构官方服务,家喻户晓(Google/Microsoft/Apple/Baidu/Yandex/Wikimedia/Reuters 等)",
    "T1": "老牌知名服务或主流生态官方入口(DuckDuckGo/Brave/StackOverflow/arXiv/npm/MDN 等)",
    "T2": "垂直领域知名站/国家级门户/社区大站",
    "T3": "小众站/自建实例/元搜索代理/暗网/长尾",
}

# 基础名 -> (tier, type)。变体(images/videos/news/...)自动继承 tier、按后缀改 type。
BASE = {
    # ---- 通用网页 ----
    "google": ("T0","web"), "google cse": ("T0","web"), "bing": ("T0","web"),
    "baidu": ("T0","web"), "yandex": ("T0","web"), "yahoo": ("T0","web"),
    "duckduckgo": ("T1","web"), "duckduckgo web": ("T1","web"), "brave": ("T1","web"),
    "braveapi": ("T1","web"), "startpage": ("T1","web"), "qwant": ("T1","web"),
    "naver": ("T1","web"), "sogou": ("T1","web"), "seznam": ("T1","web"),
    "360search": ("T1","web"), "quark": ("T1","web"), "yep": ("T2","web"),
    "mojeek": ("T2","web"), "dogpile": ("T2","web"), "swisscows": ("T2","web"),
    "presearch": ("T2","web"), "gmx": ("T2","web"), "tonline": ("T2","web"),
    "searchch": ("T2","web"), "startpagina": ("T2","web"), "marginalia": ("T2","web"),
    "grokipedia": ("T2","knowledge"),
    "privacywall": ("T3","web"), "mwmbl": ("T3","web"), "wiby": ("T3","web"),
    "crowdview": ("T3","web"), "searchmysite": ("T3","web"), "fastbot": ("T3","web"),
    "fireball": ("T3","web"), "gabanza": ("T3","web"), "ayo": ("T3","web"),
    "fynd": ("T3","web"), "kozmonavt": ("T3","web"), "xonaly": ("T3","web"),
    "zapmeta": ("T3","web"), "reloado": ("T3","web"), "vuhuv": ("T3","web"),
    "tusksearch": ("T3","web"), "luxxle": ("T3","web"), "heexy": ("T3","web"),
    "iseek": ("T3","web"), "neosearch": ("T3","web"), "seekninja": ("T3","web"),
    "searchzee": ("T3","web"), "resulthunter": ("T3","web"), "tiger": ("T3","web"),
    "searchtoday": ("T3","web"), "infospace": ("T3","web"), "cl0q": ("T3","web"),
    "rawweb": ("T3","web"), "unobtanium": ("T3","web"), "yacy": ("T3","web"),
    "neocities": ("T3","web"), "ahmia": ("T3","web"), "torch": ("T3","web"),
    "magnific": ("T3","images"), "boardreader": ("T3","social"),
    "kukei": ("T3","web"), "wikicommons": ("T0","images"),
    # ---- 知识百科(百科/词典/参考/影视书目数据库) ----
    "wikipedia": ("T0","knowledge"), "wikidata": ("T0","knowledge"),
    "wikibooks": ("T0","knowledge"), "wikiquote": ("T0","knowledge"),
    "wikisource": ("T0","knowledge"), "wikispecies": ("T0","knowledge"),
    "wikiversity": ("T0","knowledge"), "wikivoyage": ("T0","knowledge"),
    "wiktionary": ("T0","knowledge"), "wikimini": ("T3","knowledge"),
    "encyclosearch": ("T3","knowledge"), "wolframalpha": ("T1","knowledge"),
    "ddg definitions": ("T1","knowledge"), "duden": ("T1","knowledge"),
    "etymonline": ("T2","knowledge"), "jisho": ("T2","knowledge"),
    "wordnik": ("T2","knowledge"), "woxikon.de synonyme": ("T3","knowledge"),
    "dictzone": ("T3","knowledge"), "imdb": ("T1","knowledge"),
    "tmdb": ("T2","knowledge"), "rottentomatoes": ("T1","knowledge"),
    "moviepilot": ("T3","knowledge"), "senscritique": ("T2","knowledge"),
    "goodreads": ("T1","knowledge"), "openlibrary": ("T1","knowledge"),
    "emojipedia": ("T2","knowledge"), "destatis": ("T2","knowledge"),
    "bpb": ("T2","knowledge"), "erowid": ("T3","knowledge"),
    "minecraft wiki": ("T2","knowledge"),
    # ---- 学术科研 ----
    "arxiv": ("T1","academic"), "google scholar": ("T0","academic"),
    "semantic scholar": ("T1","academic"), "pubmed": ("T0","academic"),
    "crossref": ("T1","academic"), "core.ac.uk": ("T2","academic"),
    "openairedatasets": ("T2","academic"), "openairepublications": ("T2","academic"),
    "openalex": ("T2","academic"), "springer nature": ("T1","academic"),
    "astrophysics data system": ("T1","academic"), "pdbe": ("T2","academic"),
    # ---- 开发技术 ----
    "github": ("T0","dev"), "gitlab": ("T1","dev"), "bitbucket": ("T1","dev"),
    "codeberg": ("T2","dev"), "gitea.com": ("T2","dev"), "sourcehut": ("T2","dev"),
    "stackoverflow": ("T1","dev"), "askubuntu": ("T1","dev"), "superuser": ("T1","dev"),
    "discuss.python": ("T2","dev"), "caddy.community": ("T3","dev"),
    "pi-hole.community": ("T3","dev"), "hackernews": ("T1","dev"),
    "lobste.rs": ("T2","dev"), "habrahabr": ("T2","dev"), "mdn": ("T1","dev"),
    "microsoft learn": ("T0","dev"), "azure": ("T0","dev"), "baidu kaifa": ("T0","dev"),
    "npm": ("T1","dev"), "pypi": ("T1","dev"), "crates.io": ("T1","dev"),
    "docker hub": ("T1","dev"), "packagist": ("T2","dev"), "rubygems": ("T2","dev"),
    "hex": ("T2","dev"), "pub.dev": ("T2","dev"), "pkg.go.dev": ("T2","dev"),
    "metacpan": ("T2","dev"), "lib.rs": ("T2","dev"), "hoogle": ("T2","dev"),
    "mankier": ("T3","dev"), "anaconda": ("T2","dev"),
    "alpine linux packages": ("T2","dev"), "voidlinux": ("T3","dev"),
    "cachy os packages": ("T3","dev"), "arch linux wiki": ("T1","dev"),
    "gentoo": ("T2","dev"), "nixos wiki": ("T2","dev"),
    "free software directory": ("T3","dev"), "repology": ("T2","dev"),
    "national vulnerability database": ("T1","dev"), "huggingface": ("T1","dev"),
    "ollama": ("T2","dev"), "cloudflareai": ("T1","dev"),
    "elasticsearch": ("T3","dev"),
    # ---- 新闻资讯 ----
    "reuters": ("T0","news"), "tagesschau": ("T1","news"), "wikinews": ("T1","news"),
    "ansa": ("T1","news"), "abcnyheter": ("T3","news"), "il post": ("T2","news"),
    "chinaso news": ("T2","news"), "sina": ("T1","news"),
    # ---- 图片设计 ----
    "flickr": ("T1","images"), "flickr_api": ("T1","images"), "unsplash": ("T1","images"),
    "pexels": ("T2","images"), "pixabay": ("T2","images"), "deviantart": ("T1","images"),
    "artstation": ("T1","images"), "giphy": ("T1","images"), "imgur": ("T1","images"),
    "pinterest": ("T1","images"), "tineye": ("T2","images"), "500px": ("T2","images"),
    "1x": ("T3","images"), "wallhaven": ("T2","images"), "openverse": ("T2","images"),
    "openclipart": ("T3","images"), "picjumbo": ("T3","images"),
    "stocksnap": ("T3","images"), "public domain image archive": ("T3","images"),
    "adobe stock": ("T0","images"), "library of congress": ("T1","images"),
    "artic": ("T2","images"), "pixiv": ("T1","images"), "cara": ("T3","images"),
    "frinkiac": ("T3","images"), "findthatmeme": ("T3","images"),
    "material icons": ("T1","images"), "devicons": ("T3","images"),
    "lucide": ("T2","images"), "uxwing": ("T3","images"), "flaticon": ("T2","images"),
    "selfhst icons": ("T3","images"), "ipernity": ("T3","images"),
    # ---- 视频音频 ----
    "youtube": ("T0","av"), "youtube_api": ("T0","av"), "vimeo": ("T1","av"),
    "dailymotion": ("T1","av"), "bilibili": ("T1","av"), "niconico": ("T1","av"),
    "acfun": ("T2","av"), "iqiyi": ("T1","av"), "ina": ("T2","av"),
    "media.ccc.de": ("T2","av"), "sepiasearch": ("T3","av"), "peertube": ("T3","av"),
    "piped": ("T3","av"), "odysee": ("T2","av"), "bitchute": ("T3","av"),
    "rumble": ("T2","av"), "mediathekviewweb": ("T2","av"),
    "soundcloud": ("T1","av"), "bandcamp": ("T1","av"), "mixcloud": ("T2","av"),
    "deezer": ("T1","av"), "genius": ("T1","av"), "radio browser": ("T2","av"),
    "fyyd": ("T3","av"), "podchaser": ("T2","av"), "freesound": ("T2","av"),
    "yandex music": ("T0","av"),
    # ---- 社交论坛 ----
    "reddit": ("T0","social"), "lemmy": ("T2","social"), "mastodon": ("T2","social"),
    "tootfinder": ("T3","social"), "9gag": ("T2","social"),
    # ---- 文件资源 ----
    "btdigg": ("T3","files"), "piratebay": ("T2","files"), "1337x": ("T2","files"),
    "nyaa": ("T2","files"), "tokyotoshokan": ("T3","files"),
    "solidtorrents": ("T3","files"), "bt4g": ("T3","files"),
    "Torznab EZTV": ("T3","files"), "annas archive": ("T2","files"),
    "library genesis": ("T2","files"), "z-library": ("T2","files"),
    "findfiles": ("T3","files"), "openrepos": ("T3","files"),
    "apk mirror": ("T2","files"), "fdroid": ("T2","files"),
    "apple app store": ("T0","files"), "google play": ("T0","files"),
    "steam": ("T1","files"), "kickass": ("T2","files"),
    # ---- 生活工具 ----
    "openstreetmap": ("T1","life"), "apple maps": ("T0","life"),
    "photon": ("T2","life"), "wttr.in": ("T2","life"), "openmeteo": ("T2","life"),
    "currency": ("T2","life"), "lingva": ("T3","life"), "libretranslate": ("T2","life"),
    "mozhi": ("T3","life"), "mymemory translated": ("T2","life"),
    "deepl": ("T1","life"), "chefkoch": ("T2","life"), "geizhals": ("T2","life"),
    "ebay": ("T0","life"), "shopify stock": ("T3","life"),
}

# 变体后缀 -> type 覆盖(tier 继承基础名)
SUFFIX_TYPE = {
    "images": "images", "videos": "av", "video": "av", "news": "news", "music": "av",
    "audio": "av", "files": "files", "movies": "av", "apps": "files",
    "weather": "life", "definitions": "knowledge", "wechat": "social",
    "code": "dev", "datasets": "dev", "spaces": "dev",
    "communities": "social", "users": "social", "posts": "social",
    "comments": "social", "hashtags": "social", "kaifa": "dev", "web": None,
}

# ===== 裁剪判据(docs/design/02 §二,2026-07-06 拍板) =====
# 判据1 法律/灰色(14):种子站/影子图书馆/暗网,无条件踢
REMOVED_LEGAL = {
    "piratebay", "1337x", "kickass", "nyaa", "btdigg", "solidtorrents", "bt4g",
    "tokyotoshokan", "Torznab EZTV", "annas archive", "library genesis",
    "z-library", "ahmia", "torch",
}
# 判据2 需密钥/自建实例/私有连接器(15,含 piped/findfiles 全家):前提不成立
REMOVED_KEYED = {
    "google cse", "braveapi", "wolframalpha_api", "flickr_api", "youtube_api",
    "libretranslate", "lingva", "mozhi", "elasticsearch",
}
REMOVED_KEYED_FAMILY = {"piped", "findfiles"}
# 判据3 上游 inactive(62,其中 10 个与判据1/2 重叠):由 inactive 标记自动判定
# 判据4 低质元搜索/玩具索引/SEO 聚合(38):按 family 全家踢
REMOVED_LOWQ_FAMILY = {
    "dogpile", "zapmeta", "infospace", "fireball", "presearch",       # 二手聚合
    "yacy", "mwmbl", "wiby", "searchmysite", "crowdview",             # 玩具索引
    "fastbot", "gabanza", "ayo", "fynd", "vuhuv", "tusksearch",       # SEO 小站
    "resulthunter", "reloado", "searchtoday", "privacywall",
}
REMOVED_LOWQ = {"boardreader"}
# 判据5 与研究定位无关(5)
REMOVED_OFFBRAND = {"frinkiac", "findthatmeme", "9gag", "geizhals", "shopify stock"}

# L3 场景默认集(docs/design/02 §四草案 + 2026-07-06 拍板与实测修订):
# - 拍板:DDG 进通用默认;中文场景 baidu/quark/sogou/bilibili 全放(quark 家用IP实测复活)
# - 实测修订:startpage 降出通用默认(双环境全挂:数据中心 denied/家用代理 parsing error)
# - 2026-07-06 二次拍板:新增 knowledge(百科参考)与 social(社区舆情)两场景;
#   场景间允许共享引擎(scenes 是数组);zh 短期保留场景、中期升级 region 修饰符
SCENES = {
    "general":  ["brave", "duckduckgo", "mojeek", "wikipedia", "wikidata"],
    "knowledge": ["wikipedia", "wikidata", "wiktionary", "wikibooks",
                  "wikisource", "openlibrary"],
    "dev":      ["github", "stackoverflow", "askubuntu", "superuser", "mdn",
                 "hackernews", "docker hub", "pypi", "npm", "arch linux wiki"],
    "academic": ["arxiv", "semantic scholar", "pubmed", "crossref", "openalex",
                 "google scholar"],
    "news":     ["brave.news", "duckduckgo news", "bing news", "google news",
                 "reuters", "wikinews"],
    "social":   ["reddit", "hackernews", "lemmy posts", "mastodon hashtags",
                 "sogou wechat"],
    "images":   ["bing images", "duckduckgo images", "brave.images",
                 "wikicommons.images", "unsplash", "flickr"],
    "av":       ["youtube", "vimeo", "dailymotion", "soundcloud",
                 "wikicommons.videos"],
    "zh":       ["baidu", "quark", "sogou", "bilibili"],
}

# 条目备注(拍板/实测的可追溯记录)
NOTES = {
    "startpage": "2026-07-06 降出通用默认:双环境实测全挂(数据中心 denied/家用代理 parsing error),复活后可回",
    "apple app store": "拍板:留池,小众定位",
    "google play apps": "拍板:留池,小众定位",
    "steam": "拍板:留池,小众定位",
    "fdroid": "拍板:留池,小众定位",
    "apk mirror": "拍板:留池,小众定位",
    "bitchute": "拍板:留池(研究平台本身属正当需求)",
    "odysee": "拍板:留池(研究平台本身属正当需求)",
    "rumble": "拍板:留池(研究平台本身属正当需求)",
    "google cse": "可复活:申请 CSE ID+key 即为接回 Google 的合法通道",
    "deepl": "可复活:需密钥",
    "springer nature": "可复活:需密钥",
    "core.ac.uk": "可复活:需密钥",
    "astrophysics data system": "可复活:需密钥",
    "wolframalpha_api": "可复活:需密钥",
    "marginalia": "可复活:独立索引质量好,等上游修 API 适配",
}

REVIVABLE = ["google cse", "deepl", "springer nature", "core.ac.uk",
             "astrophysics data system", "wolframalpha_api", "marginalia"]

_SCENE_OF: dict[str, list] = {}
for _scene, _names in SCENES.items():
    for _n in _names:
        _SCENE_OF.setdefault(_n, []).append(_scene)


def status_of(name, family, inactive):
    """按优先级 legal > keyed > inactive > lowq > offbrand 判 removed;
    再按场景表判 default;其余 pool。返回 (status, removed_reason, scenes)。"""
    if name in REMOVED_LEGAL:
        return "removed", "legal", None
    if name in REMOVED_KEYED or family in REMOVED_KEYED_FAMILY:
        return "removed", "keyed", None
    if inactive:
        return "removed", "inactive", None
    if name in REMOVED_LOWQ or family in REMOVED_LOWQ_FAMILY:
        return "removed", "lowq", None
    if name in REMOVED_OFFBRAND:
        return "removed", "offbrand", None
    if name in _SCENE_OF:
        return "default", None, _SCENE_OF[name]
    return "pool", None, None


def classify(name):
    if name in BASE:
        return BASE[name] + (None,)
    # 尝试剥后缀:空格或点分隔的最后一个 token
    for sep in (" ", "."):
        if sep in name:
            base, suffix = name.rsplit(sep, 1)
            if suffix in SUFFIX_TYPE and base in BASE:
                tier, btype = BASE[base]
                return (tier, SUFFIX_TYPE[suffix] or btype, base)
    if name.endswith("_api") and name[:-4] in BASE:
        tier, btype = BASE[name[:-4]]
        return (tier, btype, name[:-4])
    return None

cfg = yaml.safe_load(open("vendor/searxng/searx/settings.yml"))
entries, missing = [], []
for e in cfg["engines"]:
    name = e["name"]
    c = classify(name)
    if c is None:
        missing.append(name); continue
    tier, typ, family = c
    cats = e.get("categories", ["general"])
    if isinstance(cats, str): cats = [cats]
    fam = family or name
    inactive = bool(e.get("inactive"))
    status, reason, scenes = status_of(name, fam, inactive)
    entries.append({
        "name": name, "module": e.get("engine","?"), "tier": tier, "type": typ,
        "family": fam,
        "upstream_default": not (e.get("disabled") or e.get("inactive")),
        "inactive": inactive,
        "cats": [str(c) for c in cats],
        "status": status, "removed_reason": reason, "scenes": scenes,
        "note": NOTES.get(name),
    })

if missing:
    print("UNCLASSIFIED:", missing); sys.exit(1)

type_order = list(TYPES)
tier_order = ["T0","T1","T2","T3"]
entries.sort(key=lambda x:(type_order.index(x["type"]), tier_order.index(x["tier"]), x["name"]))

# 统计
stat = collections.Counter((x["type"],) for x in entries)
stat_tier = collections.Counter((x["tier"],) for x in entries)
cross = collections.Counter((x["type"],x["tier"]) for x in entries)

st_count = collections.Counter(x["status"] for x in entries)
reason_count = collections.Counter(x["removed_reason"] for x in entries if x["removed_reason"])

out = []
out.append("# hollow · 搜索源注册表 v1.1(status 落地)")
out.append("# 数据源: vendor/searxng/searx/settings.yml @ a643858 (343 个引擎)")
out.append("# tier = 运营方知名度/可靠性分级; type = 内容域类型(10 类)")
out.append("# upstream_default = SearXNG 上游默认是否启用; inactive = 上游标记彻底停用")
out.append("# status = removed(L1 彻底移除,removed_reason 记判据) | pool(L2 池内待命)")
out.append("#          | default(L3 场景默认集,scenes 标场景) —— docs/design/02,2026-07-06 拍板")
out.append("meta:")
out.append("  source: vendor/searxng/searx/settings.yml@a643858")
out.append("  updated: '2026-07-06'")
out.append(f"  status_model: 343 = removed({st_count['removed']}) + pool({st_count['pool']}) + default({st_count['default']}); "
           f"判据 legal({reason_count['legal']})+keyed({reason_count['keyed']})+inactive({reason_count['inactive']})"
           f"+lowq({reason_count['lowq']})+offbrand({reason_count['offbrand']})")
out.append("  revivable: [" + ", ".join(f'"{n}"' for n in REVIVABLE) + "]  # 卡密钥/上游修复,条件成熟优先接回")
out.append("  scenes:")
for k, v in SCENES.items():
    out.append(f"    {k}: [" + ", ".join(f'"{n}"' for n in v) + "]")
out.append("  tiers:")
for k,v in TIERS.items(): out.append(f"    {k}: {v}")
out.append("  types:")
for k,v in TYPES.items(): out.append(f"    {k}: {v}")
out.append("engines:")
cur = None
for x in entries:
    if x["type"] != cur:
        cur = x["type"]
        out.append(f"  # ================ {TYPES[cur]} ({cur}) × {stat[(cur,)]} ================")
    flags = []
    if not x["upstream_default"]: flags.append("default: false")
    if x["inactive"]: flags.append("inactive: true")
    flags.append(f"status: {x['status']}")
    if x["removed_reason"]: flags.append(f"removed_reason: {x['removed_reason']}")
    if x["scenes"]: flags.append("scenes: [" + ", ".join(x["scenes"]) + "]")
    if x["note"]: flags.append(f"note: \"{x['note']}\"")
    flag_s = (", " + ", ".join(flags)) if flags else ""
    cats = "[" + ", ".join(x["cats"]) + "]"
    out.append(f"  - {{name: \"{x['name']}\", module: {x['module']}, tier: {x['tier']}, type: {x['type']}, cats: {cats}{flag_s}}}")

# ===== 五道断言闸(docs/design/02 §一/§三 + 2026-07-06 修订) =====
EXPECTED_POOL_MATRIX = {  # (type, tier) -> 池内数量(pool + default),design/02 §三
    ("web","T0"):4, ("web","T1"):10, ("web","T2"):4, ("web","T3"):0,
    ("knowledge","T0"):9, ("knowledge","T1"):7, ("knowledge","T2"):9, ("knowledge","T3"):6,
    ("academic","T0"):2, ("academic","T1"):3, ("academic","T2"):4, ("academic","T3"):0,
    ("dev","T0"):3, ("dev","T1"):16, ("dev","T2"):19, ("dev","T3"):6,
    ("news","T0"):3, ("news","T1"):8, ("news","T2"):2, ("news","T3"):1,
    ("images","T0"):6, ("images","T1"):16, ("images","T2"):9, ("images","T3"):10,
    ("av","T0"):8, ("av","T1"):15, ("av","T2"):10, ("av","T3"):4,
    ("social","T0"):1, ("social","T1"):1, ("social","T2"):6, ("social","T3"):1,
    ("files","T0"):3, ("files","T1"):1, ("files","T2"):2, ("files","T3"):1,
    ("life","T0"):1, ("life","T1"):2, ("life","T2"):6, ("life","T3"):0,
}
EXPECTED_REASONS = {"legal":14, "keyed":15, "inactive":52, "lowq":38, "offbrand":5}
errors = []
if st_count["removed"] != 124:
    errors.append(f"removed={st_count['removed']} != 124")
if st_count["default"] != 50:  # 43 草案 − startpage降出 + knowledge/social 新增 8
    errors.append(f"default={st_count['default']} != 50")
pool_matrix = collections.Counter(
    (x["type"], x["tier"]) for x in entries if x["status"] in ("pool","default"))
for key, want in EXPECTED_POOL_MATRIX.items():
    got = pool_matrix.get(key, 0)
    if got != want:
        names = sorted(x["name"] for x in entries
                       if (x["type"],x["tier"])==key and x["status"] in ("pool","default"))
        errors.append(f"pool{key}: got {got} want {want} -> {names}")
for reason, want in EXPECTED_REASONS.items():
    if reason_count.get(reason,0) != want:
        errors.append(f"reason {reason}: got {reason_count.get(reason,0)} want {want}")
for scene, names in SCENES.items():  # 默认集必须全部真实存在且未被踢
    by_name = {x["name"]: x for x in entries}
    for n in names:
        if n not in by_name: errors.append(f"scene {scene}: '{n}' 不存在于注册表")
        elif by_name[n]["status"] != "default": errors.append(f"scene {scene}: '{n}' status={by_name[n]['status']}")
if errors:
    print("VALIDATION FAILED:"); [print(" ", e) for e in errors]; sys.exit(1)

open("data/engine_registry.yaml","w",encoding="utf-8").write("\n".join(out)+"\n")
print("entries:", len(entries))
print("status:", dict(st_count), "| reasons:", dict(reason_count))
print("by type:", dict(sorted(stat.items())))
print("by tier:", dict(sorted(stat_tier.items())))
print()
print("TYPE x TIER matrix:")
hdr = "type      " + "".join(f"{t:>5}" for t in tier_order) + "  total"
print(hdr)
for t in type_order:
    row = f"{t:<10}" + "".join(f"{cross.get((t,tr),0):>5}" for tr in tier_order)
    print(row + f"{stat[(t,)]:>7}")
