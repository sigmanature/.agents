#!/usr/bin/env bash
set -euo pipefail

SER="${SER:-}"
OUTDIR="${OUTDIR:-}"
APK_ON_DEVICE="${APK_ON_DEVICE:-}"

usage() {
  cat <<'EOF'
Usage:
  pkgxml_install_reboot_capture.sh --serial <SERIAL> [--out <OUTDIR>] [--apk <DEVICE_APK_PATH>]

Purpose:
  Reproduce "install one app -> reboot -> PackageManager packages.xml open failed: EINVAL"
  and capture both dmesg + logcat into a timestamped folder.

Defaults:
  - If --apk not set, picks the first APK under /system/app or /system/priv-app.

Outputs:
  OUTDIR/:
    - stat_before.txt / stat_after.txt
    - pm_install.txt
    - dmesg_after.txt
    - logcat_all_after.txt
    - grep_summary.txt
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial|-s) SER="$2"; shift 2;;
    --out|-o) OUTDIR="$2"; shift 2;;
    --apk) APK_ON_DEVICE="$2"; shift 2;;
    --help|-h) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "${SER}" ]]; then
  # If only one device is connected, auto-pick it.
  SER="$(adb devices | awk 'NR>1 && $2=="device" {print $1}' | head -n 1)"
fi
if [[ -z "${SER}" ]]; then
  echo "No adb device found. Provide --serial." >&2
  exit 2
fi

if [[ -z "${OUTDIR}" ]]; then
  OUTDIR="$PWD/pkgxml_repro_$(date +%Y%m%d_%H%M%S)"
fi
mkdir -p "$OUTDIR"

adb -s "$SER" shell su -c 'stat -c "%i %s %n" /data/system/packages.xml /data/system/packages.xml.reservecopy 2>/dev/null || true' \
  | tee "$OUTDIR/stat_before.txt"

adb -s "$SER" shell su -c 'lsattr -a /data/system/packages.xml /data/system/packages.xml.reservecopy 2>/dev/null || true' \
  | tee "$OUTDIR/lsattr_before.txt"

adb -s "$SER" shell su -c 'logcat -b all -c; dmesg -c >/dev/null 2>&1 || true'
STARTTAG="PKGXML_REPRO_START_$(date +%s)"
adb -s "$SER" shell su -c "log -t $STARTTAG begin"

if [[ -z "${APK_ON_DEVICE}" ]]; then
  APK_ON_DEVICE="$(adb -s "$SER" shell 'ls -1 /system/app/*/*.apk /system/priv-app/*/*.apk 2>/dev/null | head -n 1' | tr -d '\r')"
fi
echo "APK_ON_DEVICE=$APK_ON_DEVICE" | tee "$OUTDIR/apk_choice.txt"

adb -s "$SER" shell pm install -r "$APK_ON_DEVICE" | tee "$OUTDIR/pm_install.txt" || true
adb -s "$SER" shell su -c 'sync'

adb -s "$SER" reboot
adb -s "$SER" wait-for-device
for _ in $(seq 1 180); do
  bc="$(adb -s "$SER" shell getprop sys.boot_completed | tr -d '\r')"
  [[ "$bc" == "1" ]] && break
  sleep 1
done

adb -s "$SER" shell su -c 'dmesg -T' > "$OUTDIR/dmesg_after.txt" || true
adb -s "$SER" shell logcat -b all -d > "$OUTDIR/logcat_all_after.txt" || true

adb -s "$SER" shell su -c 'stat -c "%i %s %n" /data/system/packages.xml /data/system/packages.xml.reservecopy 2>/dev/null || true' \
  > "$OUTDIR/stat_after.txt" || true
adb -s "$SER" shell su -c 'lsattr -a /data/system/packages.xml /data/system/packages.xml.reservecopy 2>/dev/null || true' \
  > "$OUTDIR/lsattr_after.txt" || true

rg -n 'F2FS_PKGXML|FSCRYPT_OPEN_ERR|fs-verity .*packages\\.xml|Unrecognized descriptor version|pm_critical_info: Error reading package manager settings|packages\\.xml: open failed: EINVAL' \
  "$OUTDIR/dmesg_after.txt" "$OUTDIR/logcat_all_after.txt" \
  | tee "$OUTDIR/grep_summary.txt" || true

echo "Saved to $OUTDIR"

