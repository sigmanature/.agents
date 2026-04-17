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
ARG_CHECK_EVERY=1
ARG_PATTERN_SAMPLE=10

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="$2"; shift 2;;
    --seconds) SECONDS="$2"; ARG_SECONDS="$2"; shift 2;;
    --out) OUT_DIR="$2"; shift 2;;
    -h|--help)
      cat <<EOF
Usage:
  $(basename "$0") --serial SERIAL [--seconds N] [--out DIR]

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

echo "[+] sanity: package installed?"
adb_host shell pm path "$PKG" >/dev/null

echo "[+] start logcat capture (WalRepro only)"
adb_host logcat -c || true
adb_host logcat -v threadtime -s WalRepro >"$OUT_DIR/logcat_WalRepro.txt" &
LOGCAT_PID=$!

cleanup() {
  kill "$LOGCAT_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[+] tracefs prep (events enabled but no PID filter yet)"
"$SCRIPT_DIR/tracefs_prep_minimal.sh" --serial "$SERIAL" || true

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
  -e checkEvery "$ARG_CHECK_EVERY" \
  -e patternSample "$ARG_PATTERN_SAMPLE" \
  "$RUNNER" \
  >"$OUT_DIR/instrument_stdout.txt" 2>"$OUT_DIR/instrument_stderr.txt" &
INSTR_PID=$!

echo "[+] polling PID for $PKG ..."
APP_PID=""
for _ in $(seq 1 200); do
  APP_PID="$(adb_host shell pidof "$PKG" 2>/dev/null | tr -d '\r' | awk '{print $1}')"
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

echo "[+] set_event_pid then clear trace buffer (defines T0)"
adb_su_sh "echo '$APP_PID' > '$ROOT/set_event_pid'"
adb_su_sh "echo 0 > '$ROOT/tracing_on' || true"
adb_su_sh ": > '$ROOT/trace' || true"
adb_su_sh "echo 1 > '$ROOT/tracing_on'"
adb_su_sh "cat '$ROOT/trace_clock' 2>/dev/null || true" >"$OUT_DIR/trace_clock.txt" || true
adb_su_sh "date +%s%N" >"$OUT_DIR/t0_marker_host_ns.txt" || true

echo "[+] capture fd->path/inode/flags snapshot (T0+)"
{
  echo "== pid=$APP_PID =="
  adb_host shell su -c "ls -l /proc/$APP_PID/fd 2>/dev/null || true"
  echo
  echo "== fdinfo (flags etc) =="
  adb_host shell su -c "for f in /proc/$APP_PID/fdinfo/*; do echo \"--- $f ---\"; cat \"$f\"; done 2>/dev/null || true"
  echo
  echo "== inode snapshot for db/wal/shm paths =="
  adb_host shell su -c "for fd in /proc/$APP_PID/fd/*; do p=$(readlink \"$fd\" 2>/dev/null || true); case \"$p\" in *databases/*|*repro.db*|*wal*|*shm*) echo \"fd=$(basename $fd) path=$p\"; stat -c 'inode=%i mode=%f size=%s' \"$p\" 2>/dev/null || true;; esac; done"
} >"$OUT_DIR/fdinfo.txt" 2>"$OUT_DIR/fdinfo.err" || true

echo "[+] wait for DETECT/FAIL (or timeout)"
end=$(( $(date +%s) + SECONDS ))
while [[ $(date +%s) -lt $end ]]; do
  if rg -n "phase=(DETECT|FAIL)" "$OUT_DIR/logcat_WalRepro.txt" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

echo "[+] extract T1 markers from logcat"
rg -n "phase=(DETECT|FAIL|SNAPSHOT)" "$OUT_DIR/logcat_WalRepro.txt" >"$OUT_DIR/t1_markers.txt" || true

echo "[+] dump tracefs trace buffer"
adb_su_sh "echo 0 > '$ROOT/tracing_on' || true"
adb_exec_out_su "cat '$ROOT/trace' 2>/dev/null || true" >"$OUT_DIR/tracefs_trace.txt" || true

echo "[+] wait instrumentation exit"
wait "$INSTR_PID" 2>/dev/null || true

echo "[OK] out=$OUT_DIR"

