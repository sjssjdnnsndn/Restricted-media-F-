#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# The bot's original web_server.py already exposes /health and performs the
# keep-alive loop. Only inject the public URL while running in a deployment;
# never use the workspace .dev domain.
if [[ "${REPLIT_DEPLOYMENT:-0}" == "1" ]]; then
  production_domain="${REPLIT_DOMAINS:-}"
  production_domain="${production_domain%%,*}"
  production_domain="${production_domain#https://}"
  production_domain="${production_domain#http://}"

  if [[ -n "$production_domain" && "$production_domain" != *.dev ]]; then
    export RENDER_URL="https://${production_domain%/}"
  else
    unset RENDER_URL
  fi
else
  unset RENDER_URL
fi

export PORT="${PORT:-8080}"

exec uv run --project "$ROOT_DIR" --frozen \
  python "$ROOT_DIR/artifacts/api-server/bot-runtime/main.py"