#!/usr/bin/env bash
set -euo pipefail

# Publishing starts artifact commands from the repository root, while the
# managed development workflow starts them from artifacts/api-server.
exec bash artifacts/api-server/run-bot.sh "$@"