#!/usr/bin/env bash
set -euo pipefail

NEW_WRAPPER="/home/nzzhao/.agents/skills/opencode-secure-mcp/scripts/opencode_secure_run.sh"
exec bash "${NEW_WRAPPER}" "$@"
