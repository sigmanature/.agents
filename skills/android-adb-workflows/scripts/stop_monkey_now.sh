#!/usr/bin/env bash
set -euo pipefail

SERIALS=()
SEND_HOME=1

usage() {
  cat <<'EOF'
stop_monkey_now.sh: stop device-side Android monkey processes immediately

Usage:
  stop_monkey_now.sh [--serial <SERIAL>]...
  stop_monkey_now.sh --all

Options:
  -s, --serial <SERIAL>   target a specific device; repeatable
  -a, --all               target all adb devices in "device" state
  --no-home               do not send KEYCODE_HOME after kill
  -h, --help              show help

Notes:
  - This stops the device-side `com.android.commands.monkey` process.
  - Unplugging USB does not reliably stop monkey once it is running on device.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial)
      SERIALS+=("${2:-}")
      shift 2
      ;;
    -a|--all)
      mapfile -t devs < <(adb devices | awk 'NR>1 && $2=="device" {print $1}')
      SERIALS+=("${devs[@]}")
      shift
      ;;
    --no-home)
      SEND_HOME=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${#SERIALS[@]}" -eq 0 ]]; then
  mapfile -t devs < <(adb devices | awk 'NR>1 && $2=="device" {print $1}')
  if [[ "${#devs[@]}" -eq 1 ]]; then
    SERIALS=("${devs[0]}")
  else
    echo "No target selected. Pass --serial <SERIAL> or --all." >&2
    exit 2
  fi
fi

mapfile -t SERIALS < <(printf '%s\n' "${SERIALS[@]}" | awk 'NF' | awk '!seen[$0]++')

for serial in "${SERIALS[@]}"; do
  ADB=(adb -s "$serial")
  before="$("${ADB[@]}" shell pidof com.android.commands.monkey 2>/dev/null || true)"
  echo "== $serial =="
  echo "before: ${before:-<none>}"

  "${ADB[@]}" shell 'pkill -f com.android.commands.monkey || true' >/dev/null 2>&1 || true
  after="$("${ADB[@]}" shell pidof com.android.commands.monkey 2>/dev/null || true)"
  if [[ -n "${after}" ]]; then
    "${ADB[@]}" shell 'for p in $(pidof com.android.commands.monkey); do kill -9 "$p"; done' >/dev/null 2>&1 || true
  fi

  if [[ "$SEND_HOME" -eq 1 ]]; then
    "${ADB[@]}" shell input keyevent KEYCODE_HOME >/dev/null 2>&1 || true
  fi

  final="$("${ADB[@]}" shell pidof com.android.commands.monkey 2>/dev/null || true)"
  echo "after: ${final:-<none>}"
done
