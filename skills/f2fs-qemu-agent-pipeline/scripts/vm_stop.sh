#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-normal}"
WAIT_SECS="${VM_STOP_WAIT_SECS:-5}"
GRACE_SECS="${VM_STOP_GRACE_SECS:-3}"

case "$MODE" in
  normal|crash|deadlock)
    ;;
  *)
    echo "usage: $0 [normal|crash|deadlock]" >&2
    exit 2
    ;;
esac

QEMU_PID="$(ps aux | grep '[q]emu-system-aarch64' | awk 'NR==1 {print $2}')"

if [ -z "$QEMU_PID" ]; then
  echo "status=blocked"
  echo "reason=no-running-qemu-system-aarch64-found"
  exit 1
fi

echo "mode=$MODE"
echo "target_pid=$QEMU_PID"
echo "precheck=ps aux | grep qemu"
ps aux | grep '[q]emu' || true

if [ "$MODE" = "crash" ] || [ "$MODE" = "deadlock" ]; then
  echo "delay_reason=collect-console-evidence"
  echo "sleep_seconds=$WAIT_SECS"
  sleep "$WAIT_SECS"
fi

echo "stop_signal=TERM"
kill "$QEMU_PID"

echo "grace_seconds=$GRACE_SECS"
sleep "$GRACE_SECS"

if ps -p "$QEMU_PID" > /dev/null 2>&1; then
  echo "escalation=KILL"
  kill -9 "$QEMU_PID"
  sleep 1
fi

echo "postcheck=ps aux | grep qemu"
ps aux | grep '[q]emu' || true

if ps -p "$QEMU_PID" > /dev/null 2>&1; then
  echo "status=failed"
  echo "reason=qemu-still-running"
  exit 1
fi

echo "status=success"
