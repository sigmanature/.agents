#!/usr/bin/env bash
set -euo pipefail

# Reproduce SQLite WAL + checkpoint IO patterns and capture correlated traces.
#
# Primary intent:
# - generate deterministic "WAL append + frequent checkpoint" syscall sequences
# - capture ftrace/perfetto evidence (syscalls + f2fs + block) for postmortem
#
# Requirements:
# - adb in PATH (host)
# - device accessible via adb (optionally rooted via Magisk su)
# - device has perfetto CLI (most Pixels do)
# - device has sqlite3 CLI (if not, push one or use a custom repro binary)
#
# Notes:
# - This script does NOT require access to app sandboxes. It creates its own DB
#   under /data/local/tmp by default.
# - If you need to reproduce under fscrypt-per-app semantics, run an app-side
#   reproducer inside /data/user/0/<pkg>/... instead.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=adb_helpers.sh
source "$SCRIPT_DIR/adb_helpers.sh"

SERIAL=""
SECONDS=120
DB_PATH="/data/local/tmp/wal_repro.db"
TX_UPDATES=200
BLOB_BYTES=4096
CHECK_EVERY=25
SLEEP_MS=0
CHECKPOINT_MODE="TRUNCATE" # PASSIVE|FULL|RESTART|TRUNCATE
SYNC_MODE="FULL"          # OFF|NORMAL|FULL|EXTRA
TRACE=1
BUF_MB=128

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --serial <SERIAL>          adb device serial (optional if single device)
  --seconds <N>              run duration (default: $SECONDS)
  --db <PATH>                db path on device (default: $DB_PATH)
  --tx-updates <N>           updates per transaction (default: $TX_UPDATES)
  --blob-bytes <N>           randomblob size (default: $BLOB_BYTES)
  --check-every <N>          integrity_check interval in tx loops (default: $CHECK_EVERY)
  --sleep-ms <N>             sleep between loops (default: $SLEEP_MS)
  --checkpoint <MODE>        PASSIVE|FULL|RESTART|TRUNCATE (default: $CHECKPOINT_MODE)
  --sync <MODE>              OFF|NORMAL|FULL|EXTRA (default: $SYNC_MODE)
  --no-trace                 do not run perfetto trace
  --buf-mb <N>               perfetto buffer size (default: $BUF_MB)

Output:
  Creates a timestamped folder in the current directory:
    sqlite_wal_repro_YYYYmmdd_HHMMSS/
  Contains:
    perfetto.pftrace (if tracing enabled)
    dmesg_before.txt, dmesg_after.txt
    sqlite_stdout.txt, sqlite_stderr.txt
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial) SERIAL="$2"; shift 2;;
    --seconds) SECONDS="$2"; shift 2;;
    --db) DB_PATH="$2"; shift 2;;
    --tx-updates) TX_UPDATES="$2"; shift 2;;
    --blob-bytes) BLOB_BYTES="$2"; shift 2;;
    --check-every) CHECK_EVERY="$2"; shift 2;;
    --sleep-ms) SLEEP_MS="$2"; shift 2;;
    --checkpoint) CHECKPOINT_MODE="$2"; shift 2;;
    --sync) SYNC_MODE="$2"; shift 2;;
    --no-trace) TRACE=0; shift 1;;
    --buf-mb) BUF_MB="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

ADB=(adb)
if [[ -n "$SERIAL" ]]; then
  ADB=(adb -s "$SERIAL")
fi

need_cmd adb

OUT_DIR="sqlite_wal_repro_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT_DIR"

echo "[+] Using device serial: $("${ADB[@]}" get-serialno 2>/dev/null || true)"
echo "[+] Output: $OUT_DIR"

echo "[+] Checking sqlite3 on device..."
if ! "${ADB[@]}" shell 'command -v sqlite3 >/dev/null 2>&1'; then
  cat >&2 <<EOF
ERROR: sqlite3 CLI not found on device.

Options:
  1) Push a static sqlite3 to /data/local/tmp/sqlite3 and add to PATH:
     adb push sqlite3-arm64 /data/local/tmp/sqlite3
     adb shell su -c 'chmod 0755 /data/local/tmp/sqlite3'
     Then rerun with:
       adb shell su -c 'PATH=/data/local/tmp:\$PATH sqlite3 -version'

  2) Use a custom repro binary that links libsqlite and logs syscalls/FD->inode mapping.
EOF
  exit 1
fi

echo "[+] Capturing dmesg (before)..."
"${ADB[@]}" shell su -c 'dmesg -T' >"$OUT_DIR/dmesg_before.txt" 2>"$OUT_DIR/dmesg_before.err" || true

TRACE_DEV_OUT="/data/misc/perfetto-traces/sqlite_wal_repro_$(date +%Y%m%d_%H%M%S).pftrace"

cleanup() {
  if [[ "$TRACE" == "1" ]]; then
    echo "[+] Pulling perfetto trace..."
    "${ADB[@]}" pull "$TRACE_DEV_OUT" "$OUT_DIR/perfetto.pftrace" >/dev/null 2>&1 || true
  fi
  echo "[+] Capturing dmesg (after)..."
  "${ADB[@]}" shell su -c 'dmesg -T' >"$OUT_DIR/dmesg_after.txt" 2>"$OUT_DIR/dmesg_after.err" || true
}
trap cleanup EXIT

PERFETTO_PID=""
if [[ "$TRACE" == "1" ]]; then
  echo "[+] Starting perfetto trace ($SECONDS s, buffer ${BUF_MB}mb)..."

  # Best-effort event selection:
  # - syscalls open/close/read/write/pwrite/fsync/fdatasync/ftruncate/rename/unlink
  # - f2fs write/sync tracepoints when present
  #
  # Missing events are simply omitted.
  SYS_EVENTS=(
    syscalls/sys_enter_openat
    syscalls/sys_enter_close
    syscalls/sys_enter_read
    syscalls/sys_enter_write
    syscalls/sys_enter_pwrite64
    syscalls/sys_enter_fsync
    syscalls/sys_enter_fdatasync
    syscalls/sys_enter_ftruncate
    syscalls/sys_enter_renameat
    syscalls/sys_enter_renameat2
    syscalls/sys_enter_unlinkat
  )
  F2FS_EVENTS=(
    f2fs/f2fs_write_begin
    f2fs/f2fs_write_end
    f2fs/f2fs_writepage
    f2fs/f2fs_do_write_data_page
    f2fs/f2fs_sync_file_enter
    f2fs/f2fs_sync_file_exit
  )
  BLK_EVENTS=(
    block/block_rq_issue
    block/block_rq_complete
  )

  have_event() {
    local evt="$1"
    # evt like "syscalls/sys_enter_openat" -> /sys/kernel/tracing/events/syscalls/sys_enter_openat/enable
    "${ADB[@]}" shell su -c "test -e /sys/kernel/tracing/events/${evt}/enable" >/dev/null 2>&1
  }

  ENABLE_EVENTS=()
  for e in "${SYS_EVENTS[@]}" "${F2FS_EVENTS[@]}" "${BLK_EVENTS[@]}"; do
    if have_event "$e"; then
      ENABLE_EVENTS+=("$e")
    fi
  done

  if [[ "${#ENABLE_EVENTS[@]}" -eq 0 ]]; then
    echo "[!] No ftrace events found under /sys/kernel/tracing/events; disabling trace."
    TRACE=0
  else
    # Build a perfetto text config on the host and stream it into device perfetto.
    {
      echo "buffers: { size_kb: $((BUF_MB * 1024)) fill_policy: RING_BUFFER }"
      echo "data_sources: { config { name: \"linux.ftrace\" ftrace_config {"
      echo "  ftrace_events: \"sched/sched_switch\""
      for e in "${ENABLE_EVENTS[@]}"; do
        echo "  ftrace_events: \"${e}\""
      done
      echo "  atrace_apps: \"*\""
      echo "} } }"
      echo "duration_ms: $((SECONDS * 1000))"
    } >"$OUT_DIR/perfetto_cfg.txt"

    # Start perfetto in background (device-side).
    "${ADB[@]}" shell su -c "perfetto -c - --txt -o '$TRACE_DEV_OUT'" \
      <"$OUT_DIR/perfetto_cfg.txt" >/dev/null 2>&1 &
    PERFETTO_PID=$!
    # Give perfetto a moment to arm ftrace.
    sleep 1
  fi
fi

echo "[+] Initializing DB (WAL + schema)..."
"${ADB[@]}" shell su -c "rm -f '$DB_PATH' '$DB_PATH-wal' '$DB_PATH-shm' && \
  sqlite3 '$DB_PATH' \"\
    PRAGMA journal_mode=WAL; \
    PRAGMA synchronous=$SYNC_MODE; \
    PRAGMA wal_autocheckpoint=1; \
    CREATE TABLE IF NOT EXISTS t(id INTEGER PRIMARY KEY, b BLOB); \
    CREATE TABLE IF NOT EXISTS m(k INTEGER PRIMARY KEY, v INTEGER); \
    INSERT OR IGNORE INTO m(k,v) VALUES(1,0); \
    WITH RECURSIVE c(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM c WHERE x<2048) \
      INSERT OR IGNORE INTO t(id,b) SELECT x, randomblob($BLOB_BYTES) FROM c; \
  \"" \
  >"$OUT_DIR/sqlite_init_stdout.txt" 2>"$OUT_DIR/sqlite_init_stderr.txt" || true

echo "[+] Running WAL workload for ${SECONDS}s..."
START_TS="$(date +%s)"
LOOP=0

while true; do
  NOW="$(date +%s)"
  if (( NOW - START_TS >= SECONDS )); then
    break
  fi

  LOOP=$((LOOP + 1))

  # One transaction: a bunch of small+blob updates, then explicit checkpoint.
  #
  # The UPDATE statements are intentionally random and repeated to force:
  # - WAL appends
  # - SHM coordination
  # - frequent checkpoint rewriting main DB pages
  SQL="PRAGMA journal_mode=WAL;
PRAGMA synchronous=$SYNC_MODE;
BEGIN;
UPDATE m SET v=v+1 WHERE k=1;
"
  for ((i=0; i<TX_UPDATES; i++)); do
    SQL+="UPDATE t SET b=randomblob($BLOB_BYTES) WHERE id=(abs(random()) % 2048)+1;
"
  done
  SQL+="COMMIT;
PRAGMA wal_checkpoint($CHECKPOINT_MODE);
"
  if (( CHECK_EVERY > 0 )) && (( LOOP % CHECK_EVERY == 0 )); then
    SQL+="PRAGMA integrity_check;
"
  fi

  # Run sqlite3 once per loop (keeps syscall sequence clear and observable).
  "${ADB[@]}" shell su -c "sqlite3 '$DB_PATH' \"$SQL\"" \
    >>"$OUT_DIR/sqlite_stdout.txt" 2>>"$OUT_DIR/sqlite_stderr.txt" || true

  if (( SLEEP_MS > 0 )); then
    "${ADB[@]}" shell "sleep $(awk \"BEGIN{print $SLEEP_MS/1000}\")" >/dev/null 2>&1 || true
  fi
done

if [[ "$TRACE" == "1" ]]; then
  echo "[+] Waiting perfetto to finish..."
  wait "$PERFETTO_PID" 2>/dev/null || true
fi

echo "[+] Done."

