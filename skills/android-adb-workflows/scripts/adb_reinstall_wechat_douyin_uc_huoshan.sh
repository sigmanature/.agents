#!/usr/bin/env bash
set -euo pipefail

SERIAL=""
BASE_DIR=""
ALLOW_DOWNGRADE=0
GRANT_PERMS=0
LAUNCH=0

usage() {
  cat <<'EOF'
adb_reinstall_wechat_douyin_uc_huoshan.sh: uninstall + reinstall WeChat/UC/Douyin/Huoshan

This is a thin wrapper around scripts/adb_reinstall_apk.sh.

Packages:
  - WeChat:         com.tencent.mm
  - UC Browser:     com.UCMobile
  - Douyin:         com.ss.android.ugc.aweme
  - Douyin Huoshan: com.ss.android.ugc.live

Usage:
  adb_reinstall_wechat_douyin_uc_huoshan.sh [options]

Options:
  -s, --serial <serial>     Target device serial
  --base-dir <dir>          Search APKs under this dir (defaults to current dir)
  --allow-downgrade         Pass -d to adb install
  --grant                   Grant runtime permissions (-g)
  --launch                  Smoke-launch via monkey after each install
  -h, --help                Show help

Examples:
  ./adb_reinstall_wechat_douyin_uc_huoshan.sh -s 21121FDF600C4G --base-dir /home/nzzhao/learn_os/output/top100_install_20260325_dual --allow-downgrade --grant --launch
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--serial) SERIAL="${2:-}"; shift 2;;
    --base-dir) BASE_DIR="${2:-}"; shift 2;;
    --allow-downgrade) ALLOW_DOWNGRADE=1; shift;;
    --grant) GRANT_PERMS=1; shift;;
    --launch) LAUNCH=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

BASE_DIR="${BASE_DIR:-$PWD}"
if [[ ! -d "$BASE_DIR" ]]; then
  echo "Base dir not found: $BASE_DIR" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ONE_REINSTALL="$SCRIPT_DIR/adb_reinstall_apk.sh"

if [[ ! -x "$ONE_REINSTALL" ]]; then
  echo "Missing helper: $ONE_REINSTALL" >&2
  exit 2
fi

pick_apk() {
  local pattern="$1"
  local search_roots=(
    "$BASE_DIR/downloads"
    "$BASE_DIR/top100_apks"
    "$BASE_DIR"
  )

  local best=""
  local best_ts="-1"

  for root in "${search_roots[@]}"; do
    [[ -d "$root" ]] || continue
    while IFS= read -r f; do
      [[ -f "$f" ]] || continue
      local ts
      ts="$(stat -c '%Y' "$f" 2>/dev/null || echo 0)"
      if [[ "$ts" -gt "$best_ts" ]]; then
        best_ts="$ts"
        best="$f"
      fi
    done < <(find "$root" -type f -iname '*.apk' 2>/dev/null | rg -i "$pattern" || true)
  done

  [[ -n "$best" ]] && echo "$best"
}

wechat_apk="$(pick_apk '(^|/)weixin[^/]*\.apk$|com\.tencent\.mm|weixin' || true)"
uc_apk="$(pick_apk '(^|/)com\.UCMobile\.apk$|com\.ucmobile|ucmobile|ucbrowser' || true)"
douyin_apk="$(pick_apk '(^|/)com\.ss\.android\.ugc\.aweme\.apk$' || true)"
huoshan_apk="$(pick_apk '(^|/)com\.ss\.android\.ugc\.live\.apk$|(^|/).*huoshan.*\.apk$|(^|/).*hotsoon.*\.apk$' || true)"

missing=0
[[ -z "$wechat_apk" ]] && echo "WeChat APK not found under $BASE_DIR" >&2 && missing=1
[[ -z "$uc_apk" ]] && echo "UC APK not found under $BASE_DIR" >&2 && missing=1
[[ -z "$douyin_apk" ]] && echo "Douyin APK not found under $BASE_DIR" >&2 && missing=1
[[ -z "$huoshan_apk" ]] && echo "Huoshan APK not found under $BASE_DIR" >&2 && missing=1
[[ "$missing" -eq 1 ]] && exit 2

common_args=()
[[ -n "$SERIAL" ]] && common_args+=( -s "$SERIAL" )
[[ "$ALLOW_DOWNGRADE" -eq 1 ]] && common_args+=( --allow-downgrade )
[[ "$GRANT_PERMS" -eq 1 ]] && common_args+=( --grant )
[[ "$LAUNCH" -eq 1 ]] && common_args+=( --launch )

echo "Base dir: $BASE_DIR"
echo "WeChat APK:   $wechat_apk"
echo "UC APK:      $uc_apk"
echo "Douyin APK:  $douyin_apk"
echo "Huoshan APK: $huoshan_apk"

"$ONE_REINSTALL" "${common_args[@]}" --package com.tencent.mm --apk "$wechat_apk"
"$ONE_REINSTALL" "${common_args[@]}" --package com.UCMobile --apk "$uc_apk"
"$ONE_REINSTALL" "${common_args[@]}" --package com.ss.android.ugc.aweme --apk "$douyin_apk"
"$ONE_REINSTALL" "${common_args[@]}" --package com.ss.android.ugc.live --apk "$huoshan_apk"

echo "Done."
