#!/usr/bin/env bash
set -euo pipefail

SERIAL=""
THIRD_PARTY=1   # 1: only non-system apps (-3), 0: include all
FILTER=""
MODE="list"     # list | listf | current

usage() {
  cat <<'EOF'
adb_pkg.sh: fast package name discovery via adb

Usage:
  adb_pkg.sh [options] <mode>

Modes:
  list     List package names (default: third-party only)
  listf    List package names with apk path ("pkg<TAB>apk_path")
  current  Print the current foreground component ("pkg/.Activity")

Options:
  -s, --serial <serial>   Target a specific device
  --all                   Include system apps too (no -3)
  -f, --filter <keyword>  Case-insensitive substring filter
  -h, --help              Show help

Examples:
  ./adb_pkg.sh list
  ./adb_pkg.sh --filter wechat list
  ./adb_pkg.sh --all list
  ./adb_pkg.sh listf
  ./adb_pkg.sh current
  ./adb_pkg.sh --serial ABC123 current
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="${2:-}"; shift 2;;
    --all) THIRD_PARTY=0; shift;;
    -f|--filter) FILTER="${2:-}"; shift 2;;
    list|listf|current) MODE="$1"; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

ADB=(adb)
[[ -n "$SERIAL" ]] && ADB+=( -s "$SERIAL" )

adb_sh() { "${ADB[@]}" shell "$@"; }

apply_filter() {
  if [[ -n "$FILTER" ]]; then
    grep -i -- "$FILTER" || true
  else
    cat
  fi
}

list_pkgs() {
  local args=()
  [[ "$THIRD_PARTY" -eq 1 ]] && args+=( -3 )

  # Prefer cmd package if available; fallback to pm.
  if adb_sh cmd package list packages "${args[@]}" >/dev/null 2>&1; then
    adb_sh cmd package list packages "${args[@]}" | sed 's/^package://'
  else
    adb_sh pm list packages "${args[@]}" | sed 's/^package://'
  fi
}

list_pkgs_with_path() {
  local args=()
  [[ "$THIRD_PARTY" -eq 1 ]] && args+=( -3 )

  # Output example: package:/data/app/.../base.apk=com.example
  adb_sh pm list packages -f "${args[@]}" \
    | sed -n 's/^package:\(.*\)=\([^=]*\)$/\2\t\1/p'
}

current_foreground() {
  # Best-effort across Android versions.
  local out line

  out="$("${ADB[@]}" shell dumpsys activity activities 2>/dev/null | tr -d '\r' || true)"

  line="$(printf '%s\n' "$out" | sed -n \
    -e 's/.*mResumedActivity:.* \([a-zA-Z0-9._]\+\/[^ ]\+\).*/\1/p' \
    -e 's/.*topResumedActivity=.* \([a-zA-Z0-9._]\+\/[^ ]\+\).*/\1/p' \
    -e 's/.*mTopResumedActivity:.* \([a-zA-Z0-9._]\+\/[^ ]\+\).*/\1/p' \
    | head -n 1)"

  if [[ -n "${line:-}" ]]; then
    echo "$line"
    return 0
  fi

  out="$("${ADB[@]}" shell dumpsys window windows 2>/dev/null | tr -d '\r' || true)"
  line="$(printf '%s\n' "$out" | sed -n \
    -e 's/.*mCurrentFocus.* \([a-zA-Z0-9._]\+\/[^} ]\+\).*/\1/p' \
    -e 's/.*mFocusedApp.* \([a-zA-Z0-9._]\+\/[^} ]\+\).*/\1/p' \
    | head -n 1)"

  if [[ -n "${line:-}" ]]; then
    echo "$line"
    return 0
  fi

  echo "UNKNOWN (could not parse current foreground app)" >&2
  return 1
}

case "$MODE" in
  list)    list_pkgs | apply_filter;;
  listf)   list_pkgs_with_path | apply_filter;;
  current) current_foreground;;
  *) echo "Unknown mode: $MODE" >&2; usage; exit 2;;
esac
