#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

set -a
[ -f ./.vars.sh ] && . ./.vars.sh
set +a

if [ "$#" -eq 0 ]; then
  cat >&2 <<'EOF'
usage: bash ./.agents/tools/vm_enable_dyn_debug.sh <spec> [<spec> ...]
example:
  bash ./.agents/tools/vm_enable_dyn_debug.sh \
    'file fs/f2fs/file.c line 127 +p' \
    'func wp_page_reuse +p' \
    'func contpte_ptep_set_access_flags +p'
EOF
  exit 2
fi

REMOTE_CMDS=()
REMOTE_CMDS+=("set -euo pipefail")
REMOTE_CMDS+=("ctrl=/sys/kernel/debug/dynamic_debug/control")
REMOTE_CMDS+=("test -w \"\$ctrl\"")
for spec in "$@"; do
  esc=$(printf '%s' "$spec" | sed 's/["\\]/\\&/g')
  REMOTE_CMDS+=("echo \"$esc\" > \"\$ctrl\"")
done
REMOTE_CMDS+=("tail -n 40 \"\$ctrl\" | grep -E 'fs/f2fs/file.c|mm/memory.c|arm64/mm/contpte.c|wp_page_reuse|contpte_ptep_set_access_flags' || true")

remote_cmd=$(printf '%s; ' "${REMOTE_CMDS[@]}")
bash ./.agents/tools/vm_ssh.sh "$remote_cmd"
