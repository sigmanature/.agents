#!/usr/bin/env bash
set -euo pipefail

SERIAL=""
PKGS=()
EVENTS=50000
THROTTLE=75
SEED=""
EXTRA=""
OUT_BASE="./monkey_logs"
OUT_DIR=""
DO_BUGREPORT=0
GLOBAL_MODE=0
CLEAR_LOGCAT=0
ABORT_ON_NATIVE_CRASH=0

usage() {
  cat <<'EOF'
run_monkey_and_collect_logs.sh: repeatable monkey run + log/artifact collection

Usage:
  run_monkey_and_collect_logs.sh [options] --package <pkg.name>
  run_monkey_and_collect_logs.sh [options] --global

Options:
  -s, --serial <serial>     target a specific device
  -p, --package <pkg.name>  package to constrain monkey (repeatable for multiple packages)
  --global                  do NOT constrain to a package (monkey may roam across apps)
  -e, --events <n>           number of events (default: 50000)
  -t, --throttle <ms>        delay between events (default: 75)
  --seed <n>                 fixed seed (default: generated)
  --extra "<flags>"          extra monkey flags appended verbatim
  --out <dir>                output dir (default: ./monkey_logs/<serial>_<timestamp>)
  --bugreport                capture adb bugreport at end (can be slow)
  --clear-logcat             clear log buffers before starting (destructive)
  --abort-on-native-crash    stop monkey when a native crash is detected
  -h, --help                 show help

Examples:
  ./scripts/run_monkey_and_collect_logs.sh --package com.example.app
  ./scripts/run_monkey_and_collect_logs.sh --serial ABC123 --package com.example.app --events 300000 --throttle 50 --seed 12345
  ./scripts/run_monkey_and_collect_logs.sh --package com.example.app --extra "--pct-touch 70 --pct-motion 20 --pct-appswitch 10"
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="${2:-}"; shift 2;;
    -p|--package) PKGS+=("${2:-}"); shift 2;;
    --global) GLOBAL_MODE=1; shift;;
    -e|--events) EVENTS="${2:-}"; shift 2;;
    -t|--throttle) THROTTLE="${2:-}"; shift 2;;
    --seed) SEED="${2:-}"; shift 2;;
    --extra) EXTRA="${2:-}"; shift 2;;
    --out) OUT_DIR="${2:-}"; shift 2;;
    --bugreport) DO_BUGREPORT=1; shift;;
    --clear-logcat) CLEAR_LOGCAT=1; shift;;
    --abort-on-native-crash) ABORT_ON_NATIVE_CRASH=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

# Resolve serial if not provided.
if [[ -z "$SERIAL" ]]; then
  mapfile -t _devs < <(adb devices | awk 'NR>1 && $2=="device" {print $1}')
  if [[ "${#_devs[@]}" -eq 1 ]]; then
    SERIAL="${_devs[0]}"
  elif [[ "${#_devs[@]}" -eq 0 ]]; then
    echo "No device found. Run: adb devices" >&2
    exit 1
  else
    echo "Multiple devices detected. Re-run with --serial <SERIAL>:" >&2
    printf '  %s\n' "${_devs[@]}" >&2
    exit 1
  fi
fi

ADB=(adb -s "$SERIAL")

ts() { date +%Y%m%d_%H%M%S; }

NOW="$(ts)"
if [[ -z "$OUT_DIR" ]]; then
  OUT_DIR="$OUT_BASE/${SERIAL}_${NOW}"
fi
mkdir -p "$OUT_DIR"

SUMMARY="$OUT_DIR/summary.txt"

if [[ -z "$SEED" ]]; then
  # reasonably portable seed
  SEED="$(date +%s)"
fi

# Basic input validation.
if [[ "$GLOBAL_MODE" -eq 0 && "${#PKGS[@]}" -eq 0 ]]; then
  echo "Missing --package <pkg.name> (or pass --global)." >&2
  exit 2
fi
if [[ "$GLOBAL_MODE" -eq 0 ]]; then
  for _pkg in "${PKGS[@]}"; do
    if ! "${ADB[@]}" shell pm path "$_pkg" >/dev/null 2>&1; then
      echo "Package not found on device: $_pkg" >&2
      echo "Tip: ./scripts/adb_pkg.sh list | grep -i <keyword>" >&2
      exit 1
    fi
  done
fi

# Root detection.
HAS_SU=0
if "${ADB[@]}" shell 'command -v su >/dev/null 2>&1 && su -c id >/dev/null 2>&1'; then
  HAS_SU=1
fi

# Start logcat streaming on host.
LOGCAT_FILE="$OUT_DIR/logcat_all_threadtime.txt"
if [[ "$CLEAR_LOGCAT" -eq 1 ]]; then
  "${ADB[@]}" logcat -c || true
fi
"${ADB[@]}" logcat -v threadtime -b all > "$LOGCAT_FILE" 2>/dev/null &
LOGCAT_PID=$!

cleanup() {
  # Best-effort stop logcat.
  if kill -0 "$LOGCAT_PID" >/dev/null 2>&1; then
    kill "$LOGCAT_PID" >/dev/null 2>&1 || true
    wait "$LOGCAT_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Snapshot basics.
"${ADB[@]}" shell getprop > "$OUT_DIR/getprop.txt" || true
"${ADB[@]}" shell uptime > "$OUT_DIR/uptime.txt" || true
"${ADB[@]}" shell date > "$OUT_DIR/device_date.txt" || true

"${ADB[@]}" shell dumpsys activity activities > "$OUT_DIR/dumpsys_activity_start.txt" || true
if [[ "$GLOBAL_MODE" -eq 0 ]]; then
  "${ADB[@]}" shell dumpsys meminfo "${PKGS[0]}" > "$OUT_DIR/dumpsys_meminfo_start.txt" || true
else
  "${ADB[@]}" shell dumpsys meminfo > "$OUT_DIR/dumpsys_meminfo_start.txt" || true
fi
"${ADB[@]}" shell dumpsys dropbox --print > "$OUT_DIR/dumpsys_dropbox_print.txt" || true

if [[ "$HAS_SU" -eq 1 ]]; then
  "${ADB[@]}" shell su -c 'sh -c "dmesg > /data/local/tmp/dmesg_start.txt; chmod 0644 /data/local/tmp/dmesg_start.txt"' || true
  "${ADB[@]}" pull /data/local/tmp/dmesg_start.txt "$OUT_DIR/dmesg_start.txt" >/dev/null 2>&1 || true
  "${ADB[@]}" shell rm -f /data/local/tmp/dmesg_start.txt >/dev/null 2>&1 || true
fi

# Monkey command.
SAFE_FLAGS="--pct-syskeys 0 --pct-majornav 0 --ignore-crashes --ignore-timeouts --ignore-native-crashes -v -v"
if [[ "$ABORT_ON_NATIVE_CRASH" -eq 1 ]]; then
  SAFE_FLAGS="--pct-syskeys 0 --pct-majornav 0 --ignore-crashes --ignore-timeouts --monitor-native-crashes --kill-process-after-error -v -v"
fi
PKG_PART=""
if [[ "$GLOBAL_MODE" -eq 0 ]]; then
  for _pkg in "${PKGS[@]}"; do
    PKG_PART="$PKG_PART -p $_pkg"
  done
fi

MONKEY_CMD="monkey$PKG_PART -s $SEED --throttle $THROTTLE $SAFE_FLAGS $EXTRA $EVENTS"

{
  echo "serial: $SERIAL"
  echo "timestamp: $NOW"
  echo "packages: ${PKGS[*]:-<global>}"
  echo "events: $EVENTS"
  echo "throttle_ms: $THROTTLE"
  echo "seed: $SEED"
  echo "extra: ${EXTRA:-<none>}"
  echo "su_available: $HAS_SU"
  echo "out_dir: $OUT_DIR"
  echo
  echo "monkey_cmd: $MONKEY_CMD"
} > "$SUMMARY"

# Run monkey.
MONKEY_STDOUT="$OUT_DIR/monkey_stdout.txt"
MONKEY_STDERR="$OUT_DIR/monkey_stderr.txt"
set +e
"${ADB[@]}" shell "$MONKEY_CMD" > "$MONKEY_STDOUT" 2> "$MONKEY_STDERR"
MONKEY_RC=$?
set -e

echo "monkey_exit_code: $MONKEY_RC" >> "$SUMMARY"

# Post snapshots.
"${ADB[@]}" shell dumpsys activity activities > "$OUT_DIR/dumpsys_activity_end.txt" || true
if [[ "$GLOBAL_MODE" -eq 0 ]]; then
  "${ADB[@]}" shell dumpsys meminfo "${PKGS[0]}" > "$OUT_DIR/dumpsys_meminfo_end.txt" || true
else
  "${ADB[@]}" shell dumpsys meminfo > "$OUT_DIR/dumpsys_meminfo_end.txt" || true
fi

if [[ "$HAS_SU" -eq 1 ]]; then
  "${ADB[@]}" shell su -c 'sh -c "dmesg > /data/local/tmp/dmesg_end.txt; chmod 0644 /data/local/tmp/dmesg_end.txt"' || true
  "${ADB[@]}" pull /data/local/tmp/dmesg_end.txt "$OUT_DIR/dmesg_end.txt" >/dev/null 2>&1 || true
  "${ADB[@]}" shell rm -f /data/local/tmp/dmesg_end.txt >/dev/null 2>&1 || true

  # Root-only artifact archive.
  DEV_TGZ="/data/local/tmp/monkey_artifacts_${NOW}.tgz"
  "${ADB[@]}" shell su -c "sh -c \"tar -czf $DEV_TGZ /data/tombstones /data/anr /data/system/dropbox 2>/dev/null || true; chmod 0644 $DEV_TGZ\"" || true
  mkdir -p "$OUT_DIR/device_artifacts"
  "${ADB[@]}" pull "$DEV_TGZ" "$OUT_DIR/device_artifacts/monkey_artifacts_${NOW}.tgz" >/dev/null 2>&1 || true
  "${ADB[@]}" shell rm -f "$DEV_TGZ" >/dev/null 2>&1 || true
fi

if [[ "$DO_BUGREPORT" -eq 1 ]]; then
  echo "bugreport: capturing..." >> "$SUMMARY"
  "${ADB[@]}" bugreport "$OUT_DIR/bugreport.zip" >/dev/null 2>&1 || true
fi

# Stop logcat now (trap will also handle).
cleanup
trap - EXIT

echo "done. output: $OUT_DIR"
exit "$MONKEY_RC"
