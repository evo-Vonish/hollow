#!/usr/bin/env bash
# 一键本地双起:SearXNG(8888) + hollow gateway(8080)。Linux/VPS。
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

echo '[1/2] starting SearXNG on http://127.0.0.1:8888 ...'
SEARXNG_SETTINGS_PATH="$root/searxng/settings.yml" \
PYTHONPATH="$root/vendor/searxng" \
  "$root/.venv-searx/bin/python" -m searx.webapp &
searx_pid=$!

echo '[2/2] starting hollow gateway on http://127.0.0.1:8080 ...'
"$root/.venv-api/bin/python" -m uvicorn api.main:app --host 127.0.0.1 --port 8080 &
gateway_pid=$!

echo "SearXNG PID=$searx_pid  gateway PID=$gateway_pid"
trap 'kill $searx_pid $gateway_pid 2>/dev/null || true' INT TERM
wait
