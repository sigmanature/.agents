#!/bin/bash
set -u -o pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

set -a
[ -f ./.vars.sh ] && . ./.vars.sh
set +a

: "${BASE:?missing BASE from .vars.sh}"
: "${SCRIPT:?missing SCRIPT from .vars.sh}"
: "${IMG_BASE:?missing IMG_BASE from .vars.sh}"

VERIFY_TIMEOUT=90
POLL_INTERVAL=2
QGA_SOCK="/tmp/qga.sock"
QMP_SOCK="/tmp/qemu-qmp.sock"
LAUNCH_LOG="${1:-$ROOT_DIR/.roo/plans/qemu-launch.log}"
PID_FILE="${2:-$ROOT_DIR/.roo/plans/qemu.pid}"
CONSOLE_LOG="${3:-$ROOT_DIR/guest_console.log}"
SKIP_VERIFY="${SKIP_VERIFY:-0}"

mkdir -p "$(dirname "$LAUNCH_LOG")"
mkdir -p "$(dirname "$PID_FILE")"

rm -f "$CONSOLE_LOG" "$QGA_SOCK" "$QMP_SOCK"

nohup /bin/bash ./myscripts/qemu_start_ori.sh --log "$CONSOLE_LOG" >"$LAUNCH_LOG" 2>&1 &
WRAPPER_PID=$!
printf 'wrapper_pid=%s\nlaunch_log=%s\nconsole_log=%s\n' "$WRAPPER_PID" "$LAUNCH_LOG" "$CONSOLE_LOG"

if [ "$SKIP_VERIFY" = "1" ]; then
  printf '%s\n' "$WRAPPER_PID" > "$PID_FILE"
  printf 'status=not_verified\nnext=manual check\n'
  exit 0
fi

# --- inline readiness verification ---

find_qemu_pid() {
  ps -eo pid,args | awk '/qemu-system-aarch64/ && !/grep/ {print $1; exit}'
}

elapsed=0
while [ "$elapsed" -lt "$VERIFY_TIMEOUT" ]; do
  QEMU_PID=$(find_qemu_pid)
  [ -n "$QEMU_PID" ] && break
  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))
done

if [ -z "$QEMU_PID" ]; then
  printf 'status=failed\nreason=no_qemu_process\nlaunch_log=%s\n' "$LAUNCH_LOG"
  exit 1
fi
printf '%s\n' "$QEMU_PID" > "$PID_FILE"
printf 'qemu_pid=%s\n' "$QEMU_PID"

elapsed=0
while [ "$elapsed" -lt "$VERIFY_TIMEOUT" ]; do
  [ -S "$QGA_SOCK" ] && [ -S "$QMP_SOCK" ] && break
  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))
done

if [ ! -S "$QGA_SOCK" ]; then
  printf 'status=failed\nreason=no_qga_socket\nqemu_pid=%s\nlaunch_log=%s\n' "$QEMU_PID" "$LAUNCH_LOG"
  exit 1
fi

for i in $(seq 1 5); do
  HANDSHAKE=$(python3 .agents/tools/qga_exec.py --sock "$QGA_SOCK" 'echo qga_ok && uname -a' 2>/dev/null)
  RC=$?
  if [ "$RC" -eq 0 ] && echo "$HANDSHAKE" | grep -q 'qga_ok'; then
    printf 'status=ready\nqemu_pid=%s\nqga_handshake=ok\nlaunch_log=%s\nconsole_log=%s\n' "$QEMU_PID" "$LAUNCH_LOG" "$CONSOLE_LOG"
    exit 0
  fi
  sleep 2
done

printf 'status=failed\nreason=qga_handshake_failed\nqemu_pid=%s\nlaunch_log=%s\n' "$QEMU_PID" "$LAUNCH_LOG"
exit 1
