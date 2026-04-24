#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/adb_helpers.sh"

SERIAL="${SERIAL:-}"
PKG="com.learnos.sqlitewalrepro"
BASE_OUT=""
MAX_ATTEMPTS=0
SLEEP_BETWEEN=0
CLEAR_DATA=1
FORWARD_ARGS=()

need_value() {
  local opt="$1"
  local argc="$2"
  if (( argc < 2 )); then
    echo "Missing value for $opt" >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) need_value "$1" "$#"; SERIAL="$2"; shift 2 ;;
    --base-out) need_value "$1" "$#"; BASE_OUT="$2"; shift 2 ;;
    --max-attempts) need_value "$1" "$#"; MAX_ATTEMPTS="$2"; shift 2 ;;
    --sleep-between) need_value "$1" "$#"; SLEEP_BETWEEN="$2"; shift 2 ;;
    --no-clear) CLEAR_DATA=0; shift 1 ;;
    --help|-h)
      cat <<USAGE
Usage:
  $(basename "$0") --serial SERIAL [loop opts] -- [plan2 args]

Loop options:
  --base-out DIR           Root output directory (default: walrepro_loop_YYYYmmdd_HHMMSS)
  --max-attempts N         0 means infinite (default: 0)
  --sleep-between SEC      Sleep between attempts (default: 0)
  --no-clear               Do not run 'pm clear' before each attempt

Everything after '--' is forwarded to:
  walrepro_plan2_capture_tracefs.sh

Example:
  $(basename "$0") --serial ABC123 --base-out myloop -- \
    --seconds 180 --writers 1 --readers 0 --updatesPerTxn 4 \
    --blobBytes 1024 --rows 256 --maxRows 8192 \
    --updatePct 50 --insertPct 25 --replacePct 25 \
    --checkpoint TRUNCATE --synchronous FULL \
    --checkEvery 200 --patternSample 10 \
    --checkpointThread 1 --checkpointEveryIters 1 \
    --checkpointBurst 1 --checkpointSleepMs 0 --klogTarget wal
USAGE
      exit 0
      ;;
    --)
      shift
      FORWARD_ARGS=("$@")
      break
      ;;
    *)
      echo "Unknown loop arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$BASE_OUT" ]]; then
  BASE_OUT="walrepro_loop_$(date +%Y%m%d_%H%M%S)"
fi
mkdir -p "$BASE_OUT"
SUMMARY="$BASE_OUT/summary.tsv"
printf 'attempt\tstatus\trun_dir\tmarker\n' > "$SUMMARY"

echo "[+] loop root=$BASE_OUT serial=${SERIAL:-default} max_attempts=$MAX_ATTEMPTS clear_data=$CLEAR_DATA"

attempt=0
while :; do
  attempt=$((attempt + 1))
  if [[ "$MAX_ATTEMPTS" != "0" && "$attempt" -gt "$MAX_ATTEMPTS" ]]; then
    echo "[OK] reached max_attempts=$MAX_ATTEMPTS without DETECT"
    exit 0
  fi

  stamp="$(date +%Y%m%d_%H%M%S)"
  run_dir="$BASE_OUT/attempt_$(printf '%03d' "$attempt")_${stamp}"

  echo "[+] attempt=$attempt run_dir=$run_dir"
  if [[ "$CLEAR_DATA" == "1" ]]; then
    adb_host shell pm clear "$PKG" >/dev/null
  fi

  PLAN2_CMD=("$SCRIPT_DIR/walrepro_plan2_capture_tracefs.sh")
  if [[ -n "$SERIAL" ]]; then
    PLAN2_CMD+=(--serial "$SERIAL")
  fi
  PLAN2_CMD+=(--out "$run_dir")
  if [[ ${#FORWARD_ARGS[@]} -gt 0 ]]; then
    PLAN2_CMD+=("${FORWARD_ARGS[@]}")
  fi

  set +e
  "${PLAN2_CMD[@]}"
  rc=$?
  set -e

  if [[ $rc -ne 0 ]]; then
    printf '%s\t%s\t%s\t%s\n' "$attempt" "script_error:$rc" "$run_dir" "-" >> "$SUMMARY"
    echo "[!] plan2 script returned rc=$rc; stopping"
    exit $rc
  fi

  marker=""
  status="clean"
  if rg -n 'phase=DETECT' "$run_dir/logcat_WalRepro.txt" >/dev/null 2>&1; then
    marker="DETECT"
    status="hit"
  elif rg -n 'phase=THREAD_FAIL' "$run_dir/logcat_WalRepro.txt" >/dev/null 2>&1; then
    marker="THREAD_FAIL"
    status="hit"
  elif rg -n 'phase=FAIL' "$run_dir/logcat_WalRepro.txt" >/dev/null 2>&1; then
    marker="FAIL"
    status="hit"
  elif rg -n 'phase=DONE .*failed=0' "$run_dir/logcat_WalRepro.txt" >/dev/null 2>&1; then
    marker="DONE_clean"
    status="clean"
  else
    marker="unknown"
    status="unknown"
  fi

  printf '%s\t%s\t%s\t%s\n' "$attempt" "$status" "$run_dir" "$marker" >> "$SUMMARY"

  if [[ "$status" == "hit" ]]; then
    echo "[HIT] attempt=$attempt marker=$marker run_dir=$run_dir"
    exit 0
  fi

  if [[ "$status" == "unknown" ]]; then
    echo "[!] unknown run outcome in $run_dir; stopping"
    exit 3
  fi

  if [[ "$SLEEP_BETWEEN" != "0" ]]; then
    sleep "$SLEEP_BETWEEN"
  fi
done
