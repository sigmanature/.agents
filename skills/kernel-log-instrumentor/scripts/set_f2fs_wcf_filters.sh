#!/usr/bin/env bash
set -euo pipefail

# Set f2fs write_cache_folios KV printk inode filters on an Android device via adb.
#
# This matches the Pixel common kernel instrumentation that adds:
#   /sys/fs/f2fs/<id>/dbg_wcf_ino1
#   /sys/fs/f2fs/<id>/dbg_wcf_ino2
#
# Usage:
#   ./set_f2fs_wcf_filters.sh --fs-id userdata --ino1 123 --ino2 456
#   ./set_f2fs_wcf_filters.sh --fs-id userdata --clear
#
# Notes:
# - Requires root on device (su).
# - If you don't know <fs-id>, run:
#     adb shell su -c 'ls -1 /sys/fs/f2fs'

FS_ID=""
INO1=""
INO2=""
CLEAR=0
SERIAL=""

usage() {
  cat <<'EOF'
set_f2fs_wcf_filters.sh

Options:
  --serial <SERIAL>   adb device serial (optional if single device)
  --fs-id <ID>        f2fs sysfs id under /sys/fs/f2fs (e.g. userdata) (required)
  --ino1 <N>          inode number for filter slot 1
  --ino2 <N>          inode number for filter slot 2
  --clear             set both ino slots to 0
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial) SERIAL="$2"; shift 2;;
    --fs-id) FS_ID="$2"; shift 2;;
    --ino1) INO1="$2"; shift 2;;
    --ino2) INO2="$2"; shift 2;;
    --clear) CLEAR=1; shift 1;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$FS_ID" ]]; then
  echo "ERROR: --fs-id is required" >&2
  exit 1
fi

ADB=(adb)
if [[ -n "$SERIAL" ]]; then
  ADB=(adb -s "$SERIAL")
fi

SYS_BASE="/sys/fs/f2fs/$FS_ID"
P1="$SYS_BASE/dbg_wcf_ino1"
P2="$SYS_BASE/dbg_wcf_ino2"

if [[ "$CLEAR" == "1" ]]; then
  INO1="0"
  INO2="0"
fi

if [[ -z "${INO1:-}" ]]; then INO1="0"; fi
if [[ -z "${INO2:-}" ]]; then INO2="0"; fi

echo "[+] Setting f2fs write_cache_folios filters:"
echo "    fs-id=$FS_ID ino1=$INO1 ino2=$INO2"

"${ADB[@]}" shell su -c "sh -c 'echo $INO1 > $P1; echo $INO2 > $P2; cat $P1; cat $P2'" || {
  echo "ERROR: failed to write sysfs. Verify kernel has dbg_wcf_ino1/2 and device is rooted." >&2
  exit 1
}

