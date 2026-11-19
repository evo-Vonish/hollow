# HOLLOW Search Browser（HOLLOW 搜索浏览器）

[English](README.md) · **简体中文**

[HOLLOW](https://hollow.vonish.dev/docs) 自托管深度研究 API 的官方搜索界面——一个纯前端的"搜索引擎形态"浏览器，覆盖 API 全部能力。

线上地址：<https://hollow.vonish.dev/search>

## 项目边界

| | 本仓库（浏览器） | HOLLOW 主仓库（搜索 API） |
|---|---|---|
| 性质 | 纯静态前端（React SPA），无服务端代码 | 服务端（FastAPI + vendored SearXNG + 三档抓取） |
| 职责 | 搜索 / 深研流式 / 双路赛制阅读 / 批量净化 / 引擎注册表、页面资产展示（外链/媒体/正文图）、会话缓存、hover 预取 | 召回（`/v1/search`）、抓取净化（`/v1/fetch`，含 PDF 抽取/外链展开/图片内联）、深研 SSE（`/v1/research`）、注册表（`/v1/engines` `/v1/scenes`） |
| 依赖关系 | 仅通过 HTTPS 调用**公开** API，不含密钥、不含内部接口 | 不感知前端存在 |
| 部署 | 任意静态托管（当前 Caddy `handle_path /search*`） | 独立 systemd 服务 |

原则：浏览器只做"呈现与交互"，所有搜索/抓取/净化逻辑都在 API 侧。两边仅由公开 HTTP 契约耦合，可各自独立开发、发版、部署。

## 功能（API 全能力覆盖）

| 页面 | URL | 能力 |
|---|---|---|
| 主页 | `/search` | 标题 + 输入框 + 9 场景 chip + 三能力入口 |
| 结果页 | `/search?q=` | **全参数工具栏**（时间范围/安全搜索/语言/域过滤/引擎点名）、**结果缩略图**（引擎 img_src/thumbnail 透传，加载失败自动隐藏）、引擎账目抽屉、infobox 即时答案、分页、hover 预取、批量多选 |
| 深研模式 | `/search?q=&mode=research` | **SSE 流式**：搜索账目 → 逐条正文抵达（档位/要点/可展开 Markdown）→ 汇总账；fast/balanced/thorough 三模式、top_n 滑杆、整单预算、中途停止；**资产开关**：`assets=1` 外链/媒体（条目正文后附外链清单+媒体小图条）、`imgs=1` 正文图片引用 |
| 阅读模式 | `/search?url=` | **双路赛制**：本地 iframe 原网页（Cookie 互通、与服务器无关）× 云端净化正文；先就绪先展示、净化失败自动切原框、tab 无缝切换、Markdown/纯文本切换；净化路自动带**正文图片引用 + 本页媒体墙（前 12 小图，点开原图）+ 本页外链栏（站内/站外分组，↗ 标记）**，均 details 折叠降噪 |
| 批量净化 | `/search?urls=a,b,c` | ≤10 条一次调用，逐条状态/档位/字数如实入账，去重与预算切断显式入账 |
| 引擎注册表 | `/search?engines` | 343 源浏览：状态（default/pool/removed）/梯队/场景过滤 + 文本搜索，removed 带除名原因 |

横切能力：URL 即状态（可分享可后退）、会话缓存（10min TTL + inflight 去重，缓存键含资产参数——预取与阅读页共用同一份）、hover/focus 预取秒开、路由级代码分割（主包 146KB gzip，Markdown 链按需）、键盘快捷键（`/` 聚焦、Esc 清空）、ErrorBoundary 诚实卡兜底、sonner toast。

**引擎账目抽屉**如实呈现每次搜索/深研的引擎级账单：使用/失败（原因+耗时）之外，**熔断退避段**列出被健康熔断的引擎（原因、`Ns 后探测` / `探测恢复中`）——引擎被风控封禁时用户看到的是显式告知而非凭空少结果。

## 架构（为客户端铺路）

```
src/
├── lib/sdk/              # 平台无关 SDK 层(零 React 依赖,可整体搬进 Tauri/Electron)
│   ├── types.ts          # 全量 API 类型(与网关契约逐字段对齐,含 SSE 事件联合类型)
│   ├── client.ts         # fetch 封装 + ApiError(OpenAI 风格错误规约)
│   ├── sse.ts            # POST 式 SSE 读取器(EventSource 只支持 GET,手写流解析)
│   ├── cache.ts          # 会话缓存 + inflight 去重(存储后端可注入)
│   ├── format.ts         # hostOf/parseDomains/账目标签等纯函数
│   └── index.ts          # 高层 API:search/fetch/researchStream/listEngines/listScenes
├── components/ui/        # 设计系统(button/badge/select/popover/drawer/slider/skeleton/empty...)
│                       #   radix 原语打底 + HOLLOW 深色工程风 token
├── components/           # 业务组件:SearchBar/SceneChips/ModeTabs/FilterBar/
│                       #   EngineLedgerDrawer/ResultCard/AppHeader/Markdown
└── pages/                # Home / Results / Research / Reader / Batch / Engines
```

SDK 层与 UI 严格分离：UI 只经 `lib/sdk` 取数，后续桌面/移动客户端直接复用 `sdk/` 目录（存储经 `setStorage` 注入即可）。

## 技术栈

React 19 · TypeScript · Vite 7 · Tailwind CSS 3.4 · react-router 7 · Radix UI · vaul · sonner · react-markdown

## 开发

```bash
npm install
npm run dev        # 默认 3000 端口

# 指向生产 API(或任意 HOLLOW 实例)
VITE_API_BASE=https://hollow.vonish.dev npm run dev
```

## 构建与部署

```bash
npm run build      # 产物在 dist/(vite base=/search/)
```

产物是纯静态文件，挂到任意 Web 服务器即可。当前生产用 Caddy：

```caddy
handle_path /search* {
    root * /var/www/hollow-search
    try_files {path} {path}/ /index.html
    file_server
}
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `VITE_API_BASE` | `''`（同源） | API 基址；跨域部署/开发时指向 HOLLOW 实例 |

## License

与 HOLLOW 主项目一致（AGPL-3.0）。
