# 生成 hollow 的搜索源注册表:知名度分级 (tier) + 内容类型 (type)
# 数据源:vendor/searxng/searx/settings.yml @ a643858
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
    entries.append({
        "name": name, "module": e.get("engine","?"), "tier": tier, "type": typ,
        "family": family or name,
        "upstream_default": not (e.get("disabled") or e.get("inactive")),
        "inactive": bool(e.get("inactive")),
        "cats": [str(c) for c in cats],
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

out = []
out.append("# hollow · 搜索源注册表 v1")
out.append("# 数据源: vendor/searxng/searx/settings.yml @ a643858 (343 个引擎)")
out.append("# tier = 运营方知名度/可靠性分级; type = 内容域类型(10 类)")
out.append("# upstream_default = SearXNG 上游默认是否启用; inactive = 上游标记彻底停用")
out.append("meta:")
out.append("  source: vendor/searxng/searx/settings.yml@a643858")
out.append("  updated: '2026-07-05'")
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
    flag_s = (", " + ", ".join(flags)) if flags else ""
    cats = "[" + ", ".join(x["cats"]) + "]"
    out.append(f"  - {{name: \"{x['name']}\", module: {x['module']}, tier: {x['tier']}, type: {x['type']}, cats: {cats}{flag_s}}}")

open("data/engine_registry.yaml","w").write("\n".join(out)+"\n")
print("entries:", len(entries))
print("by type:", dict(sorted(stat.items())))
print("by tier:", dict(sorted(stat_tier.items())))
print()
print("TYPE x TIER matrix:")
hdr = "type      " + "".join(f"{t:>5}" for t in tier_order) + "  total"
print(hdr)
for t in type_order:
    row = f"{t:<10}" + "".join(f"{cross.get((t,tr),0):>5}" for tr in tier_order)
    print(row + f"{stat[(t,)]:>7}")
