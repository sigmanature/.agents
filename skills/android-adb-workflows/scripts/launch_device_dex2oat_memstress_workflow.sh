#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
launch_device_dex2oat_memstress_workflow.sh

Launch a self-contained workflow on the device itself:
  - device-side dex2oat loop: delete-dexopt + compile, switching every 15ms
  - device-side app churn: launch/hold packages without stopping
  - device-side preserve watcher: hardlink odex/vdex and detect tombstones
  - on tombstone: stop dex2oat and churn, preserve extra artifacts, record info

The device runs everything after a single adb shell command. You can disconnect
USB and the phone will keep running until a tombstone is detected.

Usage:
  launch_device_dex2oat_memstress_workflow.sh \
    --serial <SERIAL> \
    --packages-file <FILE> \
    --targets-file <FILE> \
    [options]

Options:
  -s, --serial <SERIAL>       adb device serial
  --packages-file <FILE>     one package name per line
  --targets-file <FILE>     one odex/vdex path per line
  -h, --help                show this help
EOF
}

SERIAL=""
PACKAGES_FILE=""
TARGETS_FILE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    -s|--serial) SERIAL="${2:?missing serial}"; shift 2 ;;
    --packages-file) PACKAGES_FILE="${2:?missing packages file}"; shift 2 ;;
    --targets-file) TARGETS_FILE="${2:?missing targets file}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$SERIAL" ] || [ -z "$PACKAGES_FILE" ] || [ -z "$TARGETS_FILE" ]; then
  usage >&2
  exit 2
fi

if [ ! -f "$PACKAGES_FILE" ] || [ ! -f "$TARGETS_FILE" ]; then
  echo "Error: packages or targets file not found" >&2
  exit 2
fi

adb -s "$SERIAL" wait-for-device

BASE_DIR="/data/local/tmp/dex2oat_memstress_workflow"
DEVICE_SCRIPT="$BASE_DIR/workflow.sh"

# Stop any previous run.
adb -s "$SERIAL" shell "cat $BASE_DIR/main.pid 2>/dev/null | xargs -r kill 2>/dev/null || true" >/dev/null 2>&1 || true
sleep 0.5

adb -s "$SERIAL" shell "mkdir -p $BASE_DIR/preserved $BASE_DIR/logs"
adb -s "$SERIAL" push "$PACKAGES_FILE" "$BASE_DIR/packages.txt" >/dev/null
adb -s "$SERIAL" push "$TARGETS_FILE" "$BASE_DIR/targets.txt" >/dev/null
adb -s "$SERIAL" push /tmp/new_device_workflow.sh "$DEVICE_SCRIPT" >/dev/null
adb -s "$SERIAL" shell "chmod 755 $DEVICE_SCRIPT"

# Start on device with su + nohup so it survives adb disconnect.
adb -s "$SERIAL" shell "su -c 'nohup sh $DEVICE_SCRIPT > $BASE_DIR/start.log 2>&1 &'"
sleep 1

echo ""
echo "Device-side workflow launched."
adb -s "$SERIAL" shell "cat $BASE_DIR/start.log"
echo ""
echo "To check status:"
echo "  adb -s $SERIAL shell 'cat $BASE_DIR/tombstone_triggered.txt 2>/dev/null || echo no_tombstone_yet'"
echo ""
echo "To pull artifacts after tombstone:"
echo "  adb -s $SERIAL shell 'su -c \"tar -czf $BASE_DIR/artifacts.tgz $BASE_DIR/preserved $BASE_DIR/logs $BASE_DIR/tombstone_triggered.txt\"'"
echo "  adb -s $SERIAL pull $BASE_DIR/artifacts.tgz ./artifacts.tgz"
