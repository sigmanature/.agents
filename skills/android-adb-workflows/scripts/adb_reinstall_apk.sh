#!/usr/bin/env bash
set -euo pipefail

SERIAL=""
PACKAGE=""
APK=""
KEEP_DATA=0
ALLOW_DOWNGRADE=0
GRANT_PERMS=0
LAUNCH=0

usage() {
  cat <<'EOF'
adb_reinstall_apk.sh: uninstall + reinstall an app via adb

Usage:
  adb_reinstall_apk.sh --package <pkg.name> --apk <path.apk> [options]

Required:
  --package <pkg.name>     Package name (e.g. com.UCMobile)
  --apk <path.apk>         Host path to APK to install

Options:
  -s, --serial <serial>    Target a specific device
  --keep-data              Reinstall (do not uninstall); uses adb install -r
  --allow-downgrade        Pass -d to adb install
  --grant                  Grant all runtime permissions (-g)
  --launch                 Smoke-launch via monkey after install
  -h, --help               Show help

Examples:
  ./adb_reinstall_apk.sh --package com.UCMobile --apk downloads/top50/top50/com.UCMobile.apk
  ./adb_reinstall_apk.sh -s ABC123 --package com.example --apk /tmp/app.apk --allow-downgrade --grant --launch
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="${2:-}"; shift 2;;
    --package) PACKAGE="${2:-}"; shift 2;;
    --apk) APK="${2:-}"; shift 2;;
    --keep-data) KEEP_DATA=1; shift;;
    --allow-downgrade) ALLOW_DOWNGRADE=1; shift;;
    --grant) GRANT_PERMS=1; shift;;
    --launch) LAUNCH=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$PACKAGE" || -z "$APK" ]]; then
  echo "Missing required args: --package and --apk" >&2
  usage
  exit 2
fi

if [[ ! -f "$APK" ]]; then
  echo "APK not found: $APK" >&2
  exit 2
fi

ADB=(adb)
[[ -n "$SERIAL" ]] && ADB+=( -s "$SERIAL" )

echo "[1/4] Target device:"
"${ADB[@]}" devices -l

if [[ "$KEEP_DATA" -eq 0 ]]; then
  echo "[2/4] Uninstalling $PACKAGE"
  # If the package isn't installed, adb uninstall prints Failure [..]. Treat as non-fatal.
  if ! "${ADB[@]}" uninstall "$PACKAGE"; then
    echo "Uninstall failed (maybe not installed). Continuing." >&2
  fi
else
  echo "[2/4] Skipping uninstall (--keep-data)"
fi

echo "[3/4] Installing from $APK"
install_args=(install)
[[ "$KEEP_DATA" -eq 1 ]] && install_args+=( -r )
[[ "$ALLOW_DOWNGRADE" -eq 1 ]] && install_args+=( -d )
[[ "$GRANT_PERMS" -eq 1 ]] && install_args+=( -g )
install_args+=( "$APK" )

"${ADB[@]}" "${install_args[@]}"

echo "[4/4] Verify install"
"${ADB[@]}" shell pm path "$PACKAGE" || true

if [[ "$LAUNCH" -eq 1 ]]; then
  echo "Launching (monkey 1 event)..."
  "${ADB[@]}" shell monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1 >/dev/null
fi

