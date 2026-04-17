#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
enable_f2fs_inode_kv_logs.sh

Enables/disables dynamic_debug printsites for an inode-centric f2fs k=v logging plan
and (optionally) enables relevant f2fs tracepoints.

This script does not assume adb; it can run:
  - locally (root on the target machine)
  - or via adb by printing commands (see --print-adb)

Usage:
  enable_f2fs_inode_kv_logs.sh --mode normal|detail --enable
  enable_f2fs_inode_kv_logs.sh --mode normal|detail --disable

Options:
  --mode <m>         normal|detail (required)
  --enable           enable chosen printsites
  --disable          disable chosen printsites
  --print-adb        print equivalent 'adb shell su -c ...' commands instead of executing

Notes:
  - dynamic_debug requires debugfs mounted and CONFIG_DYNAMIC_DEBUG.
  - tracepoints require tracefs/debugfs tracing mounted.
EOF
}

MODE=""
ACTION=""
PRINT_ADB=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --enable) ACTION="enable"; shift ;;
    --disable) ACTION="disable"; shift ;;
    --print-adb) PRINT_ADB=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$MODE" || ! "$MODE" =~ ^(normal|detail)$ ]]; then
  echo "ERROR: --mode normal|detail is required" >&2
  exit 1
fi
if [[ -z "$ACTION" ]]; then
  echo "ERROR: --enable or --disable is required" >&2
  exit 1
fi

DDCTL="/sys/kernel/debug/dynamic_debug/control"
TR="/sys/kernel/debug/tracing"

dd_line() {
  local line="$1"
  if [[ $PRINT_ADB -eq 1 ]]; then
    printf "adb shell su -c %q\n" "echo '$line' > '$DDCTL'"
  else
    echo "$line" > "$DDCTL"
  fi
}

dd_add() {
  local line="$1"
  if [[ $PRINT_ADB -eq 1 ]]; then
    printf "adb shell su -c %q\n" "echo '$line' >> '$DDCTL'"
  else
    echo "$line" >> "$DDCTL"
  fi
}

tr_write() {
  local path="$1"
  local val="$2"
  if [[ $PRINT_ADB -eq 1 ]]; then
    printf "adb shell su -c %q\n" "echo '$val' > '$path'"
  else
    echo "$val" > "$path"
  fi
}

ensure_paths() {
  if [[ $PRINT_ADB -eq 1 ]]; then
    return 0
  fi
  if [[ ! -w "$DDCTL" ]]; then
    echo "ERROR: dynamic_debug control not writable: $DDCTL" >&2
    echo "Hint: mount debugfs: mount -t debugfs none /sys/kernel/debug" >&2
    exit 1
  fi
}

ensure_paths

FLAG="+p"
if [[ "$ACTION" == "disable" ]]; then
  FLAG="-p"
fi

# Start from a clean slate for repeatability.
dd_line "# f2fs inode kv logs ($MODE/$ACTION)"

# Normal mode: entry/exit + errors + state transitions.
dd_add "file fs/f2fs/data.c func f2fs_write_cache_folios $FLAG"
dd_add "file fs/f2fs/data.c func f2fs_write_single_data_folio $FLAG"
dd_add "file fs/f2fs/data.c func prepare_large_folio_atomic_write_begin $FLAG"
dd_add "file fs/f2fs/data.c func f2fs_write_end_io $FLAG"
dd_add "file fs/f2fs/inode.c func f2fs_iget $FLAG"
dd_add "file fs/f2fs/file.c func f2fs_ioc_start_atomic_write $FLAG"
dd_add "file fs/f2fs/file.c func f2fs_ioc_commit_atomic_write $FLAG"
dd_add "file fs/f2fs/file.c func f2fs_ioc_abort_atomic_write $FLAG"
dd_add "file fs/f2fs/file.c func f2fs_ioc_enable_verity $FLAG"

if [[ "$MODE" == "detail" ]]; then
  # Detail mode: these are hot; prefer tracepoints if possible.
  dd_add "file fs/f2fs/segment.c func f2fs_inplace_write_data $FLAG"
  dd_add "file fs/f2fs/segment.c func f2fs_outplace_write_data $FLAG"
fi

# Optional tracepoints: useful when printk is too noisy.
# (Safe to leave disabled; enable explicitly here only in detail mode.)
if [[ "$MODE" == "detail" ]]; then
  tr_write "$TR/events/f2fs/f2fs_submit_folio_write/enable" "1"
  tr_write "$TR/events/f2fs/f2fs_replace_atomic_write_block/enable" "1"
  tr_write "$TR/events/f2fs/f2fs_writepages/enable" "1"
fi

echo "OK: dynamic_debug updated ($MODE/$ACTION)."
if [[ "$MODE" == "detail" ]]; then
  echo "NOTE: tracepoints enabled under $TR/events/f2fs/ (disable manually if needed)."
fi

