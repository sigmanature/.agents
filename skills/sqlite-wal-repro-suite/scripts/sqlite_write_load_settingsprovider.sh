#!/usr/bin/env bash
set -euo pipefail

# sqlite_write_load_settingsprovider.sh
#
# Generate a steady stream of SettingsProvider SQLite writes WITHOUT UI automation.
#
# Rationale:
# - Most Settings UI toggles ultimately persist via SettingsProvider (SQLite).
# - Using `adb shell settings put system ...` is more deterministic than UI clicks.
# - Validate writes by watching `settings.db-wal` size/mtime change.
#
# Outputs:
# - ./sqlite_write_load_logs/<serial>_<ts>/baseline.txt
# - ./sqlite_write_load_logs/<serial>_<ts>/final.txt
# - ./sqlite_write_load_logs/<serial>_<ts>/ops.log

SERIAL=""
SECONDS=120
SLEEP_MS=200
OUT_DIR=""
NO_VERIFY=0

usage() {
  cat <<'EOF'
sqlite_write_load_settingsprovider.sh: generate SQLite write load via SettingsProvider

Usage:
  sqlite_write_load_settingsprovider.sh [--serial <SERIAL>] [--seconds <sec>] [--sleep-ms <ms>] [--out <dir>] [--no-verify]

Defaults:
  --seconds 120
  --sleep-ms 200
  --out ./sqlite_write_load_logs/<serial>_<timestamp>

Notes:
  - This script does NOT require tapping the UI.
  - It uses `adb shell settings put system ...` which usually runs as the `shell` uid.
  - Verification reads SettingsProvider DB files as root (Magisk `su`).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="${2:-}"; shift 2;;
    --seconds) SECONDS="${2:-}"; shift 2;;
    --sleep-ms) SLEEP_MS="${2:-}"; shift 2;;
    --out) OUT_DIR="${2:-}"; shift 2;;
    --no-verify) NO_VERIFY=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

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

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/adb_helpers.sh"
SERIAL="$SERIAL"

ts() { date +%Y%m%d_%H%M%S; }
NOW="$(ts)"
if [[ -z "$OUT_DIR" ]]; then
  OUT_DIR="./sqlite_write_load_logs/${SERIAL}_${NOW}"
fi
mkdir -p "$OUT_DIR"

OPS_LOG="$OUT_DIR/ops.log"
BASELINE="$OUT_DIR/baseline.txt"
FINAL="$OUT_DIR/final.txt"

echo "serial=$SERIAL out_dir=$OUT_DIR seconds=$SECONDS sleep_ms=$SLEEP_MS" | tee "$OUT_DIR/run_info.txt"

if ! adb_host shell 'command -v settings >/dev/null 2>&1'; then
  echo "Device is missing `settings` CLI (unexpected). Aborting." >&2
  exit 1
fi

HAS_SU=0
if [[ "$NO_VERIFY" -eq 0 ]]; then
  if adb_host shell 'command -v su >/dev/null 2>&1 && su -c id >/dev/null 2>&1'; then
    HAS_SU=1
  else
    echo "Warning: su not available; file-level verification will be skipped." >&2
    NO_VERIFY=1
  fi
fi

snapshot_baseline() {
  {
    echo "==== baseline $(date -Iseconds) ===="
    echo "-- settings get (system) --"
    adb_sh settings get system screen_off_timeout || true
    adb_sh settings get system accelerometer_rotation || true
    adb_sh settings get system screen_brightness || true
    echo
    echo "-- content query (SettingsProvider) --"
    # Important: run as a single command string. `adb shell sh -c ...` easily drops args (sh -c semantics),
    # and `adb shell content query ... --where "..."` is hard to quote portably.
    adb_host shell "content query --uri content://settings/system --projection name:value --where \"name='screen_off_timeout'\"" || true
    adb_host shell "content query --uri content://settings/system --projection name:value --where \"name='accelerometer_rotation'\"" || true
    adb_host shell "content query --uri content://settings/system --projection name:value --where \"name='screen_brightness'\"" || true
    echo
    if [[ "$HAS_SU" -eq 1 ]]; then
      echo "-- note: many production builds block /data/... DB reads via SELinux even with magisk su --"
    fi
    echo
  } | tee "$BASELINE"
}

snapshot_final() {
  {
    echo "==== final $(date -Iseconds) ===="
    echo "-- settings get (system) --"
    adb_sh settings get system screen_off_timeout || true
    adb_sh settings get system accelerometer_rotation || true
    adb_sh settings get system screen_brightness || true
    echo
    echo "-- content query (SettingsProvider) --"
    adb_host shell "content query --uri content://settings/system --projection name:value --where \"name='screen_off_timeout'\"" || true
    adb_host shell "content query --uri content://settings/system --projection name:value --where \"name='accelerometer_rotation'\"" || true
    adb_host shell "content query --uri content://settings/system --projection name:value --where \"name='screen_brightness'\"" || true
    echo
  } | tee "$FINAL"
}

snapshot_baseline

start_ts=$(date +%s)
end_ts=$((start_ts + SECONDS))

# Use keys that are:
# - in SYSTEM table (usually writable by shell)
# - not obviously destructive
# - likely to be persisted immediately
#
# We alternate values to force a write each time.
vals_timeout=(15000 30000 60000 120000)
vals_rotation=(0 1)
vals_brightness=(30 80 140 200)

idx=0
echo "# ops start $(date -Iseconds)" >"$OPS_LOG"

while [[ "$(date +%s)" -lt "$end_ts" ]]; do
  v_timeout="${vals_timeout[$((idx % ${#vals_timeout[@]}))]}"
  v_rot="${vals_rotation[$((idx % ${#vals_rotation[@]}))]}"
  v_bright="${vals_brightness[$((idx % ${#vals_brightness[@]}))]}"

  # screen_off_timeout
  echo "$(date -Iseconds) settings put system screen_off_timeout $v_timeout" | tee -a "$OPS_LOG"
  adb_sh settings put system screen_off_timeout "$v_timeout" >/dev/null 2>&1 || adb_su "settings put system screen_off_timeout $v_timeout" >/dev/null 2>&1 || true

  # accelerometer_rotation
  echo "$(date -Iseconds) settings put system accelerometer_rotation $v_rot" | tee -a "$OPS_LOG"
  adb_sh settings put system accelerometer_rotation "$v_rot" >/dev/null 2>&1 || adb_su "settings put system accelerometer_rotation $v_rot" >/dev/null 2>&1 || true

  # screen_brightness (may be overridden by auto brightness, but still tends to write)
  echo "$(date -Iseconds) settings put system screen_brightness $v_bright" | tee -a "$OPS_LOG"
  adb_sh settings put system screen_brightness "$v_bright" >/dev/null 2>&1 || adb_su "settings put system screen_brightness $v_bright" >/dev/null 2>&1 || true

  idx=$((idx + 1))
  sleep_sec=$(awk "BEGIN {print $SLEEP_MS/1000.0}")
  sleep "$sleep_sec"
done

echo "# ops end $(date -Iseconds)" >>"$OPS_LOG"

snapshot_final

echo "done out_dir=$OUT_DIR"
