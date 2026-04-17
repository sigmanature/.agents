#!/usr/bin/env bash
set -euo pipefail

# sqlite_write_load_launcher_monkey.sh
#
# Generate UI interaction load in the launcher via `monkey -p <launcher_pkg> ...`,
# with the intent to trigger the launcher's own persistence (often SQLite) and/or
# its downstream providers (SettingsProvider, etc.).
#
# Why monkey:
# - Fully deterministic “create folder / add widget / drag icon” needs UI selectors
#   and is fragile across device resolution/launcher versions.
# - Monkey gives a repeatable (seeded) but broad interaction stream.
#
# Outputs:
# - ./sqlite_write_load_logs/<serial>_<ts>/launcher_monkey_stdout.txt
# - ./sqlite_write_load_logs/<serial>_<ts>/launcher_monkey_stderr.txt
# - ./sqlite_write_load_logs/<serial>_<ts>/summary.txt

SERIAL=""
PKG=""
EVENTS=20000
THROTTLE=60
SEED=""
OUT_DIR=""
EXTRA=""

usage() {
  cat <<'EOF'
sqlite_write_load_launcher_monkey.sh: generate launcher UI churn (monkey)

Usage:
  sqlite_write_load_launcher_monkey.sh [options]

Options:
  -s, --serial <serial>      target a specific device (auto-picks if exactly one)
  -p, --package <pkg.name>   launcher package (default: auto-detect Pixel Launcher then AOSP Launcher3)
  -e, --events <n>           number of monkey events (default: 20000)
  -t, --throttle <ms>        delay between events (default: 60)
  --seed <n>                 fixed seed (default: generated)
  --extra "<flags>"          extra monkey flags appended verbatim
  --out <dir>                output dir (default: ./sqlite_write_load_logs/<serial>_<timestamp>)

Notes:
  - This does not guarantee SQLite writes (depends on launcher build/config),
    but it is a practical way to stress launcher state changes.
  - For best effect, keep the device awake/unlocked and stay on HOME.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="${2:-}"; shift 2;;
    -p|--package) PKG="${2:-}"; shift 2;;
    -e|--events) EVENTS="${2:-}"; shift 2;;
    -t|--throttle) THROTTLE="${2:-}"; shift 2;;
    --seed) SEED="${2:-}"; shift 2;;
    --extra) EXTRA="${2:-}"; shift 2;;
    --out) OUT_DIR="${2:-}"; shift 2;;
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

if [[ -z "$SEED" ]]; then
  SEED="$(date +%s)"
fi

if [[ -z "$PKG" ]]; then
  if adb_sh pm path com.google.android.apps.nexuslauncher >/dev/null 2>&1; then
    PKG="com.google.android.apps.nexuslauncher"
  elif adb_sh pm path com.android.launcher3 >/dev/null 2>&1; then
    PKG="com.android.launcher3"
  else
    echo "Cannot auto-detect launcher package. Please pass --package <pkg.name>." >&2
    echo "Tip: adb shell pm list packages | grep -iE 'launcher|nexuslauncher'" >&2
    exit 1
  fi
fi

if ! adb_sh pm path "$PKG" >/dev/null 2>&1; then
  echo "Package not found on device: $PKG" >&2
  exit 1
fi

SUMMARY="$OUT_DIR/summary.txt"
{
  echo "serial: $SERIAL"
  echo "timestamp: $NOW"
  echo "package: $PKG"
  echo "events: $EVENTS"
  echo "throttle_ms: $THROTTLE"
  echo "seed: $SEED"
  echo "extra: ${EXTRA:-<none>}"
  echo "out_dir: $OUT_DIR"
} >"$SUMMARY"

# Best-effort: wake/unlock + go HOME.
adb_sh input keyevent KEYCODE_WAKEUP >/dev/null 2>&1 || true
adb_sh wm dismiss-keyguard >/dev/null 2>&1 || true
adb_sh input keyevent KEYCODE_HOME >/dev/null 2>&1 || true

# Monkey flags: keep running, avoid syskeys, avoid major-nav that escapes too much.
SAFE_FLAGS="--pct-syskeys 0 --pct-majornav 10 --pct-nav 10 --pct-touch 45 --pct-motion 35 --pct-appswitch 0 --ignore-crashes --ignore-timeouts --monitor-native-crashes --kill-process-after-error -v -v"

MONKEY_CMD="monkey -p $PKG -s $SEED --throttle $THROTTLE $SAFE_FLAGS $EXTRA $EVENTS"
echo "monkey_cmd: $MONKEY_CMD" >>"$SUMMARY"

STDOUT_F="$OUT_DIR/launcher_monkey_stdout.txt"
STDERR_F="$OUT_DIR/launcher_monkey_stderr.txt"

set +e
adb_sh $MONKEY_CMD >"$STDOUT_F" 2>"$STDERR_F"
RC=$?
set -e

echo "monkey_exit_code: $RC" >>"$SUMMARY"
echo "done out_dir=$OUT_DIR"

