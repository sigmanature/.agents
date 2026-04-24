#!/usr/bin/env bash
set -euo pipefail

# Prepare tracefs for WAL repro correlation.
#
# Why this exists:
# - `adb shell su -c "echo 1 > ..."` often fails because `>` redirection happens
#   in the *non-root* shell before `su` runs.
# - This script uses `adb_su_sh` so redirections happen inside a root `sh -c`.
#
# Usage:
#   tracefs_prep_minimal.sh [--serial SERIAL] [--pid PID]
#
# Notes:
# - `--pid` uses /sys/kernel/tracing/set_event_pid. This only helps when the
#   workload pid is stable (e.g. an app process). It does NOT help much for
#   repeatedly spawning `sqlite3` CLI.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=adb_helpers.sh
source "$SCRIPT_DIR/adb_helpers.sh"

SERIAL="${SERIAL:-}"
PID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="$2"; shift 2;;
    --pid) PID="$2"; shift 2;;
    -h|--help)
      cat <<'EOF'
Usage:
  tracefs_prep_minimal.sh [--serial SERIAL] [--pid PID]

Does:
  - tracing_on=0, clear trace buffer
  - set trace_clock=mono (best effort)
  - optionally set_event_pid=PID
  - enable raw_syscalls sys_enter/sys_exit
  - enable selected f2fs and block events
  - tracing_on=1
EOF
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

ROOT="/sys/kernel/tracing"

echo "[+] tracefs: $ROOT"
echo "[+] serial: ${SERIAL:-<default>}"

# Avoid nested quoting here: on some devices / su implementations, mixing
# single-quotes inside the command string can get re-parsed in surprising ways.
# `$ROOT` has no spaces, so keep it unquoted for reliability.
adb_su_sh "test -d $ROOT || { echo tracefs_missing; exit 2; }"

echo "[+] disable tracing + clear buffer"
adb_su_sh "echo 0 > $ROOT/tracing_on || true"
adb_su_sh ": > $ROOT/trace || true"

echo "[+] set trace_clock=mono (best effort)"
adb_su_sh "test -e $ROOT/trace_clock && echo mono > $ROOT/trace_clock || true"

if [[ -n "$PID" ]]; then
  echo "[+] set_event_pid=$PID"
  adb_su_sh "echo $PID > $ROOT/set_event_pid"
else
  echo "[+] set_event_pid cleared"
  adb_su_sh "echo > $ROOT/set_event_pid || true"
fi

enable_evt() {
  local evt="$1"
  local path="$ROOT/events/$evt/enable"
  adb_su_sh "test -e $path && echo 1 > $path || true"
}

echo "[+] enable raw_syscalls"
enable_evt "raw_syscalls/sys_enter"
enable_evt "raw_syscalls/sys_exit"

echo "[+] enable f2fs events (rename/unlink/evict/write/mmap-ish)"
for e in \
  f2fs/f2fs_rename_start \
  f2fs/f2fs_rename_end \
  f2fs/f2fs_unlink_enter \
  f2fs/f2fs_unlink_exit \
  f2fs/f2fs_evict_inode \
  f2fs/f2fs_drop_inode \
  f2fs/f2fs_file_write_iter \
  f2fs/f2fs_do_write_data_page \
  f2fs/f2fs_sync_file_enter \
  f2fs/f2fs_sync_file_exit \
  f2fs/f2fs_sync_dirty_inodes_enter \
  f2fs/f2fs_sync_dirty_inodes_exit \
  f2fs/f2fs_filemap_fault \
  f2fs/f2fs_vm_page_mkwrite \
  f2fs/f2fs_replace_atomic_write_block \
; do
  enable_evt "$e"
done

echo "[+] enable block events (optional)"
for e in block/block_rq_issue block/block_rq_complete; do
  enable_evt "$e"
done

echo "[+] tracing_on=1"
adb_su_sh "echo 1 > $ROOT/tracing_on"

echo "[OK] tracefs prepared"
