#!/usr/bin/env bash
set -euo pipefail

# Plan-2 capture for WAL repro on a real device:
# - start instrumentation (it begins workload immediately)
# - poll for PID
# - apply set_event_pid, clear trace buffer -> define T0
# - wait for DETECT/FAIL in logcat -> define T1 candidates
# - dump tracefs trace + capture per-fd inode/flags mapping
#
# This script does NOT require a persistent interactive shell.
# It uses root `sh -c` wrappers so redirections happen as root.
#
# Usage:
#   walrepro_plan2_capture_tracefs.sh --serial SERIAL [--seconds N] [--out DIR]

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/adb_helpers.sh"

SERIAL="${SERIAL:-}"
SECONDS=300
OUT_DIR=""

PKG="com.learnos.sqlitewalrepro"
RUNNER="com.learnos.sqlitewalrepro.test/androidx.test.runner.AndroidJUnitRunner"

# workload args (defaults match the app defaults reasonably; override by editing here if needed)
ARG_SECONDS=300
ARG_CHECKPOINT="TRUNCATE"
ARG_SYNC="FULL"
ARG_WRITERS=1
ARG_READERS=0
ARG_UPDATES_PER_TXN=200
ARG_BLOB_BYTES=4096
ARG_ROWS=2048
ARG_MAX_ROWS=2048
ARG_UPDATE_PCT=100
ARG_INSERT_PCT=0
ARG_REPLACE_PCT=0
ARG_CHECK_EVERY=1
ARG_PATTERN_SAMPLE=10
ARG_CHECKPOINT_THREAD=0
ARG_CHECKPOINT_EVERY_ITERS=1
ARG_CHECKPOINT_BURST=1
ARG_CHECKPOINT_SLEEP_MS=0
ARG_START_GATE_TIMEOUT_MS=60000
ARG_SNAPSHOT_ON_DETECT=1
ARG_KLOG_TARGET="wal"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="$2"; shift 2;;
    --seconds) SECONDS="$2"; ARG_SECONDS="$2"; shift 2;;
    --out) OUT_DIR="$2"; shift 2;;
    --writers) ARG_WRITERS="$2"; shift 2;;
    --readers) ARG_READERS="$2"; shift 2;;
    --updatesPerTxn) ARG_UPDATES_PER_TXN="$2"; shift 2;;
    --blobBytes) ARG_BLOB_BYTES="$2"; shift 2;;
    --rows) ARG_ROWS="$2"; shift 2;;
    --maxRows) ARG_MAX_ROWS="$2"; shift 2;;
    --updatePct) ARG_UPDATE_PCT="$2"; shift 2;;
    --insertPct) ARG_INSERT_PCT="$2"; shift 2;;
    --replacePct) ARG_REPLACE_PCT="$2"; shift 2;;
    --checkpoint) ARG_CHECKPOINT="$2"; shift 2;;
    --synchronous) ARG_SYNC="$2"; shift 2;;
    --checkEvery) ARG_CHECK_EVERY="$2"; shift 2;;
    --patternSample) ARG_PATTERN_SAMPLE="$2"; shift 2;;
    --checkpointThread) ARG_CHECKPOINT_THREAD="$2"; shift 2;;
    --checkpointEveryIters) ARG_CHECKPOINT_EVERY_ITERS="$2"; shift 2;;
    --checkpointBurst) ARG_CHECKPOINT_BURST="$2"; shift 2;;
    --checkpointSleepMs) ARG_CHECKPOINT_SLEEP_MS="$2"; shift 2;;
    --klogTarget) ARG_KLOG_TARGET="$2"; shift 2;;
    -h|--help)
      cat <<EOF
Usage:
  $(basename "$0") --serial SERIAL [--seconds N] [--out DIR]

Workload args (passed to instrumentation):
  --writers N
  --readers N
  --updatesPerTxn N
  --blobBytes N
  --rows N
  --maxRows N
  --updatePct N
  --insertPct N
  --replacePct N
  --checkpoint {PASSIVE|FULL|RESTART|TRUNCATE}
  --synchronous {OFF|NORMAL|FULL|EXTRA}
  --checkEvery N
  --patternSample N
  --checkpointThread {0|1}
  --checkpointEveryIters N
  --checkpointBurst N
  --checkpointSleepMs N
  --klogTarget {auto|db|wal|shm}

Output:
  OUT_DIR contains:
    logcat_WalRepro.txt
    tracefs_trace.txt
    fdinfo.txt (fd->path + inode + flags snapshots)
    t0_marker.txt / t1_markers.txt
EOF
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

if [[ -z "$OUT_DIR" ]]; then
  OUT_DIR="walrepro_plan2_$(date +%Y%m%d_%H%M%S)"
fi
mkdir -p "$OUT_DIR"

echo "[+] serial=$SERIAL out=$OUT_DIR seconds=$SECONDS"

ROOT="/sys/kernel/tracing"
START_GATE_PATH="/sdcard/Android/data/$PKG/files/wal_repro_gates/start.go"
DB_PATH="/data/user/0/$PKG/databases/repro.db"
WAL_PATH="${DB_PATH}-wal"
SHM_PATH="${DB_PATH}-shm"
KLOG_TARGET_PATH=""
KLOG_TARGET_INO=""
KLOG_SYSFS_DIR=""

echo "[+] sanity: package installed?"
adb_host shell pm path "$PKG" >/dev/null

echo "[+] start logcat capture (WalRepro only)"
# `logcat -c` can be permission-gated. Clear as root best-effort so we don't
# accidentally match stale DETECT/FAIL markers from a previous run.
adb_su_sh "logcat -c" || true
adb_host logcat -v threadtime -s WalRepro >"$OUT_DIR/logcat_WalRepro.txt" &
LOGCAT_PID=$!

cleanup() {
  kill "$LOGCAT_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

arm_klog_for_inode_best_effort() {
  local ino="$1"
  local target_path="$2"
  local f2fs_dev=""

  f2fs_dev="$(adb_su_sh "ls -1 /sys/fs/f2fs 2>/dev/null | grep '^dm-' | head -n 1" | tr -d '\r' | tail -n 1 || true)"
  if [[ -z "${f2fs_dev:-}" || -z "${ino:-}" ]]; then
    return 0
  fi

  KLOG_SYSFS_DIR="/sys/fs/f2fs/${f2fs_dev}"
  adb_su_sh "echo 0 > '$KLOG_SYSFS_DIR/klog_wb_enable'" || true
  adb_su_sh "echo 2 > '$KLOG_SYSFS_DIR/klog_wb_detail'" || true
  adb_su_sh "echo 1 > '$KLOG_SYSFS_DIR/klog_wb_sample'" || true
  adb_su_sh "echo 0 > '$KLOG_SYSFS_DIR/klog_wb_idx_lo'" || true
  adb_su_sh "echo 0 > '$KLOG_SYSFS_DIR/klog_wb_idx_hi'" || true
  adb_su_sh "echo '$ino' > '$KLOG_SYSFS_DIR/klog_wb_ino'" || true
  adb_su_sh "echo 1 > '$KLOG_SYSFS_DIR/klog_wb_enable'" || true

  {
    echo "klog_target=$ARG_KLOG_TARGET"
    echo "path=$target_path"
    echo "ino=$ino"
    echo "sysfs=$KLOG_SYSFS_DIR"
  } >"$OUT_DIR/klog_target.txt"
}

echo "[+] tracefs prep (events enabled but no PID filter yet)"
"$SCRIPT_DIR/tracefs_prep_minimal.sh" --serial "$SERIAL" || true

echo "[+] reset start gate"
adb_host shell "mkdir -p '${START_GATE_PATH%/*}' && rm -f '$START_GATE_PATH'" || true

echo "[+] launch instrumentation (workload starts immediately)"
adb_host shell am instrument -w -r \
  -e seconds "$ARG_SECONDS" \
  -e writers "$ARG_WRITERS" \
  -e readers "$ARG_READERS" \
  -e checkpoint "$ARG_CHECKPOINT" \
  -e synchronous "$ARG_SYNC" \
  -e blobBytes "$ARG_BLOB_BYTES" \
  -e updatesPerTxn "$ARG_UPDATES_PER_TXN" \
  -e rows "$ARG_ROWS" \
  -e maxRows "$ARG_MAX_ROWS" \
  -e updatePct "$ARG_UPDATE_PCT" \
  -e insertPct "$ARG_INSERT_PCT" \
  -e replacePct "$ARG_REPLACE_PCT" \
  -e checkEvery "$ARG_CHECK_EVERY" \
  -e patternSample "$ARG_PATTERN_SAMPLE" \
  -e checkpointThread "$ARG_CHECKPOINT_THREAD" \
  -e checkpointEveryIters "$ARG_CHECKPOINT_EVERY_ITERS" \
  -e checkpointBurst "$ARG_CHECKPOINT_BURST" \
  -e checkpointSleepMs "$ARG_CHECKPOINT_SLEEP_MS" \
  -e startGatePath "$START_GATE_PATH" \
  -e startGateTimeoutMs "$ARG_START_GATE_TIMEOUT_MS" \
  -e snapshotOnDetect "$ARG_SNAPSHOT_ON_DETECT" \
  "$RUNNER" \
  >"$OUT_DIR/instrument_stdout.txt" 2>"$OUT_DIR/instrument_stderr.txt" &
INSTR_PID=$!

echo "[+] polling PID for $PKG ..."
APP_PID=""
for _ in $(seq 1 200); do
  # `pidof` returns non-zero when the process isn't up yet. With `set -e` +
  # `pipefail`, that would abort the script on the first miss, defeating the
  # whole “poll until PID appears” intent.
  raw_pid="$(adb_host shell pidof "$PKG" 2>/dev/null || true)"
  APP_PID="$(printf '%s' "$raw_pid" | tr -d '\r' | awk '{print $1}')"
  if [[ -n "$APP_PID" ]]; then
    break
  fi
  sleep 0.05
done

if [[ -z "$APP_PID" ]]; then
  echo "ERROR: failed to get pidof $PKG" >&2
  exit 1
fi
echo "[+] app pid=$APP_PID"
echo "$APP_PID" >"$OUT_DIR/app_pid.txt"

echo "[+] set_event_pid (all tids) then clear trace buffer (defines T0)"
# IMPORTANT:
# - ftrace `set_event_pid` matches Linux thread IDs (TIDs), not the process TGID.
# - Java workloads often perform I/O on background threads, so filtering to only
#   the TGID would miss most syscalls (and the actual failing fsync()).
# - We therefore populate `set_event_pid` with *all* current tids under
#   /proc/<tgid>/task.
adb_su_sh "for t in \$(ls /proc/$APP_PID/task 2>/dev/null || true); do echo \$t; done > $ROOT/set_event_pid"
adb_su_sh "cat $ROOT/set_event_pid 2>/dev/null || true" >"$OUT_DIR/set_event_pid.txt" || true
# Best-effort refresh once (some threads start slightly later).
sleep 0.5
adb_su_sh "for t in \$(ls /proc/$APP_PID/task 2>/dev/null || true); do echo \$t; done > $ROOT/set_event_pid"
adb_su_sh "cat $ROOT/set_event_pid 2>/dev/null || true" >"$OUT_DIR/set_event_pid_2.txt" || true

adb_su_sh "echo 0 > $ROOT/tracing_on || true"
adb_su_sh ": > $ROOT/trace || true"
adb_su_sh "echo 1 > $ROOT/tracing_on"
adb_su_sh "cat $ROOT/trace_clock 2>/dev/null || true" >"$OUT_DIR/trace_clock.txt" || true
adb_su_sh "date +%s%N" >"$OUT_DIR/t0_marker_host_ns.txt" || true

echo "[+] capture fd->path/inode/flags snapshot (T0+)"
{
  echo "== pid=$APP_PID =="
  adb_su_sh "ls -l /proc/$APP_PID/fd 2>/dev/null || true"
  echo
  echo "== fdinfo (flags etc) =="
  adb_su_sh "for f in /proc/$APP_PID/fdinfo/*; do echo \"--- \$f ---\"; cat \"\$f\"; done 2>/dev/null || true"
  echo
  echo "== inode snapshot for db/wal/shm paths =="
  adb_su_sh "for fd in /proc/$APP_PID/fd/*; do p=\$(readlink \"\$fd\" 2>/dev/null || true); case \"\$p\" in *databases/*|*repro.db*|*wal*|*shm*) echo \"fd=\$(basename \"\$fd\") path=\$p\"; stat -c 'inode=%i mode=%f size=%s' \"\$p\" 2>/dev/null || true;; esac; done"
  echo
  echo "== direct path stat =="
  adb_su_sh "for p in '$DB_PATH' '$WAL_PATH' '$SHM_PATH'; do echo \"path=\$p\"; stat -c 'inode=%i mode=%f size=%s' \"\$p\" 2>/dev/null || true; done"
} >"$OUT_DIR/fdinfo.txt" 2>"$OUT_DIR/fdinfo.err" || true

echo "[+] choose klog target and arm f2fs inode filter (best-effort)"
case "$ARG_KLOG_TARGET" in
  db) KLOG_TARGET_PATH="$DB_PATH" ;;
  wal) KLOG_TARGET_PATH="$WAL_PATH" ;;
  shm) KLOG_TARGET_PATH="$SHM_PATH" ;;
  auto)
    if rg -F "path=$WAL_PATH" "$OUT_DIR/fdinfo.txt" >/dev/null 2>&1; then
      KLOG_TARGET_PATH="$WAL_PATH"
    elif rg -F "path=$DB_PATH" "$OUT_DIR/fdinfo.txt" >/dev/null 2>&1; then
      KLOG_TARGET_PATH="$DB_PATH"
    else
      KLOG_TARGET_PATH="$SHM_PATH"
    fi
    ;;
  *)
    echo "ERROR: unsupported --klogTarget=$ARG_KLOG_TARGET" >&2
    exit 2
    ;;
esac

KLOG_TARGET_INO="$(
  awk -v want="path=$KLOG_TARGET_PATH" '
    $0 == want { hit=1; next }
    hit && $1 ~ /^inode=/ {
      sub(/^inode=/, "", $1)
      print $1
      exit
    }
  ' "$OUT_DIR/fdinfo.txt"
)"

if [[ -n "${KLOG_TARGET_INO:-}" ]]; then
  echo "[+] klog_wb target path=$KLOG_TARGET_PATH ino=$KLOG_TARGET_INO"
  arm_klog_for_inode_best_effort "$KLOG_TARGET_INO" "$KLOG_TARGET_PATH"
else
  echo "[!] failed to resolve klog target inode for path=$KLOG_TARGET_PATH" | tee "$OUT_DIR/klog_target.txt"
fi

echo "[+] open start gate"
printf 'go %s\n' "$(date +%s%N)" >"$OUT_DIR/start_gate_host_ns.txt"
adb_host shell "mkdir -p '${START_GATE_PATH%/*}' && : > '$START_GATE_PATH'"

echo "[+] wait for DETECT/FAIL (or timeout)"
end=$(( $(date +%s) + SECONDS ))
while [[ $(date +%s) -lt $end ]]; do
  if rg -n "phase=(DETECT|THREAD_FAIL|FAIL|DONE)" "$OUT_DIR/logcat_WalRepro.txt" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

echo "[+] capture fd->path/inode/flags snapshot (T1-ish)"
{
  echo "== pid=$APP_PID =="
  adb_su_sh "ls -l /proc/$APP_PID/fd 2>/dev/null || true"
  echo
  echo "== fdinfo (flags etc) =="
  adb_su_sh "for f in /proc/$APP_PID/fdinfo/*; do echo \"--- \$f ---\"; cat \"\$f\"; done 2>/dev/null || true"
  echo
  echo "== inode snapshot for db/wal/shm paths =="
  adb_su_sh "for fd in /proc/$APP_PID/fd/*; do p=\$(readlink \"\$fd\" 2>/dev/null || true); case \"\$p\" in *databases/*|*repro.db*|*wal*|*shm*) echo \"fd=\$(basename \"\$fd\") path=\$p\"; stat -c 'inode=%i mode=%f size=%s' \"\$p\" 2>/dev/null || true;; esac; done"
  echo
  echo "== direct path stat =="
  adb_su_sh "for p in '$DB_PATH' '$WAL_PATH' '$SHM_PATH'; do echo \"path=\$p\"; stat -c 'inode=%i mode=%f size=%s' \"\$p\" 2>/dev/null || true; done"
} >"$OUT_DIR/fdinfo_t1.txt" 2>"$OUT_DIR/fdinfo_t1.err" || true

echo "[+] extract T1 markers from logcat"
rg -n "phase=(DETECT|THREAD_FAIL|FAIL|SNAPSHOT|DONE)" "$OUT_DIR/logcat_WalRepro.txt" >"$OUT_DIR/t1_markers.txt" || true

echo "[+] dump tracefs trace buffer"
adb_su_sh "echo 0 > $ROOT/tracing_on || true"
adb_exec_out_su "cat $ROOT/trace 2>/dev/null || true" >"$OUT_DIR/tracefs_trace.txt" || true

echo "[+] save F2FS_WB-related dmesg slice"
if [[ -n "${KLOG_TARGET_INO:-}" ]]; then
  adb_exec_out_su "dmesg 2>/dev/null | grep -E 'F2FS_WB|setattr_size|truncate|write_cache_folios|ffs_(mark|clear)_subrange|ino=${KLOG_TARGET_INO}' || true" \
    >"$OUT_DIR/dmesg_f2fs_wb_filtered.txt" || true
else
  adb_exec_out_su "dmesg 2>/dev/null | grep -E 'F2FS_WB|setattr_size|truncate|write_cache_folios|ffs_(mark|clear)_subrange' || true" \
    >"$OUT_DIR/dmesg_f2fs_wb_filtered.txt" || true
fi

echo "[+] wait instrumentation exit"
wait "$INSTR_PID" 2>/dev/null || true

echo "[OK] out=$OUT_DIR"
