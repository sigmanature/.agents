#!/usr/bin/env bash
set -euo pipefail
RUN_DIR=""
SERIAL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2;;
    --serial) SERIAL="$2"; shift 2;;
    -h|--help) echo "Usage: cf_probe_tracefs.sh --run-dir RUN_DIR [--serial SERIAL]"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -n "$RUN_DIR" && -x "$RUN_DIR/bin/adb" ]] || { echo "invalid --run-dir" >&2; exit 1; }
ADB=("$RUN_DIR/bin/adb")
[[ -n "$SERIAL" ]] && ADB+=( -s "$SERIAL" )
shadb(){ "${ADB[@]}" shell "$@"; }

echo "== adb/root =="
"${ADB[@]}" wait-for-device
"${ADB[@]}" root || true
"${ADB[@]}" wait-for-device
shadb 'id; cat /proc/self/status | grep CapEff || true; getprop ro.build.type; getprop ro.product.device'

echo "== tracefs paths =="
shadb 'ls -ld /sys/kernel/tracing /sys/kernel/debug/tracing 2>/dev/null || true'
shadb 'mount -t tracefs nodev /sys/kernel/tracing 2>/dev/null || true'
shadb 'ls -ld /sys/kernel/tracing 2>/dev/null || true'

echo "== available_events head =="
shadb 'cat /sys/kernel/tracing/available_events 2>/dev/null | head -n 30 || true'

echo "== event probes =="
EVENTS=(
  sched/sched_switch
  raw_syscalls/sys_enter
  raw_syscalls/sys_exit
  syscalls/sys_enter_openat
  syscalls/sys_enter_write
  syscalls/sys_enter_pwrite64
  syscalls/sys_enter_fsync
  syscalls/sys_enter_fdatasync
  syscalls/sys_enter_ftruncate
  syscalls/sys_enter_renameat2
  syscalls/sys_enter_unlinkat
  block/block_rq_issue
  block/block_rq_complete
  f2fs/f2fs_file_write_iter
  f2fs/f2fs_do_write_data_page
  f2fs/f2fs_sync_file_enter
  f2fs/f2fs_sync_file_exit
  f2fs/f2fs_replace_atomic_write_block
)
for ev in "${EVENTS[@]}"; do
  shadb "test -e /sys/kernel/tracing/events/$ev/enable && echo OK $ev || echo NO $ev"
done

echo "== /data mount =="
shadb 'mount | grep " /data " || true'
