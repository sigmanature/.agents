#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-normal}"
INSTANCE_NAME=""
WAIT_SECS="${VM_STOP_WAIT_SECS:-5}"
GRACE_SECS="${VM_STOP_GRACE_SECS:-3}"

case "$MODE" in
  normal|crash|deadlock)
    ;;
  *)
    echo "usage: $0 [normal|crash|deadlock] [--instance <name>]" >&2
    exit 2
    ;;
esac

if [ "${2:-}" = "--instance" ]; then
  INSTANCE_NAME="${3:-}"
  if [ -z "$INSTANCE_NAME" ]; then
    echo "usage: $0 [normal|crash|deadlock] [--instance <name>]" >&2
    exit 2
  fi
  PID_FILE="./myscripts/vm_instances/${INSTANCE_NAME}/qemu.pid"
  if [ ! -f "$PID_FILE" ]; then
    echo "status=blocked"
    echo "reason=missing-pid-file"
    echo "pid_file=$PID_FILE"
    exit 1
  fi
  QEMU_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
else
  QEMU_PIDS="$(ps aux | grep '[q]emu-system-aarch64' | awk '{print $2}')"
  QEMU_PID_COUNT="$(printf '%s\n' "$QEMU_PIDS" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [ "$QEMU_PID_COUNT" -ne 1 ]; then
    echo "status=blocked"
    echo "reason=multiple-qemu-found-require-instance"
    echo "hint=run: bash scripts/vm_stop.sh $MODE --instance vm2"
    ps aux | grep '[q]emu-system-aarch64' || true
    exit 1
  fi
  QEMU_PID="$(printf '%s\n' "$QEMU_PIDS" | head -n 1)"
fi

if [ -z "$QEMU_PID" ]; then
  echo "status=blocked"
  echo "reason=no-running-qemu-system-aarch64-found"
  exit 1
fi

echo "mode=$MODE"
echo "target_pid=$QEMU_PID"
if [ -n "$INSTANCE_NAME" ]; then
  echo "instance=$INSTANCE_NAME"
fi
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
