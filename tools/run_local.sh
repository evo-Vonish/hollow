#!/usr/bin/env bash
# 一键本地三起：SearXNG(8888) + hollow gateway(8080) + React 前端(3000)。
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

for executable in .venv-searx/bin/python .venv-api/bin/python frontend/node_modules/vite/bin/vite.js; do
  if [[ ! -f "$root/$executable" ]]; then
    echo "missing $executable; follow README.md local setup first" >&2
    exit 1
  fi
done
command -v node >/dev/null || { echo "Node.js 22.12+ is required" >&2; exit 1; }

echo '[1/3] starting SearXNG on http://127.0.0.1:8888 ...'
SEARXNG_SETTINGS_PATH="$root/searxng/settings.yml" \
PYTHONPATH="$root/vendor/searxng" \
  "$root/.venv-searx/bin/python" -m searx.webapp &
searx_pid=$!

echo '[2/3] starting hollow gateway on http://127.0.0.1:8080 ...'
"$root/.venv-api/bin/python" -m uvicorn api.main:app --host 127.0.0.1 --port 8080 &
gateway_pid=$!

echo '[3/3] starting search UI on http://127.0.0.1:3000/search/ ...'
node "$root/frontend/node_modules/vite/bin/vite.js" "$root/frontend" --host 127.0.0.1 &
frontend_pid=$!

echo "SearXNG PID=$searx_pid  gateway PID=$gateway_pid  frontend PID=$frontend_pid"
trap 'kill $searx_pid $gateway_pid $frontend_pid 2>/dev/null || true' INT TERM
wait
