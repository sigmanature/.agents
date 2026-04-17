#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: cf_boot_from_dist.sh --dist DIR [--run-root DIR] [--name NAME] [--extra-launch-args '...']

Boot Cuttlefish from local dist artifacts:
  - cvd-host_package.tar.gz
  - aosp_cf_*_img*.zip

Examples:
  ./scripts/cf_boot_from_dist.sh --dist /home/nzzhao/learn_os/pixel/out/dist
  ./scripts/cf_boot_from_dist.sh --dist /path/to/dist --name sqlite-fsync --extra-launch-args '--resume=false'
EOF
}

DIST=""
RUN_ROOT="$HOME/cf_runs"
NAME="pixel-fsync-$(date +%Y%m%d_%H%M%S)"
EXTRA="--resume=false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dist) DIST="$2"; shift 2;;
    --run-root) RUN_ROOT="$2"; shift 2;;
    --name) NAME="$2"; shift 2;;
    --extra-launch-args) EXTRA="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$DIST" ]]; then echo "--dist is required" >&2; usage; exit 2; fi
if [[ ! -d "$DIST" ]]; then echo "dist dir not found: $DIST" >&2; exit 1; fi

HOST_PKG=$(find "$DIST" -maxdepth 1 -type f -name 'cvd-host_package.tar.gz' | head -n1 || true)
IMG_ZIP=$(find "$DIST" -maxdepth 1 -type f \( -name 'aosp_cf_*_img*.zip' -o -name 'aosp_cf_*.zip' \) | head -n1 || true)

if [[ -z "$HOST_PKG" ]]; then echo "missing cvd-host_package.tar.gz in $DIST" >&2; exit 1; fi
if [[ -z "$IMG_ZIP" ]]; then echo "missing aosp_cf_*_img*.zip in $DIST" >&2; exit 1; fi

RUN_DIR="$RUN_ROOT/$NAME"
mkdir -p "$RUN_DIR"
cd "$RUN_DIR"

echo "[cf] run dir: $RUN_DIR"
echo "[cf] host pkg: $HOST_PKG"
echo "[cf] image zip: $IMG_ZIP"

tar -xzf "$HOST_PKG"
unzip -oq "$IMG_ZIP"

# shellcheck disable=SC2086
HOME="$RUN_DIR" ./bin/launch_cvd --daemon $EXTRA
HOME="$RUN_DIR" ./bin/adb wait-for-device
HOME="$RUN_DIR" ./bin/adb devices
HOME="$RUN_DIR" ./bin/adb root || true
HOME="$RUN_DIR" ./bin/adb wait-for-device
HOME="$RUN_DIR" ./bin/adb shell 'echo device=$(getprop ro.product.device) build=$(getprop ro.build.type) sdk=$(getprop ro.build.version.sdk); id; cat /proc/self/status | grep CapEff || true'

echo "$RUN_DIR" > "$RUN_DIR/RUN_DIR.txt"
echo "[cf] boot complete: $RUN_DIR"
