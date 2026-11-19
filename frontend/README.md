# HOLLOW Search Browser

**English** · [简体中文](README.zh-CN.md)

The official search UI for the [HOLLOW](https://hollow.vonish.dev/docs) self-hosted deep-research API — a pure-frontend browser in the shape of a search engine, covering the API's full capability surface.

Live at <https://hollow.vonish.dev/search>

## Project boundary

| | This repo (browser) | HOLLOW main repo (search API) |
|---|---|---|
| Nature | Pure static frontend (React SPA), no server code | Server side (FastAPI + vendored SearXNG + three-tier fetching) |
| Responsibilities | Search / research streaming / dual-lane reading / batch purification / engine registry, page asset display (links/media/body images), session cache, hover prefetch | Recall (`/v1/search`), fetch & purify (`/v1/fetch`, incl. PDF extraction / link expansion / image embedding), research SSE (`/v1/research`), registry (`/v1/engines` `/v1/scenes`) |
| Dependency | Calls only the **public** API over HTTPS — no keys, no internal endpoints | Unaware of the frontend's existence |
| Deployment | Any static hosting (currently Caddy `handle_path /search*`) | Independent systemd service |

Principle: the browser only does "presentation & interaction"; all search/fetch/purify logic lives on the API side. The two are coupled solely by the public HTTP contract and can be developed, released, and deployed independently.

## Features (full API capability coverage)

| Page | URL | Capabilities |
|---|---|---|
| Home | `/search` | Title + input + 9 scene chips + three capability entries |
| Results | `/search?q=` | **Full-parameter toolbar** (time range / safe search / language / domain filter / engine naming), **result thumbnails** (engine `img_src`/`thumbnail` passthrough, auto-hidden on load failure), engine ledger drawer, infobox instant answers, pagination, hover prefetch, batch multi-select |
| Research mode | `/search?q=&mode=research` | **SSE streaming**: search ledger → per-item content arrival (tier/highlights/expandable Markdown) → summary ledger; fast/balanced/thorough modes, top_n slider, per-request budget, mid-flight stop; **asset switches**: `assets=1` links/media (per-item link list + media strip after content), `imgs=1` body image references |
| Reader mode | `/search?url=` | **Dual-lane race**: local iframe original page (shared cookies, server-independent) × cloud purified content; first-ready-first-shown, auto-fallback to original frame on purify failure, seamless tab switch, Markdown/plain-text toggle; purified lane automatically carries **body image references + page media wall (first 12 thumbnails, click through to originals) + page link panel (internal/external grouped, ↗ marked)**, all folded in `<details>` to reduce noise |
| Batch purify | `/search?urls=a,b,c` | ≤10 URLs per call; per-item status/tier/char-count honestly ledgered; dedup and budget cutoffs explicitly recorded |
| Engine registry | `/search?engines` | Browse 343 sources: status (default/pool/removed) / tier / scene filters + text search; removed entries carry their removal reason |

Cross-cutting: URL-as-state (shareable, back-button safe), session cache (10min TTL + inflight dedup; cache keys include asset params — prefetch and reader share one entry), hover/focus prefetch for instant opens, route-level code splitting (main bundle 146KB gzip, Markdown chain on demand), keyboard shortcuts (`/` to focus, Esc to clear), ErrorBoundary honest-card fallback, sonner toasts.

The **engine ledger drawer** honestly renders per-engine bills for every search/research: beyond used/failed (reason + latency), a **circuit-breaker section** lists engines tripped by health backoff (reason, `retry in Ns` / `probing`) — when an engine gets anti-bot banned, users see an explicit notice rather than silently fewer results.

## Architecture (paving the way for clients)

```
src/
├── lib/sdk/              # Platform-agnostic SDK layer (zero React deps, portable to Tauri/Electron)
│   ├── types.ts          # Full API types (field-aligned with the gateway contract, incl. SSE event unions)
│   ├── client.ts         # fetch wrapper + ApiError (OpenAI-style error spec)
│   ├── sse.ts            # POST-style SSE reader (EventSource is GET-only; hand-rolled stream parsing)
│   ├── cache.ts          # Session cache + inflight dedup (injectable storage backend)
│   ├── format.ts         # Pure helpers: hostOf/parseDomains/ledger labels...
│   └── index.ts          # High-level API: search/fetch/researchStream/listEngines/listScenes
├── components/ui/        # Design system (button/badge/select/popover/drawer/slider/skeleton/empty...)
│                       #   radix primitives + HOLLOW paper-style tokens
├── components/           # Business components: SearchBar/SceneChips/ModeTabs/FilterBar/
│                       #   EngineLedgerDrawer/ResultCard/AppHeader/Markdown
└── pages/                # Home / Results / Research / Reader / Batch / Engines
```

The SDK layer is strictly separated from the UI: the UI only fetches through `lib/sdk`. Future desktop/mobile clients can reuse the `sdk/` directory as-is (inject storage via `setStorage`).

## Tech stack

React 19 · TypeScript · Vite 7 · Tailwind CSS 3.4 · react-router 7 · Radix UI · vaul · sonner · react-markdown

## Development

```bash
npm install
npm run dev        # defaults to port 3000

# Point at the production API (or any HOLLOW instance)
VITE_API_BASE=https://hollow.vonish.dev npm run dev
```

## Build & deploy

```bash
npm run build      # output in dist/ (vite base=/search/)
```

The output is pure static files — mount it on any web server. Current production uses Caddy:

```caddy
handle_path /search* {
    root * /var/www/hollow-search
    try_files {path} {path}/ /index.html
    file_server
}
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `VITE_API_BASE` | `''` (same origin) | API base URL; point at a HOLLOW instance for cross-origin deploy/dev |

## License

Same as the HOLLOW main project (AGPL-3.0).
