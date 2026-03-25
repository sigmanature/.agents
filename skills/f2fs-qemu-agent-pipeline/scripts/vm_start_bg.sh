#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

set -a
[ -f ./.vars.sh ] && . ./.vars.sh
set +a

: "${BASE:?missing BASE from .vars.sh}"
: "${SCRIPT:?missing SCRIPT from .vars.sh}"
: "${IMG_BASE:?missing IMG_BASE from .vars.sh}"

LAUNCH_LOG="${1:-$ROOT_DIR/.roo/plans/qemu-launch.log}"
PID_FILE="${2:-$ROOT_DIR/.roo/plans/qemu.pid}"
CONSOLE_LOG="${3:-$ROOT_DIR/guest_console.log}"

mkdir -p "$(dirname "$LAUNCH_LOG")"
mkdir -p "$(dirname "$PID_FILE")"

rm -f "$CONSOLE_LOG"
nohup /bin/bash ./myscripts/qemu_start_ori.sh >"$LAUNCH_LOG" 2>&1 &
PID=$!
printf '%s\n' "$PID" > "$PID_FILE"
printf 'pid=%s\nlaunch_log=%s\nconsole_log=%s\n' "$PID" "$LAUNCH_LOG" "$CONSOLE_LOG"
