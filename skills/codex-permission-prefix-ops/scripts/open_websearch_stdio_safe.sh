#!/usr/bin/env bash
set -euo pipefail

exe="${OPEN_WEBSEARCH_EXE:-$HOME/.local/bin/open-websearch}"
export MODE=stdio
export ENABLE_CORS=false

exec "$exe" "$@"
