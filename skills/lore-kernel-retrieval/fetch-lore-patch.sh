#!/usr/bin/env bash
# fetch-lore-patch.sh - Stable retrieval of patches from lore.kernel.org
# Usage: fetch-lore-patch.sh <message-id> [output-dir]
# Example: fetch-lore-patch.sh 20260422005608.342028-1-fmayle@google.com ./patches

set -euo pipefail

MSGID="${1:-}"
OUTDIR="${2:-.}"

if [[ -z "$MSGID" ]]; then
    echo "Usage: $0 <message-id> [output-dir]" >&2
    echo "Example: $0 20260422005608.342028-1-fmayle@google.com" >&2
    exit 1
fi

mkdir -p "$OUTDIR"

# Method 1: Try b4 if available (best for patch series)
if command -v b4 &>/dev/null; then
    echo "[fetch-lore] Using b4 to retrieve ${MSGID} ..."
    cd "$OUTDIR" && b4 am "$MSGID"
    exit 0
fi

# Method 2: Direct curl + gunzip fallback (always works, bypasses Anubis)
echo "[fetch-lore] b4 not found, using direct curl fallback for ${MSGID} ..."

# Construct URL from msgid. If it starts with http, use as-is; otherwise assume lore redirect.
if [[ "$MSGID" == http* ]]; then
    URL="${MSGID}"
else
    URL="https://lore.kernel.org/r/${MSGID}"
fi

# Follow redirect to get canonical URL, then fetch t.mbox.gz
CANONICAL=$(curl -s -o /dev/null -w '%{redirect_url}' -L "$URL")
if [[ -z "$CANONICAL" ]]; then
    CANONICAL="$URL"
fi
CANONICAL="${CANONICAL%/}"

MBOX_URL="${CANONICAL}/t.mbox.gz"
OUTFILE="${OUTDIR}/${MSGID}.mbox"

echo "[fetch-lore] Downloading ${MBOX_URL} ..."
curl -s -L "$MBOX_URL" | gunzip > "$OUTFILE"

echo "[fetch-lore] Saved thread mbox to ${OUTFILE}"
echo "[fetch-lore] Tip: install b4 (pip install b4) for git-am ready output with sorted patches and trailers."
