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
LAUNCHER="ori"
INSTANCE_NAME=""

if [ "${1:-}" = "--launcher" ] || [ "${1:-}" = "--instance" ]; then
  LAUNCH_LOG="$ROOT_DIR/.roo/plans/qemu-launch.log"
  PID_FILE="$ROOT_DIR/.roo/plans/qemu.pid"
  CONSOLE_LOG="$ROOT_DIR/guest_console.log"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --launcher) LAUNCHER="${2:-}"; shift 2 ;;
      --instance) INSTANCE_NAME="${2:-}"; shift 2 ;;
      --launch-log) LAUNCH_LOG="${2:-}"; shift 2 ;;
      --pid-file) PID_FILE="${2:-}"; shift 2 ;;
      --console-log) CONSOLE_LOG="${2:-}"; shift 2 ;;
      -h|--help)
        echo "usage: $0 [LAUNCH_LOG PID_FILE CONSOLE_LOG] | $0 --launcher <ori|ubuntu-cow> [--instance vm2] [--launch-log p] [--pid-file p] [--console-log p]" >&2
        exit 2
        ;;
      *)
        echo "unknown arg: $1" >&2
        exit 2
        ;;
    esac
  done
fi

mkdir -p "$(dirname "$LAUNCH_LOG")"
mkdir -p "$(dirname "$PID_FILE")"

rm -f "$CONSOLE_LOG"
case "$LAUNCHER" in
  ori)
    nohup /bin/bash ./myscripts/qemu_start_ori.sh --log "$CONSOLE_LOG" >"$LAUNCH_LOG" 2>&1 &
    ;;
  ubuntu-cow)
    if [ -z "$INSTANCE_NAME" ]; then
      echo "ERROR: --instance is required for --launcher ubuntu-cow" >&2
      exit 2
    fi
    # Use instance-aware launcher; it owns per-instance pidfile and socket naming.
    nohup /bin/bash ./myscripts/qemu_start_ubuntu.sh start "$INSTANCE_NAME" --log "$CONSOLE_LOG" >"$LAUNCH_LOG" 2>&1 &
    ;;
  *)
    echo "ERROR: unknown launcher: $LAUNCHER" >&2
    exit 2
    ;;
esac
PID=$!
printf '%s\n' "$PID" > "$PID_FILE"
printf 'pid=%s\nlaunch_log=%s\nconsole_log=%s\nlauncher=%s\n' "$PID" "$LAUNCH_LOG" "$CONSOLE_LOG" "$LAUNCHER"
if [ -n "$INSTANCE_NAME" ]; then
  printf 'instance=%s\ninstance_env=%s\n' "$INSTANCE_NAME" "$ROOT_DIR/myscripts/vm_instances/$INSTANCE_NAME/instance.env"
fi
