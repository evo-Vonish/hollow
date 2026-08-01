# hollow — AI Research Browser · Deep Research API

**English** · [简体中文](README.zh-CN.md)

A free, open-source (AGPL-3.0) deep-research API service: **multi-engine search recall → three-tier escalating full-text crawling → content purification → traceable Evidence Packs**.
It borrows OpenAI's envelope/error/SSE conventions, but the domain model is hollow's own (no imitation of its endpoints).

> **Status: running in production**. Complete API surface, 341 unit tests + 20 integration tests, GitHub Actions green on every push;
> live traffic served and a first calibration round done (engine relevance tiers, timeout matrix, anti-bot routing).
> Official search UI: [hollow-browser-front-end](https://github.com/evo-Vonish/hollow-browser-front-end) · Live at https://hollow.vonish.dev

## What it does

| Endpoint | Purpose |
|---|---|
| `POST /v1/search` | Pure search recall: sub-second URL/title/snippet (+ image thumbnails) + relevance rerank + full ledger, no crawling |
| `POST /v1/research` | Search + parallel fetch + purification in one call (optional SSE streaming); each item carries content/highlights/links/media/traceability fields |
| `POST /v1/fetch` | Direct URL fetch: crawl + purify without search; supports PDF text extraction, outbound-link auto-expansion, inline body images |
| `GET /v1/engines` · `GET /v1/scenes` | Engine registry (343 sources) / scene → engine-set mapping |

Uniform `{error:{message,type,param,code}}` envelope with correct HTTP codes; `stream:true` uses **semantic** SSE events (not token deltas);
optional `HOLLOW_API_KEY` Bearer auth (declared in OpenAPI). Contract details: [docs/design/04-v1-api.md](docs/design/04-v1-api.md).

## Content capabilities (2026-07 iteration)

**Page asset extraction** (`include_links` / `include_media`, on both fetch and research)
Native trafilatura pipeline path (`output_format='markdown', with_links/media`) — zero extra requests. Each source carries an internal/external classified link list (≤100) and a media list (≤50, image/video/audio with `source` annotation). Aggregate frames explicitly echo the requested fields.

**Outbound-link auto-expansion** (`expand_links` / `expand_depth` / `expand_scope`, fetch only)
After fetching the primary URL, automatically follows page links into a homomorphic `children` tree. Four hard caps against runaway: ≤10 links per expansion, ≤3 levels deep, ≤20 nodes per tree, and a shared 45s time budget per tree; a visited set (including redirect targets) prevents loops; expansion counts go into the fetch ledger honestly.

**Two-tier body images** (`include_images` / `embed_images`)
- Reference tier: inline `![alt](url)` image references in the Markdown body (source structure preserved)
- Embed tier: small images (≤32KB) converted to data URIs — zero external requests when reading. Four guardrails: ≤32KB per image / ≤10 images per page / ≤256KB total / 5s per-image timeout; SSRF protection reuses netguard with a Content-Type whitelist; **on any failure the original URL reference is kept — content is never dropped**.

**PDF text extraction**
When a fetch lands on a PDF, pypdf extraction kicks in automatically (dual caps: ≤30 pages / ≤100K chars), header-annotated `[PDF · N pages]`; scanned/corrupt files honestly return `no_content` — no fake success. Direct arXiv paper reading verified in production (15 pages / 39K chars).

**Media forwarding**
Search responses pass through engine image fields (`img_src`/`thumbnail`) — 100% of results in the images scene carry thumbnails; clients can render a masonry wall with zero crawling.

## Engine reliability (from "mass extinction" to self-healing)

**Engine health backoff** (in-process state machine): 3 consecutive failures trip a circuit breaker — the engine is removed from default/scene sets (**explicit `engines=` naming exempts it**); exponential backoff 300s×2^n capped at 3600s, half-open probe on expiry, success resets to zero. The tripped list is recorded in every search's `meta.engines_degraded` — clients can honestly tell users "engine X is temporarily unavailable, retry in Ns" instead of pretending results are complete.

**Anti-bot routing matrix**: engines are proxied per measured datacenter connectivity — engines unreachable from Beijing (duckduckgo/sogou) egress via the Tokyo proxy, fast direct ones (360search) stay direct; per-engine timeouts (slow sources like arxiv relaxed). Configuration as documentation — see comments in `searxng/settings.yml`.

**Background**: SearXNG's default reaction to a CAPTCHA is suspending the engine for 3600s (in-memory) — one trigger disables it for a whole hour, and cascading triggers across engines create the illusion of "everything is dead". The health-backoff layer turns this into observable, self-healing, exemptible, explicit behavior.

## Four non-negotiable principles

1. Success claims must come from measured responses, not intent
2. **No silent drops** (engines_failed / engines_degraded / fetch_status all explicit; set-difference reconciliation as backstop)
3. All content traceable (engine / fetched_at / url / final_url)
4. Thresholds must be calibrated before shipping

## Architecture: borrow infrastructure, build only thin differentiators

FastAPI gateway + **SearXNG** (search, vendored) + **Scrapling** (three-tier fetching: static→dynamic→stealthy) + **trafilatura** (purification & asset extraction) + **pypdf** (PDF).
Self-built boundary: API orchestration, Evidence Pack assembly, relevance rerank/highlights (pure lexical, zero models), engine health state machine, link expander, image embedder. No language rewriting, no engine adapter rewrites.

## Local quickstart

```bash
git clone https://github.com/evo-Vonish/hollow.git && cd hollow
git checkout claude/new-project-setup-1dzma1
# Two venvs (SearXNG and gateway deps are separated)
python -m venv .venv-searx && .venv-searx/bin/pip install -r vendor/searxng/requirements.txt tzdata
python -m venv .venv-api   && .venv-api/bin/pip install -r requirements.txt
# One-shot dual launch (Linux: tools/run_local.sh; Windows: tools\run_local.ps1)
tools/run_local.sh                       # SearXNG :8888 + gateway :8080
curl -s localhost:8080/healthz           # {"status":"ok","searxng":"ok"}
curl -s localhost:8080/v1/search -H 'content-type: application/json' \
     -d '{"query":"transformer attention","scenes":["academic"]}'
# Research with assets: links + media + body images
curl -s localhost:8080/v1/research -H 'content-type: application/json' \
     -d '{"query":"giant panda","top_n":3,"include_links":true,"include_media":true,"include_images":true}'
# Fetch with expansion: take a page and follow its links
curl -s localhost:8080/v1/fetch -H 'content-type: application/json' \
     -d '{"urls":["https://en.wikipedia.org/wiki/Giant_panda"],"expand_links":5,"expand_depth":2}'
```

## Deployment (single Linux VPS)

Artifacts in [`deploy/`](deploy/) (systemd×2 + nginx SSE-safe + gateway.env.example).
**Full steps and the pre-launch checklist are in [docs/design/07-deployment.md](docs/design/07-deployment.md)** — especially target-environment smoke tests, threshold calibration, and egress firewall.
Reference production topology: two hosts over WireGuard — gateway & SearXNG on an in-country host (Beijing), reverse proxy & frontend on an overseas host (Tokyo); engines with restricted overseas egress ride the tunnel proxy out of Tokyo.

## Testing

```bash
.venv-api/bin/pip install -r requirements-dev.txt
.venv-api/bin/pytest                                   # 341 unit (network-free, runs in CI)
HOLLOW_TEST_LIVE=1 .venv-api/bin/pytest tests/integration   # 20 integration (needs a live gateway)
```
CI (GitHub Actions) runs the unit suite on every push. Offline recall-quality evaluation harness in [`eval/`](eval/).
**Discipline: any pip package installed on the server must land in `requirements*.txt` in the same commit** — manual installs are invisible to CI. Transitive deps that have bitten us (e.g. `apify_fingerprint_datapoints`) are pinned with a comment explaining why.

## Known boundaries (stated honestly)

- **Recall quality**: zh scenes (bilibili/360search/sogou) are solid; some EN queries still get noisy candidate sets — continuous calibration via the `eval/` harness.
- **Anti-bot is the norm**: baidu triggers CAPTCHAs on both egress paths (Beijing direct / Tokyo proxy); the health-backoff handles trip-and-recover automatically, recorded explicitly rather than silently.
- **Single process**: in-flight limiter / upstream gate / browser gate / engine health state are all in-process — `--workers 1` is a hard constraint; horizontal scaling needs shared storage (Redis).
- **Auth**: single shared Bearer key, no multi-tenancy/key rotation; for public deployments add Cloudflare rate limiting.
- **Browser fetch tiers**: dynamic/stealthy depend on Playwright/Chromium — fingerprint warfare in datacenter environments is ongoing engineering.

## License

AGPL-3.0.
