#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENTRY="${ROOT_DIR}/scripts/opencode_secure_mcp_entry.sh"
NAME="${1:-opencode_secure}"

command -v codex >/dev/null 2>&1 || {
  echo "ERROR: missing required command: codex" >&2
  exit 1
}

[[ -x "$ENTRY" ]] || chmod 700 "$ENTRY"

codex mcp remove "$NAME" >/dev/null 2>&1 || true
codex mcp add "$NAME" -- bash "$ENTRY"
codex mcp list --json
