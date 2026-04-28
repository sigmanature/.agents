#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="${ROOT_DIR}/scripts/opencode_secure_mcp_server.py"
STATE_DIR="${OPENCODE_SECURE_MCP_STATE_DIR:-$HOME/.local/state/opencode-secure-mcp}"
ENTRY_LOG="${OPENCODE_SECURE_MCP_ENTRY_LOG:-${STATE_DIR}/entry.log}"
export OPENCODE_SECURE_MCP_DEBUG_LOG="${OPENCODE_SECURE_MCP_DEBUG_LOG:-${STATE_DIR}/server.log}"

mkdir -p "$(dirname "${ENTRY_LOG}")"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"${ENTRY_LOG}"
}

log "wrapper_start pid=$$ ppid=$PPID argv=$*"
trap 'rc=$?; log "wrapper_exit rc=${rc}"' EXIT
PYTHONUNBUFFERED=1 python3 -u "${SERVER}" "$@"
